"""Load and validate Moodle configuration from environment.

Expects MOODLE_URL and MOODLE_TOKEN to be set by the MCP client (e.g. Cursor
mcp.json env block); no .env file is used.
"""

import os
from typing import NamedTuple


class MoodleConfig(NamedTuple):
    """Moodle API URL and web service token."""

    url: str
    token: str


def get_moodle_url() -> str:
    """Return the Moodle REST API base URL."""
    return _get_config().url


def get_moodle_token() -> str:
    """Return the Moodle web service token."""
    return _get_config().token


def get_moodle_config() -> MoodleConfig:
    """Load and return validated Moodle config (url, token)."""
    return _get_config()


def _get_config() -> MoodleConfig:
    url = os.environ.get("MOODLE_URL", "").strip()
    token = os.environ.get("MOODLE_TOKEN", "").strip()
    if not url or not token:
        raise ValueError(
            "MOODLE_URL and MOODLE_TOKEN must be set in the environment "
            "(e.g. in Cursor: Settings → MCP → your server → env)"
        )
    return MoodleConfig(url=url, token=token)
