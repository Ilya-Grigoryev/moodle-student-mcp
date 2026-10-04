"""Shared fixtures: an in-memory fake Moodle behind respx, and a configured app."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx
import pytest
import respx

from moodle_mcp.app import app
from moodle_mcp.config import Settings

SITE = "https://moodle.example.edu"
WS = SITE + "/webservice/rest/server.php"
NOW = int(time.time())
DAY = 86400


def unflatten(form: dict[str, str]) -> dict[str, Any]:
    """courseids[0]=1&options[a]=2 → {'courseids': ['1'], 'options': {'a': '2'}} (lists as dicts with int keys)."""
    root: dict[str, Any] = {}
    for key, value in form.items():
        parts = key.replace("]", "").split("[")
        node = root
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value

    def fix(node: Any) -> Any:
        if isinstance(node, dict):
            if node and all(k.isdigit() for k in node):
                return [fix(node[k]) for k in sorted(node, key=int)]
            return {k: fix(v) for k, v in node.items()}
        return node

    return fix(root)


class FakeMoodle:
    """Minimal Moodle WS emulation: handlers keyed by wsfunction, receiving decoded params."""

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.files: dict[str, bytes] = {}
        self.file_types: dict[str, str] = {}
        self.batch_supported = True
        self.router: respx.MockRouter | None = None
        self.on(
            "core_webservice_get_site_info",
            lambda p: {"userid": 7, "username": "student", "fullname": "Test Student", "sitename": "Example Moodle",
                       "release": "5.0", "functions": [{"name": "x"}] * 3, "downloadfiles": 1},
        )

    def on(self, function: str, handler: Callable[[dict[str, Any]], Any] | Any) -> None:
        self.handlers[function] = handler if callable(handler) else (lambda p, _v=handler: _v)

    def dispatch(self, function: str, params: dict[str, Any]) -> Any:
        self.calls.append((function, params))
        if function == "tool_mobile_call_external_functions":
            if not self.batch_supported:
                return {"exception": "x", "errorcode": "accessexception", "message": "not allowed"}
            responses = []
            for req in params.get("requests", []):
                fn = req["function"]
                args = json.loads(req.get("arguments") or "{}")
                result = self.dispatch(fn, args)
                if isinstance(result, dict) and "exception" in result:
                    responses.append({"error": True, "exception": json.dumps(result)})
                    break  # Moodle stops at the first error
                responses.append({"error": False, "data": json.dumps(result)})
            return {"responses": responses}
        handler = self.handlers.get(function)
        if handler is None:
            return {"exception": "moodle_exception", "errorcode": "invalidrecord", "message": f"no handler for {function}"}
        return handler(params)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/webservice/pluginfile.php"):
            key = request.url.path.replace("/webservice/pluginfile.php", "")
            if key in self.files:
                ctype = self.file_types.get(key, "application/octet-stream")
                return httpx.Response(200, content=self.files[key], headers={"content-type": ctype})
            return httpx.Response(404, json={"error": "not found", "errorcode": "filenotfound"})
        form = dict(parse_qsl(request.content.decode()))
        function = form.pop("wsfunction")
        for k in ("wstoken", "moodlewsrestformat", "moodlewssettingfilter", "moodlewssettingfileurl", "moodlewssettinglang"):
            form.pop(k, None)
        return httpx.Response(200, json=self.dispatch(function, unflatten(form)))


def course(cid: int, short: str, full: str, start: int, **extra: Any) -> dict[str, Any]:
    return {"id": cid, "shortname": short, "fullname": full, "displayname": full, "startdate": start, "enddate": 0,
            "lastaccess": NOW - DAY, "isfavourite": False, "hidden": False, "visible": 1, **extra}


@pytest.fixture
def fake(tmp_path: Path) -> Iterator[FakeMoodle]:
    moodle = FakeMoodle()
    settings = Settings(site_url=SITE, token="t" * 32, state_dir=tmp_path / "state", cache_dir=tmp_path / "cache",
                        download_dir=tmp_path / "downloads", timezone="Europe/Vienna")
    app.configure(settings)
    with respx.mock(assert_all_called=False) as router:
        router.route(host="moodle.example.edu").mock(side_effect=moodle)
        moodle.router = router  # for routes to other hosts
        yield moodle


def text_of(result: Any) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)
