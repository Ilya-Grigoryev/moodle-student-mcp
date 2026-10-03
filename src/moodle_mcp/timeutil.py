"""Timezone-aware formatting of Moodle timestamps and parsing of user-supplied dates."""

from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TZ: tzinfo = timezone.utc
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_WEEKDAY_NAMES = {
    "mon": 0, "monday": 0, "mo": 0, "montag": 0, "пн": 0, "понедельник": 0,
    "tue": 1, "tuesday": 1, "di": 1, "dienstag": 1, "вт": 1, "вторник": 1,
    "wed": 2, "wednesday": 2, "mi": 2, "mittwoch": 2, "ср": 2, "среда": 2,
    "thu": 3, "thursday": 3, "do": 3, "donnerstag": 3, "чт": 3, "четверг": 3,
    "fri": 4, "friday": 4, "fr": 4, "freitag": 4, "пт": 4, "пятница": 4,
    "sat": 5, "saturday": 5, "sa": 5, "samstag": 5, "сб": 5, "суббота": 5,
    "sun": 6, "sunday": 6, "so": 6, "sonntag": 6, "вс": 6, "воскресенье": 6,
}


def set_timezone(name: str) -> None:
    global _TZ
    try:
        _TZ = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        _TZ = timezone.utc


def tz() -> tzinfo:
    return _TZ


def now() -> datetime:
    return datetime.now(_TZ)


def now_ts() -> int:
    return int(time.time())


def to_dt(ts: int | float) -> datetime:
    return datetime.fromtimestamp(int(ts), _TZ)


def relative(ts: int | float, ref: datetime | None = None) -> str:
    """Human relative time: 'in 3 h', 'in 2 days', '5 days ago', 'today 14:00' style hints."""
    ref = ref or now()
    target = to_dt(ts)
    delta = (target - ref).total_seconds()
    future = delta >= 0
    secs = abs(delta)
    if secs < 90:
        return "now"
    if secs < 3600:
        n = round(secs / 60)
        return f"in {n} min" if future else f"{n} min ago"
    if secs < 36 * 3600 and target.date() == ref.date():
        n = round(secs / 3600)
        return f"in {n} h" if future else f"{n} h ago"
    days = (target.date() - ref.date()).days
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    if abs(days) < 60:
        return f"in {days} days" if days > 0 else f"{-days} days ago"
    months = round(abs(days) / 30.4)
    return f"in {months} months" if days > 0 else f"{months} months ago"


def fmt_dt(ts: int | float | None, *, rel: bool = False, time_of_day: bool = True) -> str:
    """'Mon 05.10.2026 14:00' (+ ' (in 2 days)' when rel=True). 00:00/23:59 keep their time."""
    if not ts or int(ts) <= 0:
        return "—"
    dt = to_dt(ts)
    text = f"{WEEKDAYS[dt.weekday()]} {dt:%d.%m.%Y}"
    if time_of_day:
        text += f" {dt:%H:%M}"
    if rel:
        text += f" ({relative(ts)})"
    return text


def fmt_date(ts: int | float | None) -> str:
    return fmt_dt(ts, time_of_day=False)


def fmt_short(ts: int | float | None) -> str:
    """Compact '05.10. 14:00' (year only if not the current one)."""
    if not ts or int(ts) <= 0:
        return "—"
    dt = to_dt(ts)
    year = "" if dt.year == now().year else str(dt.year)
    return f"{dt:%d.%m.}{year} {dt:%H:%M}"


def day_bounds(d: date) -> tuple[int, int]:
    start = datetime(d.year, d.month, d.day, tzinfo=_TZ)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


_REL_RE = re.compile(r"^([+-]?)(\d+)\s*(m|min|h|d|day|days|w|week|weeks|mo|month|months|y)$")


def parse_when(text: str | None, *, default: datetime | None = None, past: bool = False) -> datetime:
    """Parse a user date: ISO, DD.MM.YYYY, DD.MM., today/tomorrow/yesterday, weekday names, +3d/-1w/3d.

    For bare relative values ("3d") `past=True` means "3 days ago", otherwise "in 3 days".
    Dates without time resolve to 00:00 local time.
    """
    ref = now()
    if text is None or not str(text).strip():
        if default is None:
            raise ValueError("empty date")
        return default
    s = str(text).strip().lower()
    if s in ("now",):
        return ref
    midnight = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    if s in ("today", "heute", "сегодня"):
        return midnight
    if s in ("tomorrow", "morgen", "завтра"):
        return midnight + timedelta(days=1)
    if s in ("yesterday", "gestern", "вчера"):
        return midnight - timedelta(days=1)
    if s in ("last", "last-sync"):
        raise ValueError("'last' must be handled by the caller")
    m = _REL_RE.match(s)
    if m:
        sign, n, unit = m.group(1), int(m.group(2)), m.group(3)
        if unit in ("m", "min"):
            delta = timedelta(minutes=n)
        elif unit == "h":
            delta = timedelta(hours=n)
        elif unit in ("d", "day", "days"):
            delta = timedelta(days=n)
        elif unit in ("w", "week", "weeks"):
            delta = timedelta(weeks=n)
        elif unit in ("mo", "month", "months"):
            delta = timedelta(days=30 * n)
        else:
            delta = timedelta(days=365 * n)
        backwards = sign == "-" or (sign == "" and past)
        return ref - delta if backwards else ref + delta
    if s in _WEEKDAY_NAMES:
        wd = _WEEKDAY_NAMES[s]
        days_ahead = (wd - ref.weekday()) % 7
        if past:
            days_back = (ref.weekday() - wd) % 7 or 7
            return midnight - timedelta(days=days_back)
        return midnight + timedelta(days=days_ahead)
    # ISO date / datetime
    try:
        dt = datetime.fromisoformat(s.replace("z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=_TZ)
    except ValueError:
        pass
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2,4})?(?:\s+(\d{1,2}):(\d{2}))?$", s)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else ref.year
        if year < 100:
            year += 2000
        hour = int(m.group(4)) if m.group(4) else 0
        minute = int(m.group(5)) if m.group(5) else 0
        return datetime(year, month, day, hour, minute, tzinfo=_TZ)
    raise ValueError(
        f"Cannot parse date {text!r}. Use YYYY-MM-DD, DD.MM.YYYY, today/tomorrow, a weekday, or +3d/-1w."
    )
