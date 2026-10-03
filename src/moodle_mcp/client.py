"""Async client for the Moodle web-service REST API (and token-authenticated file downloads)."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from . import __version__
from .cache import TTLCache
from .config import Settings

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

USER_AGENT = f"moodle-student-mcp/{__version__}"
DEFAULT_TTL = 60.0
FUNCTION_TTL: dict[str, float] = {
    "core_webservice_get_site_info": 3600,
    "tool_mobile_get_config": 3600,
    "tool_mobile_get_plugins_supporting_mobile": 3600,
    "core_enrol_get_users_courses": 600,
    "core_course_get_courses_by_field": 600,
    "core_group_get_course_user_groups": 600,
    "gradereport_overview_get_course_grades": 120,
}
AUTH_ERROR_CODES = {"invalidtoken", "servicerequireslogin", "usernotfullysetup", "invalidlogin"}
BATCH_FUNCTION = "tool_mobile_call_external_functions"
BATCH_CHUNK = 25
MAX_PARALLEL = 6


class MoodleError(Exception):
    """An error reported by Moodle (or while talking to it)."""

    def __init__(self, message: str, errorcode: str | None = None, function: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errorcode = errorcode
        self.function = function

    def __str__(self) -> str:
        where = f" in {self.function}" if self.function else ""
        code = f" [{self.errorcode}]" if self.errorcode else ""
        return f"Moodle error{where}{code}: {self.message}"


class MoodleAuthError(MoodleError):
    def __str__(self) -> str:
        return (
            f"Moodle rejected the token ({self.errorcode}: {self.message}). The token is invalid or expired — "
            "run `moodle-mcp setup` to get a new one."
        )


def flatten_params(params: dict[str, Any]) -> dict[str, str]:
    """Encode nested dicts/lists the way Moodle expects: a[0][b]=1."""
    out: dict[str, str] = {}

    def add(key: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, bool):
            out[key] = "1" if value else "0"
        elif isinstance(value, dict):
            for k, v in value.items():
                add(f"{key}[{k}]", v)
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                add(f"{key}[{i}]", v)
        else:
            out[key] = str(value)

    for k, v in params.items():
        add(k, v)
    return out


def _raise_for_payload(data: Any, function: str) -> None:
    if isinstance(data, dict) and "exception" in data and ("errorcode" in data or "message" in data):
        code = data.get("errorcode")
        msg = str(data.get("message") or data.get("exception"))
        if code in AUTH_ERROR_CODES:
            raise MoodleAuthError(msg, code, function)
        raise MoodleError(msg, code, function)


class MoodleClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache = TTLCache()
        self._http: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sem: asyncio.Semaphore | None = None
        self._batch_supported: bool | None = None

    # ------------------------------------------------------------------ plumbing
    def _ensure_http(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._http is None or self._loop is not loop:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=15.0),
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                limits=httpx.Limits(max_connections=MAX_PARALLEL + 2, max_keepalive_connections=MAX_PARALLEL),
            )
            self._loop = loop
            self._sem = asyncio.Semaphore(MAX_PARALLEL)
            self.cache.clear()  # cached futures/values are bound to the old loop's lifetime
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _base_payload(self, function: str) -> dict[str, str]:
        return {
            "wstoken": self.settings.token,
            "wsfunction": function,
            "moodlewsrestformat": "json",
            "moodlewssettingfilter": "true",
            "moodlewssettingfileurl": "true",
            "moodlewssettinglang": self.settings.lang,
        }

    async def _post(self, function: str, flat: dict[str, str]) -> Any:
        http = self._ensure_http()
        assert self._sem is not None
        payload = {**self._base_payload(function), **flat}
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with self._sem:
                    resp = await http.post(self.settings.ws_url, data=payload)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
                if resp.status_code == 404:
                    raise MoodleError(
                        f"Web service endpoint not found at {self.settings.ws_url} (HTTP 404). Check MOODLE_URL.",
                        "notfound",
                        function,
                    )
                try:
                    data = resp.json()
                except ValueError as exc:
                    snippet = re.sub(r"\s+", " ", resp.text[:200])
                    raise MoodleError(f"Unexpected non-JSON response (HTTP {resp.status_code}): {snippet}", None, function) from exc
                _raise_for_payload(data, function)
                return data
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.6 * (attempt + 1) + random.random() * 0.3)
                    continue
        raise MoodleError(f"Cannot reach Moodle ({type(last_exc).__name__}: {last_exc})", "network", function)

    # ------------------------------------------------------------------ API
    async def call(self, function: str, *, ttl: float | None = None, **params: Any) -> Any:
        """Call one WS function. Results are cached for a short time (see FUNCTION_TTL)."""
        self._ensure_http()
        flat = flatten_params(params)
        ttl = FUNCTION_TTL.get(function, DEFAULT_TTL) if ttl is None else ttl
        key = (function, tuple(sorted(flat.items())))
        return await self.cache.get_or_fetch(key, ttl, lambda: self._post(function, flat))

    async def try_call(self, function: str, *, ttl: float | None = None, **params: Any) -> Any:
        """Like call() but returns a MoodleError instance instead of raising (auth errors still raise)."""
        try:
            return await self.call(function, ttl=ttl, **params)
        except MoodleAuthError:
            raise
        except MoodleError as exc:
            return exc

    async def batch(
        self, calls: list[tuple[str, dict[str, Any]]], ttl: float | None = None
    ) -> list[Any]:
        """Run several WS calls, using Moodle's batch endpoint when available.

        Returns results in order; a failed call yields a MoodleError instance (not raised).
        """
        self._ensure_http()
        results: list[Any] = [None] * len(calls)
        pending: list[int] = []
        keys: list[Any] = []
        for i, (fn, params) in enumerate(calls):
            flat = flatten_params(params)
            key = (fn, tuple(sorted(flat.items())))
            keys.append(key)
            hit, value = self.cache.get(key)
            if hit:
                results[i] = value
            else:
                pending.append(i)
        if not pending:
            return results

        if self._batch_supported is not False and len(pending) > 1:
            # Moodle runs a batch sequentially, so split it into a few chunks that run in parallel.
            size = max(2, min(BATCH_CHUNK, -(-len(pending) // MAX_PARALLEL)))
            chunks = [pending[i : i + size] for i in range(0, len(pending), size)]
            try:
                await asyncio.gather(*(self._run_batch_chunk(calls, chunk, results) for chunk in chunks))
                self._batch_supported = True
            except MoodleAuthError:
                raise
            except MoodleError as exc:
                if exc.function == BATCH_FUNCTION and self._batch_supported is None:
                    log.info("Batch endpoint unavailable (%s); falling back to parallel calls", exc)
                    self._batch_supported = False
                else:
                    raise
        if self._batch_supported is False or len(pending) == 1:
            outs = await asyncio.gather(
                *(self.try_call(calls[i][0], ttl=0, **calls[i][1]) for i in pending)
            )
            for i, out in zip(pending, outs):
                results[i] = out

        for i in pending:
            fn = calls[i][0]
            if not isinstance(results[i], MoodleError):
                self.cache.set(keys[i], results[i], FUNCTION_TTL.get(fn, DEFAULT_TTL) if ttl is None else ttl)
        return results

    async def _run_batch_chunk(self, calls: list[tuple[str, dict[str, Any]]], chunk: list[int], results: list[Any]) -> None:
        # Moodle stops processing a batch at the first failing call, so resend the rest after a failure.
        remaining = list(chunk)
        while remaining:
            requests = [
                {
                    "function": calls[i][0],
                    "arguments": json.dumps(calls[i][1]),
                    "settingfilter": True,
                    "settingfileurl": True,
                    "settinglang": self.settings.lang,
                }
                for i in remaining
            ]
            data = await self._post(BATCH_FUNCTION, flatten_params({"requests": requests}))
            responses = data.get("responses", []) if isinstance(data, dict) else []
            if not responses or len(responses) > len(remaining):
                raise MoodleError("Unexpected batch response", None, BATCH_FUNCTION)
            self._store_batch_responses(calls, remaining[: len(responses)], responses, results)
            if len(responses) < len(remaining) and not responses[-1].get("error"):
                raise MoodleError("Batch response ended early without an error", None, BATCH_FUNCTION)
            remaining = remaining[len(responses):]

    def _store_batch_responses(
        self, calls: list[tuple[str, dict[str, Any]]], indexes: list[int], responses: list[dict[str, Any]], results: list[Any]
    ) -> None:
        for i, resp in zip(indexes, responses):
            fn = calls[i][0]
            if resp.get("error"):
                try:
                    exc_data = json.loads(resp.get("exception") or "{}")
                except ValueError:
                    exc_data = {}
                code = exc_data.get("errorcode")
                msg = str(exc_data.get("message") or "unknown error")
                if code in AUTH_ERROR_CODES:
                    raise MoodleAuthError(msg, code, fn)
                results[i] = MoodleError(msg, code, fn)
            else:
                raw = resp.get("data")
                try:
                    results[i] = json.loads(raw) if raw not in (None, "") else None
                except ValueError:
                    results[i] = MoodleError("Invalid JSON in batch response", None, fn)

    # ------------------------------------------------------------------ identity
    async def site_info(self) -> dict[str, Any]:
        return await self.call("core_webservice_get_site_info")

    async def userid(self) -> int:
        return int((await self.site_info())["userid"])

    # ------------------------------------------------------------------ files
    def is_site_url(self, url: str) -> bool:
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            return False
        return host.lower() == self.settings.host

    def authed_file_url(self, url: str) -> str:
        """Turn a Moodle file URL into a token-authenticated download URL."""
        if not self.is_site_url(url):
            raise MoodleError(f"Refusing to download from a non-Moodle host: {url}", "badhost")
        parts = urlsplit(url)
        path = parts.path
        if "/webservice/pluginfile.php" not in path:
            path = path.replace("/pluginfile.php", "/webservice/pluginfile.php", 1)
        if "/tokenpluginfile.php" in path:
            # /tokenpluginfile.php/<filetoken>/<rest> -> /webservice/pluginfile.php/<rest>
            path = re.sub(r"/tokenpluginfile\.php/[^/]+/", "/webservice/pluginfile.php/", path)
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != "token"]
        query.append(("token", self.settings.token))
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))

    @staticmethod
    def public_url(url: str) -> str:
        """Browser-friendly URL without any token."""
        parts = urlsplit(url)
        path = parts.path.replace("/webservice/pluginfile.php", "/pluginfile.php")
        query = [(k, v) for k, v in parse_qsl(parts.query) if k not in ("token", "forcedownload")]
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), ""))

    async def download(self, url: str, dest: Path, max_bytes: int | None = None) -> int:
        """Stream a Moodle file to `dest` (written atomically). Returns the number of bytes."""
        http = self._ensure_http()
        assert self._sem is not None
        target = self.authed_file_url(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        written = 0
        try:
            async with self._sem, http.stream("GET", target, timeout=httpx.Timeout(300.0, connect=15.0)) as resp:
                ctype = resp.headers.get("content-type", "")
                if resp.status_code >= 400 or ctype.startswith("application/json"):
                    body = (await resp.aread())[:2000]
                    try:
                        data = json.loads(body)
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and ("errorcode" in data or "error" in data):
                        code = data.get("errorcode")
                        msg = str(data.get("error") or data.get("message"))
                        if code in AUTH_ERROR_CODES:
                            raise MoodleAuthError(msg, code, "download")
                        raise MoodleError(msg, code, "download")
                    if resp.status_code >= 400:
                        raise MoodleError(f"Download failed with HTTP {resp.status_code}", str(resp.status_code), "download")
                    # A genuine JSON file: fall through and save what we already read.
                    with open(tmp, "wb") as fh:
                        fh.write(body)
                        written = len(body)
                        async for chunk in resp.aiter_bytes():
                            fh.write(chunk)
                            written += len(chunk)
                else:
                    with open(tmp, "wb") as fh:
                        async for chunk in resp.aiter_bytes():
                            written += len(chunk)
                            if max_bytes is not None and written > max_bytes:
                                raise MoodleError(
                                    f"File is larger than the limit of {max_bytes // (1024 * 1024)} MB", "toolarge", "download"
                                )
                            fh.write(chunk)
            tmp.replace(dest)
        except httpx.HTTPError as exc:
            raise MoodleError(f"Download failed: {type(exc).__name__}: {exc}", "network", "download") from exc
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return written

    async def get_text(self, url: str) -> str:
        """Fetch a small text resource (e.g. a book chapter) with token authentication."""
        http = self._ensure_http()
        assert self._sem is not None
        async with self._sem:
            resp = await http.get(self.authed_file_url(url))
        if resp.status_code >= 400:
            raise MoodleError(f"HTTP {resp.status_code} while fetching {self.public_url(url)}", str(resp.status_code), "download")
        return resp.text
