"""Browser callback validation, setup safety, and opt-in real Chromium tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from moodle_mcp.browser_login import BrowserLoginError, browser_login, decode_callback
from moodle_mcp.cli import _save_setup, main
from moodle_mcp.config import read_config_file, write_config_file

SITE = "https://moodle.example/moodle"
PASSPORT = "test-login-attempt"
TOKEN = "a" * 32
PRIVATE = "b" * 64


def callback(site=SITE, passport=PASSPORT, token=TOKEN, private=PRIVATE):
    binding = hashlib.md5((site + passport).encode(), usedforsecurity=False).hexdigest()
    payload = f"{binding}:::{token}" + (f":::{private}" if private else "")
    return "moodlemobile://token=" + base64.b64encode(payload.encode()).decode()


def test_callback_with_and_without_private_token():
    assert decode_callback(callback(), SITE, PASSPORT) == (TOKEN, PRIVATE)
    assert decode_callback(callback(private=None), SITE, PASSPORT) == (TOKEN, None)
    # Moodle may force an institution-specific app scheme.
    assert decode_callback(callback().replace("moodlemobile:", "institution-app:"), SITE, PASSPORT) == (TOKEN, PRIVATE)


@pytest.mark.parametrize("value", [
    callback(passport="stale"), callback(site="https://other.example"), callback(token="short"),
    "moodlemobile://token=garbage", "https://evil.example/?token=secret", TOKEN,
    "moodlemobile://token=" + base64.b64encode(("é:::" + TOKEN).encode()).decode(),
])
def test_callback_rejects_unbound_or_invalid_tokens_without_echo(value):
    with pytest.raises(BrowserLoginError) as error:
        decode_callback(value, SITE, PASSPORT)
    assert value not in str(error.value)
    assert TOKEN not in str(error.value)


def test_browser_failure_preserves_config(monkeypatch, tmp_path, capsys):
    path = tmp_path / "config.json"
    monkeypatch.setenv("MOODLE_MCP_CONFIG", str(path))
    write_config_file({"url": SITE, "token": "old-token"})

    async def fail(*args, **kwargs):
        raise BrowserLoginError("Browser login was cancelled.")

    monkeypatch.setattr("moodle_mcp.browser_login.browser_login", fail)
    with pytest.raises(SystemExit) as exit_info:
        main(["setup", "--browser", "--url", SITE])
    assert exit_info.value.code == 1
    assert read_config_file()["token"] == "old-token"
    assert "cancelled" in capsys.readouterr().out


def test_browser_success_uses_shared_verification(monkeypatch):
    import moodle_mcp.cli as cli

    async def login(site, timeout):
        assert site == SITE
        assert timeout == 42
        return TOKEN, PRIVATE

    saved = []
    monkeypatch.setattr("moodle_mcp.browser_login.browser_login", login)
    monkeypatch.setattr(cli, "_save_setup", lambda *args: saved.append(args) or 0)
    with pytest.raises(SystemExit) as exit_info:
        main(["setup", "--browser", "--url", SITE, "--timeout", "42"])
    assert exit_info.value.code == 0
    assert saved == [(SITE, TOKEN, PRIVATE)]


def test_rejected_token_preserves_config_and_redacts_errors(monkeypatch, tmp_path, capsys):
    from moodle_mcp.client import MoodleClient

    path = tmp_path / "config.json"
    monkeypatch.setenv("MOODLE_MCP_CONFIG", str(path))
    write_config_file({"url": SITE, "token": "old-token"})

    async def reject(self):
        raise RuntimeError("secret-url?token=" + TOKEN)

    monkeypatch.setattr(MoodleClient, "site_info", reject)
    assert _save_setup(SITE, TOKEN, PRIVATE) == 1
    assert read_config_file()["token"] == "old-token"
    assert TOKEN not in capsys.readouterr().out


def test_verified_login_clears_stale_private_token(monkeypatch, tmp_path):
    from moodle_mcp.client import MoodleClient

    path = tmp_path / "config.json"
    monkeypatch.setenv("MOODLE_MCP_CONFIG", str(path))
    write_config_file({"url": SITE, "token": "old", "private_token": "stale"})

    async def verify(self):
        return {"fullname": "Student", "username": "student"}

    monkeypatch.setattr(MoodleClient, "site_info", verify)
    assert _save_setup(SITE, TOKEN, None) == 0
    assert read_config_file()["token"] == TOKEN
    assert read_config_file()["private_token"] == ""
    assert path.stat().st_mode & 0o777 == 0o600


def test_relogin_with_same_token_keeps_private_token(monkeypatch, tmp_path):
    from moodle_mcp.client import MoodleClient

    monkeypatch.setenv("MOODLE_MCP_CONFIG", str(tmp_path / "config.json"))
    write_config_file({"url": SITE, "token": TOKEN, "private_token": PRIVATE})

    async def verify(self):
        return {"fullname": "Student", "username": "student"}

    monkeypatch.setattr(MoodleClient, "site_info", verify)
    # Without a fresh login Moodle omits the private token; the saved one still belongs to this token.
    assert _save_setup(SITE, TOKEN, None) == 0
    assert read_config_file()["private_token"] == PRIVATE


def test_moodle_rejection_reason_is_shown(monkeypatch, tmp_path, capsys):
    from moodle_mcp.client import MoodleClient, MoodleError

    monkeypatch.setenv("MOODLE_MCP_CONFIG", str(tmp_path / "config.json"))
    write_config_file({"url": SITE, "token": "old-token"})

    async def reject(self):
        raise MoodleError("Invalid token - token not found", "invalidtoken", "core_webservice_get_site_info")

    monkeypatch.setattr(MoodleClient, "site_info", reject)
    assert _save_setup(SITE, TOKEN, None) == 1
    assert "Invalid token - token not found" in capsys.readouterr().out
    assert read_config_file()["token"] == "old-token"


@pytest.fixture
def fake_login_site():
    class Handler(BaseHTTPRequestHandler):
        mode = "redirect"

        def log_message(self, *args):
            pass  # Do not log callbacks, even the fake ones.

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/sso":
                self.send_response(302)
                self.send_header("Set-Cookie", "signedin=yes; Path=/")
                self.send_header("Location", query["return"][0])
                self.end_headers()
                return
            if not self.headers.get("Cookie"):
                self.send_response(302)
                self.send_header("Location", "/sso?return=" + quote(self.path, safe=""))
                self.end_headers()
                return
            site = f"http://127.0.0.1:{self.server.server_port}/moodle"
            passport = "wrong" if self.mode == "mismatch" else query["passport"][0]
            target = callback(site, passport)
            if self.mode in ("redirect", "mismatch"):
                self.send_response(303)
                self.send_header("Location", target)
                self.end_headers()
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                body = f'<a id="launchapp" href="{target}">Launch app</a>' if self.mode == "html" else "Mobile service unavailable"
                self.wfile.write(body.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/moodle", Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.skipif(not os.environ.get("MOODLE_TEST_BROWSER"), reason="Set MOODLE_TEST_BROWSER=1 with Playwright/Chromium installed")
@pytest.mark.parametrize("mode", ["redirect", "html", "mismatch", "timeout"])
async def test_real_browser_capture(fake_login_site, mode):
    pytest.importorskip("playwright.async_api")
    site, handler = fake_login_site
    handler.mode = mode
    if mode in ("mismatch", "timeout"):
        with pytest.raises(BrowserLoginError, match=r"match this login|timed out"):
            await browser_login(site, timeout=2 if mode == "timeout" else 10, headless=True)
    else:
        assert await browser_login(site, timeout=10, headless=True) == (TOKEN, PRIVATE)


@pytest.mark.skipif(not os.environ.get("MOODLE_TEST_BROWSER"), reason="Requires Playwright/Chromium")
async def test_closing_last_window_cancels_login(monkeypatch, fake_login_site):
    api = pytest.importorskip("playwright.async_api")
    original = api.BrowserContext.new_page
    site, handler = fake_login_site
    handler.mode = "timeout"
    tasks = []

    async def create_page(context):
        page = await original(context)

        async def close():
            await asyncio.sleep(0.5)
            await page.close()

        tasks.append(asyncio.create_task(close()))
        return page

    monkeypatch.setattr(api.BrowserContext, "new_page", create_page)
    try:
        with pytest.raises(BrowserLoginError, match="cancelled"):
            await browser_login(site, timeout=10, headless=True)
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)
