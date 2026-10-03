"""fetch_page: read any Moodle page through the optional web session."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from ..app import app
from ..toolkit import UserError, moodle_tool


@moodle_tool(title="Read a Moodle web page")
async def fetch_page(
    url: Annotated[str, Field(description="Any page of your Moodle site, e.g. …/mod/grouptool/view.php?id=…")],
    max_chars: Annotated[int, Field(ge=1000, le=100_000)] = 20_000,
) -> str:
    """Fetch a Moodle page as Markdown via the optional web session — for things without a web-service
    API (group registration, appointment slots, moodleoverflow threads…). Needs MOODLE_PRIVATE_TOKEN."""
    if not app.settings.private_token:
        raise UserError(
            "The web session is not configured (no private token). Most data is available through the other tools; "
            "for web-only pages run `moodle-mcp setup` right after a fresh Moodle login — see README → Web session."
        )
    if not app.client.is_site_url(url):
        raise UserError("Only pages of your Moodle site can be fetched.")
    return await app.web.fetch_markdown(url, max_chars=max_chars)
