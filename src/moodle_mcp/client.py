"""Moodle REST API HTTP client and error handling."""

from typing import Any

import requests

from .config import get_moodle_url, get_moodle_token


def call_moodle_api(wsfunction: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
    """
    Call a Moodle web service function via REST.

    Appends wstoken and moodlewsrestformat=json to every request.
    Raises if the response indicates a Moodle error (exception or errorcode in JSON).
    """
    url = get_moodle_url()
    token = get_moodle_token()
    payload: dict[str, Any] = {
        "wstoken": token,
        "moodlewsrestformat": "json",
        "wsfunction": wsfunction,
        **kwargs,
    }
    response = requests.post(url, data=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    if isinstance(data, dict) and ("exception" in data or "errorcode" in data):
        msg = data.get("message", data.get("exception", str(data)))
        raise RuntimeError(f"Moodle API error: {msg}")

    return data
