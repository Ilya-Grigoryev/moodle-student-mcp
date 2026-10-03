"""Call every tool against your real Moodle and report errors and timings.

Usage: python scripts/live_smoke.py [--verbose]
Read-only except `download_files`, which writes into a temporary folder that is deleted afterwards.
State (get_updates snapshot) goes to a temporary directory so your real "last check" is untouched.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from typing import Any

TMP = tempfile.mkdtemp(prefix="moodle-mcp-smoke-")
os.environ["MOODLE_MCP_STATE_DIR"] = os.path.join(TMP, "state")

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from moodle_mcp import data  # noqa: E402
from moodle_mcp.app import app  # noqa: E402
from moodle_mcp.server import mcp  # noqa: E402


async def run(name: str, args: dict[str, Any], verbose: bool) -> tuple[bool, float, str]:
    t = time.perf_counter()
    try:
        res = await mcp.call_tool(name, args)
        ok = not getattr(res, "is_error", False)
        text = "\n".join(getattr(c, "text", f"<{c.type}>") for c in res.content)
    except ToolError as exc:
        ok, text = False, str(exc)
    dt = time.perf_counter() - t
    print(f"{'OK ' if ok else 'ERR'} {dt:5.1f}s  {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})  [{len(text)} chars]")
    if verbose or not ok:
        print("    " + text[:600].replace("\n", "\n    "))
    return ok, dt, text


async def main() -> None:
    verbose = "--verbose" in sys.argv
    courses = await app.courses.select("current")
    if not courses:
        courses = await app.courses.select("all")
    c = courses[0]
    mods = await data.mods(c.id)
    by_type: dict[str, int] = {}
    for m in mods:
        by_type.setdefault(m.modname, m.cmid)
    file_mod = next((m for m in mods if m.files and not all(f.get("mimetype", "").startswith("video") for f in m.files)), None)
    plan: list[tuple[str, dict[str, Any]]] = [
        ("status", {}),
        ("list_courses", {}),
        ("list_courses", {"semester": "all"}),
        ("get_course", {"course": c.id}),
        ("get_course", {"course": c.id, "compact": True}),
        ("search", {"query": "exam", "scope": "current"}),
        ("get_deadlines", {"days": 30}),
        ("get_calendar", {"days": 14}),
        ("get_grades", {}),
        ("get_grades", {"course": c.id}),
        ("get_activity_status", {"course": c.id}),
        ("get_announcements", {"days": 60}),
        ("get_forum", {"course": c.id}),
        ("get_notifications", {"limit": 5}),
        ("get_messages", {"limit": 5}),
        ("list_files", {"course": c.id}),
        ("get_updates", {"since": "14d", "remember": False}),
    ]
    for cmid in list(by_type.values())[:12]:
        plan.append(("get_activity", {"cmid": cmid}))
    if file_mod:
        name = next(f["filename"] for f in file_mod.files if not f.get("mimetype", "").startswith("video"))
        plan.append(("read_file", {"cmid": file_mod.cmid, "filename": name, "max_chars": 2000}))
    plan.append(("download_files", {"course": c.id, "dest": os.path.join(TMP, "dl"), "extensions": ["pdf"]}))
    if "forum" in by_type:
        plan.append(("get_forum", {"forum": by_type["forum"]}))
    results = []
    for name, args in plan:
        results.append(await run(name, args, verbose))
    errors = sum(1 for ok, _, _ in results if not ok)
    print(f"\n{len(results)} calls · {errors} errors · total {sum(dt for _, dt, _ in results):.1f}s · course used: {c.label}")
    await app.client.aclose()
    import shutil

    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    asyncio.run(main())
