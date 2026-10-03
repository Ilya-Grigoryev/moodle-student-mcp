"""Settings: environment variables override the JSON config file written by `moodle-mcp setup`."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WS_PATH = "/webservice/rest/server.php"

ENV_KEYS = {
    "url": "MOODLE_URL",
    "token": "MOODLE_TOKEN",
    "private_token": "MOODLE_PRIVATE_TOKEN",
    "lang": "MOODLE_LANG",
    "timezone": "MOODLE_TIMEZONE",
    "download_dir": "MOODLE_DOWNLOAD_DIR",
    "exclude_courses": "MOODLE_EXCLUDE_COURSES",
}


class ConfigError(Exception):
    """Configuration is missing or invalid."""


def config_path() -> Path:
    custom = os.environ.get("MOODLE_MCP_CONFIG")
    if custom:
        return Path(custom).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "moodle-mcp" / "config.json"


def state_dir() -> Path:
    custom = os.environ.get("MOODLE_MCP_STATE_DIR")
    if custom:
        return Path(custom).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
    return Path(base).expanduser() / "moodle-mcp"


def cache_dir() -> Path:
    custom = os.environ.get("MOODLE_MCP_CACHE_DIR")
    if custom:
        return Path(custom).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return Path(base).expanduser() / "moodle-mcp"


def normalize_site_url(url: str) -> str:
    """Accept the site root or the full REST endpoint and return the site root without trailing slash."""
    url = url.strip()
    if not url:
        return url
    if not re.match(r"^https?://", url):
        url = "https://" + url
    url = url.split("?", 1)[0].rstrip("/")
    for suffix in (WS_PATH, "/webservice/rest", "/login/index.php", "/my", "/my/courses.php"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url.rstrip("/")


@dataclass(frozen=True)
class Settings:
    site_url: str
    token: str
    private_token: str | None = None
    lang: str = "en"
    timezone: str = "Europe/Vienna"
    download_dir: Path = field(default_factory=lambda: Path("~/Downloads/moodle").expanduser())
    exclude_courses: tuple[str, ...] = ()
    state_dir: Path = field(default_factory=state_dir)
    cache_dir: Path = field(default_factory=cache_dir)

    @property
    def ws_url(self) -> str:
        return self.site_url + WS_PATH

    @property
    def host(self) -> str:
        """Host name without port."""
        return re.sub(r"^https?://", "", self.site_url).split("/", 1)[0].split(":", 1)[0].lower()


def read_config_file(path: Path | None = None) -> dict[str, Any]:
    path = path or config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a JSON object")
    return data


def write_config_file(values: dict[str, Any], path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_config_file(path) if path.exists() else {}
    existing.update({k: v for k, v in values.items() if v is not None})
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never readable by others, not even briefly
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(existing, indent=2, ensure_ascii=False) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path


def load_settings() -> Settings:
    values: dict[str, Any] = dict(read_config_file())
    for key, env in ENV_KEYS.items():
        env_value = os.environ.get(env, "").strip()
        if env_value:
            values[key] = env_value
    url = normalize_site_url(str(values.get("url") or ""))
    token = str(values.get("token") or "").strip()
    if not url or not token:
        raise ConfigError(
            "Moodle is not configured. Run `moodle-mcp setup` in a terminal, or set MOODLE_URL and "
            "MOODLE_TOKEN in the MCP server's environment."
        )
    kwargs: dict[str, Any] = {"site_url": url, "token": token}
    if values.get("private_token"):
        kwargs["private_token"] = str(values["private_token"]).strip()
    if values.get("lang"):
        kwargs["lang"] = str(values["lang"]).strip()
    if values.get("timezone"):
        kwargs["timezone"] = str(values["timezone"]).strip()
    if values.get("download_dir"):
        kwargs["download_dir"] = Path(str(values["download_dir"])).expanduser()
    excl = values.get("exclude_courses")
    if excl:
        items = excl if isinstance(excl, list) else str(excl).split(",")
        kwargs["exclude_courses"] = tuple(str(x).strip() for x in items if str(x).strip())
    return Settings(**kwargs)
