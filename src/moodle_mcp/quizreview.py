"""Turn Moodle quiz review HTML (one question) into readable Markdown: question, my answer, correctness, feedback."""

from __future__ import annotations

import html as htmllib
import re
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag

from .html import clean_inline, to_markdown, to_text

_MARKS = {"correct": "✓", "incorrect": "✗", "partiallycorrect": "½"}


def _verdict(el: Tag | None) -> str:
    """Correctness of a control: from its classes, its container's classes or the icon after it."""
    if el is None:
        return ""
    for node in (el, el.parent):
        if isinstance(node, Tag):
            classes = " ".join(node.get("class") or [])
            for key in ("partiallycorrect", "incorrect", "correct"):
                if re.search(rf"(?:^|\s|-|\d){key}(?:\s|$)", classes):
                    return _MARKS[key]
    sib = el.find_next_sibling("i") if isinstance(el, Tag) else None
    if sib is None and isinstance(el.parent, Tag):
        sib = el.parent.find("i", title=True)
    if isinstance(sib, Tag):
        title = (sib.get("title") or "").lower()
        if "partially" in title:
            return "½"
        if "incorrect" in title or "falsch" in title:
            return "✗"
        if "correct" in title or "richtig" in title:
            return "✓"
    return ""


def _popover_answer(container: Tag | None) -> str:
    """Cloze (multianswer) puts feedback incl. the right answer into a popover attribute."""
    if container is None:
        return ""
    trigger = container.find("a", attrs={"data-bs-content": True}) or container.find("a", attrs={"data-content": True})
    if not isinstance(trigger, Tag):
        return ""
    content = htmllib.unescape(trigger.get("data-bs-content") or trigger.get("data-content") or "")
    text = to_text(content.replace("<br />", "\n").replace("<br>", "\n"))
    m = re.search(r"(?:correct answer is|richtige Antwort ist)\s*:?\s*(.+?)(?:\s+(?:Mark|Marks|Erreichte Punkte|Punkte)\b|$)", text, re.I)
    return clean_inline(m.group(1)) if m else ""


def _replace(el: Tag, text: str) -> None:
    el.replace_with(NavigableString(f" {text} "))


def _transform(form: Tag) -> None:
    # Drop UI chrome first.
    for sel in ("h4", "input[type=hidden]:not(.placeinput)", ".accesshide", ".visually-hidden", ".sr-only",
                "label.subq", ".questionflag", "legend"):
        for el in form.select(sel):
            el.decompose()

    # Drag & drop into text: placeholders + hidden inputs with the chosen choice number.
    choices: dict[tuple[str, str], str] = {}
    for home in form.select("span.draghome"):
        classes = home.get("class") or []
        choice = next((c[6:] for c in classes if c.startswith("choice")), None)
        group = next((c[5:] for c in classes if c.startswith("group")), "1")
        if choice:
            choices[(group, choice)] = clean_inline(home.get_text(" "))
    if choices:
        values = {}
        for inp in form.select("input.placeinput"):
            classes = inp.get("class") or []
            place = next((c[5:] for c in classes if c.startswith("place") and c != "placeinput"), None)
            group = next((c[5:] for c in classes if c.startswith("group")), "1")
            if place:
                values[place] = choices.get((group, str(inp.get("value") or "")), "")
            inp.decompose()
        for drop in form.select("span.drop"):
            classes = drop.get("class") or []
            place = next((c[5:] for c in classes if c.startswith("place")), None)
            verdict = _verdict(drop)
            icon = drop.find_next_sibling("i")
            if isinstance(icon, Tag):
                verdict = verdict or _verdict(icon.parent) or ""
                title = (icon.get("title") or "").lower()
                verdict = verdict or ("✗" if "incorrect" in title else "✓" if "correct" in title else "")
                icon.decompose()
            answer = values.get(place or "", "") or "(empty)"
            _replace(drop, f"[{answer}{' ' + verdict if verdict else ''}]")
        for box in form.select(".answercontainer, .draggrouphomes1, .draghomes"):
            box.decompose()

    # Dropdowns (match, gapselect, cloze).
    for sel in form.find_all("select"):
        chosen = sel.find("option", selected=True)
        answer = clean_inline(chosen.get_text(" ")) if isinstance(chosen, Tag) else ""
        if not answer or answer.lower() in ("choose...", "auswählen..."):
            answer = "(no answer)"
        verdict = _verdict(sel)
        container = sel.find_parent(class_="subquestion") or sel.parent
        right = _popover_answer(container if isinstance(container, Tag) else None)
        extra = f" — correct: {right}" if right and verdict != "✓" else ""
        for icon in (container.find_all("i") if isinstance(container, Tag) else []):
            icon.decompose()
        for trigger in (container.find_all("a", attrs={"data-bs-content": True}) if isinstance(container, Tag) else []):
            trigger.decompose()
        _replace(sel, f"[{answer}{' ' + verdict if verdict else ''}{extra}]")

    # Text inputs (shortanswer, numerical, cloze gaps).
    for inp in form.find_all("input", attrs={"type": "text"}):
        value = (inp.get("value") or "").strip() or "(no answer)"
        verdict = _verdict(inp)
        container = inp.find_parent(class_="subquestion") or inp.parent
        right = _popover_answer(container if isinstance(container, Tag) else None)
        extra = f" — correct: {right}" if right and verdict != "✓" else ""
        if isinstance(container, Tag):
            for icon in container.find_all("i"):
                icon.decompose()
            for trigger in container.find_all("a", attrs={"data-bs-content": True}):
                trigger.decompose()
        _replace(inp, f"[{value}{' ' + verdict if verdict else ''}{extra}]")

    # Choice rows (multichoice, truefalse, multichoiceset): one line per option.
    for inp in form.find_all("input", attrs={"type": re.compile("^(radio|checkbox)$")}):
        checked = inp.has_attr("checked")
        row = inp.find_parent("div", class_=re.compile(r"^r\d$")) or inp.parent
        verdict = _verdict(row) if checked else ""
        marker = f"[{'x' if checked else ' '}]{verdict}"
        if isinstance(row, Tag) and row.name == "div":
            label = row.find(attrs={"data-region": "answer-label"}) or row.find("label") or row
            for icon in row.find_all("i"):
                icon.decompose()
            text = " ".join(to_markdown(str(label)).split())
            new = BeautifulSoup("<p></p>", "html.parser").p
            assert new is not None
            new.string = f"{marker} {text}"
            row.replace_with(new)
        else:
            _replace(inp, marker)

    for icon in form.find_all("i"):
        icon.decompose()


def _choice_rows_to_lines(md: str) -> str:
    """Put each '[x]'/'[ ]' option on its own bullet line."""
    md = re.sub(r"\s*\[(x| )\](✓|✗|½)?\s*", lambda m: f"\n- [{m.group(1)}]{m.group(2) or ''} ", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def render_question(q: dict[str, Any], *, include_feedback: bool = True) -> str:
    """Render one question of mod_quiz_get_attempt_review."""
    soup = BeautifulSoup(q.get("html") or "", "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    number = q.get("questionnumber") or q.get("number") or q.get("slot")
    qtype = q.get("type") or "?"
    mark, maxmark = q.get("mark"), q.get("maxmark")
    status = clean_inline(q.get("status") or "")
    head = f"### Q{number} · {qtype}"
    if maxmark not in (None, "", 0, "0"):
        head += f" · {mark if mark not in (None, '') else '–'}/{maxmark}"
    if status:
        head += f" · {status}"
    if qtype == "description":
        form = soup.select_one(".formulation") or soup
        return f"### Info\n{to_markdown(str(form))}"
    form = soup.select_one(".formulation")
    body = ""
    if isinstance(form, Tag):
        _transform(form)
        body = to_markdown(str(form))
        if qtype in ("multichoice", "truefalse", "multichoiceset") or re.search(r"\[(x| )\]", body):
            body = _choice_rows_to_lines(body)
    parts = [head, body]
    if include_feedback:
        outcome = soup.select_one(".outcome")
        if isinstance(outcome, Tag):
            for cls, label in (("specificfeedback", "Feedback"), ("numpartscorrect", ""), ("generalfeedback", "Explanation"),
                               ("rightanswer", "")):
                el = outcome.select_one(f".{cls}")
                if isinstance(el, Tag):
                    text = to_markdown(str(el)).strip()
                    if text:
                        parts.append(f"**{label}:** {text}" if label else f"**{text}**" if cls == "rightanswer" else text)
        comment = soup.select_one(".comment")
        if isinstance(comment, Tag):
            text = to_markdown(str(comment)).strip()
            if text:
                parts.append(f"**Teacher comment:** {text}")
    return "\n\n".join(p for p in parts if p)


def is_wrong(q: dict[str, Any]) -> bool:
    state = (q.get("state") or "").lower()
    if state in ("gradedwrong", "gradedpartial", "gaveup", "mangrwrong", "mangrpartial"):
        return True
    try:
        return float(str(q.get("mark") or "0").replace(",", ".")) < float(q.get("maxmark") or 0)
    except ValueError:
        return False
