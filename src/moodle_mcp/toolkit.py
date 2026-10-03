"""The MCPServer instance and the decorator every tool uses (annotations + error mapping)."""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from . import __version__
from .client import MoodleError
from .config import ConfigError
from .courses import AmbiguousCourse, CourseNotFound

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Read-only access to the user's Moodle (courses, deadlines, grades, files, forums, quizzes, messages).

- `course` arguments accept a course id, LV number (051050), acronym (SE2, DLfNLP), shortname or part
  of the title. Add a semester to disambiguate older courses: "SE1 2025W".
- Semesters are codes like 2026W / 2026S; "current" is the default scope of cross-course tools.
- Outputs contain ids for follow-up calls: `cmid` (activity) → get_activity / read_file / get_quiz_review,
  `discussion` → get_discussion, `attempt` → get_quiz_review.
- Typical entry points: get_deadlines (what is due), get_updates (what changed), list_courses,
  get_course (structure + materials), get_grades, get_announcements, search.
- read_file returns the text of PDFs/slides/docs; download_files syncs course files to a local folder.
- Times are local (timezone shown by `status`). Nothing in Moodle is ever modified.
"""

mcp = MCPServer(
    name="moodle",
    title="Moodle",
    description="Read access to Moodle for students",
    instructions=INSTRUCTIONS,
    version=__version__,
    website_url="https://github.com/Ilya-Grigoryev/moodle-student-mcp",
)

F = TypeVar("F", bound=Callable[..., Awaitable[str]])

EXPECTED_ERRORS = (ConfigError, MoodleError, CourseNotFound, AmbiguousCourse, ValueError, LookupError)


class UserError(Exception):
    """An anticipated problem the model should read (bad argument, nothing found, …)."""


def moodle_tool(*, title: str, read_only: bool = True, idempotent: bool = True) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> str:
            try:
                return await fn(*args, **kwargs)
            except ToolError:
                raise
            except (UserError, *EXPECTED_ERRORS) as exc:
                raise ToolError(str(exc)) from exc
            except Exception as exc:
                log.exception("Tool %s crashed", fn.__name__)
                raise ToolError(f"Internal error ({type(exc).__name__}): {exc}") from exc

        mcp.add_tool(
            wrapper,
            name=fn.__name__,
            title=title,
            annotations=ToolAnnotations(
                title=title,
                read_only_hint=read_only,
                destructive_hint=False,
                idempotent_hint=idempotent,
                open_world_hint=True,
            ),
            structured_output=False,
        )
        return fn

    return decorator
