"""Output formatting helpers (compact Markdown)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from . import timeutil

OUTPUT_LIMIT = 40_000


def size(n: int | float | None) -> str:
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def when(ts: int | float | None, *, rel_days: int = 45) -> str:
    """Absolute date; adds a relative hint when the date is within ±rel_days of now."""
    if not ts or int(ts) <= 0:
        return "—"
    near = abs(int(ts) - timeutil.now_ts()) <= rel_days * 86400
    return timeutil.fmt_dt(ts, rel=near)


def cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip() or "–"


def table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for row in rows:
        lines.append("| " + " | ".join(cell(v) for v in row) + " |")
    return "\n".join(lines)


def cap(text: str, limit: int = OUTPUT_LIMIT, hint: str = "narrow the request") -> str:
    """Hard cap on tool output size."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    pos = cut.rfind("\n")
    if pos > limit * 0.8:
        cut = cut[:pos]
    return cut + f"\n\n… output truncated ({len(text) - len(cut)} more chars) — {hint}."


def plural(n: int, word: str, plural_form: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural_form or word + 's')}"
