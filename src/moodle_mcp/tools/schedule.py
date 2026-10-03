"""Time-related tools: get_deadlines, get_calendar."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Annotated, Any

from pydantic import Field

from .. import data, fmt, timeutil
from ..app import app
from ..client import MoodleError
from ..courses import Course
from ..html import clean_inline, to_text
from ..toolkit import UserError, moodle_tool

_CMID_RE = re.compile(r"[?&]id=(\d+)")


def _course_name(course_id: int, fallback: str = "") -> str:
    c = app.courses.by_id(course_id)
    if c is None:
        return clean_inline(fallback) or f"course {course_id}"
    return c.title


def _cmid_of(event: dict[str, Any]) -> int | None:
    m = _CMID_RE.search(str(event.get("url") or ""))
    return int(m.group(1)) if m else None


async def _action_events(course: Course | None, start: int, end: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    after: int | None = None
    for _ in range(10):  # 10 pages × 50 events is plenty
        params: dict[str, Any] = {"timesortfrom": start, "timesortto": end, "limitnum": 50}
        if after:
            params["aftereventid"] = after
        if course is not None:
            res = await app.client.call("core_calendar_get_action_events_by_course", courseid=course.id, **params)
        else:
            res = await app.client.call("core_calendar_get_action_events_by_timesort", limittononsuspendedevents=True, **params)
        page = (res or {}).get("events") or []
        events.extend(page)
        if len(page) < 50:
            break
        after = page[-1]["id"]
    return events


def _quiz_status(attempts: Any, quiz_grade: float | None = None) -> tuple[str, bool]:
    """Returns (text, needs_attention)."""
    if isinstance(attempts, MoodleError) or not isinstance(attempts, dict):
        return "", False
    items = attempts.get("attempts") or []
    if not items:
        return "not attempted yet", True
    in_progress = [a for a in items if a.get("state") in ("inprogress", "overdue")]
    finished = [a for a in items if a.get("state") == "finished"]
    if in_progress:
        a = in_progress[-1]
        return (f"⚠ attempt {a.get('attempt')} IN PROGRESS — NOT SUBMITTED (started {timeutil.fmt_short(a.get('timestart'))})", True)
    if finished:
        a = finished[-1]
        return (f"✓ submitted {timeutil.fmt_short(a.get('timefinish'))} ({len(finished)} attempt(s))", False)
    return f"attempt state: {items[-1].get('state')}", False


def _assign_status(status: Any) -> tuple[str, bool]:
    if isinstance(status, MoodleError) or not isinstance(status, dict):
        return "", False
    last = status.get("lastattempt") or {}
    sub = last.get("teamsubmission") or last.get("submission") or {}
    st = sub.get("status")
    grading = last.get("gradingstatus")
    if st == "submitted":
        return f"✓ submitted {timeutil.fmt_short(sub.get('timemodified'))}", False
    if st == "draft":
        return "⚠ DRAFT — not submitted for grading", True
    if st == "reopened":
        return "reopened — new attempt needed", True
    if grading == "graded":
        return "graded", False
    return "not submitted yet", True


async def _instances(events: list[dict[str, Any]]) -> dict[int, int]:
    """Map event id → activity instance id (action events carry the cmid, not the instance)."""
    course_ids = sorted({int((e.get("course") or {}).get("id") or e.get("courseid") or 0) for e in events} - {0})
    contents = await data.contents_many(course_ids)
    by_cmid: dict[int, int] = {}
    for cid, secs in contents.items():
        if isinstance(secs, list):
            for m in data.mods_from_contents(cid, secs):
                by_cmid[m.cmid] = m.instance
    out = {}
    for e in events:
        cmid = _cmid_of(e)
        if cmid and cmid in by_cmid:
            out[e["id"]] = by_cmid[cmid]
    return out


async def _enrich(events: list[dict[str, Any]]) -> dict[int, tuple[str, bool]]:
    calls: list[tuple[str, dict[str, Any]]] = []
    owners: list[int] = []
    instances = await _instances(events)
    for e in events:
        mod, inst = e.get("modulename"), instances.get(e["id"])
        if not inst:
            continue
        if mod == "quiz":
            calls.append(("mod_quiz_get_user_attempts", {"quizid": inst, "status": "all", "includepreviews": False}))
        elif mod == "assign":
            calls.append(("mod_assign_get_submission_status", {"assignid": inst}))
        elif mod == "choice":
            calls.append(("mod_choice_get_choice_options", {"choiceid": inst}))
        elif mod == "feedback":
            calls.append(("mod_feedback_get_feedback_access_information", {"feedbackid": inst}))
        else:
            continue
        owners.append(e["id"])
    if not calls:
        return {}
    results = await app.client.batch(calls)
    out: dict[int, tuple[str, bool]] = {}
    for (fn, _), event_id, res in zip(calls, owners, results):
        if fn == "mod_quiz_get_user_attempts":
            out[event_id] = _quiz_status(res)
        elif fn == "mod_assign_get_submission_status":
            out[event_id] = _assign_status(res)
        elif fn == "mod_choice_get_choice_options" and isinstance(res, dict):
            chosen = [clean_inline(o.get("text")) for o in res.get("options") or [] if o.get("checked")]
            out[event_id] = (f"✓ answered: {', '.join(chosen)}", False) if chosen else ("not answered yet", True)
        elif fn == "mod_feedback_get_feedback_access_information" and isinstance(res, dict):
            out[event_id] = ("✓ completed", False) if res.get("iscomplete") else ("not completed yet", True)
    return out


@moodle_tool(title="Upcoming deadlines")
async def get_deadlines(
    days: Annotated[int, Field(ge=1, le=365, description="How many days ahead")] = 14,
    course: Annotated[str | int | None, Field(description="Limit to one course (id/LV/acronym/name)")] = None,
    overdue_days: Annotated[int, Field(ge=0, le=60, description="Also show open items that became due in the last N days")] = 7,
) -> str:
    """To-dos with deadlines (dashboard timeline): assignments, quizzes, group registrations, choices,
    feedbacks… with submission status — flags quiz attempts that were started but NOT submitted."""
    c = await app.courses.resolve(course) if course is not None else None
    await app.courses.all()
    now = timeutil.now_ts()
    start = now - overdue_days * 86400
    end = now + days * 86400
    events = await _action_events(c, start, end)
    hidden_count = 0
    if c is None:
        excluded = {x.id for x in await app.courses.all() if x.excluded}
        kept = [e for e in events if int((e.get("course") or {}).get("id") or 0) not in excluded]
        hidden_count = len(events) - len(kept)
        events = kept
    events.sort(key=lambda e: (e.get("timesort") or 0, e.get("id")))
    scope = f" for {c.title}" if c else ""
    if not events:
        return f"No open deadlines{scope} in the next {days} days (and none overdue in the last {overdue_days} days)."
    status = await _enrich(events)
    attention = sum(1 for e in events if status.get(e["id"], ("", False))[1] and "IN PROGRESS" in status[e["id"]][0])
    overdue = [e for e in events if (e.get("timesort") or 0) < now]
    upcoming = [e for e in events if (e.get("timesort") or 0) >= now]
    out = [f"# Deadlines{scope}: next {days} days — {len(upcoming)} upcoming" + (f", {len(overdue)} overdue" if overdue else "")]
    out.append(f"Now: {timeutil.fmt_dt(now)}")
    if attention:
        out.append(f"⚠ {attention} quiz attempt(s) started but not submitted — they will not count until submitted.")

    def line(e: dict[str, Any]) -> str:
        ts = int(e.get("timesort") or 0)
        course_name = _course_name(int((e.get("course") or {}).get("id") or 0), (e.get("course") or {}).get("fullname", ""))
        rel = f" ({timeutil.relative(ts)})" if ts < now or ts - now < 36 * 3600 else ""
        parts = [f"- **{timeutil.to_dt(ts):%H:%M}**{rel} · {course_name} · {_event_title(e)}"]
        cmid = _cmid_of(e)
        if cmid:
            parts.append(f"cmid {cmid}")
        st = status.get(e["id"])
        if st and st[0]:
            parts.append(st[0])
        action = e.get("action") or {}
        if action.get("name") and not (st and st[0].startswith("✓")):
            parts.append(f"action: {clean_inline(action['name'])}" + ("" if action.get("actionable", True) else " (not yet possible)"))
        return " · ".join(parts)

    if overdue:
        out.append("\n## Overdue")
        out.extend(line(e) for e in overdue)
    current_day = None
    for e in upcoming:
        day = timeutil.to_dt(int(e.get("timesort") or 0)).date()
        if day != current_day:
            current_day = day
            ts = int(e.get("timesort") or 0)
            out.append(f"\n## {timeutil.fmt_date(ts)} ({timeutil.relative(timeutil.day_bounds(day)[0]) if day != timeutil.now().date() else 'today'})")
        out.append(line(e))
    if hidden_count:
        out.append(f"\n({hidden_count} item(s) from excluded/hidden courses not shown — pass `course` to see them.)")
    out.append("\nOnly items Moodle still lists as open are shown; use get_calendar for all events and get_activity(cmid) for details.")
    return fmt.cap("\n".join(out))


def _event_title(e: dict[str, Any]) -> str:
    """'quiz **Quiz 01** closes' — built from activity name + event type (stored event names are localised)."""
    activity = clean_inline(e.get("activityname"))
    mod = e.get("modulename") or ""
    etype = str(e.get("eventtype") or "")
    if activity and mod:
        label = {"close": "closes", "due": "due", "open": "opens", "deadline": "registration deadline",
                 "expectcompletionon": "expected completion", "gradingdue": "grading due"}.get(etype, etype)
        return f"{mod} **{activity}** {label}".rstrip()
    return f"**{clean_inline(e.get('name'))}**"


_EVENT_TYPES = {
    "due": "due", "close": "closes", "open": "opens", "course": "course event", "group": "group event",
    "user": "personal", "site": "site event", "category": "category event", "expectcompletionon": "expected completion",
}


@moodle_tool(title="Calendar events")
async def get_calendar(
    start: Annotated[str | None, Field(description="Start date: YYYY-MM-DD, DD.MM.YYYY, today, tomorrow, monday, +3d … (default today)")] = None,
    end: Annotated[str | None, Field(description="End date (exclusive). Default start + days")] = None,
    days: Annotated[int, Field(ge=1, le=366, description="Range length when end is not given")] = 7,
    course: Annotated[str | int | None, Field(description="Only this course; default all courses of the current semester")] = None,
) -> str:
    """All calendar events in a date range (deadlines, openings, course/group/personal events, booked
    appointments), grouped by day."""
    try:
        start_dt = timeutil.parse_when(start, default=timeutil.now().replace(hour=0, minute=0, second=0, microsecond=0))
        end_dt = timeutil.parse_when(end) if end else start_dt + timedelta(days=days)
    except ValueError as exc:
        raise UserError(str(exc)) from exc
    if end_dt <= start_dt:
        raise UserError("end must be after start.")
    if course is not None:
        courses = [await app.courses.resolve(course)]
    else:
        courses = await app.courses.select("current")
    params: dict[str, Any] = {
        "options": {"timestart": int(start_dt.timestamp()), "timeend": int(end_dt.timestamp()),
                    "userevents": True, "siteevents": True},
        "events": {"courseids": [c.id for c in courses]},
    }
    res = await app.client.call("core_calendar_get_calendar_events", **params)
    events = sorted((res or {}).get("events") or [], key=lambda e: (e.get("timestart") or 0, e.get("id")))
    scope = courses[0].title if course is not None else f"{len(courses)} current courses + personal/site events"
    head = f"# Calendar {timeutil.fmt_date(start_dt.timestamp())} – {timeutil.fmt_date(end_dt.timestamp() - 1)} ({scope})"
    if not events:
        return head + "\n\nNo events."
    out = [head, f"{len(events)} event(s). Lectures are usually not in Moodle's calendar (check the university timetable)."]
    current_day = None
    for e in events:
        ts = int(e.get("timestart") or 0)
        day = timeutil.to_dt(ts).date()
        if day != current_day:
            current_day = day
            out.append(f"\n## {timeutil.fmt_date(ts)}")
        dur = int(e.get("timeduration") or 0)
        t = f"{timeutil.to_dt(ts):%H:%M}" + (f"–{timeutil.to_dt(ts + dur):%H:%M}" if dur else "")
        etype = _EVENT_TYPES.get(str(e.get("eventtype")), str(e.get("eventtype") or ""))
        cname = _course_name(int(e.get("courseid") or 0)) if e.get("courseid") else "personal"
        title = _event_title(e) if e.get("activityname") else f"**{clean_inline(e.get('name'))}**"
        parts = [f"- **{t}** · {cname} · {title}"]
        if not e.get("activityname"):
            parts.append(f"{e.get('modulename') or ''} ({etype})".strip() if e.get("modulename") else etype)
        if e.get("location"):
            parts.append(f"📍 {clean_inline(e['location'])}")
        line = " · ".join(parts)
        desc = to_text(e.get("description"), max_chars=200)
        if desc:
            line += f"\n  {desc}"
        out.append(line)
    return fmt.cap("\n".join(out))
