"""Server assembly: tools and prompts are registered on import."""

from __future__ import annotations

from . import prompts, tools  # noqa: F401  (registers tools and prompts)
from .toolkit import mcp

__all__ = ["mcp"]
