"""System-browser callback and cleanup tests; real macOS handler tests are opt-in."""

from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest

from moodle_mcp import system_browser as native
from moodle_mcp.browser_login import BrowserLoginError

from .test_browser_login import PRIVATE, SITE, TOKEN, callback


@pytest.fixture
def native_setup(monkeypatch, tmp_path):
    monkeypatch.setattr(native.sys, "platform", "darwin")
    monkeypatch.setenv("MOODLE_MCP_CONFIG", str(tmp_path / "config.json"))
    artifacts = []

    def register(folder, scheme):
        app = folder / "Moodle MCP Login.app"
        app.mkdir()
        target = folder / "callback.txt"
        target.touch(mode=0o600)
        artifacts.append((app, target))
        return app, target

    monkeypatch.setattr(native, "_register_handler", register)
    monkeypatch.setattr(native.subprocess, "run", lambda *args, **kwargs: None)
    return artifacts


async def test_system_browser_existing_profile_and_cleanup(monkeypatch, native_setup):
    def open_browser(url):
        query = parse_qs(urlsplit(url).query)
        target = native_setup[0][1]
        target.write_text(callback(SITE, query["passport"][0]).replace("moodlemobile:", query["urlscheme"][0] + ":"))
        return True

    monkeypatch.setattr(native.webbrowser, "open", open_browser)
    assert await native.system_browser_login(SITE) == (TOKEN, PRIVATE)
    assert not native_setup[0][0].parent.exists()


@pytest.mark.parametrize("mode", ["timeout", "open-failed"])
async def test_system_browser_failure_cleanup(monkeypatch, native_setup, mode):
    monkeypatch.setattr(native.webbrowser, "open", lambda url: mode != "open-failed")
    with pytest.raises(BrowserLoginError, match=r"timed out|Could not open"):
        await native.system_browser_login(SITE, timeout=0.03)
    assert not native_setup[0][0].parent.exists()


async def test_system_browser_unsupported_platform(monkeypatch):
    monkeypatch.setattr(native.sys, "platform", "win32")
    with pytest.raises(BrowserLoginError, match="requires macOS"):
        await native.system_browser_login(SITE)


@pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("MOODLE_TEST_SYSTEM_BROWSER"),
                    reason="Set MOODLE_TEST_SYSTEM_BROWSER=1 on macOS to test the real URL handler")
async def test_real_macos_handler(monkeypatch, tmp_path):
    monkeypatch.setenv("MOODLE_MCP_CONFIG", str(tmp_path / "config.json"))

    def fake_moodle(url):
        query = parse_qs(urlsplit(url).query)
        target = callback(SITE, query["passport"][0]).replace("moodlemobile:", query["urlscheme"][0] + ":")
        subprocess.run(["/usr/bin/open", target], capture_output=True, check=True)
        return True

    monkeypatch.setattr(native.webbrowser, "open", fake_moodle)
    assert await native.system_browser_login(SITE, timeout=15) == (TOKEN, PRIVATE)
    assert list(tmp_path.iterdir()) == []
