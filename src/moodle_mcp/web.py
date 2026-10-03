"""Optional web session for pages that have no web-service API (grouptool, organizer, moodleoverflow, …).

How it works: with the *private token* (issued together with the mobile token right after a fresh
login), Moodle's `tool_mobile_get_autologin_key` returns a one-time key; opening
`/admin/tool/mobile/autologin.php?userid=…&key=…` creates a normal browser session. Moodle only
accepts this from the Moodle app user agent and at most once every 6 minutes, so the session cookie
is cached in the state dir and reused until it expires.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .client import USER_AGENT, MoodleAuthError, MoodleClient, MoodleError
from .html import to_markdown
from .state import State

APP_USER_AGENT = "Mozilla/5.0 MoodleMobile 4.5.0 (moodle-student-mcp)"
AUTOLOGIN_INTERVAL = 6 * 60 + 5
STATE_KEY = "web_session"


class WebSessionError(MoodleError):
    pass


_UNSAFE_PATHS = ("/login/logout.php", "/course/togglecompletion.php", "/mod/forum/subscribe.php", "/user/action_redir.php")


def is_safe_page_url(url: str) -> bool:
    """GET requests that carry a sesskey (or hit known action endpoints) can change data — never follow them."""
    low = url.lower()
    return "sesskey" not in low and not any(p in low for p in _UNSAFE_PATHS) and "action=" not in low.split("#")[0]


class WebSession:
    def __init__(self, client: MoodleClient, state: State) -> None:
        self.client = client
        self.state = state
        self._http: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._ok_until = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.client.settings.private_token)

    def _ensure_http(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._http is None or self._loop is not loop:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True, headers={"User-Agent": USER_AGENT}
            )
            self._loop = loop
            self._lock = asyncio.Lock()
            saved = self.state.load(STATE_KEY, {}) or {}
            for name, value in (saved.get("cookies") or {}).items():
                self._http.cookies.set(name, value, domain=self.client.settings.host)
        return self._http

    def _save_cookies(self) -> None:
        assert self._http is not None
        cookies = {c.name: c.value for c in self._http.cookies.jar if "moodle" in c.name.lower()}
        saved = self.state.load(STATE_KEY, {}) or {}
        saved["cookies"] = cookies
        saved["saved_at"] = int(time.time())
        self.state.save(STATE_KEY, saved, private=True)

    @staticmethod
    def _is_login_page(resp: httpx.Response) -> bool:
        path = resp.url.path
        return "/login/" in path or "/auth/saml2/" in path or 'id="login"' in resp.text[:20000]

    async def _autologin(self) -> None:
        settings = self.client.settings
        if not settings.private_token:
            raise WebSessionError(
                "The web session needs MOODLE_PRIVATE_TOKEN. Run `moodle-mcp setup` right after a fresh Moodle login "
                "so the private token is captured (see README → Web session).", "noprivatetoken"
            )
        saved = self.state.load(STATE_KEY, {}) or {}
        last = int(saved.get("last_autologin") or 0)
        wait = AUTOLOGIN_INTERVAL - (int(time.time()) - last)
        if wait > 0:
            raise WebSessionError(
                f"Moodle allows a new web login only every 6 minutes — try again in {wait // 60 + 1} min.", "autologinlockout"
            )
        http = self._ensure_http()
        saved["last_autologin"] = int(time.time())
        self.state.save(STATE_KEY, saved, private=True)
        resp = await http.post(
            settings.ws_url,
            data={"wstoken": settings.token, "wsfunction": "tool_mobile_get_autologin_key", "moodlewsrestformat": "json",
                  "privatetoken": settings.private_token},
            headers={"User-Agent": APP_USER_AGENT},
        )
        data: Any = resp.json()
        if isinstance(data, dict) and "exception" in data:
            code = data.get("errorcode")
            if code == "invalidtoken":
                raise MoodleAuthError(str(data.get("message")), code, "tool_mobile_get_autologin_key")
            raise WebSessionError(f"Autologin refused: {data.get('message')} [{code}]", code, "tool_mobile_get_autologin_key")
        key, url = data.get("key"), data.get("autologinurl")
        if not key or not url:
            raise WebSessionError("Autologin key missing in Moodle's response.", None, "tool_mobile_get_autologin_key")
        userid = await self.client.userid()
        page = await http.get(url, params={"userid": userid, "key": key, "urltogo": settings.site_url + "/my/"},
                              headers={"User-Agent": APP_USER_AGENT})
        if self._is_login_page(page):
            raise WebSessionError("Autologin did not create a session (Moodle showed the login page).", "autologinfailed")
        self._save_cookies()
        self._ok_until = time.monotonic() + 600

    async def ensure(self) -> None:
        http = self._ensure_http()
        assert self._lock is not None
        async with self._lock:
            if time.monotonic() < self._ok_until:
                return
            if http.cookies:
                resp = await http.get(self.client.settings.site_url + "/my/")
                if resp.status_code < 400 and not self._is_login_page(resp):
                    self._ok_until = time.monotonic() + 600
                    return
            await self._autologin()

    async def fetch_html(self, url: str) -> tuple[str, str]:
        """Returns (final_url, html) of a Moodle page fetched with the web session."""
        if not self.client.is_site_url(url):
            raise WebSessionError("Only pages of your Moodle site can be fetched.", "badhost")
        if not is_safe_page_url(url):
            raise WebSessionError("Refusing to open an action URL (sesskey/logout) — this server only reads.", "unsafeurl")
        url = self.client.to_site_url(url)  # always the site's scheme: cookies never go over http
        await self.ensure()
        http = self._ensure_http()
        resp = await http.get(url)
        if self._is_login_page(resp):
            self._ok_until = 0
            await self.ensure()
            resp = await http.get(url)
        if resp.status_code >= 400:
            raise WebSessionError(f"HTTP {resp.status_code} for {url}", str(resp.status_code))
        self._save_cookies()
        return str(resp.url), resp.text

    async def fetch_markdown(self, url: str, max_chars: int = 20_000) -> str:
        final_url, html = await self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")
        main = soup.select_one("#region-main") or soup.select_one("[role=main]") or soup.body or soup
        for sel in (".activity-header .activity-information", "nav", "footer", ".modal", "form .mform .fdescription",
                    "[data-region='drawer']", ".secondary-navigation", "#page-footer"):
            for el in main.select(sel):
                el.decompose()
        # Form controls carry useful state (checked boxes, selected options) — make it visible.
        for inp in main.find_all("input", attrs={"type": re.compile("checkbox|radio")}):
            inp.replace_with("[x] " if inp.has_attr("checked") else "[ ] ")
        text = to_markdown(str(main), max_chars=max_chars)
        title = soup.title.get_text(" ").strip() if soup.title else ""
        return f"{title}\n{final_url}\n\n{text}".strip()

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
