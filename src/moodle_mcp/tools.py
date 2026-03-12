"""FastMCP server and Moodle MCP tools.

All tools strip Moodle API responses to essential fields only to avoid
bloating the LLM context and reduce hallucinations.
"""

import html
import json
import re
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from .client import call_moodle_api

mcp = FastMCP("Moodle")

# Minimal keys from get_site_info: user identity only (saves context).
_SITE_INFO_KEYS = ("userid", "username", "fullname")


def _clean_text(s: Any) -> Any:
    """Unescape HTML entities and strip Moodle {mlang xx}...{mlang} tags."""
    if s is None or not isinstance(s, str):
        return s
    s = html.unescape(s)
    # Replace each {mlang XX}content{mlang} block with content; join adjacent with " / "
    s = re.sub(r"\{mlang\s+[^}]+\}(.*?)\{mlang\}", r"\1 / ", s, flags=re.DOTALL)
    return s.rstrip(" / ")


def _strip_site_info(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract only userid, username, fullname; no site URLs or pictures."""
    out: dict[str, Any] = {}
    for k in _SITE_INFO_KEYS:
        v = raw.get(k)
        out[k] = _clean_text(v) if k in ("username", "fullname") else v
    return out


@mcp.tool()
def get_site_info() -> dict[str, Any]:
    """
    Get basic current user info.

    Returns only: userid, username, fullname. Use userid for get_enrolled_courses.
    """
    raw = call_moodle_api("core_webservice_get_site_info")
    if not isinstance(raw, dict):
        return {k: None for k in _SITE_INFO_KEYS}
    return _strip_site_info(raw)


# Max courses to return when filtering enrolled courses (saves context).
_MAX_ENROLLED_COURSES = 12


def _filter_recent_courses(courses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Prefer courses from current year (in shortname/fullname), then sort by id desc,
    return at most _MAX_ENROLLED_COURSES. Output: only id and fullname (cleaned).
    """
    current_year = str(datetime.now(timezone.utc).year)
    # Prefer courses whose shortname or fullname contains current year
    with_year = [c for c in courses if current_year in str(c.get("shortname") or "") or current_year in str(c.get("fullname") or "")]
    pool = with_year if with_year else courses
    # Sort by id descending (most recent first), take top N
    sorted_pool = sorted(pool, key=lambda x: (x.get("id") or 0), reverse=True)
    limited = sorted_pool[:_MAX_ENROLLED_COURSES]
    return [
        {"id": c.get("id"), "fullname": _clean_text(c.get("fullname"))}
        for c in limited
    ]


@mcp.tool()
def get_enrolled_courses(userid: int) -> CallToolResult:
    """
    Get recent courses the student is enrolled in (filtered to save context).

    Requires userid from get_site_info. Excludes hidden courses if Moodle provides
    visible flag. Prefers courses from the current year (in shortname/fullname),
    then sorts by id descending and returns at most 12 courses. Returns a single
    JSON array of objects with id and fullname only (no HTML entities).
    """
    raw = call_moodle_api("core_enrol_get_users_courses", userid=userid)
    if not isinstance(raw, list):
        return CallToolResult(content=[TextContent(type="text", text="[]")])
    # Optional: exclude hidden courses (Moodle often returns visible=0/1)
    result: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        if c.get("visible") == 0:
            continue
        result.append({
            "id": c.get("id"),
            "shortname": c.get("shortname"),
            "fullname": c.get("fullname"),
        })
    courses = _filter_recent_courses(result)
    # Single JSON block for the LLM (saves tokens, better than one block per course)
    text = json.dumps(courses, indent=2, ensure_ascii=False)
    return CallToolResult(content=[TextContent(type="text", text=text)])


def _simplify_section(section: dict[str, Any]) -> dict[str, Any]:
    """Keep only id, name, and modules with id, name, modname, url (if present). Names cleaned from HTML."""
    out: dict[str, Any] = {
        "id": section.get("id"),
        "name": _clean_text(section.get("name")),
    }
    modules = section.get("modules")
    if not isinstance(modules, list):
        out["modules"] = []
        return out
    out["modules"] = []
    for m in modules:
        if not isinstance(m, dict):
            continue
        mod = {
            "id": m.get("id"),
            "name": _clean_text(m.get("name")),
            "modname": m.get("modname"),
        }
        url = m.get("url")
        if url is not None:
            mod["url"] = url
        out["modules"].append(mod)
    return out


@mcp.tool()
def get_course_contents(courseid: int) -> CallToolResult:
    """
    Get course structure: sections and modules (materials, URLs).

    Returns a single JSON array of sections with id and name; each section's
    modules have id, name, modname (e.g. resource, assign, url, folder), and url if present.
    No descriptions, HTML entities, completion, or dates.
    """
    raw = call_moodle_api("core_course_get_contents", courseid=courseid)
    if not isinstance(raw, list):
        return CallToolResult(content=[TextContent(type="text", text="[]")])
    result: list[dict[str, Any]] = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        result.append(_simplify_section(s))
    text = json.dumps(result, indent=2, ensure_ascii=False)
    return CallToolResult(content=[TextContent(type="text", text=text)])


def _format_duedate(ts: int | None) -> str | None:
    """Convert Unix timestamp to ISO string; return None for 0 or missing."""
    if ts is None or ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _strip_assignment(a: dict[str, Any]) -> dict[str, Any]:
    """Keep only id, name, intro, duedate, duedate_readable. Name and intro cleaned from HTML."""
    duedate = a.get("duedate")
    return {
        "id": a.get("id"),
        "name": _clean_text(a.get("name")),
        "intro": _clean_text(a.get("intro")),
        "duedate": duedate,
        "duedate_readable": _format_duedate(duedate),
    }


@mcp.tool()
def get_course_assignments(courseid: int) -> CallToolResult:
    """
    Get assignments for a course with descriptions and due dates.

    Returns a single JSON array with id, name, intro (description), duedate (unix),
    duedate_readable (ISO). No grading config, attachment limits, or internal flags.
    Text fields are cleaned from HTML entities.
    """
    raw = call_moodle_api("mod_assign_get_assignments", **{"courseids[0]": courseid})
    if not isinstance(raw, dict):
        return CallToolResult(content=[TextContent(type="text", text="[]")])
    courses = raw.get("courses")
    if not isinstance(courses, list):
        return CallToolResult(content=[TextContent(type="text", text="[]")])
    for course in courses:
        if not isinstance(course, dict):
            continue
        if course.get("id") == courseid:
            assignments = course.get("assignments")
            if not isinstance(assignments, list):
                return CallToolResult(content=[TextContent(type="text", text="[]")])
            result = [_strip_assignment(a) for a in assignments if isinstance(a, dict)]
            text = json.dumps(result, indent=2, ensure_ascii=False)
            return CallToolResult(content=[TextContent(type="text", text=text)])
    return CallToolResult(content=[TextContent(type="text", text="[]")])
