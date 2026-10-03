"""Forum tools: get_announcements, get_forum, get_discussion."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from .. import data, fmt, timeutil
from ..app import app
from ..client import MoodleError
from ..courses import Course
from ..html import clean_inline, to_markdown
from ..toolkit import UserError, moodle_tool


def _attachments(items: list[dict[str, Any]] | None) -> str:
    names = [f"{a.get('filename')} ({fmt.size(a.get('filesize'))})" for a in items or [] if a.get("filename")]
    return ("📎 " + ", ".join(names)) if names else ""


async def discussions_lines(forumid: int, *, page: int, per_page: int) -> list[str]:
    res = await app.client.call("mod_forum_get_forum_discussions", forumid=forumid, sortorder=-1, page=page, perpage=per_page)
    discussions = (res or {}).get("discussions") or []
    if not discussions:
        return ["No discussions." if page == 0 else "No more discussions."]
    lines = [f"**Discussions** (page {page}, newest activity first):"]
    for d in discussions:
        flags = []
        if d.get("pinned"):
            flags.append("📌")
        if d.get("numunread"):
            flags.append(f"{d['numunread']} unread")
        if d.get("locked"):
            flags.append("locked")
        replies = int(d.get("numreplies") or 0)
        last = d.get("timemodified") or d.get("modified")
        lines.append(
            f"- **{clean_inline(d.get('name') or d.get('subject'))}** · discussion {d.get('discussion')} · "
            f"{clean_inline(d.get('userfullname'))}, {timeutil.fmt_short(d.get('created'))} · {fmt.plural(replies, 'reply', 'replies')}"
            + (f" · last activity {timeutil.fmt_short(last)}" if replies and last else "")
            + (f" · {' '.join(flags)}" if flags else "")
        )
    if len(discussions) == per_page:
        lines.append(f"More: get_forum(forum=…, page={page + 1})")
    return lines


async def _forums_for(courses: list[Course]) -> list[dict[str, Any]]:
    if not courses:
        return []
    res = await app.client.call("mod_forum_get_forums_by_courses", courseids=[c.id for c in courses])
    return res if isinstance(res, list) else []


@moodle_tool(title="Announcements")
async def get_announcements(
    course: Annotated[str | int | None, Field(description="One course; default: all courses of the current semester")] = None,
    days: Annotated[int, Field(ge=1, le=3650, description="Only posts from the last N days")] = 30,
    limit: Annotated[int, Field(ge=1, le=100, description="Max posts")] = 20,
) -> str:
    """Announcements (news forum posts) with full text, newest first — across all current courses or one course."""
    courses = [await app.courses.resolve(course)] if course is not None else await app.courses.select("current")
    forums = [f for f in await _forums_for(courses) if f.get("type") == "news"]
    since = timeutil.now_ts() - days * 86400
    calls = [("mod_forum_get_forum_discussions", {"forumid": f["id"], "sortorder": -1, "page": 0, "perpage": limit}) for f in forums]
    results = await app.client.batch(calls)
    posts: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for f, res in zip(forums, results):
        if isinstance(res, MoodleError):
            continue
        for d in (res or {}).get("discussions") or []:
            if int(d.get("created") or 0) >= since:
                posts.append((f, d))
    posts.sort(key=lambda x: int(x[1].get("created") or 0), reverse=True)
    scope = courses[0].title if course is not None else f"{len(courses)} current courses"
    if not posts:
        return f"No announcements in the last {days} days ({scope})."
    out = [f"# Announcements — {scope}, last {days} days ({len(posts[:limit])} of {len(posts)})"]
    for f, d in posts[:limit]:
        cname = (app.courses.by_id(int(f.get("course") or 0)) or courses[0]).title
        head = (f"## {clean_inline(d.get('subject') or d.get('name'))}\n"
                f"{cname} · {clean_inline(d.get('userfullname'))} · {timeutil.fmt_dt(d.get('created'), rel=True)} · "
                f"discussion {d.get('discussion')}")
        if d.get("numreplies"):
            head += f" · {fmt.plural(int(d['numreplies']), 'reply', 'replies')}"
        if d.get("numunread"):
            head += " · unread"
        body = to_markdown(d.get("message"))
        att = _attachments(d.get("attachments"))
        out.append("\n".join(x for x in (head, body, att) if x))
    return fmt.cap("\n\n".join(out), hint="lower `limit` or `days`")


@moodle_tool(title="Forums and discussions")
async def get_forum(
    forum: Annotated[str | int | None, Field(description="Forum cmid or URL → list its discussions")] = None,
    course: Annotated[str | int | None, Field(description="Course → list all its forums")] = None,
    page: Annotated[int, Field(ge=0, description="Discussion page (0 = newest)")] = 0,
    per_page: Annotated[int, Field(ge=1, le=100)] = 25,
) -> str:
    """List the forums of a course (with discussion/unread counts), or the discussions of one forum."""
    if forum is None and course is None:
        raise UserError("Pass `forum` (cmid/URL) to list discussions, or `course` to list its forums.")
    if forum is not None:
        c, m = await data.get_mod(forum)
        if m.modname != "forum":
            raise UserError(f"cmid {m.cmid} is a {m.modname}, not a forum.")
        forums = await _forums_for([c] if c else [])
        rec = next((f for f in forums if int(f.get("cmid") or 0) == m.cmid), None)
        if rec is None:
            raise UserError("Forum not accessible.")
        head = [f"# Forum: {m.name}", f"{c.label if c else ''} · cmid {m.cmid} · type {rec.get('type')}"]
        intro = to_markdown(rec.get("intro"), max_chars=1500)
        if intro:
            head.append(intro)
        lines = await discussions_lines(int(rec["id"]), page=page, per_page=per_page)
        lines.append("Read a thread: get_discussion(discussion_id=…)")
        return fmt.cap("\n\n".join(head) + "\n\n" + "\n".join(lines))
    c = await app.courses.resolve(course)  # type: ignore[arg-type]
    forums = await _forums_for([c])
    if not forums:
        return f"{c.label}: no forums."
    rows = []
    for f in forums:
        rows.append([f"{clean_inline(f.get('name'))} · cmid {f.get('cmid')}", f.get("type"), f.get("numdiscussions", ""),
                     f.get("unreadpostscount", "") or ""])
    return f"# Forums — {c.label} [{c.id}]\n\n" + fmt.table(["Forum", "Type", "Discussions", "Unread"], rows) + \
        "\n\nList discussions: get_forum(forum=<cmid>). Note: 'moodleoverflow' Q&A forums have no API (see get_activity)."


@moodle_tool(title="Discussion thread")
async def get_discussion(
    discussion_id: Annotated[int, Field(description="Discussion id (from get_forum / get_announcements)")],
    max_posts: Annotated[int, Field(ge=1, le=500)] = 100,
) -> str:
    """A whole discussion thread: every post (nested), author, date, text and attachments."""
    res = await app.client.call("mod_forum_get_discussion_posts", discussionid=discussion_id, sortby="created", sortdirection="ASC")
    posts = (res or {}).get("posts") or []
    if not posts:
        return f"Discussion {discussion_id} has no visible posts."
    by_parent: dict[int | None, list[dict[str, Any]]] = {}
    ids = {p["id"] for p in posts}
    for p in posts:
        parent = p.get("parentid") if p.get("parentid") in ids else None
        by_parent.setdefault(parent, []).append(p)
    out = [f"# {clean_inline(posts[0].get('subject'))}", f"discussion {discussion_id} · {len(posts)} post(s)"]
    count = 0

    def walk(parent: int | None, depth: int) -> None:
        nonlocal count
        for p in by_parent.get(parent, []):
            if count >= max_posts:
                return
            count += 1
            author = clean_inline((p.get("author") or {}).get("fullname"))
            indent = "  " * depth
            marker = "↳ " if depth else ""
            head = f"{indent}**{marker}{author}** · {timeutil.fmt_short(p.get('timecreated'))}"
            if p.get("isprivatereply"):
                head += " · private reply"
            if p.get("unread"):
                head += " · unread"
            subject = clean_inline(p.get("subject"))
            if depth and subject and not subject.lower().startswith("re:"):
                head += f" · {subject}"
            body = to_markdown(p.get("message")).replace("\n", "\n" + indent)
            att = _attachments(p.get("attachments"))
            out.append("\n".join(x for x in (head, indent + body, indent + att if att else "") if x.strip()))
            walk(p["id"], depth + 1)

    walk(None, 0)
    if count < len(posts):
        out.append(f"… {len(posts) - count} more posts (raise max_posts).")
    return fmt.cap("\n\n".join(out))
