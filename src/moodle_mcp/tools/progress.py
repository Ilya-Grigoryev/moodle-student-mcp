"""Progress tools: get_grades, get_activity_status."""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import Field

from .. import data, fmt, timeutil
from ..app import app
from ..client import MoodleError
from ..courses import Course, semester_key
from ..html import clean_inline, to_text
from ..toolkit import moodle_tool
from .courses import CourseArg, SemesterArg, final_grades

_CONTENT_ONLY = {"resource", "url", "folder", "page", "label", "book", "imscp", "lightboxgallery", "subsection"}


def _pct(item: dict[str, Any]) -> str:
    raw, gmax, gmin = item.get("graderaw"), item.get("grademax"), item.get("grademin") or 0
    if raw is None or not gmax:
        return ""
    try:
        return f"{(float(raw) - float(gmin)) / (float(gmax) - float(gmin)) * 100:.0f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return ""


def _item_name(item: dict[str, Any]) -> str:
    itype = item.get("itemtype")
    name = clean_inline(item.get("itemname"))
    if itype == "course":
        return "**Course total**"
    if itype == "category":
        return f"Σ {name or 'Category total'}"
    if item.get("cmid"):
        return f"{name} · cmid {item['cmid']}"
    return name or "(unnamed)"


def _grade_rows(items: list[dict[str, Any]], only_graded: bool) -> tuple[list[list[Any]], list[str]]:
    rows, feedback = [], []
    show_weight = any(clean_inline(i.get("weightformatted")) not in ("", "-", "0.00 %", "0,00 %") for i in items)
    for it in items:
        if it.get("itemtype") == "course":
            continue
        graded = it.get("graderaw") is not None
        if only_graded and not graded and it.get("itemtype") != "category":
            continue
        grade = clean_inline(it.get("gradeformatted")) or "–"
        if it.get("gradeishidden"):
            grade = "hidden"
        row = [_item_name(it), grade, clean_inline(it.get("rangeformatted")), _pct(it)]
        if show_weight:
            row.append(clean_inline(it.get("weightformatted")))
        row.append(timeutil.fmt_date(it.get("gradedategraded")) if it.get("gradedategraded") else "")
        rows.append(row)
        fb = to_text(it.get("feedback"))
        if fb:
            feedback.append(f"- **{clean_inline(it.get('itemname')) or 'item'}**: {fmt.cell(fb)[:2000]}")
    headers = ["Item", "Grade", "Range", "%"] + (["Weight"] if show_weight else []) + ["Graded"]
    return [headers, *rows], feedback


async def _grade_items(course: Course) -> list[dict[str, Any]]:
    userid = await app.client.userid()
    res = await app.client.call("gradereport_user_get_grade_items", courseid=course.id, userid=userid)
    groups = (res or {}).get("usergrades") or []
    return groups[0].get("gradeitems") or [] if groups else []


@moodle_tool(title="Grades")
async def get_grades(
    course: Annotated[str | int | None, Field(description="Course (id/LV/acronym/name) for the detailed report; omit for an overview")] = None,
    semester: SemesterArg = "current",
    only_graded: Annotated[bool, Field(description="Detailed report: hide items without a grade yet")] = False,
) -> str:
    """Grades. With `course`: every grade item (grade, range, %, weight, date, teacher feedback) and the
    course total. Without: one line per course of the semester(s) with total and number of graded items."""
    if course is not None:
        c = await app.courses.resolve(course)
        items = await _grade_items(c)
        if not items:
            return f"No grade items visible in {c.label}."
        total = next((i for i in items if i.get("itemtype") == "course"), None)
        out = [f"# Grades — {c.label} [{c.id}]"]
        if total:
            out.append(
                f"Course total: **{clean_inline(total.get('gradeformatted')) or '–'}** "
                f"(range {clean_inline(total.get('rangeformatted'))}{', ' + _pct(total) if _pct(total) else ''})"
            )
        graded = sum(1 for i in items if i.get("graderaw") is not None and i.get("itemtype") not in ("course", "category"))
        countable = sum(1 for i in items if i.get("itemtype") not in ("course", "category"))
        out.append(f"{graded} of {countable} items graded.")
        table_rows, feedback = _grade_rows(items, only_graded)
        out.append(fmt.table(table_rows[0], table_rows[1:]))
        if feedback:
            out.append("\n## Feedback\n" + "\n".join(feedback))
        return fmt.cap("\n\n".join(out))

    courses = await app.courses.select(semester)
    if not courses:
        return f"No courses for semester {semester!r}."
    userid = await app.client.userid()
    finals = await final_grades()
    results = await app.client.batch(
        [("gradereport_user_get_grade_items", {"courseid": c.id, "userid": userid}) for c in courses]
    )
    out = [f"# Grades overview ({semester})"]
    by_sem: dict[str, list[tuple[Course, Any]]] = {}
    for c, res in zip(courses, results):
        by_sem.setdefault(c.semester or "?", []).append((c, res))
    for sem in sorted(by_sem, key=semester_key, reverse=True):
        rows = []
        for c, res in by_sem[sem]:
            items = []
            if isinstance(res, dict) and res.get("usergrades"):
                items = res["usergrades"][0].get("gradeitems") or []
            gradeable = [i for i in items if i.get("itemtype") not in ("course", "category")]
            graded = [i for i in gradeable if i.get("graderaw") is not None]
            latest = max(graded, key=lambda i: i.get("gradedategraded") or 0, default=None)
            total = next((i for i in items if i.get("itemtype") == "course"), {})
            latest_txt = ""
            if latest:
                latest_txt = f"{clean_inline(latest.get('itemname'))}: {clean_inline(latest.get('gradeformatted'))}"
                if latest.get("gradedategraded"):
                    latest_txt += f" ({timeutil.fmt_short(latest['gradedategraded'])})"
            error = " (no access)" if isinstance(res, MoodleError) else ""
            rows.append([c.title + error, c.id, finals.get(c.id, clean_inline(total.get("gradeformatted")) or "–"),
                         f"{len(graded)}/{len(gradeable)}", latest_txt])
        out.append(f"## {sem}\n" + fmt.table(["Course", "id", "Total", "Graded items", "Latest grade"], rows))
    out.append("Use get_grades(course=…) for item details and feedback.")
    return fmt.cap("\n\n".join(out))


# ---------------------------------------------------------------- activity status

def _overview_value(item: dict[str, Any]) -> str | None:
    """Render one overview cell. Returns None for columns that should be dropped (toggles, buttons)."""
    ctype = item.get("contenttype") or ""
    try:
        content = json.loads(item.get("contentjson") or "null")
    except ValueError:
        content = None
    if "action_link" in ctype:
        return None
    if content is None:
        return ""
    if "humandate" in ctype and isinstance(content, dict):
        return fmt.when(content.get("timestamp"), rel_days=14)
    if ctype.endswith("\\completion") and isinstance(content, dict):
        state = content.get("state")
        return "✓ done" if state in (1, 2) else ("✗ failed" if state == 3 else "–")
    if "pix_icon" in ctype and isinstance(content, dict):
        extras = {e.get("name"): e.get("value") for e in content.get("extras") or []}
        return extras.get("alt") or extras.get("title") or ""
    if isinstance(content, dict):
        if content.get("datatype") == "boolean":
            return "✓" if content.get("value") else "–"
        raw = content.get("content")
        if isinstance(raw, str):
            if "<input" in raw or "inplaceeditable" in raw:
                return None
            if "<" in raw:
                return to_text(raw, max_chars=120) or None
            return clean_inline(raw)
        if "activityname" in content:
            return clean_inline(content.get("activityname"))
        return clean_inline(str(raw if raw is not None else content.get("value") or ""))
    return clean_inline(str(content))


@moodle_tool(title="Activity status overview")
async def get_activity_status(
    course: CourseArg,
    types: Annotated[
        list[str] | None,
        Field(description="Activity types to include, e.g. ['assign','quiz']; default: all types with status info"),
    ] = None,
    show_unavailable: Annotated[bool, Field(description="Also list activities you cannot access (other groups etc.)")] = False,
) -> str:
    """Moodle's 'Activities' overview for a course: per activity due date, submission status, grade,
    responded, phase… — works for plugins without their own API too (offline quizzes, checkmarks…)."""
    c = await app.courses.resolve(course)
    mods = await data.mods(c.id)
    present = sorted({m.modname for m in mods})
    wanted = [t for t in present if (t in types if types else t not in _CONTENT_ONLY)]
    missing = [t for t in types if t not in present] if types else []
    if not wanted:
        return f"{c.label}: no activities of type(s) {types or 'with status'} (present: {', '.join(present)})."
    results = await app.client.batch(
        [("core_courseformat_get_overview_information", {"courseid": c.id, "modname": t}) for t in wanted]
    )
    out = [f"# Activity status — {c.label} [{c.id}]"]
    for t, res in zip(wanted, results):
        if isinstance(res, MoodleError) or not isinstance(res, dict):
            out.append(f"## {t}\n(overview not available: {res})")
            continue
        acts = res.get("activities") or []
        if not acts:
            continue
        headers = [h for h in res.get("headers") or [] if h.get("key") not in ("name", "actions")]
        rows: list[list[str]] = []
        keep: dict[str, bool] = {}
        hidden = 0
        for a in acts:
            cells: dict[str, str | None] = {}
            available = True
            for it in a.get("items") or []:
                if it.get("key") == "name":
                    try:
                        available = bool(json.loads(it.get("contentjson") or "{}").get("available", True))
                    except ValueError:
                        pass
                    continue
                cells[it.get("key")] = _overview_value(it)
            if not available and not show_unavailable:
                hidden += 1
                continue
            for h in headers:
                v = cells.get(h["key"])
                if v is not None and v != "":
                    keep[h["key"]] = True
                elif h["key"] not in keep and v is None:
                    keep.setdefault(h["key"], False)
            name = clean_inline(a.get("name"))
            rows.append([f"{name} · cmid {a.get('cmid')}", *[cells.get(h["key"]) or "–" for h in headers]])
        cols = [i for i, h in enumerate(headers) if keep.get(h["key"])]
        table_headers = ["Activity"] + [clean_inline(headers[i].get("name")) for i in cols]
        table_rows = [[r[0], *[r[i + 1] for i in cols]] for r in rows]
        note = f"\n({hidden} activities not available to you are hidden — e.g. other groups' copies)" if hidden else ""
        if not table_rows:
            out.append(f"## {t}\nNo activities available to you.{note}")
            continue
        out.append(f"## {t} ({len(table_rows)})\n" + fmt.table(table_headers, table_rows) + note)
    if missing:
        out.append(f"Not in this course: {', '.join(missing)}.")
    return fmt.cap("\n\n".join(out))
