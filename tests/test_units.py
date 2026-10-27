"""Unit tests for parsing/formatting helpers (no network)."""

from __future__ import annotations

import base64
from datetime import datetime

import pytest

from moodle_mcp import timeutil
from moodle_mcp.cli import decode_launch_token
from moodle_mcp.client import MoodleClient, MoodleError, flatten_params
from moodle_mcp.config import Settings, normalize_site_url
from moodle_mcp.courses import (
    acronyms,
    clean_title,
    normalize_semester,
    semester_for_date,
    semester_key,
    semester_of,
)
from moodle_mcp.extract import parse_ranges
from moodle_mcp.html import clean_inline, resolve_mlang, set_language, to_markdown, truncate


def test_normalize_site_url():
    assert normalize_site_url("moodle.univie.ac.at") == "https://moodle.univie.ac.at"
    assert normalize_site_url("https://m.x.org/webservice/rest/server.php") == "https://m.x.org"
    assert normalize_site_url("https://m.x.org/moodle/") == "https://m.x.org/moodle"


def test_flatten_params():
    flat = flatten_params({"courseids": [1, 2], "options": {"a": True, "b": None}, "events": {"ids": [3]}, "x": "y"})
    assert flat == {"courseids[0]": "1", "courseids[1]": "2", "options[a]": "1", "events[ids][0]": "3", "x": "y"}


def test_semester_detection():
    assert semester_of("WS2026-051050-4", "2026W 051050 Software Engineering 2", None) == "2026W"
    assert semester_of("SS2025-051031-3", "", None) == "2025S"
    assert semester_of("x", "2024W 051131-4 Einführende Statistik", None) == "2024W"
    assert semester_for_date(datetime(2026, 10, 3)) == "2026W"
    assert semester_for_date(datetime(2027, 2, 10)) == "2026W"
    assert semester_for_date(datetime(2026, 5, 1)) == "2026S"
    assert semester_key("2026W") > semester_key("2026S") > semester_key("2025W")
    assert normalize_semester("ws2025") == "2025W"
    assert normalize_semester("2026s") == "2026S"
    assert normalize_semester("SoSe2024") == "2024S"


def test_titles_and_acronyms():
    assert clean_title("2026W 051050 Software Engineering 2") == "Software Engineering 2"
    assert clean_title("2025S 051021-1 Betriebssysteme (Prüfungstermin 15.10.2025)") == "Betriebssysteme (Prüfungstermin 15.10.2025)"
    assert clean_title("2026S Mensch-Computer-Interaktion ") == "Mensch-Computer-Interaktion"
    assert "dlfnlp" in acronyms("Deep Learning for Natural Language Processing")
    assert "dlnlp" in acronyms("Deep Learning for Natural Language Processing")
    assert "se2" in acronyms("Software Engineering 2")


def test_mlang_and_inline():
    set_language("en")
    assert resolve_mlang("{mlang de}Ankündigungen{mlang}{mlang en}Announcements{mlang}") == "Announcements"
    assert resolve_mlang("{mlang de}Nur Deutsch{mlang}") == "Nur Deutsch"
    assert clean_inline("Information &amp; <b>Systems</b>") == "Information & Systems"


def test_markdown_conversion():
    md = to_markdown('<p>Hello <a href="https://x.org/a">link</a></p><img src="https://m.x.org/webservice/pluginfile.php/1/mod_label/intro/pic.png?token=abc">')
    assert "[link](https://x.org/a)" in md
    assert "![pic.png](https://m.x.org/pluginfile.php/1/mod_label/intro/pic.png)" in md
    assert "token" not in md
    assert to_markdown('<span class="accesshide">hidden</span>visible') == "visible"
    assert truncate("a" * 100, 50).startswith("a" * 50)


def test_parse_when():
    timeutil.set_timezone("Europe/Vienna")
    d = timeutil.parse_when("2026-10-05")
    assert (d.year, d.month, d.day, d.hour) == (2026, 10, 5, 0)
    d = timeutil.parse_when("05.10.2026 14:00")
    assert (d.day, d.hour, d.minute) == (5, 14, 0)
    now = timeutil.now()
    assert abs((now - timeutil.parse_when("3d", past=True)).total_seconds() - 3 * 86400) < 5
    assert abs((timeutil.parse_when("+2d") - now).total_seconds() - 2 * 86400) < 5
    assert timeutil.parse_when("monday").weekday() == 0
    with pytest.raises(ValueError):
        timeutil.parse_when("someday")


def test_relative():
    now = timeutil.now_ts()
    assert timeutil.relative(now + 3 * 86400).startswith("in 3 days")
    assert timeutil.relative(now - 2 * 86400) == "2 days ago"
    assert timeutil.fmt_dt(0) == "—"


def test_parse_ranges():
    assert parse_ranges("1-3,5", 10) == [1, 2, 3, 5]
    assert parse_ranges("8-", 10) == [8, 9, 10]
    assert parse_ranges(None, 3) == [1, 2, 3]
    with pytest.raises(ValueError):
        parse_ranges("a-b", 3)


def test_decode_launch_token():
    token, private = "a" * 32, "B" * 64
    blob = base64.b64encode(f"sig:::{token}:::{private}".encode()).decode()
    assert decode_launch_token(f"moodlemobile://token={blob}") == (token, private)
    blob2 = base64.b64encode(f"sig:::{token}".encode()).decode()
    assert decode_launch_token(blob2) == (token, None)
    odd = "?~" * 32  # its base64 needs '+' and '/', i.e. '-' and '_' in the URL-safe alphabet
    blob3 = base64.urlsafe_b64encode(f"sig:::{token}:::{odd}".encode()).decode().rstrip("=")
    assert set(blob3) & set("-_")
    assert decode_launch_token(blob3) == (token, odd)
    assert decode_launch_token("0123456789abcdef0123456789abcdef") == ("0123456789abcdef0123456789abcdef", None)
    with pytest.raises(ValueError):
        decode_launch_token("nonsense")


def test_file_urls():
    client = MoodleClient(Settings(site_url="https://m.x.org", token="T" * 32))
    url = client.authed_file_url("https://m.x.org/pluginfile.php/5/mod_resource/content/1/a b.pdf?forcedownload=1")
    assert "/webservice/pluginfile.php/5/" in url and "token=" + "T" * 32 in url
    assert MoodleClient.public_url(url) == "https://m.x.org/pluginfile.php/5/mod_resource/content/1/a%20b.pdf"
    with pytest.raises(MoodleError, match="non-Moodle host"):
        client.authed_file_url("https://evil.example.com/pluginfile.php/1/x")
