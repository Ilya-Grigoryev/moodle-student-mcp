"""Server assembly: tools and prompts are registered on import."""

from __future__ import annotations

from . import tools  # noqa: F401  (registers tools)
from .toolkit import mcp

__all__ = ["mcp"]
