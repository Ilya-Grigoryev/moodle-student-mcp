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


# ---------------------------------------------------------------- get_quiz_questions

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
Q_IMAGE = f"""<div class="que multichoice"><div class="content"><div class="formulation clearfix">
<div class="qtext"><p>How many leaves?</p><img src="{SITE}/pluginfile.php/1/question/questiontext/9/1/5/tree.png" alt="decision tree" /></div>
<fieldset class="ablock"><div class="answer">
<div class="r0"><input type="radio" value="0" checked="checked" /><div data-region="answer-label"><span class="answernumber">a. </span>4</div></div>
<div class="r1"><input type="radio" value="1" /><div data-region="answer-label"><span class="answernumber">b. </span>5</div></div>
</div></fieldset></div></div></div>"""
Q_DIAGRAM = """<div class="que shortanswer"><div class="content"><div class="formulation clearfix">
<div class="qtext"><p>Name the class.</p><svg xmlns="http://www.w3.org/2000/svg" width="100" height="40" viewBox="0 0 100 40">
<rect x="1" y="1" width="98" height="38" fill="#fff" stroke="#000"/><text x="10" y="25">Order</text></svg></div>
<div class="ablock"><label>Answer:</label><input type="text" value="" /></div></div></div></div>"""


def setup_quiz(fake: FakeMoodle, attempts: list[dict[str, Any]]) -> None:
    setup_courses(fake)
    contents_for(fake, [QUIZ_MOD, RES_MOD])
    fake.on("core_course_get_course_module", {"cm": {"id": 5001, "course": 101, "modname": "quiz", "instance": 77}})
    fake.on("mod_quiz_get_quizzes_by_courses", {"quizzes": [{"id": 77, "coursemodule": 5001, "name": "Worksheet 1", "navmethod": "free"}]})
    fake.on("mod_quiz_get_user_attempts", {"attempts": attempts})


async def test_quiz_questions_of_open_attempt(fake: FakeMoodle):
    setup_quiz(fake, [])
    # A cached "no attempts" answer (e.g. from get_deadlines a moment ago) must not hide a just started attempt.
    await app.client.call("mod_quiz_get_user_attempts", quizid=77, status="all", includepreviews=False)
    fake.on("mod_quiz_get_user_attempts", {"attempts": [
        {"id": 900, "attempt": 1, "state": "inprogress", "timestart": NOW - 600, "timecheckstate": NOW + 1800}]})
    fake.on("mod_quiz_get_attempt_summary", lambda p: {"questions": [
        {"slot": 1, "page": 0, "number": 1, "questionnumber": "1", "type": "multichoice", "maxmark": 1,
         "status": "Answer saved", "html": Q_IMAGE},
        {"slot": 2, "page": 1, "number": 2, "questionnumber": "2", "type": "shortanswer", "maxmark": 2,
         "status": "Not yet answered", "html": Q_DIAGRAM},
    ], "totalunanswered": 1} if str(p["attemptid"]) == "900" else {"exception": "x", "errorcode": "invalidrecord", "message": "?"})
    fake.files["/1/question/questiontext/9/1/5/tree.png"] = PNG
    fake.file_types["/1/question/questiontext/9/1/5/tree.png"] = "image/png"

    result = await mcp.call_tool("get_quiz_questions", {"quiz": 5001})
    out = text_of(result)
    assert "# Quiz questions: Worksheet 1" in out and "Attempt 1 (id 900) · in progress, not submitted" in out
    assert "⏱ Time runs out" in out and "2 questions on 2 pages · 1 answered, 1 not yet answered" in out
    assert "## Page 1" in out and "## Page 2" in out
    assert f"[image 1: decision tree]({SITE}/pluginfile.php/1/question/questiontext/9/1/5/tree.png)" in out
    assert "- [x] a. 4" in out and "- [ ] b. 5" in out
    assert "[image 2: diagram]" in out and "Answer: [___]" in out
    images = [c for c in result.content if getattr(c, "type", "") == "image"]
    assert len(images) == 2 and all(i.mime_type == "image/png" for i in images)
    calls = [fn for fn, _ in fake.calls]
    assert not any(fn in calls for fn in ("mod_quiz_start_attempt", "mod_quiz_save_attempt", "mod_quiz_process_attempt"))

    one_page = await call("get_quiz_questions", quiz=5001, page=2, include_images=False)
    assert "Q1" not in one_page and "[diagram — labels: Order]" in one_page


async def test_quiz_questions_without_open_attempt(fake: FakeMoodle):
    setup_quiz(fake, [])
    assert "No attempt started yet" in await call("get_quiz_questions", quiz=5001)
    fake.on("mod_quiz_get_user_attempts", {"attempts": [{"id": 899, "attempt": 1, "state": "finished"}]})
    assert "get_quiz_review(quiz=5001)" in await call("get_quiz_questions", quiz=5001)
    fake.on("mod_quiz_get_attempt_data", {"exception": "x", "errorcode": "attemptalreadyclosed", "message": "closed"})
    fake.on("mod_quiz_get_attempt_summary", {"exception": "x", "errorcode": "attemptalreadyclosed", "message": "closed"})
    with pytest.raises(ToolError, match="already submitted"):
        await mcp.call_tool("get_quiz_questions", {"attempt_id": 899})


async def test_quiz_questions_pictures_from_other_hosts(fake: FakeMoodle):
    import base64

    import httpx

    setup_quiz(fake, [{"id": 901, "attempt": 1, "state": "inprogress", "timestart": NOW - 60}])
    html = f"""<div class="que multichoice"><div class="content"><div class="formulation clearfix">
<div class="qtext"><p>Which version?</p><img src="https://images.example.org/v1.png" />
<img src="data:image/png;base64,{base64.b64encode(PNG).decode()}" /><img src="https://images.example.org/gone.png" /></div>
<div class="answer"><div class="r0"><input type="radio" value="0" /><div data-region="answer-label">2.0.0</div></div></div></div></div></div>"""
    fake.on("mod_quiz_get_attempt_summary", {"questions": [{"slot": 1, "page": 0, "number": 1, "type": "multichoice",
                                                            "maxmark": 1, "html": html}]})
    assert fake.router is not None
    fake.router.get("https://images.example.org/v1.png").mock(
        return_value=httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    fake.router.get("https://images.example.org/gone.png").mock(return_value=httpx.Response(404))
    result = await mcp.call_tool("get_quiz_questions", {"quiz": 5001})
    out = text_of(result)
    assert "[image 1: picture]" in out and "[image 2: picture]" in out and "[image 3: picture]" in out
    assert "(image 3 could not be loaded from https://images.example.org/gone.png (HTTP 404))" in out
    assert len([c for c in result.content if getattr(c, "type", "") == "image"]) == 2
