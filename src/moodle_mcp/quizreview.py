"""Turn Moodle quiz question HTML (one question) into readable Markdown: question, answer options, my answer,
correctness, feedback. Handles reviews of finished attempts and questions of an attempt in progress."""

from __future__ import annotations

import html as htmllib
import json
import re
from typing import Any, Literal

from bs4 import BeautifulSoup, NavigableString, Tag

from .html import clean_inline, to_markdown, to_text

Mode = Literal["review", "attempt"]
PICTURE = "picture:"  # pseudo URL of a picture collected into `pictures` (inline SVG, data: URI, other host)

_MARKS = {"correct": "✓", "incorrect": "✗", "partiallycorrect": "½"}
_SVG_TAG = re.compile(r"<(/?)svg\b[^>]*?(/?)>", re.I)
_SVG_SHAPES = ["rect", "path", "line", "polyline", "polygon", "circle", "ellipse", "image", "foreignobject", "use"]
_DRAWIO_NOTICE = "Viewer does not support full SVG"
_PLACEHOLDER_OPTION = re.compile(r"^(?:choose|auswählen|wählen)\b", re.I)
_EMPTY = "___"
_HINTS = {
    "multichoiceset": "choose all correct options (all-or-nothing)",
    "truefalse": "choose True or False",
    "shortanswer": "type a short text answer",
    "numerical": "type a number",
    "calculated": "type a number",
    "calculatedsimple": "type a number",
    "essay": "write a free-text answer",
    "match": "pick the matching option for every item",
    "randomsamatch": "pick the matching option for every item",
    "gapselect": "pick a word for every gap",
    "ddwtos": "drag one of the choices into every blank",
    "ddimageortext": "drag the items onto the drop zones of the image",
    "ddmarker": "place the markers on the image",
    "multianswer": "fill in every embedded field (dropdowns, text gaps, choices)",
    "ordering": "put the items into the right order",
    "gapfill": "fill in the gaps",
}


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


def _is_icon(el: Any) -> bool:
    """<i> used as an icon (Font Awesome, ✓/✗ marks) — unlike <i> for italic text, which has text."""
    if not isinstance(el, Tag) or el.name != "i":
        return False
    classes = el.get("class") or []
    return not el.get_text(strip=True) or "icon" in classes or any(c == "fa" or c.startswith("fa-") for c in classes)


def _drop_icons(root: Tag) -> None:
    for icon in [i for i in root.find_all("i") if _is_icon(i)]:
        icon.decompose()


# ---------------------------------------------------------------- inline SVG (code listings, diagrams)

def _extract_svgs(html: str) -> tuple[str, list[str]]:
    """Cut top-level <svg>…</svg> out of the raw HTML (the HTML parser would lowercase viewBox & co.)."""
    svgs: list[str] = []
    parts: list[str] = []
    depth = start = last = 0
    for m in _SVG_TAG.finditer(html):
        if m.group(1):
            if depth == 0:
                continue
            depth -= 1
            if depth == 0:
                parts.append(html[last:start] + f'<span data-mcp-svg="{len(svgs)}"></span>')
                svgs.append(html[start:m.end()])
                last = m.end()
        elif not m.group(2):
            if depth == 0:
                start = m.start()
            depth += 1
    parts.append(html[last:])
    return "".join(parts), svgs


def _svg_texts(svg: str) -> list[tuple[float, float, str]]:
    """(y, x, text) of every <text> element."""
    out = []
    for t in BeautifulSoup(svg, "html.parser").find_all("text"):
        text = t.get_text().replace("\xa0", " ").rstrip()
        if not text.strip() or _DRAWIO_NOTICE in text:
            continue
        try:
            y, x = float(t.get("y") or 0), float(t.get("x") or 0)
        except ValueError:
            y = x = 0.0
        out.append((y, x, text))
    return out


def svg_labels(svg: str) -> list[str]:
    """The distinct texts of a diagram, in reading order."""
    seen: dict[str, None] = {}
    for _, _, text in sorted(_svg_texts(svg)):
        seen.setdefault(" ".join(text.split()), None)
    return list(seen)[:60]


def _svg_node(svg: str, pictures: list[str] | None) -> Tag | NavigableString:
    """Text-only SVG (code rendered as a picture) → <pre>; a diagram → image placeholder or its labels."""
    soup = BeautifulSoup(svg, "html.parser")
    texts = _svg_texts(svg)
    if texts and not soup.find(_SVG_SHAPES):
        lines: dict[float, list[tuple[float, str]]] = {}
        for y, x, text in texts:
            lines.setdefault(round(y), []).append((x, text))
        pre = BeautifulSoup("<pre></pre>", "html.parser").pre
        assert pre is not None
        pre.string = "\n".join(" ".join(t for _, t in sorted(row)) for _, row in sorted(lines.items()))
        return pre
    if pictures is not None:
        pictures.append(svg)
        return NavigableString(f" ![diagram]({PICTURE}{len(pictures) - 1}) ")
    labels = svg_labels(svg)
    return NavigableString(f" [diagram{' — labels: ' + ', '.join(labels) if labels else ''}] ")


def _collect_picture(img: Tag, pictures: list[str] | None) -> None:
    """A picture the Markdown converter would drop: embedded (data:) or hosted elsewhere (e.g. pasted from
    Google Docs). Moodle files and theme icons are left to the converter."""
    src = str(img.get("src") or "").strip()
    if not src.startswith(("data:image/", "https://", "http://")) or "pluginfile.php" in src:
        return
    if src.startswith("http") and any(p in src for p in ("/theme/image.php", "/pix/")):
        return
    alt = re.sub(r"[\[\]]", "", clean_inline(img.get("alt") or img.get("title") or ""))
    if pictures is None:
        _replace(img, f"[image{': ' + alt if alt else ''}]")
        return
    pictures.append(src)
    _replace(img, f"![{alt or 'picture'}]({PICTURE}{len(pictures) - 1})")


def _xml_entities(svg: str) -> str:
    """HTML named entities (&nbsp; …) are not valid XML: turn them into numeric references."""
    def sub(m: re.Match[str]) -> str:
        if m.group(1) in ("amp", "lt", "gt", "quot", "apos"):
            return m.group(0)
        char = htmllib.unescape(m.group(0))
        return f"&#{ord(char)};" if len(char) == 1 else m.group(0)

    return re.sub(r"&([A-Za-z][A-Za-z0-9]*);", sub, svg)


def svg_to_png(svg: str) -> bytes:
    """Draw an inline SVG on a white background. RuntimeError when it cannot be drawn."""
    try:
        import resvg_py
    except ImportError as exc:  # optional at runtime: the text fallback still works
        raise RuntimeError("the resvg-py package is not installed") from exc
    # HTML inside <foreignObject> is often not well-formed XML; renderers fall back to the <text> next to it.
    svg = re.sub(r"<foreignObject\b.*?</foreignObject>", "", svg, flags=re.S | re.I)
    svg = re.sub(rf"<a\b[^>]*>(?:(?!</a>).)*?{_DRAWIO_NOTICE}(?:(?!</a>).)*?</a>", "", svg, flags=re.S)
    # Never let a question make the renderer read local files.
    svg = re.sub(r"<image\b[^>]*?href\s*=\s*[\"'](?!data:)[^\"']*[\"'][^>]*?(?:/>|>.*?</image>)", "", svg, flags=re.S | re.I)
    head = svg[: svg.find(">") + 1]
    if "xmlns=" not in head:
        svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    if "xlink:" in svg and "xmlns:xlink" not in head:
        svg = svg.replace("<svg", '<svg xmlns:xlink="http://www.w3.org/1999/xlink"', 1)
    try:
        return bytes(resvg_py.svg_to_bytes(svg_string=_xml_entities(svg), background="white", zoom=1.5))
    except Exception as exc:  # resvg raises ValueError / its own errors on bad SVG
        raise RuntimeError(f"the SVG could not be rendered ({str(exc)[:120]})") from exc


def _num(classes: list[str], prefix: str) -> str | None:
    """The number in a class like 'choice3' / 'place2' / 'group1' ('placeinput' does not count)."""
    for c in classes:
        if c.startswith(prefix) and c[len(prefix):].isdigit():
            return c[len(prefix):]
    return None


def _html(markup: str) -> BeautifulSoup:
    return BeautifulSoup(markup, "html.parser")


def _select_options(sel: Tag) -> list[str]:
    out = []
    for opt in sel.find_all("option"):
        text = clean_inline(opt.get_text(" "))
        if text and not _PLACEHOLDER_OPTION.match(text):
            out.append(text)
    return out


def _drag_item(home: Tag) -> str:
    """HTML for one drag item: its picture (kept as <img> so it is shown) or its escaped text."""
    img = home if home.name == "img" else home.find("img", src=re.compile("pluginfile"))
    if isinstance(img, Tag):
        return str(img)
    return htmllib.escape(clean_inline(home.get_text(" ")))


def _dd_area(area: Tag, attempt: bool) -> None:
    """Drag & drop onto an image (ddimageortext, ddmarker): background, drop zones, items and where they are."""
    items: dict[tuple[str, str], str] = {}
    reusable: set[tuple[str, str]] = set()
    markers = False
    for home in area.select(".draghome, .marker"):
        classes = home.get("class") or []
        choice = _num(classes, "choice")
        if choice is None:
            continue
        markers = markers or "marker" in classes
        key = (_num(classes, "group") or "1", choice)
        items.setdefault(key, _drag_item(home))
        if "infinite" in classes:
            reusable.add(key)
    groups = {g for g, _ in items}

    placed: dict[str, str] = {}
    for inp in area.select("input.placeinput"):
        classes = inp.get("class") or []
        place, value = _num(classes, "place"), str(inp.get("value") or "")
        if place and value not in ("", "0"):
            placed[place] = items.get((_num(classes, "group") or "1", value), f"item {value}")
    zones: list[dict[str, Any]] = []
    holder = area.find(attrs={"data-place-info": True})
    if isinstance(holder, Tag):
        try:
            info = json.loads(str(holder.get("data-place-info") or "{}"))
        except ValueError:
            info = {}
        zones = [z for z in (info.values() if isinstance(info, dict) else info) if isinstance(z, dict)]

    parts: list[str] = []
    bg = area.select_one("img.dropbackground")
    if isinstance(bg, Tag):
        bg["alt"] = "Background image"
        parts.append(f"<p>{bg}</p>")
    if zones:
        parts.append("<p>Drop zones (x, y = pixels from the top-left corner of the background image):</p><ul>")
        for z in sorted(zones, key=lambda z: int(str(z.get("no") or 0)) if str(z.get("no") or "").isdigit() else 0):
            no = str(z.get("no") or "?")
            xy = z.get("xy") or []
            line = f"zone {no}" + (f" at ({xy[0]}, {xy[1]})" if len(xy) == 2 else "")
            if z.get("text"):
                line += f" «{htmllib.escape(clean_inline(str(z['text'])))}»"
            if len(groups) > 1 and z.get("group"):
                line += f" (group {z['group']})"
            parts.append(f"<li>{line}: {placed.get(no) or ('(empty)' if not attempt else _EMPTY)}</li>")
        parts.append("</ul>")
    elif placed:
        parts.append("<ul>" + "".join(f"<li>zone {p}: {v}</li>" for p, v in sorted(placed.items())) + "</ul>")

    if markers:
        coords: dict[str, list[str]] = {}
        for inp in area.select("input.choices"):
            choice = _num(inp.get("class") or [], "choice")
            if choice is not None:
                coords[choice] = [c.strip() for c in str(inp.get("value") or "").split(";") if c.strip()]
        parts.append("<p>Markers (positions in pixels of the background image):</p><ul>")
        for (_, choice), label in sorted(items.items(), key=lambda kv: int(kv[0][1])):
            where = ", ".join(f"({c})" for c in coords.get(choice, [])) or "not placed"
            parts.append(f"<li>{label}: {where}</li>")
        parts.append("</ul>")
    elif items and (attempt or not placed):
        parts.append(_choices_html(items, reusable, "Items to drag"))
    area.replace_with(_html("".join(parts)))


def _choices_html(items: dict[tuple[str, str], str], reusable: set[tuple[str, str]], title: str) -> str:
    """'Items to drag: a | b | c' — one line per group when there are several."""
    by_group: dict[str, list[str]] = {}
    for key in sorted(items, key=lambda k: (int(k[0]), int(k[1]))):
        by_group.setdefault(key[0], []).append(items[key] + (" (reusable)" if key in reusable else ""))
    if len(by_group) == 1:
        return f"<p>{title}: {' | '.join(next(iter(by_group.values())))}</p>"
    return f"<p>{title}:</p><ul>" + "".join(f"<li>group {g}: {' | '.join(v)}</li>" for g, v in by_group.items()) + "</ul>"


def _transform(form: Tag, mode: Mode = "review") -> None:
    attempt = mode == "attempt"
    # Drag & drop onto an image first: it needs the hidden inputs that are dropped below.
    for area in form.select(".ddarea"):
        _dd_area(area, attempt)

    # Drop UI chrome.
    for sel in ("h4", "input[type=hidden]:not(.placeinput)", ".accesshide", ".visually-hidden", ".sr-only",
                "label.subq", ".questionflag", "legend", ".qtype_multichoice_clearchoice", ".im-controls"):
        for el in form.select(sel):
            el.decompose()

    # Drag & drop into text: placeholders + hidden inputs with the chosen choice number.
    choices: dict[tuple[str, str], str] = {}
    reusable: set[tuple[str, str]] = set()
    for home in form.select(".draghome"):
        classes = home.get("class") or []
        choice = _num(classes, "choice")
        if choice:
            key = (_num(classes, "group") or "1", choice)
            choices[key] = clean_inline(home.get_text(" "))
            if "infinite" in classes:
                reusable.add(key)
    if choices:
        values = {}
        for inp in form.select("input.placeinput"):
            classes = inp.get("class") or []
            place = _num(classes, "place")
            if place:
                values[place] = choices.get((_num(classes, "group") or "1", str(inp.get("value") or "")), "")
            inp.decompose()
        for drop in form.select("span.drop"):
            place = _num(drop.get("class") or [], "place")
            if attempt:
                _replace(drop, f"[blank {place}: {values.get(place or '', '') or _EMPTY}]")
                continue
            verdict = _verdict(drop)
            icon = drop.find_next_sibling("i")
            if isinstance(icon, Tag) and _is_icon(icon):
                verdict = verdict or _verdict(icon.parent) or ""
                title = (icon.get("title") or "").lower()
                verdict = verdict or ("✗" if "incorrect" in title else "✓" if "correct" in title else "")
                icon.decompose()
            answer = values.get(place or "", "") or "(empty)"
            _replace(drop, f"[{answer}{' ' + verdict if verdict else ''}]")
        for box in form.select(".answercontainer, .draggrouphomes1, .draghomes"):
            box.decompose()
        if attempt:
            escaped = {k: htmllib.escape(v) for k, v in choices.items()}
            form.append(_html(_choices_html(escaped, reusable, "Choices to drag into the blanks")))

    # Dropdowns (match, gapselect, cloze). In an attempt every dropdown lists its options — once for the
    # whole question when all dropdowns offer the same ones (typical for matching).
    selects = form.find_all("select")
    option_lists = [_select_options(s) for s in selects]
    shared = attempt and len(selects) > 1 and all(o == option_lists[0] for o in option_lists)
    for sel, options in zip(selects, option_lists):
        chosen = sel.find("option", selected=True)
        answer = clean_inline(chosen.get_text(" ")) if isinstance(chosen, Tag) else ""
        if not answer or _PLACEHOLDER_OPTION.match(answer):
            answer = _EMPTY if attempt else "(no answer)"
        if attempt:
            listing = "" if shared else f" — options: {' | '.join(options)}"
            _replace(sel, f"[▾ {answer}{listing}]")
            continue
        verdict = _verdict(sel)
        container = sel.find_parent(class_="subquestion") or sel.parent
        right = _popover_answer(container if isinstance(container, Tag) else None)
        extra = f" — correct: {right}" if right and verdict != "✓" else ""
        if isinstance(container, Tag):
            _drop_icons(container)
        for trigger in (container.find_all("a", attrs={"data-bs-content": True}) if isinstance(container, Tag) else []):
            trigger.decompose()
        _replace(sel, f"[{answer}{' ' + verdict if verdict else ''}{extra}]")
    if shared:
        form.append(_html(f"<p>Options for every dropdown: {htmllib.escape(' | '.join(option_lists[0]))}</p>"))

    # Text inputs (shortanswer, numerical, cloze gaps).
    for inp in form.find_all("input", attrs={"type": "text"}):
        value = (inp.get("value") or "").strip()
        if attempt:
            _replace(inp, f"[{value or _EMPTY}]")
            continue
        verdict = _verdict(inp)
        container = inp.find_parent(class_="subquestion") or inp.parent
        right = _popover_answer(container if isinstance(container, Tag) else None)
        extra = f" — correct: {right}" if right and verdict != "✓" else ""
        if isinstance(container, Tag):
            _drop_icons(container)
            for trigger in container.find_all("a", attrs={"data-bs-content": True}):
                trigger.decompose()
        _replace(inp, f"[{value or '(no answer)'}{' ' + verdict if verdict else ''}{extra}]")

    # Free text (essay in an attempt) and the upload field next to it.
    for area in form.find_all("textarea"):
        text = to_text(area.get_text())
        _replace(area, f"[answer text: {text or '(empty)'}]")
    if attempt:
        for box in form.select(".attachments"):
            box.replace_with(_html("<p>[file upload field — attach files in Moodle]</p>"))
        for box in form.select(".filemanager"):
            box.replace_with(_html("<p>[file upload field — attach files in Moodle]</p>"))

    # Ordering: the list shows the current order.
    for lst in form.select("ul.sortablelist"):
        lst.name = "ol"
        if attempt:
            lst.insert_before(_html("<p>Current order (top to bottom):</p>"))

    # Choice rows (multichoice, truefalse, multichoiceset): one line per option.
    for inp in form.find_all("input", attrs={"type": re.compile("^(radio|checkbox)$")}):
        checked = inp.has_attr("checked")
        row = inp.find_parent("div", class_=re.compile(r"^r\d$")) or inp.parent
        verdict = _verdict(row) if checked and not attempt else ""
        marker = f"[{'x' if checked else ' '}]{verdict}"
        if isinstance(row, Tag) and row.name == "div":
            label = row.find(attrs={"data-region": "answer-label"}) or row.find("label") or row
            _drop_icons(row)
            text = " ".join(to_markdown(str(label)).split())
            new = BeautifulSoup("<p></p>", "html.parser").p
            assert new is not None
            new.string = f"{marker} {text}"
            row.replace_with(new)
        else:
            _replace(inp, marker)

    _drop_icons(form)


def _choice_rows_to_lines(md: str) -> str:
    """Put each '[x]'/'[ ]' option on its own bullet line."""
    md = re.sub(r"\s*\[(x| )\](✓|✗|½)?\s*", lambda m: f"\n- [{m.group(1)}]{m.group(2) or ''} ", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def _answer_hint(qtype: str, form: Tag) -> str:
    if qtype in ("multichoice", "calculatedmulti"):
        multi = form.find("input", attrs={"type": "checkbox"}) is not None
        return "choose one or more options" if multi else "choose one option"
    return _HINTS.get(qtype, "")


def _points(value: Any) -> str:
    try:
        return f"{float(str(value).replace(',', '.')):g} pt"
    except ValueError:
        return f"{value} pt"


def render_question(q: dict[str, Any], *, include_feedback: bool = True, mode: Mode = "review",
                    pictures: list[str] | None = None) -> str:
    """Render one question of mod_quiz_get_attempt_review (mode="review") or of an attempt in progress
    (mod_quiz_get_attempt_summary / _data, mode="attempt": options are listed, [x] marks the saved answer).

    Moodle-hosted pictures keep their URL: ![alt](…/pluginfile.php/…). Inline SVG drawn as text (code
    listings) becomes a code block. Other pictures — SVG diagrams, data: URIs, images on other hosts — are
    appended to `pictures` and referenced as ![alt](picture:<index>); without that list they become
    [diagram — labels: …] / [image: alt]."""
    html, svgs = _extract_svgs(q.get("html") or "")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for holder in soup.select("span[data-mcp-svg]"):
        holder.replace_with(_svg_node(svgs[int(str(holder["data-mcp-svg"]))], pictures))
    for img in soup.find_all("img"):
        _collect_picture(img, pictures)
    number = q.get("questionnumber") or q.get("number") or q.get("slot")
    qtype = q.get("type") or "?"
    mark, maxmark = q.get("mark"), q.get("maxmark")
    status = clean_inline(q.get("status") or "")
    head = f"### Q{number} · {qtype}"
    if mode == "attempt":
        if maxmark not in (None, ""):
            head += f" · {_points(maxmark)}"
    elif maxmark not in (None, "", 0, "0"):
        head += f" · {mark if mark not in (None, '') else '–'}/{maxmark}"
    if status:
        head += f" · {status}"
    if mode == "attempt" and q.get("flagged"):
        head += " · flagged"
    if mode == "attempt" and q.get("blockedbyprevious"):
        head += " · locked until the previous question is answered"
    if qtype == "description":
        form = soup.select_one(".formulation") or soup
        return f"### Info\n{to_markdown(str(form))}"
    form = soup.select_one(".formulation")
    body = ""
    parts = [head]
    if isinstance(form, Tag):
        if mode == "attempt":
            hint = _answer_hint(qtype, form)
            if hint:
                parts.append(f"*How to answer: {hint}.*")
        _transform(form, mode)
        body = to_markdown(str(form))
        if qtype in ("multichoice", "truefalse", "multichoiceset") or re.search(r"\[(x| )\]", body):
            body = _choice_rows_to_lines(body)
    parts.append(body)
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
    if state in ("gradedright", "mangrright"):
        return False
    mark = q.get("mark")
    if mark in (None, ""):  # marks hidden by the review options and no usable state
        return False
    try:
        return float(str(mark).replace(",", ".")) < float(q.get("maxmark") or 0)
    except ValueError:
        return False
