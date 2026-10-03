"""Quiz review tool."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from .. import data, fmt, timeutil
from ..app import app
from ..client import MoodleError
from ..html import to_markdown
from ..quizreview import is_wrong, render_question
from ..toolkit import UserError, moodle_tool
from .activity import _record


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
    unfinished = [a for a in attempts if a.get("state") in ("inprogress", "overdue")]
    if unfinished:
        head.append(f"⚠ Attempt {unfinished[-1].get('attempt')} is in progress and NOT SUBMITTED — it cannot be reviewed until submitted.")
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
