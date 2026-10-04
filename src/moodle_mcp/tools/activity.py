"""get_activity: full details of one activity, with type-specific handlers and a generic fallback."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from pydantic import Field

from .. import data, fmt, timeutil
from ..app import app
from ..client import MoodleError
from ..courses import Course
from ..html import clean_inline, to_markdown, to_text, truncate
from ..toolkit import moodle_tool
from .progress import _overview_value

Handler = Callable[[Course | None, data.Mod], Awaitable[list[str]]]
HANDLERS: dict[str, Handler] = {}
_OWN_DATES = {"assign", "quiz", "choice", "feedback", "lesson", "workshop", "bigbluebuttonbn", "checkmark"}
_GRADE_METHOD = {1: "highest grade", 2: "average grade", 3: "first attempt", 4: "last attempt"}


def handler(*names: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        for n in names:
            HANDLERS[n] = fn
        return fn

    return deco


async def _record(function: str, list_key: str, course_id: int, cmid: int, **extra: Any) -> dict[str, Any] | None:
    """Find the instance record of an activity in a mod_*_get_*_by_courses response."""
    res = await app.client.try_call(function, courseids=[course_id], **extra)
    items = res if isinstance(res, list) else (res.get(list_key) or []) if isinstance(res, dict) else []
    for item in items:
        if int(item.get("coursemodule") or item.get("cmid") or 0) == cmid:
            return item
    return None


def _intro(rec: dict[str, Any] | None, key: str = "intro") -> list[str]:
    if not rec:
        return []
    text = to_markdown(rec.get(key))
    return [text] if text else []


def _files_lines(files: list[dict[str, Any]], title: str = "Files") -> list[str]:
    if not files:
        return []
    lines = [f"**{title}** ({len(files)}):"]
    for f in files:
        path = (f.get("filepath") or "/").strip("/")
        name = f"{path}/{f.get('filename')}" if path else f.get("filename")
        lines.append(f"- {name} · {fmt.size(f.get('filesize'))} · modified {timeutil.fmt_date(f.get('timemodified'))}")
    return lines


def _ts_line(pairs: list[tuple[str, Any]]) -> str:
    parts = [f"{label}: {fmt.when(ts)}" for label, ts in pairs if ts and int(ts) > 0]
    return " · ".join(parts)


# ---------------------------------------------------------------- assign

def _submission_lines(status: dict[str, Any]) -> list[str]:
    out: list[str] = []
    last = status.get("lastattempt") or {}
    sub = last.get("teamsubmission") or last.get("submission")
    if sub:
        st = sub.get("status") or "?"
        label = {"new": "not submitted", "draft": "DRAFT (not submitted for grading)", "submitted": "submitted for grading",
                 "reopened": "reopened"}.get(st, st)
        line = f"**My submission:** {label}"
        if sub.get("timemodified") and st != "new":
            line += f" · last change {timeutil.fmt_dt(sub['timemodified'])}"
        if last.get("teamsubmission"):
            line += " · group submission"
        out.append(line)
        for plugin in sub.get("plugins") or []:
            for area in plugin.get("fileareas") or []:
                names = [f"{f.get('filename')} ({fmt.size(f.get('filesize'))})" for f in area.get("files") or []]
                if names:
                    out.append(f"- files: {', '.join(names)}")
            for ed in plugin.get("editorfields") or []:
                text = to_markdown(ed.get("text"))
                if text:
                    out.append(f"- online text:\n{truncate(text, 3000)}")
    if last.get("extensionduedate"):
        out.append(f"Extension until {timeutil.fmt_dt(last['extensionduedate'])}")
    if last.get("gradingstatus"):
        out.append(f"Grading status: {last['gradingstatus']}")
    if last.get("locked"):
        out.append("Submissions are locked.")
    fb = status.get("feedback") or {}
    if fb:
        grade = clean_inline(fb.get("gradefordisplay"))
        if grade:
            out.append(f"**Grade:** {grade}" + (f" (graded {timeutil.fmt_dt(fb['gradeddate'])})" if fb.get("gradeddate") else ""))
        for plugin in fb.get("plugins") or []:
            for ed in plugin.get("editorfields") or []:
                text = to_markdown(ed.get("text"))
                if text:
                    out.append(f"**Feedback ({plugin.get('name') or plugin.get('type')}):**\n{text}")
            for area in plugin.get("fileareas") or []:
                names = [f.get("filename") for f in area.get("files") or [] if not str(f.get("filename", "")).startswith("image_page")]
                if names:
                    out.append(f"Feedback files: {', '.join(names)}")
    return out


@handler("assign")
async def _assign(course: Course | None, m: data.Mod) -> list[str]:
    res, status = await asyncio.gather(
        app.client.try_call("mod_assign_get_assignments", courseids=[m.course_id]),
        app.client.try_call("mod_assign_get_submission_status", assignid=m.instance),
    )
    out: list[str] = []
    rec = None
    if isinstance(res, dict):
        for c in res.get("courses") or []:
            for a in c.get("assignments") or []:
                if int(a.get("cmid") or 0) == m.cmid:
                    rec = a
    if rec:
        out.append(_ts_line([("Opens", rec.get("allowsubmissionsfromdate")), ("Due", rec.get("duedate")),
                             ("Cut-off", rec.get("cutoffdate")), ("Grading due", rec.get("gradingduedate"))]))
        configs = {(c.get("plugin"), c.get("subtype"), c.get("name")): c.get("value") for c in rec.get("configs") or []}
        kinds = []
        if configs.get(("file", "assignsubmission", "enabled")) == "1":
            maxfiles = configs.get(("file", "assignsubmission", "maxfilesubmissions"))
            types = configs.get(("file", "assignsubmission", "filetypeslist")) or "any type"
            maxsize = configs.get(("file", "assignsubmission", "maxsubmissionsizebytes"))
            kinds.append(f"file upload (max {maxfiles} files, {types}{', ≤ ' + fmt.size(int(maxsize)) if maxsize and maxsize != '0' else ''})")
        if configs.get(("onlinetext", "assignsubmission", "enabled")) == "1":
            limit = configs.get(("onlinetext", "assignsubmission", "wordlimit"))
            kinds.append("online text" + (f" (≤ {limit} words)" if limit and limit != "0" else ""))
        settings = []
        if kinds:
            settings.append("Submission: " + "; ".join(kinds))
        if rec.get("teamsubmission"):
            settings.append("group submission")
        if rec.get("grade"):
            settings.append(f"max grade {rec['grade']}" if int(rec["grade"]) > 0 else "graded with a scale")
        if rec.get("requiresubmissionstatement"):
            settings.append("requires accepting the submission statement")
        if rec.get("maxattempts") not in (None, -1, 1):
            settings.append(f"max attempts {rec['maxattempts']}")
        if settings:
            out.append(" · ".join(settings))
        out.extend(_intro(rec))
        out.extend(_files_lines(rec.get("introattachments") or [], "Attached files (use read_file / download_files)"))
    if isinstance(status, dict):
        out.extend(_submission_lines(status))
    elif isinstance(status, MoodleError):
        out.append(f"(submission status unavailable: {status.message})")
    return out


# ---------------------------------------------------------------- quiz

def attempt_lines(attempts: list[dict[str, Any]], quiz: dict[str, Any] | None) -> list[str]:
    if not attempts:
        return ["No attempts yet."]
    sumgrades = float((quiz or {}).get("sumgrades") or 0)
    grade = float((quiz or {}).get("grade") or 0)
    lines = ["**My attempts:**"]
    for a in attempts:
        state = a.get("state")
        txt = f"- attempt {a.get('attempt')} (id {a.get('id')}) · {state}"
        if state in ("inprogress", "overdue"):
            txt += " ⚠ NOT SUBMITTED"
        txt += f" · started {timeutil.fmt_short(a.get('timestart'))}"
        if a.get("timefinish"):
            txt += f" · finished {timeutil.fmt_short(a['timefinish'])}"
        if a.get("sumgrades") is not None and sumgrades:
            scaled = float(a["sumgrades"]) / sumgrades * grade if grade else float(a["sumgrades"])
            txt += f" · {scaled:.2f}/{grade:g}" if grade else f" · {a['sumgrades']} points"
        lines.append(txt)
    return lines


@handler("quiz")
async def _quiz(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_quiz_get_quizzes_by_courses", "quizzes", m.course_id, m.cmid)
    quizid = int((rec or {}).get("id") or m.instance)
    attempts, best, access = await asyncio.gather(
        app.client.try_call("mod_quiz_get_user_attempts", quizid=quizid, status="all", includepreviews=False),
        app.client.try_call("mod_quiz_get_user_best_grade", quizid=quizid),
        app.client.try_call("mod_quiz_get_quiz_access_information", quizid=quizid),
    )
    out: list[str] = []
    if rec:
        out.append(_ts_line([("Opens", rec.get("timeopen")), ("Closes", rec.get("timeclose"))]))
        bits = []
        if rec.get("timelimit"):
            bits.append(f"time limit {int(rec['timelimit']) // 60} min")
        bits.append("unlimited attempts" if not rec.get("attempts") else f"{rec['attempts']} attempt(s) allowed")
        if rec.get("grademethod") in _GRADE_METHOD and rec.get("attempts") != 1:
            bits.append(f"grading: {_GRADE_METHOD[rec['grademethod']]}")
        if rec.get("grade"):
            bits.append(f"max grade {float(rec['grade']):g}")
        out.append(" · ".join(bits))
        out.extend(_intro(rec))
    if isinstance(access, dict):
        rules = [clean_inline(r) for r in access.get("accessrules") or []]
        if rules:
            out.append("Rules: " + "; ".join(rules))
        reasons = [clean_inline(r) for r in access.get("preventaccessreasons") or []]
        if reasons:
            out.append("Cannot attempt now: " + "; ".join(reasons))
    if isinstance(attempts, dict):
        out.extend(attempt_lines(attempts.get("attempts") or [], rec))
    if isinstance(best, dict) and best.get("hasgrade"):
        out.append(f"**Best grade:** {float(best.get('grade') or 0):.2f}" + (f"/{float(rec['grade']):g}" if rec and rec.get("grade") else ""))
    if isinstance(attempts, dict) and any(a.get("state") in ("inprogress", "overdue") for a in attempts.get("attempts") or []):
        out.append(f"Questions of the attempt in progress: get_quiz_questions(quiz={m.cmid})")
    out.append(f"Review questions & answers: get_quiz_review(quiz={m.cmid})")
    return out


# ---------------------------------------------------------------- content types

@handler("page")
async def _page(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_page_get_pages_by_courses", "pages", m.course_id, m.cmid)
    if not rec:
        return [to_markdown(m.description)] if m.description else ["(page content not available)"]
    out = _intro(rec) if to_text(rec.get("intro")) else []
    out.append(to_markdown(rec.get("content")) or "(empty page)")
    if rec.get("timemodified"):
        out.append(f"_Last modified {timeutil.fmt_dt(rec['timemodified'])}_")
    return out


@handler("book")
async def _book(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_book_get_books_by_courses", "books", m.course_id, m.cmid)
    out = _intro(rec)
    items = m.raw.get("contents") or []
    structure: list[dict[str, Any]] = []
    for item in items:
        if item.get("filename") == "structure" and item.get("content"):
            try:
                structure = json.loads(item["content"])
            except ValueError:
                structure = []
    chapter_files = {str(i.get("filepath") or "").strip("/"): i for i in items if i.get("filename") == "index.html"}

    flat: list[tuple[int, dict[str, Any]]] = []

    def walk(nodes: list[dict[str, Any]], level: int) -> None:
        for n in nodes:
            flat.append((level, n))
            walk(n.get("subitems") or [], level + 1)

    walk(structure, 0)
    if not flat:
        return [*out, "(no chapters)"]

    async def load(node: dict[str, Any]) -> str:
        key = str(node.get("href") or "").split("/")[0]
        f = chapter_files.get(key)
        if not f or not f.get("fileurl"):
            return ""
        try:
            return to_markdown(await app.client.get_text(f["fileurl"]))
        except MoodleError as exc:
            return f"(could not load chapter: {exc.message})"

    texts = await asyncio.gather(*(load(n) for _, n in flat))
    for (level, node), text in zip(flat, texts):
        title = clean_inline(node.get("title"))
        if not text.lstrip("# ").startswith(title):
            out.append(f"{'#' * min(6, 3 + level)} {title}" + (" (hidden)" if str(node.get("hidden")) == "1" else ""))
        if text:
            out.append(text)
    return out


@handler("label")
async def _label(course: Course | None, m: data.Mod) -> list[str]:
    return [to_markdown(m.description) or "(empty)"]


@handler("url")
async def _url(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_url_get_urls_by_courses", "urls", m.course_id, m.cmid)
    target = (rec or {}).get("externalurl") or next(
        (c.get("fileurl") for c in m.raw.get("contents") or [] if c.get("fileurl")), None
    )
    out = [f"**Link:** {target}" if target else "(no link)"]
    out.extend(_intro(rec))
    return out


@handler("resource")
async def _resource(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_resource_get_resources_by_courses", "resources", m.course_id, m.cmid)
    out = _intro(rec)
    out.extend(_files_lines(m.files))
    out.append(f"Read the text: read_file(cmid={m.cmid}) · save locally: download_files")
    return out


@handler("folder")
async def _folder(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_folder_get_folders_by_courses", "folders", m.course_id, m.cmid)
    out = _intro(rec)
    out.extend(_files_lines(m.files))
    if m.files:
        out.append(f"Read one: read_file(cmid={m.cmid}, filename=…) · save all: download_files")
    return out


@handler("forum")
async def _forum(course: Course | None, m: data.Mod) -> list[str]:
    from .forums import discussions_lines

    rec = await _record("mod_forum_get_forums_by_courses", "forums", m.course_id, m.cmid)
    out = _intro(rec)
    if rec:
        out.extend(await discussions_lines(int(rec["id"]), page=0, per_page=15))
    return out


@handler("choice")
async def _choice(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_choice_get_choices_by_courses", "choices", m.course_id, m.cmid)
    choiceid = int((rec or {}).get("id") or m.instance)
    options, results = await asyncio.gather(
        app.client.try_call("mod_choice_get_choice_options", choiceid=choiceid),
        app.client.try_call("mod_choice_get_choice_results", choiceid=choiceid),
    )
    out = []
    if rec:
        out.append(_ts_line([("Opens", rec.get("timeopen")), ("Closes", rec.get("timeclose"))]))
        bits = []
        if rec.get("allowupdate"):
            bits.append("answer can be changed")
        if rec.get("allowmultiple"):
            bits.append("multiple answers allowed")
        if bits:
            out.append(" · ".join(bits))
        out.extend(_intro(rec))
    counts: dict[int, Any] = {}
    if isinstance(results, dict):
        for r in results.get("options") or []:
            counts[int(r.get("id") or 0)] = r.get("numberofuser")
    if isinstance(options, dict):
        out.append("**Options:**")
        for o in options.get("options") or []:
            mark = "[x]" if o.get("checked") else "[ ]"
            extra = []
            if o.get("maxanswers"):
                extra.append(f"{o.get('countanswers', '?')}/{o['maxanswers']} taken")
            if int(o.get("id") or 0) in counts and counts[int(o["id"])] is not None:
                extra.append(f"{counts[int(o['id'])]} votes")
            if o.get("disabled"):
                extra.append("full/disabled")
            out.append(f"- {mark} {clean_inline(o.get('text'))}" + (f" ({', '.join(extra)})" if extra else ""))
    return out


@handler("feedback")
async def _feedback(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_feedback_get_feedbacks_by_courses", "feedbacks", m.course_id, m.cmid)
    out = []
    if rec:
        out.append(_ts_line([("Opens", rec.get("timeopen")), ("Closes", rec.get("timeclose"))]))
        out.append("anonymous" if rec.get("anonymous") == 1 else "not anonymous")
        out.extend(_intro(rec))
        access = await app.client.try_call("mod_feedback_get_feedback_access_information", feedbackid=rec["id"])
        if isinstance(access, dict):
            out.append("**Status:** " + ("✓ completed" if access.get("iscomplete") else "not completed"))
    return out


@handler("lesson")
async def _lesson(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_lesson_get_lessons_by_courses", "lessons", m.course_id, m.cmid)
    out = []
    if rec:
        out.append(_ts_line([("Available", rec.get("available")), ("Deadline", rec.get("deadline"))]))
        if rec.get("timelimit"):
            out.append(f"time limit {int(rec['timelimit']) // 60} min")
        out.extend(_intro(rec))
        grade = await app.client.try_call("mod_lesson_get_user_grade", lessonid=rec["id"])
        if isinstance(grade, dict) and grade.get("formattedgrade"):
            out.append(f"**My grade:** {clean_inline(grade['formattedgrade'])}")
    return out


@handler("workshop")
async def _workshop(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_workshop_get_workshops_by_courses", "workshops", m.course_id, m.cmid)
    out = []
    if not rec:
        return out
    phases = {10: "setup", 20: "submission", 30: "assessment", 40: "grading evaluation", 50: "closed"}
    out.append(f"Phase: {phases.get(int(rec.get('phase') or 0), rec.get('phase'))}")
    out.append(_ts_line([("Submissions from", rec.get("submissionstart")), ("Submissions until", rec.get("submissionend")),
                         ("Assessment from", rec.get("assessmentstart")), ("Assessment until", rec.get("assessmentend"))]))
    out.extend(_intro(rec))
    instr = to_markdown(rec.get("instructauthors"))
    if instr:
        out.append(f"**Instructions for submission:**\n{instr}")
    plan, grades = await asyncio.gather(
        app.client.try_call("mod_workshop_get_user_plan", workshopid=rec["id"]),
        app.client.try_call("mod_workshop_get_grades", workshopid=rec["id"]),
    )
    if isinstance(plan, dict):
        for phase in (plan.get("userplan") or {}).get("phases") or []:
            tasks = [f"{'✓' if t.get('completed') else '–'} {clean_inline(t.get('title'))}" for t in phase.get("tasks") or []]
            if tasks:
                out.append(f"- {clean_inline(phase.get('title'))}{' (current)' if phase.get('active') else ''}: " + "; ".join(tasks))
    if isinstance(grades, dict):
        for key, label in (("submissionlongstrgrade", "Submission grade"), ("assessmentlongstrgrade", "Assessment grade")):
            if grades.get(key):
                out.append(f"**{label}:** {clean_inline(grades[key])}")
    return out


@handler("glossary")
async def _glossary(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_glossary_get_glossaries_by_courses", "glossaries", m.course_id, m.cmid)
    out = _intro(rec)
    if rec:
        entries = await app.client.try_call("mod_glossary_get_entries_by_letter", id=rec["id"], letter="ALL", limit=100)
        if isinstance(entries, dict):
            out.append(f"**Entries** ({entries.get('count', 0)}):")
            for e in entries.get("entries") or []:
                out.append(f"- **{clean_inline(e.get('concept'))}**: {to_text(e.get('definition'), max_chars=500)}")
    return out


@handler("wiki")
async def _wiki(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_wiki_get_wikis_by_courses", "wikis", m.course_id, m.cmid)
    out = _intro(rec)
    if rec:
        pages = await app.client.try_call("mod_wiki_get_subwiki_pages", wikiid=rec["id"])
        if isinstance(pages, dict):
            plist = pages.get("pages") or []
            contents = await app.client.batch([("mod_wiki_get_page_contents", {"pageid": p["id"]}) for p in plist[:20]])
            for p, c in zip(plist[:20], contents):
                out.append(f"### {clean_inline(p.get('title'))}")
                if isinstance(c, dict):
                    out.append(to_markdown((c.get("page") or {}).get("cachedcontent")))
            if len(plist) > 20:
                out.append(f"… {len(plist) - 20} more pages")
    return out


@handler("data")
async def _database(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_data_get_databases_by_courses", "databases", m.course_id, m.cmid)
    out = _intro(rec)
    if rec:
        entries = await app.client.try_call("mod_data_get_entries", databaseid=rec["id"], returncontents=True, perpage=30)
        if isinstance(entries, dict):
            out.append(f"**Entries** ({entries.get('totalcount', 0)}):")
            for e in entries.get("entries") or []:
                values = [to_text(c.get("content"), max_chars=200) for c in e.get("contents") or [] if c.get("content")]
                out.append(f"- {e.get('fullname', '')}: " + " | ".join(values))
    return out


@handler("bigbluebuttonbn")
async def _bbb(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_bigbluebuttonbn_get_bigbluebuttonbns_by_courses", "bigbluebuttonbns", m.course_id, m.cmid)
    out = []
    if rec:
        out.append(_ts_line([("Opens", rec.get("openingtime")), ("Closes", rec.get("closingtime"))]))
        out.extend(_intro(rec))
        recs = await app.client.try_call("mod_bigbluebuttonbn_get_recordings", bigbluebuttonbnid=rec["id"])
        if isinstance(recs, dict):
            table = recs.get("tabledata") or {}
            try:
                rows = json.loads(table.get("data") or "[]")
            except ValueError:
                rows = []
            if rows:
                out.append(f"**Recordings** ({len(rows)}):")
                for r in rows:
                    name = to_text(r.get("recording") or r.get("name") or "")
                    date = to_text(str(r.get("date") or ""))
                    links = re.findall(r'href="([^"]+)"', str(r.get("playback") or ""))
                    out.append(f"- {name} · {date}" + (f" · {links[0]}" if links else ""))
            else:
                out.append("No recordings.")
    return out


async def _mobile_content(component: str, method: str, m: data.Mod) -> str:
    args = [{"name": "cmid", "value": str(m.cmid)}, {"name": "courseid", "value": str(m.course_id)},
            {"name": "userid", "value": str(await app.client.userid())}]
    res = await app.client.try_call("tool_mobile_get_content", component=component, method=method, args=args)
    if not isinstance(res, dict):
        return ""
    return " ".join(t.get("html") or "" for t in res.get("templates") or [])


def _humanize_key(match: re.Match[str]) -> str:
    key = match.group(1)
    return {"core.yes": "yes", "core.no": "no"}.get(key, key.rsplit(".", 1)[-1].replace("_", " "))


def _mobile_lines(html: str) -> list[str]:
    """Render an app template: description + 'label: value' pairs from ion-item / ion-row."""
    from bs4 import BeautifulSoup

    html = re.sub(r"\{\{\s*'([a-z0-9_.]+)'\s*\|\s*translate\s*\}\}", _humanize_key, html)
    html = re.sub(r"\{\{[^}]*\}\}", "", html)
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for desc in soup.find_all(["core-course-module-description", "core-course-module-info"]):
        text = to_markdown(desc.get("description") or "")
        if text:
            out.append(text)
        desc.decompose()
    for item in soup.find_all(["ion-item", "ion-row"]):
        if item.name == "ion-item" and item.find("ion-row"):
            continue
        cells = [" ".join(c.get_text(" ").split()) for c in item.find_all(["p", "h2", "h3", "ion-col"], recursive=True)]
        cells = [c for c in cells if c]
        if not cells:
            continue
        label = _ATTENDANCE_LABELS.get(cells[0].replace(" ", ""), cells[0])
        out.append(f"- {label}: {' · '.join(cells[1:])}" if len(cells) > 1 else f"- {label}")
    return out


_ATTENDANCE_LABELS = {
    "sessionscompleted": "Sessions completed", "pointssessionscompleted": "Points (completed sessions)",
    "percentagesessionscompleted": "Percentage (completed sessions)", "sessionstotal": "Sessions total",
    "pointsallsessions": "Points (all sessions)", "percentageallsessions": "Percentage (all sessions)",
    "maxpossiblepoints": "Max possible points", "maxpossiblepercentage": "Max possible percentage",
}


@handler("attendance")
async def _attendance(course: Course | None, m: data.Mod) -> list[str]:
    lines = _mobile_lines(await _mobile_content("mod_attendance", "mobile_view_activity", m))
    return ["**Attendance:**\n" + "\n".join(lines)] if lines else []


@handler("zoom")
async def _zoom(course: Course | None, m: data.Mod) -> list[str]:
    lines = _mobile_lines(await _mobile_content("mod_zoom", "mobile_course_view", m))
    desc = [x for x in lines if not x.startswith("- ")]
    items = [x for x in lines if x.startswith("- ")]
    return [*desc, "\n".join(items)] if items else desc


@handler("checkmark")
async def _checkmark(course: Course | None, m: data.Mod) -> list[str]:
    res = await app.client.try_call("mod_checkmark_get_checkmarks_by_courses")
    if not isinstance(res, dict):
        return []
    for ck in res.get("checkmarks") or []:
        if int(ck.get("instance") or 0) == m.instance or int(ck.get("id") or 0) == m.cmid:
            out = [_ts_line([("Due", ck.get("timedue")), ("Cut-off", ck.get("cutoffdate"))])]
            out.extend(_intro(ck))
            examples = ck.get("examples") or []
            checked = [clean_inline(e.get("name")) for e in examples if e.get("checked")]
            out.append(f"**Checked examples:** {len(checked)}/{len(examples)}" + (f" — {', '.join(checked)}" if checked else ""))
            unchecked = [clean_inline(e.get("name")) for e in examples if not e.get("checked")]
            if unchecked:
                out.append(f"Not checked: {', '.join(unchecked)}")
            if ck.get("submission_timemodified"):
                out.append(f"Submitted {timeutil.fmt_dt(ck['submission_timemodified'])}")
            return out
    return []


@handler("lti", "streamlti")
async def _lti(course: Course | None, m: data.Mod) -> list[str]:
    fn, key = ("mod_lti_get_ltis_by_courses", "ltis") if m.modname == "lti" else ("mod_streamlti_get_streamltis_by_courses", "streamltis")
    rec = await _record(fn, key, m.course_id, m.cmid)
    out = _intro(rec)
    out.append(f"External tool — open it in the browser: {m.url}")
    return out


@handler("h5pactivity")
async def _h5p(course: Course | None, m: data.Mod) -> list[str]:
    rec = await _record("mod_h5pactivity_get_h5pactivities_by_courses", "h5pactivities", m.course_id, m.cmid)
    out = _intro(rec)
    if rec:
        att = await app.client.try_call("mod_h5pactivity_get_user_attempts", h5pactivityid=rec["id"])
        if isinstance(att, dict):
            for u in att.get("usersattempts") or []:
                for a in u.get("attempts") or []:
                    out.append(f"- attempt {a.get('attempt')} · {timeutil.fmt_short(a.get('timemodified'))} · score {a.get('rawscore')}/{a.get('maxscore')}")
    return out


# ---------------------------------------------------------------- generic fallback

async def _overview_for(m: data.Mod) -> list[str]:
    res = await app.client.try_call("core_courseformat_get_overview_information", courseid=m.course_id, modname=m.modname)
    if not isinstance(res, dict):
        return []
    headers = {h.get("key"): clean_inline(h.get("name")) for h in res.get("headers") or []}
    for a in res.get("activities") or []:
        if int(a.get("cmid") or 0) != m.cmid:
            continue
        bits = []
        for it in a.get("items") or []:
            if it.get("key") in ("name", "actions"):
                continue
            value = _overview_value(it)
            if value:
                bits.append(f"{headers.get(it.get('key'), it.get('key'))}: {value}")
        return ["**Status:** " + " · ".join(bits)] if bits else []
    return []


async def _events_for(m: data.Mod) -> list[str]:
    now = timeutil.now_ts()
    res = await app.client.try_call(
        "core_calendar_get_calendar_events",
        events={"courseids": [m.course_id]},
        options={"userevents": True, "siteevents": False, "timestart": now - 400 * 86400, "timeend": now + 400 * 86400},
    )
    if not isinstance(res, dict):
        return []
    lines = []
    for e in res.get("events") or []:
        if e.get("modulename") == m.modname and int(e.get("instance") or 0) == m.instance:
            dur = int(e.get("timeduration") or 0)
            span = f"–{timeutil.to_dt(int(e['timestart']) + dur):%H:%M}" if dur else ""
            lines.append(f"- {timeutil.fmt_dt(e.get('timestart'))}{span} · {clean_inline(e.get('name'))}"
                         + (f" · 📍 {clean_inline(e['location'])}" if e.get("location") else ""))
    return ["**Calendar events:**", *lines] if lines else []


async def _generic(course: Course | None, m: data.Mod) -> list[str]:
    out = []
    if m.description:
        out.append(to_markdown(m.description))
    out.extend(await _overview_for(m))
    out.extend(await _events_for(m))
    if app.settings.private_token:
        try:
            page = await app.web.fetch_markdown(m.url, max_chars=15000)
            out.append("**Page content (web):**\n" + page)
        except Exception as exc:
            out.append(f"(web page not available: {exc})")
    else:
        out.append(f"Moodle has no web-service API for '{m.modname}'. Full page: {m.url}"
                   " (enable the optional web session to read it here — see README).")
    return out


# ---------------------------------------------------------------- tool

@moodle_tool(title="Activity details")
async def get_activity(
    cmid: Annotated[str | int, Field(description="Activity id (cmid) or a Moodle activity URL (…/mod/<type>/view.php?id=…)")],
) -> str:
    """Everything about one activity: description, dates, files and type-specific details — assignment
    submission/grade/feedback, quiz attempts, page/book text, forum discussions, choice options, BBB
    recordings, attendance, checkmarks… Plugins without an API get status, events and description."""
    course, m = await data.get_mod(cmid)
    head = [f"# {m.modname}: {m.name}"]
    where = f"{course.label} [{course.id}]" if course else f"course {m.course_id}"
    head.append(f"{where} · section {m.section_num} «{m.section_name}» · cmid {m.cmid} · {m.url}")
    flags = []
    if m.hidden:
        flags.append("hidden on the course page")
    if not m.user_visible:
        flags.append("NOT AVAILABLE to you")
    if m.raw.get("availabilityinfo"):
        flags.append("restriction: " + to_text(m.raw["availabilityinfo"], max_chars=400))
    comp = m.raw.get("completiondata") or {}
    if isinstance(comp, dict) and comp.get("hascompletion"):
        flags.append("completion: " + ("✓ done" if comp.get("state") in (1, 2) else "✗ failed" if comp.get("state") == 3 else "not done"))
    if flags:
        head.append(" · ".join(flags))
    if m.dates and m.modname not in _OWN_DATES:
        head.append(" · ".join(f"{label}: {fmt.when(ts)}" for label, ts in m.dates))
    body_fn = HANDLERS.get(m.modname, _generic)
    try:
        body = await body_fn(course, m)
    except MoodleError as exc:
        body = [f"(details unavailable: {exc.message})"]
    if m.modname not in HANDLERS and body_fn is not _generic:
        body.extend(await _overview_for(m))
    return fmt.cap(join_blocks(head + [b for b in body if b]), hint="for long pages/books use read_file or search")


def join_blocks(parts: list[str]) -> str:
    """Blank line between blocks, but keep consecutive list items (and their heading) together."""
    out = ""
    prev = ""
    for part in parts:
        if not out:
            out = part
        elif part.startswith("- ") and (prev.startswith("- ") or prev.rstrip().endswith(":**") or prev.rstrip().endswith(":")):
            out += "\n" + part
        else:
            out += "\n\n" + part
        prev = part
    return out
