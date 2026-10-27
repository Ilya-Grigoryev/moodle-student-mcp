"""Optional browser login using Moodle's official mobile-app launch flow."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import math
import re
import secrets
import sys
from contextlib import suppress
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup


class BrowserLoginError(Exception):
    """A safe, actionable error that never includes browser URLs or credentials."""


def split_launch_payload(encoded: str) -> tuple[str, str, str | None] | None:
    """Decode Moodle's base64 launch payload `binding:::token[:::private token]` (either base64 alphabet)."""
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode()
    except (binascii.Error, UnicodeError, ValueError):
        return None
    parts = decoded.split(":::")
    if len(parts) not in (2, 3) or not re.fullmatch(r"[a-zA-Z0-9]{32}", parts[1]):
        return None
    return parts[0], parts[1], (parts[2] or None) if len(parts) == 3 else None


def decode_callback(callback: str, site_url: str, passport: str) -> tuple[str, str | None]:
    """Validate Moodle's site/passport binding before accepting its credentials.

    Moodle uses MD5 here as a protocol identifier, not as a password hash:
    public/admin/tool/mobile/launch.php builds md5(wwwroot + passport).
    """
    match = re.fullmatch(r"[a-zA-Z][a-zA-Z0-9+.-]*://token=([A-Za-z0-9+/=_-]{1,16384})", callback)
    payload = split_launch_payload(match.group(1)) if match else None
    if payload is None:
        raise BrowserLoginError("Moodle returned an invalid mobile-app callback.")
    binding, token, private = payload
    expected = hashlib.md5((site_url + passport).encode(), usedforsecurity=False).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{32}", binding) or not secrets.compare_digest(binding, expected):
        raise BrowserLoginError("Moodle's callback does not match this login attempt. Please retry.")
    return token, private


async def browser_login(site_url: str, timeout: float = 300, *, headless: bool = False) -> tuple[str, str | None]:
    """Open an isolated browser; the user completes login and MFA themselves.

    Only intercept the requested site's launch endpoint. Never persist the browser
    profile, enable tracing, or include Playwright errors (which can contain tokens).
    """
    try:
        from playwright.async_api import Error, async_playwright
    except ImportError:
        raise BrowserLoginError(
            'Browser login needs the optional dependency: install "moodle-student-mcp[browser]" '
            f"(see README → Set up your token), then run `{sys.executable} -m playwright install chromium`. "
            "Or use `moodle-mcp setup` for manual setup."
        ) from None

    if not math.isfinite(timeout) or timeout <= 0:
        raise BrowserLoginError("The browser login timeout must be positive.")
    passport = secrets.token_hex(24)
    endpoint = site_url + "/admin/tool/mobile/launch.php"
    launch_url = endpoint + "?" + urlencode({
        "service": "moodle_mobile_app", "passport": passport, "urlscheme": "moodlemobile",
    })
    result: asyncio.Future[tuple[str, str | None]] = asyncio.get_running_loop().create_future()

    async def capture(session, event):
        request_id = event["requestId"]
        parsed = urlsplit(event["request"]["url"])
        query = parse_qs(parsed.query)
        if (parsed._replace(query="", fragment="").geturl() != endpoint
                or query.get("passport") != [passport]
                or query.get("service") != ["moodle_mobile_app"]):
            await session.send("Fetch.continueRequest", {"requestId": request_id})
            return
        try:
            headers = {h["name"].lower(): h["value"] for h in event.get("responseHeaders", [])}
            location = headers.get("location", "")
            callback = location if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://token=", location) else None
            # Moodle can also render a launch link instead of sending a redirect.
            if not callback and "text/html" in headers.get("content-type", "") and event.get("responseStatusCode") == 200:
                response = await session.send("Fetch.getResponseBody", {"requestId": request_id})
                body = response["body"]
                if response.get("base64Encoded"):
                    body = base64.b64decode(body).decode("utf-8", "replace")
                link = BeautifulSoup(body, "html.parser").find("a", id="launchapp")
                if link:
                    callback = str(link.get("href", ""))
            if callback:
                credentials = decode_callback(callback, site_url, passport)
                body = base64.b64encode(b"<h1>Moodle connected</h1>You can return to the terminal.").decode()
                await session.send("Fetch.fulfillRequest", {
                    "requestId": request_id, "responseCode": 200,
                    "responseHeaders": [{"name": "Content-Type", "value": "text/html; charset=utf-8"}],
                    "body": body,
                })
                if not result.done():
                    result.set_result(credentials)
            else:
                await session.send("Fetch.continueRequest", {"requestId": request_id})
        except (Error, BrowserLoginError) as exc:
            if not result.done():
                result.set_exception(exc if isinstance(exc, BrowserLoginError) else BrowserLoginError(
                    "Could not complete the Moodle launch request. Check your connection and retry."
                ))
            # Suppress any token-bearing redirect, including an invalid callback.
            try:
                await session.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "Aborted"})
            except Error:
                pass  # The browser may already have been closed by the user.

    async def connect():
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.launch(headless=headless)
            except Error:
                # sys.executable: the interpreter this tool runs in (e.g. uv's tool environment).
                raise BrowserLoginError(
                    f"Could not start Chromium. Run `{sys.executable} -m playwright install chromium` and retry "
                    "from a desktop session, or use `moodle-mcp setup` for manual setup."
                ) from None
            try:
                context = await browser.new_context(service_workers="block")

                def closed():
                    if not result.done():
                        result.set_exception(BrowserLoginError("Browser login was cancelled. Run setup again to retry."))

                browser.on("disconnected", closed)
                context.on("close", closed)

                async def watch_page(opened):
                    def page_closed():
                        if not context.pages:
                            closed()

                    opened.on("close", page_closed)
                    try:
                        session = await context.new_cdp_session(opened)
                        session.on("Fetch.requestPaused", lambda event: capture(session, event))
                        # Response-stage interception sees every hop of SSO redirects;
                        # Playwright routes only intercept the first request in a chain.
                        await session.send("Fetch.enable", {"patterns": [
                            {"urlPattern": endpoint + "?*", "requestStage": "Response"},
                        ]})
                    except Error:
                        if not result.done():
                            result.set_exception(BrowserLoginError("Could not prepare the login window. Please retry."))

                page = await context.new_page()
                await watch_page(page)
                context.on("page", watch_page)
                try:
                    await page.goto(launch_url, wait_until="domcontentloaded")
                except Error:
                    if not result.done():
                        raise BrowserLoginError("Could not open Moodle. Check your connection and retry.") from None
                return await result
            finally:
                if not result.done():
                    result.cancel()
                with suppress(Exception):
                    await browser.close()

    try:
        return await asyncio.wait_for(connect(), timeout=timeout)
    except asyncio.TimeoutError:
        raise BrowserLoginError("Browser login timed out. Run setup again and finish login in the browser.") from None
    except Error:
        raise BrowserLoginError("Browser login failed. Retry setup, or use `moodle-mcp setup` for manual setup.") from None
