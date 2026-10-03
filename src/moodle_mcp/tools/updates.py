"""get_updates: what changed since the last check (or a given date), across courses."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from pydantic import Field

from .. import data, fmt, timeutil
from ..app import app
from ..client import MoodleError
from ..courses import Course
from ..html import clean_inline, to_text
from ..toolkit import UserError, moodle_tool

_AREA_LABELS = {
    "configuration": "settings/description", "contentfiles": "files", "introfiles": "description files",
    "discussions": "new forum posts", "submissions": "submissions", "grades": "grades", "gradeitems": "grade items",
    "questions": "questions", "entries": "entries", "comments": "comments", "ratings": "ratings", "pages": "pages",
    "chapters": "chapters", "answers": "answers", "attempts": "attempts", "recordings": "recordings",
}
_IGNORED_AREAS = {"completion", "outcomes"}


def _snapshot(mods: list[data.Mod]) -> dict[str, Any]:
    return {
        str(m.cmid): {
            "name": m.name, "type": m.modname, "section": m.section_num,
            "files": sorted(f"{f.get('filename')}|{f.get('timemodified')}" for f in m.files),
        }
        for m in mods
    }


async def _course_updates(c: Course, since: int, snapshot: dict[str, Any] | None) -> tuple[list[str], dict[str, Any]]:
    sections = await data.contents(c.id)
    mods = data.mods_from_contents(c.id, sections)
    by_cmid = {m.cmid: m for m in mods}
    userid = await app.client.userid()
    check, grades, forums = await asyncio.gather(
        app.client.try_call(
            "core_course_check_updates", courseid=c.id, ttl=0,
            tocheck=[{"contextlevel": "module", "id": m.cmid, "since": since} for m in mods],
        ) if mods else asyncio.sleep(0, result=None),
        app.client.try_call("gradereport_user_get_grade_items", courseid=c.id, userid=userid, ttl=0),
        app.client.try_call("mod_forum_get_forums_by_courses", courseids=[c.id]),
    )
    lines: list[str] = []

    # 1. Activities: snapshot diff (precise) + check_updates (works without a snapshot).
    new_cmids: set[int] = set()
    if snapshot is not None:
        for m in mods:
            if str(m.cmid) not in snapshot:
                new_cmids.add(m.cmid)
                lines.append(f"- NEW {m.modname} «{m.name}» · cmid {m.cmid} · section {m.section_num} «{m.section_name}»"
                             + (" · " + " · ".join(f"{lbl}: {fmt.when(ts)}" for lbl, ts in m.dates) if m.dates else ""))
        for cmid, old in snapshot.items():
            if int(cmid) not in by_cmid:
                lines.append(f"- REMOVED/hidden {old.get('type')} «{old.get('name')}» (cmid {cmid})")
            elif by_cmid[int(cmid)].name != old.get("name"):
                lines.append(f"- RENAMED «{old.get('name')}» → «{by_cmid[int(cmid)].name}» (cmid {cmid})")
    if isinstance(check, dict):
        for inst in check.get("instances") or []:
            cmid = int(inst.get("id") or 0)
            m = by_cmid.get(cmid)
            if m is None or cmid in new_cmids:
                continue
            areas = [u.get("name") for u in inst.get("updates") or [] if u.get("name") not in _IGNORED_AREAS]
            if m.modname == "forum":
                areas = [a for a in areas if a != "discussions"]  # reported with details below
            if not areas:
                continue
            labels = ", ".join(_AREA_LABELS.get(a, a) for a in areas)
            verb = "new or changed" if snapshot is None else "changed"
            lines.append(f"- {verb} {m.modname} «{m.name}» · cmid {cmid}: {labels}")

    # 2. Files modified since.
    for m in mods:
        for f in m.files:
            mt = int(f.get("timemodified") or 0)
            if mt >= since:
                lines.append(f"- file {f.get('filename')} ({fmt.size(f.get('filesize'))}) in «{m.name}» · cmid {m.cmid} · {timeutil.fmt_short(mt)}")

    # 3. Forum posts.
    forum_list = forums if isinstance(forums, list) else []
    calls = [("mod_forum_get_forum_discussions", {"forumid": f["id"], "sortorder": -1, "page": 0, "perpage": 15}) for f in forum_list]
    results = await app.client.batch(calls, ttl=0) if calls else []
    for f, res in zip(forum_list, results):
        if isinstance(res, MoodleError) or not isinstance(res, dict):
            continue
        for d in res.get("discussions") or []:
            created = int(d.get("created") or 0)
            modified = int(d.get("timemodified") or 0)
            if created >= since:
                snippet = to_text(d.get("message"), max_chars=160)
                lines.append(f"- post in «{clean_inline(f.get('name'))}»: **{clean_inline(d.get('subject'))}** by "
                             f"{clean_inline(d.get('userfullname'))}, {timeutil.fmt_short(created)} · discussion {d.get('discussion')}"
                             + (f" — {snippet}" if snippet else ""))
            elif modified >= since:
                lines.append(f"- new replies in «{clean_inline(f.get('name'))}» → **{clean_inline(d.get('subject'))}** "
                             f"(last {timeutil.fmt_short(modified)}) · discussion {d.get('discussion')}")

    # 4. Grades.
    if isinstance(grades, dict):
        for ug in grades.get("usergrades") or []:
            for it in ug.get("gradeitems") or []:
                graded = int(it.get("gradedategraded") or 0)
                if graded >= since and it.get("itemtype") not in ("course", "category") and it.get("graderaw") is not None:
                    fb = to_text(it.get("feedback"), max_chars=200)
                    lines.append(f"- grade {clean_inline(it.get('itemname'))}: **{clean_inline(it.get('gradeformatted'))}** "
                                 f"(range {clean_inline(it.get('rangeformatted'))}) · {timeutil.fmt_short(graded)}"
                                 + (f" — feedback: {fb}" if fb else ""))
    return lines, _snapshot(mods)


@moodle_tool(title="What's new", read_only=True, idempotent=False)
async def get_updates(
    since: Annotated[
        str | None,
        Field(description="Since when: a date (YYYY-MM-DD / DD.MM.YYYY), '3d', '1w'… Default: since the previous get_updates call (else 7 days)"),
    ] = None,
    course: Annotated[str | int | None, Field(description="Only this course; default all courses of the current semester")] = None,
    remember: Annotated[bool, Field(description="Store this check as the new 'last seen' point")] = True,
) -> str:
    """What changed in Moodle: new/changed activities, new or updated files, new forum posts, new
    grades (with feedback) and new notifications — per course, since the last check or a date."""
    state = app.state.load("updates", {}) or {}
    snapshots: dict[str, Any] = state.get("snapshots") or {}
    last_runs: dict[str, int] = state.get("last_runs") or {}
    now = timeutil.now_ts()
    default_since = now - 7 * 86400
    courses = [await app.courses.resolve(course)] if course is not None else await app.courses.select("current")
    # Each course has its own "last checked" point, so checking one course never hides news of another.
    if since is None:
        course_since = {c.id: int(last_runs.get(str(c.id)) or default_since) for c in courses}
        notes_since = int(state.get("notifications_last_run") or default_since)
        known = [c for c in courses if str(c.id) in last_runs]
        if known and len(known) == len(courses):
            since_label = "your last check"
        elif known:
            since_label = "your last check (7 days for courses checked for the first time)"
        else:
            since_label = "7 days ago (first check)"
    else:
        try:
            explicit = int(timeutil.parse_when(since, past=True).timestamp())
        except ValueError as exc:
            raise UserError(str(exc)) from exc
        course_since = {c.id: explicit for c in courses}
        notes_since = explicit
        since_label = since
    results = await asyncio.gather(
        *(_course_updates(c, course_since[c.id], snapshots.get(str(c.id)) if since is None else None) for c in courses)
    )
    userid = await app.client.userid()
    notes = await app.client.try_call("message_popup_get_popup_notifications", useridto=userid, newestfirst=True, limit=30, offset=0, ttl=0)

    oldest = min(course_since.values(), default=notes_since)
    out = [f"# What's new since {timeutil.fmt_dt(oldest)} ({since_label})"]
    total = 0
    for c, (lines, snap) in zip(courses, results):
        if lines:
            total += len(lines)
            head = f"## {c.title} [{c.id}]"
            if course_since[c.id] != oldest:
                head += f" — since {timeutil.fmt_short(course_since[c.id])}"
            out.append(head + "\n" + "\n".join(lines))
        snapshots[str(c.id)] = snap
    if isinstance(notes, dict) and course is None:
        fresh = [n for n in notes.get("notifications") or [] if int(n.get("timecreated") or 0) >= notes_since]
        if fresh:
            nl = [f"- {timeutil.fmt_short(int(n.get('timecreated') or 0))} · {clean_inline(n.get('subject'))}"
                  + ("" if n.get("read") else " · unread") for n in fresh]
            out.append(f"## Notifications ({len(fresh)})\n" + "\n".join(nl))
            total += len(fresh)
    if total == 0:
        out.append("Nothing new.")
    if remember:
        state["snapshots"] = snapshots
        for c in courses:
            last_runs[str(c.id)] = now
        state["last_runs"] = last_runs
        if course is None:
            state["notifications_last_run"] = now
        state.pop("last_run", None)
        app.state.save("updates", state)
        out.append(f"_Saved this check ({timeutil.fmt_short(now)}); next get_updates() without `since` starts here._")
    return fmt.cap("\n\n".join(out), hint="pass `course` to focus on one course")
