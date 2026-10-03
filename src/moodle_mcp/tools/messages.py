"""Notifications and private messages."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from .. import fmt, timeutil
from ..app import app
from ..html import clean_inline, to_markdown, to_text
from ..toolkit import moodle_tool


@moodle_tool(title="Notifications")
async def get_notifications(
    unread_only: Annotated[bool, Field(description="Only unread notifications")] = False,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> str:
    """Moodle notifications (graded submissions, quiz reminders, forum digests, …), newest first."""
    userid = await app.client.userid()
    res = await app.client.call(
        "message_popup_get_popup_notifications", useridto=userid, newestfirst=True, limit=limit if not unread_only else 100, offset=0
    )
    items = res.get("notifications") or []
    if unread_only:
        items = [n for n in items if not n.get("read")]
    items = items[:limit]
    unread = res.get("unreadcount")
    if not items:
        return "No notifications." if not unread_only else "No unread notifications."
    out = [f"# Notifications ({len(items)} shown, {unread} unread in total)"]
    for n in items:
        line = f"- {timeutil.fmt_short(int(n.get('timecreated') or 0))} · {'' if n.get('read') else '**unread** · '}{clean_inline(n.get('subject'))}"
        if n.get("contexturl"):
            line += f" · {n['contexturl']}"
        body = to_text(n.get("smallmessage") or n.get("fullmessage"), max_chars=300)
        if body and body != clean_inline(n.get("subject")):
            line += f"\n  {body}"
        out.append(line)
    return fmt.cap("\n".join(out))


@moodle_tool(title="Messages")
async def get_messages(
    conversation_id: Annotated[int | None, Field(description="Show this conversation; omit to list conversations")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    unread_only: Annotated[bool, Field(description="List only conversations with unread messages")] = False,
) -> str:
    """Private and group messages: list of conversations with the latest message, or one conversation."""
    userid = await app.client.userid()
    if conversation_id is None:
        res = await app.client.call("core_message_get_conversations", userid=userid, limitfrom=0, limitnum=limit)
        convs = res.get("conversations") or []
        if unread_only:
            convs = [c for c in convs if c.get("unreadcount")]
        if not convs:
            return "No conversations."
        out = [f"# Conversations ({len(convs)})"]
        for c in convs:
            members = ", ".join(clean_inline(m.get("fullname")) for m in c.get("members") or [] if m.get("id") != userid)
            name = clean_inline(c.get("name")) or members or "(self)"
            kind = {1: "private", 2: "group", 3: "self"}.get(int(c.get("type") or 0), "")
            line = f"- **{name}** · {kind} · conversation {c.get('id')}"
            if c.get("unreadcount"):
                line += f" · {c['unreadcount']} unread"
            msgs = c.get("messages") or []
            if msgs:
                last = msgs[0]
                line += f"\n  last {timeutil.fmt_short(int(last.get('timecreated') or 0))}: {to_text(last.get('text'), max_chars=200)}"
            out.append(line)
        return fmt.cap("\n".join(out))
    res = await app.client.call(
        "core_message_get_conversation_messages", currentuserid=userid, convid=conversation_id, limitfrom=0,
        limitnum=limit, newest=True,
    )
    names = {m.get("id"): clean_inline(m.get("fullname")) for m in res.get("members") or []}
    msgs = sorted(res.get("messages") or [], key=lambda m: int(m.get("timecreated") or 0))
    if not msgs:
        return f"Conversation {conversation_id} has no messages."
    out = [f"# Conversation {conversation_id} ({len(msgs)} latest messages)"]
    for m in msgs:
        who = "me" if m.get("useridfrom") == userid else names.get(m.get("useridfrom"), f"user {m.get('useridfrom')}")
        out.append(f"**{who}** · {timeutil.fmt_short(int(m.get('timecreated') or 0))}\n{to_markdown(m.get('text'))}")
    return fmt.cap("\n\n".join(out))
