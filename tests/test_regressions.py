"""Regression tests for issues found in code review."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from moodle_mcp.app import app
from moodle_mcp.cache import TTLCache
from moodle_mcp.client import MoodleClient, MoodleError
from moodle_mcp.config import Settings, write_config_file
from moodle_mcp.extract import _decode, parse_ranges
from moodle_mcp.quizreview import is_wrong
from moodle_mcp.server import mcp
from moodle_mcp.web import is_safe_page_url

from .conftest import SITE, FakeMoodle, text_of
from .test_tools import QUIZ_MOD, RES_MOD, call, contents_for, setup_courses


def test_token_never_sent_over_http_or_other_ports():
    client = MoodleClient(Settings(site_url="https://m.x.org", token="T" * 32))
    url = client.authed_file_url("http://m.x.org/pluginfile.php/1/a.pdf")
    assert url.startswith("https://m.x.org/webservice/pluginfile.php/1/a.pdf?")
    assert client.is_site_url("https://m.x.org:443/x")
    assert not client.is_site_url("https://m.x.org:8443/x")
    assert not client.is_site_url("ftp://m.x.org/x")
    with pytest.raises(MoodleError):
        client.authed_file_url("https://m.x.org:8443/pluginfile.php/1/a.pdf")
    ported = MoodleClient(Settings(site_url="https://m.x.org:8443", token="T" * 32))
    assert ported.authed_file_url("https://m.x.org:8443/pluginfile.php/1/a").startswith("https://m.x.org:8443/")


async def test_json_file_downloaded_intact(fake: FakeMoodle, tmp_path: Path):
    key = "/1/mod_resource/content/1/data.json"
    fake.files[key] = b"[1, 2, 3]"
    fake.file_types[key] = "application/json"
    dest = tmp_path / "data.json"
    await app.client.download(f"{SITE}/pluginfile.php{key}", dest)
    assert dest.read_bytes() == b"[1, 2, 3]"
    # Moodle's own error payload is recognised as an error, not saved as a file
    fake.files["/1/x/err.json"] = b'{"error": "File not found", "errorcode": "filenotfound"}'
    fake.file_types["/1/x/err.json"] = "application/json"
    with pytest.raises(MoodleError, match="File not found"):
        await app.client.download(f"{SITE}/pluginfile.php/1/x/err.json", tmp_path / "err.json")
    assert not (tmp_path / "err.json").exists()


async def test_cancelled_caller_does_not_cancel_others():
    cache = TTLCache()
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return 42

    a = asyncio.create_task(cache.get_or_fetch("k", 60, fetch))
    await asyncio.sleep(0)
    b = asyncio.create_task(cache.get_or_fetch("k", 60, fetch))
    await asyncio.sleep(0)
    a.cancel()
    assert await b == 42 and calls == 1


async def test_parallel_reads_of_one_file(fake: FakeMoodle):
    setup_courses(fake)
    contents_for(fake, [QUIZ_MOD, RES_MOD])
    fake.on("core_course_get_course_module", {"cm": {"id": 5002, "course": 101, "modname": "resource", "instance": 88}})
    fake.files["/9/mod_resource/content/1/notes.txt"] = b"hello notes"
    outs = await asyncio.gather(*(call("read_file", cmid=5002) for _ in range(4)))
    assert all("hello notes" in o for o in outs)


async def test_updates_survive_inaccessible_course(fake: FakeMoodle):
    setup_courses(fake)
    fake.on("core_course_get_contents", lambda p: {"exception": "x", "errorcode": "requireloginerror", "message": "no access"}
            if str(p.get("courseid")) == "102" else [])
    fake.on("gradereport_user_get_grade_items", {"usergrades": [{"gradeitems": []}]})
    fake.on("mod_forum_get_forums_by_courses", [])
    fake.on("message_popup_get_popup_notifications", {"notifications": [], "unreadcount": 0})
    out = await call("get_updates")
    assert "could not check this course: no access" in out


async def test_parallel_updates_keep_each_others_state(fake: FakeMoodle):
    setup_courses(fake)
    contents_for(fake, [QUIZ_MOD])
    fake.on("core_course_check_updates", {"instances": []})
    fake.on("gradereport_user_get_grade_items", {"usergrades": [{"gradeitems": []}]})
    fake.on("mod_forum_get_forums_by_courses", [])
    fake.on("message_popup_get_popup_notifications", {"notifications": [], "unreadcount": 0})
    await asyncio.gather(call("get_updates", course="SE2"), call("get_updates", course="DLfNLP"))
    assert set(app.state.load("updates")["last_runs"]) == {"101", "102"}


def test_legacy_encodings():
    assert _decode("Prüfung am Montag\n".encode("cp1252")) == "Prüfung am Montag\n"
    assert _decode("Prüfung".encode("utf-16")) == "Prüfung"
    assert _decode("Prüfung".encode()) == "Prüfung"


def test_is_wrong_with_hidden_marks():
    assert not is_wrong({"state": "gradedright", "maxmark": 2})
    assert not is_wrong({"state": "", "maxmark": 2})
    assert is_wrong({"state": "gradedwrong", "maxmark": 2})


def test_page_ranges_are_clamped():
    assert parse_ranges("1-1000000000", 3) == [1, 2, 3]
    assert parse_ranges("0-2", 5) == [1, 2]


def test_action_urls_are_refused():
    assert is_safe_page_url("https://m/mod/grouptool/view.php?id=1")
    assert not is_safe_page_url("https://m/login/logout.php?sesskey=abc")
    assert not is_safe_page_url("https://m/mod/choice/view.php?id=3&action=delchoice")


async def test_fetch_page_refuses_action_urls(fake: FakeMoodle):
    app.configure(Settings(site_url=SITE, token="t" * 32, private_token="p" * 32, state_dir=app.settings.state_dir,
                           cache_dir=app.settings.cache_dir))
    with pytest.raises(ToolError, match="performs an action"):
        await mcp.call_tool("fetch_page", {"url": f"{SITE}/login/logout.php?sesskey=x"})


def test_config_file_is_private_from_the_start(tmp_path: Path):
    path = write_config_file({"url": "https://m.x.org", "token": "secret"}, tmp_path / "c.json")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


async def test_unexpected_errors_are_reported_as_bugs(fake: FakeMoodle):
    fake.on("core_enrol_get_users_courses", [{"no_id": True}])  # malformed response → KeyError inside
    with pytest.raises(ToolError, match="bug in moodle-mcp"):
        await mcp.call_tool("list_courses", {})
    _ = text_of
