"""Discovery tools: status, list_courses, get_course, search."""

from __future__ import annotations

import asyncio
import re
from typing import Annotated

from pydantic import Field

from .. import __version__, data, fmt, timeutil
from ..app import app
from ..config import config_path
from ..courses import Course, fold, semester_key
from ..html import clean_inline, to_markdown, to_text, truncate
from ..toolkit import UserError, moodle_tool

CourseArg = Annotated[
    str | int,
    Field(description="Course id, LV number (051050), acronym (SE2), shortname or part of the title; add e.g. '2025W' to pick an older run"),
]
SemesterArg = Annotated[
    str, Field(description="'current', 'past', 'all' or a semester code like 2026W / 2025S")
]

_COMPLETION = {1: "✓ done", 2: "✓ passed", 3: "✗ failed"}


async def final_grades() -> dict[int, str]:
    userid = await app.client.userid()
    res = await app.client.try_call("gradereport_overview_get_course_grades", userid=userid)
    out: dict[int, str] = {}
    if isinstance(res, dict):
        for g in res.get("grades") or []:
            grade = clean_inline(str(g.get("grade") or ""))
            if grade and grade != "-":
                out[int(g["courseid"])] = grade
    return out


async def my_groups() -> dict[int, list[str]]:
    userid = await app.client.userid()
    res = await app.client.try_call("core_group_get_course_user_groups", courseid=0, userid=userid)
    out: dict[int, list[str]] = {}
    if isinstance(res, dict):
        for g in res.get("groups") or []:
            out.setdefault(int(g.get("courseid") or 0), []).append(clean_inline(g.get("name") or ""))
    return out


@moodle_tool(title="Connection status")
async def status() -> str:
    """Check the Moodle connection: user, site, available features, current semester and configuration."""
    settings = app.settings
    info = await app.client.site_info()
    courses = await app.courses.all()
    current = await app.courses.current_semester()
    sems = await app.courses.semesters()
    n_current = sum(1 for c in courses if c.semester == current)
    fn_count = len(info.get("functions") or [])
    lines = [
        "# Moodle connection: OK",
        f"- User: {clean_inline(info.get('fullname'))} ({info.get('username')}, id {info.get('userid')})",
        f"- Site: {clean_inline(info.get('sitename'))} — {settings.site_url} (Moodle {info.get('release', '?')})",
        f"- Web-service functions available: {fn_count} · file downloads: {'yes' if info.get('downloadfiles') else 'no'}",
        f"- Courses: {len(courses)} in {len(sems)} semesters ({', '.join(sems[:8])}{'…' if len(sems) > 8 else ''})",
        f"- Current semester: {current} ({n_current} courses)"
        + (f" · excluded from overviews: {', '.join(c.title for c in courses if c.excluded and c.semester == current)}"
           if any(c.excluded and c.semester == current for c in courses) else ""),
        f"- Time: {timeutil.fmt_dt(timeutil.now_ts())} ({settings.timezone}) · language: {settings.lang}",
        f"- Web session for web-only pages: {'configured' if settings.private_token else 'off (optional, needs MOODLE_PRIVATE_TOKEN)'}",
        f"- Config file: {config_path()} · state: {settings.state_dir}",
        f"- Default download folder: {settings.download_dir}",
        f"- moodle-student-mcp {__version__}",
    ]
    return "\n".join(lines)


@moodle_tool(title="List courses")
async def list_courses(
    semester: SemesterArg = "current",
    query: Annotated[str | None, Field(description="Optional filter on title/shortname/LV number")] = None,
) -> str:
    """List enrolled courses grouped by semester with id, LV number, group, final grade and last access."""
    courses = await app.courses.select(semester, include_excluded=True)
    if query:
        fq = fold(query)
        courses = [c for c in courses if fq in fold(f"{c.title} {c.shortname} {c.lv or ''} {' '.join(c.acronyms)}")]
    if not courses:
        sems = await app.courses.semesters()
        return f"No courses for semester={semester!r}{f' and query={query!r}' if query else ''}. Semesters with courses: {', '.join(sems)}."
    grades, groups = await asyncio.gather(final_grades(), my_groups())
    by_sem: dict[str, list[Course]] = {}
    for c in courses:
        by_sem.setdefault(c.semester or "unknown", []).append(c)
    out = []
    for sem in sorted(by_sem, key=semester_key, reverse=True):
        items = by_sem[sem]
        out.append(f"## {sem} ({len(items)})")
        rows = []
        for c in items:
            grp = ", ".join(g for g in groups.get(c.id, []) if g) or ""
            last = timeutil.fmt_dt(c.lastaccess, time_of_day=False) if c.lastaccess else "never"
            name = c.title + (" ★" if c.favourite else "")
            if c.hidden:
                name += " (hidden in Moodle → excluded from overviews)"
            elif c.excluded:
                name += " (excluded via exclude_courses)"
            rows.append([c.id, c.lv or "", name, grp, grades.get(c.id, ""), last])
        out.append(fmt.table(["id", "LV", "Course", "My groups", "Grade", "Last access"], rows))
    return fmt.cap("\n\n".join(out))


def _mod_lines(m: data.Mod, *, compact: bool, full: bool) -> list[str]:
    raw = m.raw
    flags = []
    if m.hidden:
        flags.append("hidden")
    if not m.user_visible:
        flags.append("not available")
    comp = raw.get("completiondata") or {}
    if isinstance(comp, dict) and comp.get("state") in _COMPLETION:
        flags.append(_COMPLETION[comp["state"]])
    if m.modname == "label":
        text = to_markdown(m.description) or m.name
        if compact:
            return [f"- label: {truncate(to_text(text), 120)}"]
        text = text if full else truncate(text, 700, "see get_course(section=…)")
        indented = text.replace("\n", "\n  ")
        return [f"- label (cmid {m.cmid}): {indented}"]
    head = f"- {m.modname} **{m.name}** · cmid {m.cmid}"
    if flags:
        head += " · " + ", ".join(flags)
    if m.dates:
        head += " · " + " · ".join(f"{label}: {fmt.when(ts)}" for label, ts in m.dates)
    lines = [head]
    if compact:
        return lines
    if m.modname == "url":
        target = next((c.get("fileurl") for c in raw.get("contents") or [] if c.get("fileurl")), None)
        if target:
            lines.append(f"  → {target}")
    files = m.files
    if files:
        shown = files if full else files[:6]
        listing = ", ".join(f"{f.get('filename')} ({fmt.size(f.get('filesize'))})" for f in shown)
        more = f", … +{len(files) - len(shown)} more" if len(files) > len(shown) else ""
        lines.append(f"  files: {listing}{more}")
    avail = raw.get("availabilityinfo")
    if avail:
        lines.append(f"  restriction: {to_text(avail, max_chars=300)}")
    if m.description:
        desc = to_markdown(m.description)
        desc = desc if full else truncate(desc, 400, "get_activity for more")
        lines.append("  " + desc.replace("\n", "\n  "))
    return lines


@moodle_tool(title="Course structure")
async def get_course(
    course: CourseArg,
    section: Annotated[
        str | int | None, Field(description="Show only this section (number or part of its name), untruncated")
    ] = None,
    compact: Annotated[bool, Field(description="Outline only: no summaries, labels, file lists")] = False,
) -> str:
    """Course overview: teachers, groups, grade, then every section with summary and activities
    (type, name, cmid, dates, files, restrictions). Use the cmids with get_activity/read_file."""
    c = await app.courses.resolve(course)
    sections, meta, grades, groups = await asyncio.gather(
        data.contents(c.id),
        app.client.try_call("core_course_get_courses_by_field", field="id", value=c.id),
        final_grades(),
        my_groups(),
    )
    header = [f"# {c.title} ({c.semester or '?'}{', LV ' + c.lv if c.lv else ''})",
              f"id {c.id} · {c.shortname} · {c.url}"]
    info_bits = []
    if isinstance(meta, dict) and meta.get("courses"):
        cm = meta["courses"][0]
        teachers = [clean_inline(t.get("fullname")) for t in cm.get("contacts") or []]
        if teachers:
            info_bits.append("Teachers: " + ", ".join(teachers))
    if groups.get(c.id):
        info_bits.append("My groups: " + ", ".join(groups[c.id]))
    if c.id in grades:
        info_bits.append(f"Final grade: {grades[c.id]}")
    if c.startdate:
        info_bits.append(f"Period: {timeutil.fmt_date(c.startdate)} – {timeutil.fmt_date(c.enddate) if c.enddate else 'open'}")
    if info_bits:
        header.append(" · ".join(info_bits))
    if isinstance(meta, dict) and meta.get("courses") and not compact and section is None:
        summary = to_markdown(meta["courses"][0].get("summary"), max_chars=600)
        if summary:
            header.append("\n" + summary)

    selected = sections
    if section is not None:
        s = str(section).strip()
        if s.isdigit():
            selected = [x for x in sections if int(x.get("section") or 0) == int(s)]
        else:
            fs = fold(s)
            selected = [x for x in sections if fs in fold(data.section_title(x))]
        if not selected:
            names = "; ".join(f"{x.get('section')}: {data.section_title(x)}" for x in sections)
            raise UserError(f"No section matches {section!r}. Sections: {names}")
    full = section is not None
    out = ["\n".join(header)]
    for sec in selected:
        num = int(sec.get("section") or 0)
        title = data.section_title(sec)
        lines = [f"## {num} · {title}"]
        if not sec.get("uservisible", True):
            lines[0] += " (not available)"
        if sec.get("availabilityinfo"):
            lines.append(f"restriction: {to_text(sec['availabilityinfo'], max_chars=300)}")
        if not compact:
            summary = to_markdown(sec.get("summary"))
            if summary:
                lines.append(summary if full else truncate(summary, 900, f"get_course(section={num})"))
        for m in data.mods_from_contents(c.id, [sec]):
            lines.extend(_mod_lines(m, compact=compact, full=full))
        if len(lines) == 1 and not sec.get("modules"):
            lines.append("(empty)")
        out.append("\n".join(lines))
    return fmt.cap("\n\n".join(out), hint="use section=… or compact=true")


# ---------------------------------------------------------------- search

def _snippet(text: str, words: list[str], width: int = 160) -> str:
    folded = fold(text)
    pos = min((folded.find(w) for w in words if folded.find(w) >= 0), default=-1)
    if pos < 0:
        return truncate(text, width)
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")


@moodle_tool(title="Search course content")
async def search(
    query: Annotated[str, Field(description="Words to find (all must match; case/diacritics-insensitive)")],
    scope: Annotated[
        str, Field(description="'current', 'all', a semester code (2025W) or one course reference")
    ] = "current",
    limit: Annotated[int, Field(ge=1, le=200, description="Max results")] = 40,
) -> str:
    """Search section names/summaries, activity names and descriptions, labels, file names and page
    texts across courses (not the contents of files — use read_file for that). Returns matches
    grouped by course with cmids and snippets."""
    words = [w for w in re.findall(r"\w+", fold(query)) if w]
    if not words:
        raise UserError("Empty query.")
    s = scope.strip().lower()
    if s in ("current", "all", "past") or re.match(r"^\d{4}[ws]$", s) or re.match(r"^[ws]s\d{4}$", s):
        courses = await app.courses.select(scope)
    else:
        courses = [await app.courses.resolve(scope)]
    if not courses:
        return f"No courses in scope {scope!r}."
    ids = [c.id for c in courses]
    contents_map, pages_res = await asyncio.gather(
        data.contents_many(ids), app.client.try_call("mod_page_get_pages_by_courses", courseids=ids)
    )
    page_text: dict[int, str] = {}
    if isinstance(pages_res, dict):
        for p in pages_res.get("pages") or []:
            page_text[int(p.get("coursemodule") or 0)] = to_text(p.get("content"))

    def match(text: str) -> bool:
        ft = fold(text)
        return all(w in ft for w in words)

    results: dict[int, list[tuple[int, str]]] = {}
    total = 0
    for c in courses:
        secs = contents_map.get(c.id)
        if not isinstance(secs, list):
            continue
        hits: list[tuple[int, str]] = []
        for sec in secs:
            num = int(sec.get("section") or 0)
            title = data.section_title(sec)
            summary = to_text(sec.get("summary"))
            if match(title):
                hits.append((3, f"- section {num} **{title}**"))
            elif summary and match(summary):
                hits.append((2, f"- section {num} **{title}** (summary): {_snippet(summary, words)}"))
            for m in data.mods_from_contents(c.id, [sec]):
                where = f"{m.modname} **{m.name}** · cmid {m.cmid} · section {num}"
                desc = to_text(m.description)
                if match(m.name):
                    hits.append((3, f"- {where}"))
                elif desc and match(desc):
                    hits.append((2, f"- {where}: {_snippet(desc, words)}"))
                elif m.cmid in page_text and match(page_text[m.cmid]):
                    hits.append((2, f"- {where} (page text): {_snippet(page_text[m.cmid], words)}"))
                for f in m.files:
                    fname = f.get("filename") or ""
                    if match(fname):
                        hits.append((2, f"- file `{fname}` in {where}"))
                for item in m.raw.get("contents") or []:
                    if m.modname == "book" and item.get("type") == "file" and item.get("content") and match(str(item["content"])):
                        hits.append((2, f"- {where} (chapter): {clean_inline(item['content'])}"))
        if hits:
            hits.sort(key=lambda h: -h[0])
            results[c.id] = hits
            total += len(hits)
    if not results:
        return f"No matches for {query!r} in {len(courses)} course(s) (scope {scope}). Note: file contents are not searched — use read_file."
    out = [f"# {total} match(es) for {query!r} in {len(results)} course(s)"]
    shown = 0
    for c in courses:
        if c.id not in results or shown >= limit:
            continue
        out.append(f"## {c.label} [{c.id}]")
        for _, line in results[c.id][: max(1, limit - shown)]:
            out.append(line)
            shown += 1
    if shown < total:
        out.append(f"\n… {total - shown} more — raise `limit` or narrow `scope`.")
    return fmt.cap("\n".join(out))
