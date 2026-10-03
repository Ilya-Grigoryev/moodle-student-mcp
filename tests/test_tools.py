"""Tool tests against a fake Moodle (respx), called through the real MCP server object."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from moodle_mcp import timeutil
from moodle_mcp.app import app
from moodle_mcp.courses import AmbiguousCourse, semester_for_date
from moodle_mcp.server import mcp

from .conftest import DAY, NOW, SITE, FakeMoodle, course, text_of

CUR = semester_for_date(timeutil.now())
CUR_YEAR, CUR_KIND = int(CUR[:4]), CUR[4]
PREV = f"{CUR_YEAR}S" if CUR_KIND == "W" else f"{CUR_YEAR - 1}W"


def _short(sem: str, lv: str) -> str:
    return f"{'WS' if sem.endswith('W') else 'SS'}{sem[:4]}-{lv}-1"


def setup_courses(fake: FakeMoodle) -> None:
    courses = [
        course(101, _short(CUR, "051050"), f"{CUR} 051050 Software Engineering 2", NOW - 10 * DAY),
        course(102, _short(CUR, "052316"), f"{CUR} 052316-1 Deep Learning for Natural Language Processing", NOW - 10 * DAY),
        course(201, _short(PREV, "051040"), f"{PREV} Software Engineering 1", NOW - 200 * DAY),
        course(202, _short(PREV, "051034"), f"{PREV} 051034-1 Netzwerktechnologien", NOW - 200 * DAY),
        course(203, _short(PREV, "051035"), f"{PREV} 051035 Netzwerktechnologien", NOW - 200 * DAY),
    ]
    fake.on("core_enrol_get_users_courses", courses)
    fake.on("gradereport_overview_get_course_grades", {"grades": [{"courseid": 201, "grade": "1", "rawgrade": "90"}]})
    fake.on("core_group_get_course_user_groups", {"groups": [{"courseid": 101, "name": "G3"}]})


def contents_for(fake: FakeMoodle, mods: list[dict[str, Any]]) -> None:
    fake.on("core_course_get_contents", lambda p: [
        {"id": 1, "section": 0, "name": "General", "summary": "", "modules": mods[:1]},
        {"id": 2, "section": 1, "name": "Week 1", "summary": "<p>Read the slides</p>", "modules": mods[1:]},
    ] if str(p.get("courseid")) == "101" else [])


QUIZ_MOD = {"id": 5001, "modname": "quiz", "name": "Worksheet 1", "instance": 77, "uservisible": True, "visible": 1,
            "dates": [{"label": "Closes:", "timestamp": NOW + 2 * DAY}], "url": f"{SITE}/mod/quiz/view.php?id=5001"}
RES_MOD = {"id": 5002, "modname": "resource", "name": "Slides", "instance": 88, "uservisible": True, "visible": 1,
           "contents": [{"type": "file", "filename": "notes.txt", "filepath": "/", "filesize": 11, "timemodified": NOW - DAY,
                         "mimetype": "text/plain", "fileurl": f"{SITE}/webservice/pluginfile.php/9/mod_resource/content/1/notes.txt"}]}


async def call(name: str, **args: Any) -> str:
    result = await mcp.call_tool(name, args)
    return text_of(result)


async def test_course_resolution(fake: FakeMoodle):
    setup_courses(fake)
    idx = app.courses
    assert (await idx.resolve("SE2")).id == 101
    assert (await idx.resolve("051050")).id == 101
    assert (await idx.resolve("dlfnlp")).id == 102
    assert (await idx.resolve("Software Engineering 1")).id == 201
    assert (await idx.resolve(f"SE1 {PREV}")).id == 201
    with pytest.raises(AmbiguousCourse):
        await idx.resolve("Netzwerktechnologien")
    assert (await idx.resolve("102")).id == 102


async def test_list_courses(fake: FakeMoodle):
    setup_courses(fake)
    out = await call("list_courses")
    assert f"## {CUR} (2)" in out and "Software Engineering 2" in out and "G3" in out
    assert "Software Engineering 1" not in out
    out_all = await call("list_courses", semester="all")
    assert f"## {PREV}" in out_all and "| 1 |" in out_all


async def test_get_course(fake: FakeMoodle):
    setup_courses(fake)
    contents_for(fake, [QUIZ_MOD, RES_MOD])
    fake.on("core_course_get_courses_by_field", {"courses": [{"id": 101, "contacts": [{"fullname": "Prof X"}], "summary": ""}]})
    out = await call("get_course", course="SE2")
    assert "# Software Engineering 2" in out and "Teachers: Prof X" in out
    assert "quiz **Worksheet 1** · cmid 5001" in out and "notes.txt (11 B)" in out
    assert "Read the slides" in out


async def test_deadlines_flag_unsubmitted_attempt(fake: FakeMoodle):
    setup_courses(fake)
    contents_for(fake, [QUIZ_MOD, RES_MOD])
    fake.on("core_calendar_get_action_events_by_timesort", {"events": [{
        "id": 1, "name": "Worksheet 1 closes", "activityname": "Worksheet 1", "modulename": "quiz", "eventtype": "close",
        "instance": 5001, "timesort": NOW + 2 * DAY, "course": {"id": 101}, "url": f"{SITE}/mod/quiz/view.php?id=5001",
        "action": {"name": "Attempt quiz now", "actionable": True}}]})
    fake.on("mod_quiz_get_user_attempts", lambda p: {"attempts": [{"id": 9, "attempt": 1, "state": "inprogress", "timestart": NOW - 3600}]}
            if int(p["quizid"]) == 77 else {"attempts": []})
    out = await call("get_deadlines", days=7)
    assert "quiz **Worksheet 1** closes" in out
    assert "IN PROGRESS — NOT SUBMITTED" in out
    assert "1 quiz attempt(s) started but not submitted" in out


async def test_batch_continues_after_error(fake: FakeMoodle):
    setup_courses(fake)
    fake.on("mod_quiz_get_user_attempts", lambda p: {"exception": "x", "errorcode": "invalidrecord", "message": "bad"}
            if int(p["quizid"]) == 2 else {"attempts": [], "q": p["quizid"]})
    calls = [("mod_quiz_get_user_attempts", {"quizid": i, "status": "all"}) for i in (1, 2, 3)]
    res = await app.client.batch(calls)
    assert res[0]["q"] == 1 and res[2]["q"] == 3
    assert getattr(res[1], "errorcode", None) == "invalidrecord"


async def test_batch_fallback_without_endpoint(fake: FakeMoodle):
    setup_courses(fake)
    fake.batch_supported = False
    fake.on("mod_quiz_get_user_attempts", lambda p: {"attempts": [], "q": p["quizid"]})
    res = await app.client.batch([("mod_quiz_get_user_attempts", {"quizid": i}) for i in (1, 2)])
    assert [r["q"] for r in res] == ["1", "2"]


async def test_read_and_download_files(fake: FakeMoodle, tmp_path: Path):
    setup_courses(fake)
    contents_for(fake, [QUIZ_MOD, RES_MOD])
    fake.on("core_course_get_course_module", {"cm": {"id": 5002, "course": 101, "modname": "resource", "instance": 88}})
    fake.on("mod_assign_get_assignments", {"courses": []})
    fake.files["/9/mod_resource/content/1/notes.txt"] = b"hello notes"
    out = await call("read_file", cmid=5002)
    assert "# notes.txt" in out and "hello notes" in out

    dest = tmp_path / "dl"
    out = await call("download_files", course="SE2", dest=str(dest))
    assert "Downloaded 1 new" in out and (dest / "notes.txt").read_bytes() == b"hello notes"
    out = await call("download_files", course="SE2", dest=str(dest))
    assert "0 new + 0 updated" in out and "1 unchanged" in out
    RES_MOD["contents"][0]["timemodified"] = NOW  # teacher re-uploads
    app.client.cache.clear()
    try:
        out = await call("download_files", course="SE2", dest=str(dest))
        assert "updated: notes.txt" in out
    finally:
        RES_MOD["contents"][0]["timemodified"] = NOW - DAY


async def test_updates_detects_new_activity(fake: FakeMoodle):
    setup_courses(fake)
    mods = [QUIZ_MOD, RES_MOD]
    contents_for(fake, mods)
    fake.on("core_course_check_updates", {"instances": [], "warnings": []})
    fake.on("gradereport_user_get_grade_items", {"usergrades": [{"gradeitems": []}]})
    fake.on("mod_forum_get_forums_by_courses", [])
    fake.on("message_popup_get_popup_notifications", {"notifications": [], "unreadcount": 0})
    first = await call("get_updates", course="SE2")
    assert "first check" in first
    assert "Nothing new" in await call("get_updates", course="SE2")
    new_mod = {"id": 5003, "modname": "assign", "name": "Exercise 2", "instance": 99, "uservisible": True, "visible": 1}
    contents_for(fake, [*mods, new_mod])
    app.client.cache.clear()
    out = await call("get_updates", course="SE2")
    assert "NEW assign «Exercise 2» · cmid 5003" in out


async def test_invalid_token_message(fake: FakeMoodle):
    fake.on("core_webservice_get_site_info", {"exception": "moodle_exception", "errorcode": "invalidtoken", "message": "Invalid token"})
    with pytest.raises(ToolError, match="moodle-mcp setup"):
        await mcp.call_tool("status", {})


async def test_course_typos_and_unknown(fake: FakeMoodle):
    setup_courses(fake)
    assert (await app.courses.resolve("Software Enginering 2")).id == 101  # fuzzy
    with pytest.raises(ToolError, match="No enrolled course matches"):
        await mcp.call_tool("get_course", {"course": "Quantum Basket Weaving"})


async def test_updates_last_check_is_per_course(fake: FakeMoodle):
    setup_courses(fake)
    contents_for(fake, [QUIZ_MOD, RES_MOD])
    fake.on("core_course_check_updates", {"instances": [], "warnings": []})
    fake.on("gradereport_user_get_grade_items", {"usergrades": [{"gradeitems": []}]})
    fake.on("mod_forum_get_forums_by_courses", [])
    fake.on("message_popup_get_popup_notifications", {"notifications": [], "unreadcount": 0})
    await call("get_updates", course="SE2")  # only SE2 is now "seen"
    state = app.state.load("updates")
    assert set(state["last_runs"]) == {"101"} and "notifications_last_run" not in state
    out = await call("get_updates")  # DLfNLP still uses the 7-day default
    assert "7 days for courses checked for the first time" in out
    state = app.state.load("updates")
    assert set(state["last_runs"]) == {"101", "102"} and "notifications_last_run" in state


async def test_semester_only_reference(fake: FakeMoodle):
    setup_courses(fake)
    with pytest.raises(ToolError, match="only a semester"):
        await mcp.call_tool("get_course", {"course": PREV})


async def test_download_never_overwrites_local_changes(fake: FakeMoodle, tmp_path: Path):
    setup_courses(fake)
    contents_for(fake, [QUIZ_MOD, RES_MOD])
    fake.on("mod_assign_get_assignments", {"courses": []})
    fake.files["/9/mod_resource/content/1/notes.txt"] = b"hello notes"
    dest = tmp_path / "dl"
    await call("download_files", course="SE2", dest=str(dest))
    (dest / "notes.txt").write_bytes(b"my own annotated notes")  # user edits the file
    out = await call("download_files", course="SE2", dest=str(dest))
    assert "Kept 1 locally modified" in out
    RES_MOD["contents"][0]["timemodified"] = NOW  # and the teacher uploads a new version
    app.client.cache.clear()
    try:
        out = await call("download_files", course="SE2", dest=str(dest))
    finally:
        RES_MOD["contents"][0]["timemodified"] = NOW - DAY
    assert (dest / "notes.txt").read_bytes() == b"my own annotated notes"
    side = [p for p in dest.iterdir() if p.name.startswith("notes (moodle ")]
    assert len(side) == 1 and side[0].read_bytes() == b"hello notes"
    assert "saved as notes (moodle" in out


async def test_download_preexisting_different_file_is_kept(fake: FakeMoodle, tmp_path: Path):
    setup_courses(fake)
    contents_for(fake, [QUIZ_MOD, RES_MOD])
    fake.on("mod_assign_get_assignments", {"courses": []})
    fake.files["/9/mod_resource/content/1/notes.txt"] = b"hello notes"
    dest = tmp_path / "dl"
    dest.mkdir()
    (dest / "notes.txt").write_bytes(b"something else entirely")
    await call("download_files", course="SE2", dest=str(dest))
    assert (dest / "notes.txt").read_bytes() == b"something else entirely"
    assert any(p.name.startswith("notes (moodle ") for p in dest.iterdir())
