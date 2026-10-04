"""Quiz tools: questions of an attempt in progress, review of finished attempts."""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Image
from pydantic import Field

from .. import data, fmt, timeutil
from ..app import app
from ..client import MoodleError
from ..extract import IMAGE_TYPES
from ..html import to_markdown
from ..quizreview import PICTURE, is_wrong, render_question, svg_labels, svg_to_png
from ..toolkit import UserError, moodle_tool
from .activity import _record
from .files import MAX_IMAGE_BYTES, _filename_from_url, fetch_cached

MAX_INLINE_IMAGES = 20
_IMG_MD = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_OPEN_STATES = ("inprogress", "overdue")
_IMAGE_MIME = {"image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg", "image/gif": "gif", "image/webp": "webp"}


async def _review(attempt_id: int, only_incorrect: bool, include_feedback: bool) -> list[str]:
    try:
        res = await app.client.call("mod_quiz_get_attempt_review", attemptid=attempt_id, page=-1)
    except MoodleError as exc:
        if exc.errorcode in ("noreview", "noreviewattempt", "noreviewuntil", "noreviewuntilshort"):
            return [f"Review of attempt {attempt_id} is not available: {exc.message} "
                    "(the teacher's review options hide it — often until the quiz closes)."]
        raise
    attempt = res.get("attempt") or {}
    questions = res.get("questions") or []
    head = f"## Attempt {attempt.get('attempt')} (id {attempt_id}) · {attempt.get('state')}"
    if attempt.get("timefinish"):
        head += f" · finished {timeutil.fmt_dt(attempt['timefinish'])}"
    if res.get("grade") not in (None, ""):
        head += f" · grade {res['grade']}"
    out = [head]
    for item in res.get("additionaldata") or []:
        text = to_markdown(item.get("content"))
        if text and item.get("id") not in ("user", "state", "startedon", "completedon", "timetaken", "grade"):
            out.append(f"**{item.get('title')}:** {text}")
    shown = [q for q in questions if not only_incorrect or is_wrong(q)]
    if only_incorrect:
        out.append(f"{len(shown)} of {len(questions)} questions not fully correct.")
    for q in shown:
        out.append(render_question(q, include_feedback=include_feedback))
    return out


@moodle_tool(title="Quiz review (questions & answers)")
async def get_quiz_review(
    quiz: Annotated[str | int | None, Field(description="Quiz cmid or URL")] = None,
    attempt_id: Annotated[int | None, Field(description="A specific attempt id (from get_activity)")] = None,
    which: Annotated[Literal["last", "best", "all"], Field(description="Which finished attempt(s) to show")] = "last",
    only_incorrect: Annotated[bool, Field(description="Only questions that were not fully correct")] = False,
    include_feedback: Annotated[bool, Field(description="Include feedback, explanations and correct answers")] = True,
) -> str:
    """Review a quiz attempt question by question: question text, my answer ([x] choices / [filled
    gaps] ✓✗), marks, the correct answer and feedback when the teacher released them. Great for exam prep."""
    if attempt_id is None and quiz is None:
        raise UserError("Pass `quiz` (cmid/URL) or `attempt_id`.")
    if attempt_id is not None:
        return fmt.cap("\n\n".join(await _review(attempt_id, only_incorrect, include_feedback)), hint="use only_incorrect=true")
    course, m = await data.get_mod(quiz)  # type: ignore[arg-type]
    if m.modname != "quiz":
        raise UserError(f"cmid {m.cmid} is a {m.modname}, not a quiz.")
    rec = await _record("mod_quiz_get_quizzes_by_courses", "quizzes", m.course_id, m.cmid)
    quizid = int((rec or {}).get("id") or m.instance)
    res = await app.client.call("mod_quiz_get_user_attempts", quizid=quizid, status="all", includepreviews=False)
    attempts: list[dict[str, Any]] = res.get("attempts") or []
    head = [f"# Quiz review: {m.name}", f"{course.label if course else ''} · cmid {m.cmid}"]
    finished = [a for a in attempts if a.get("state") == "finished"]
    unfinished = [a for a in attempts if a.get("state") in _OPEN_STATES]
    if unfinished:
        head.append(f"⚠ Attempt {unfinished[-1].get('attempt')} is in progress and NOT SUBMITTED — it cannot be reviewed "
                    f"until submitted. Its questions: get_quiz_questions(quiz={m.cmid}).")
    if not finished:
        head.append("No finished attempts to review.")
        return "\n\n".join(head)
    if which == "last":
        chosen = [finished[-1]]
    elif which == "best":
        chosen = [max(finished, key=lambda a: float(a.get("sumgrades") or 0))]
    else:
        chosen = finished
    out = list(head)
    for a in chosen:
        out.extend(await _review(int(a["id"]), only_incorrect, include_feedback))
    return fmt.cap("\n\n".join(out), hint="use only_incorrect=true or attempt_id")


# ---------------------------------------------------------------- questions of an attempt in progress

async def _svg_image(svg: str) -> Image | str:
    try:
        return Image(data=await asyncio.to_thread(svg_to_png, svg), format="png")
    except RuntimeError as exc:
        labels = svg_labels(svg)
        return f"could not be drawn: {exc}" + (f". Its labels: {', '.join(labels)}" if labels else "")


def _bytes_image(content: bytes, mime: str) -> Image | str:
    if len(content) > MAX_IMAGE_BYTES:
        return f"is too large to show inline ({fmt.size(len(content))})"
    if mime not in _IMAGE_MIME:
        return f"has an unsupported format ({mime or 'unknown'})"
    return Image(data=content, format=_IMAGE_MIME[mime])


async def _picture(src: str) -> Image | str:
    """A picture collected by render_question: inline SVG, data: URI or an image on another host."""
    if src.lstrip().startswith("<"):
        return await _svg_image(src)
    if src.startswith("data:"):
        head, _, payload = src.partition(",")
        mime = head[5:].split(";")[0].strip().lower()
        try:
            content = base64.b64decode(payload, validate=False) if ";base64" in head else payload.encode()
        except (binascii.Error, ValueError):
            return "could not be decoded"
        if mime == "image/svg+xml":
            return await _svg_image(content.decode("utf-8", "replace"))
        return _bytes_image(content, mime)
    try:
        content, mime = await app.client.fetch_public(src, MAX_IMAGE_BYTES)
    except MoodleError as exc:
        return f"could not be loaded from {src[:80]} ({exc.message})"
    if mime == "image/svg+xml":
        return await _svg_image(content.decode("utf-8", "replace"))
    return _bytes_image(content, mime)


async def _load_image(url: str, pictures: list[str]) -> Image | str:
    """The picture itself, or why it is not shown inline."""
    if url.startswith(PICTURE):
        return await _picture(pictures[int(url[len(PICTURE):])])
    name = _filename_from_url(url)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in IMAGE_TYPES:
        return f"is a {ext.upper() or 'non-image'} file and cannot be shown inline"
    if not app.client.is_site_url(url):
        return "is hosted outside Moodle"
    try:
        path = await fetch_cached(url, name, None)
    except (MoodleError, OSError) as exc:
        return f"could not be loaded ({getattr(exc, 'message', exc)})"
    if path.stat().st_size > MAX_IMAGE_BYTES:
        return f"is too large to show inline ({fmt.size(path.stat().st_size)})"
    return Image(data=path.read_bytes(), format=IMAGE_TYPES[ext])


def _number_images(md: str, numbers: dict[str, int]) -> tuple[str, list[str]]:
    """'![alt](url)' → '[image N: alt](url)', numbered across the whole quiz; returns the urls seen first here."""
    new: list[str] = []

    def sub(match: re.Match[str]) -> str:
        alt, url = match.groups()
        if url not in numbers:
            numbers[url] = len(numbers) + 1
            new.append(url)
        if url.startswith(PICTURE):
            return f"[image {numbers[url]}: {alt}]"
        return f"[image {numbers[url]}: {alt or _filename_from_url(url)}]({url})"

    return _IMG_MD.sub(sub, md), new


def _attempt_lines(attempt: dict[str, Any]) -> list[str]:
    state = attempt.get("state")
    lines = [f"Attempt {attempt.get('attempt')} (id {attempt.get('id')}) · "
             f"{'OVERDUE' if state == 'overdue' else 'in progress, not submitted'} · started {timeutil.fmt_short(attempt.get('timestart'))}"]
    ends = int(attempt.get("timecheckstate") or 0)
    if state == "overdue":
        lines.append("⚠ Time is up — submit the attempt in Moodle" + (f" before {timeutil.fmt_dt(ends, rel=True)}" if ends else "") + ".")
    elif ends:
        lines.append(f"⏱ Time runs out {timeutil.fmt_dt(ends, rel=True)}.")
    return lines


def _merge(content: list[Any]) -> list[Any]:
    """Join neighbouring text parts so the result alternates text and images."""
    out: list[Any] = []
    for item in content:
        if isinstance(item, str) and out and isinstance(out[-1], str):
            out[-1] += "\n\n" + item
        else:
            out.append(item)
    return out


@moodle_tool(title="Quiz questions (attempt in progress)")
async def get_quiz_questions(
    quiz: Annotated[str | int | None, Field(description="Quiz cmid or URL")] = None,
    attempt_id: Annotated[int | None, Field(description="A specific attempt id (from get_activity)")] = None,
    page: Annotated[int | None, Field(ge=1, description="Only this page of the quiz (1 = first page)")] = None,
    include_images: Annotated[bool, Field(description="Show the pictures of the questions (diagrams, screenshots, "
                                                      "drag & drop backgrounds) as images")] = True,
) -> Any:
    """Questions of my quiz attempt that is in progress (started, not submitted yet): question text, how to
    answer, every option (choices, dropdown options, drag items, drop zones on images, …), the answer saved so
    far and the points. Question pictures (photos, screenshots of code, UML/SVG diagrams) are shown as images.
    Read-only: never starts, saves or submits an attempt, so start it in Moodle first. Finished attempts:
    get_quiz_review."""
    if attempt_id is None and quiz is None:
        raise UserError("Pass `quiz` (cmid/URL) or `attempt_id`.")
    navmethod = ""
    if attempt_id is None:
        course, m = await data.get_mod(quiz)  # type: ignore[arg-type]
        if m.modname != "quiz":
            raise UserError(f"cmid {m.cmid} is a {m.modname}, not a quiz.")
        rec = await _record("mod_quiz_get_quizzes_by_courses", "quizzes", m.course_id, m.cmid)
        quizid = int((rec or {}).get("id") or m.instance)
        navmethod = str((rec or {}).get("navmethod") or "")
        # Fresh, not cached: the attempt may have been started a moment ago.
        res = await app.client.call("mod_quiz_get_user_attempts", quizid=quizid, status="all", includepreviews=False, ttl=0)
        attempts: list[dict[str, Any]] = res.get("attempts") or []
        head = [f"# Quiz questions: {m.name}", f"{course.label if course else ''} · cmid {m.cmid}"]
        running = [a for a in attempts if a.get("state") in _OPEN_STATES]
        if not running:
            finished = [a for a in attempts if a.get("state") == "finished"]
            if finished:
                head.append(f"No attempt in progress ({fmt.plural(len(finished), 'finished attempt')}). Questions with "
                            f"my answers and the correct ones: get_quiz_review(quiz={m.cmid}).")
            else:
                head.append("No attempt started yet. Start the attempt in Moodle (this starts the timer, if the quiz has "
                            "one) and call this tool again — it never starts an attempt itself.")
            return "\n\n".join(head)
        attempt = running[-1]
        attempt_id = int(attempt["id"])
    else:
        head = [f"# Quiz attempt {attempt_id}"]
        first = await app.client.try_call("mod_quiz_get_attempt_data", attemptid=attempt_id, page=0, ttl=0)
        attempt = (first.get("attempt") or {}) if isinstance(first, dict) else {}
    if attempt:
        head.extend(_attempt_lines(attempt))

    try:
        summary = await app.client.call("mod_quiz_get_attempt_summary", attemptid=attempt_id, ttl=0)
    except MoodleError as exc:
        if exc.errorcode == "attemptalreadyclosed":
            raise UserError(f"Attempt {attempt_id} is already submitted — use get_quiz_review(attempt_id={attempt_id}).") from exc
        raise
    questions: list[dict[str, Any]] = summary.get("questions") or []
    if not questions:
        return "\n\n".join([*head, "Moodle returned no questions for this attempt."])
    pages = sorted({int(q.get("page") or 0) for q in questions})
    real = [q for q in questions if q.get("number") not in (None, "")]  # info texts have no number
    stats = f"{fmt.plural(len(real), 'question')} on {fmt.plural(len(pages), 'page')}"
    unanswered = summary.get("totalunanswered")
    if isinstance(unanswered, int):
        stats += f" · {len(real) - unanswered} answered, {unanswered} not yet answered"
    head.append(stats)
    if navmethod == "sequential":
        head.append("Sequential navigation: Moodle only shows the current page, earlier pages cannot be revisited.")
    head.append("[x] and filled fields show the answer saved in Moodle so far, ___ an empty one. "
                "Answer and submit in Moodle — this tool cannot change anything.")
    if page is not None:
        questions = [q for q in questions if int(q.get("page") or 0) == page - 1]
        if not questions:
            raise UserError(f"Page {page} has no visible questions (pages: {', '.join(str(p + 1) for p in pages)}).")

    blocks: list[tuple[str, list[str]]] = []  # (Markdown of a question, image urls first shown in it)
    numbers: dict[str, int] = {}
    pictures: list[str] = []  # SVG diagrams, data: URIs, images on other hosts
    used = sum(len(h) for h in head)
    current_page: int | None = None
    left = 0
    for i, q in enumerate(questions):
        md = render_question(q, mode="attempt", pictures=pictures if include_images else None)
        md, new = _number_images(md, numbers)
        pg = int(q.get("page") or 0)
        if len(pages) > 1 and page is None and pg != current_page:
            md = f"## Page {pg + 1}\n\n{md}"
        current_page = pg
        if blocks and used + len(md) > fmt.OUTPUT_LIMIT:
            left = len(questions) - i
            break
        used += len(md)
        blocks.append((md, new))

    urls = [u for _, new in blocks for u in new]
    wanted = urls[:MAX_INLINE_IMAGES] if include_images else []
    loaded = dict(zip(wanted, await asyncio.gather(*(_load_image(u, pictures) for u in wanted))))
    content: list[Any] = ["\n\n".join(head)]
    for md, new in blocks:
        content.append(md)
        for url in new if include_images else []:
            img = loaded.get(url, f"was not loaded (at most {MAX_INLINE_IMAGES} images per call — use page)")
            if isinstance(img, Image):
                content.extend([f"image {numbers[url]}:", img])
            elif url.startswith(PICTURE):
                content.append(f"(image {numbers[url]} {img})")
            else:
                content.append(f"(image {numbers[url]} {img}; read_file(url=\"{url}\"))")
    if left:
        content.append(f"… {fmt.plural(left, 'more question')} not shown — call again with page=N.")
    merged = _merge(content)
    return merged[0] if len(merged) == 1 else merged
