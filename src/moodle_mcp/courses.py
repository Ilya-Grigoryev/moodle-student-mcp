"""Course index: semester detection, display titles, acronyms and flexible course resolution."""

from __future__ import annotations

import difflib
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import timeutil
from .client import MoodleClient
from .html import clean_inline

_SEM_SHORT_RE = re.compile(r"^(WS|SS|WiSe|SoSe)\s*(\d{4})", re.I)
_SEM_FULL_RE = re.compile(r"^\s*(\d{4})\s*([WS])\b")
_SEM_CODE_RE = re.compile(r"^(\d{4})([WS])$", re.I)
_LV_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_STOPWORDS = {"for", "and", "of", "the", "in", "to", "on", "with", "a", "an", "und", "der", "die", "das",
              "für", "zur", "zum", "im", "von", "mit", "des", "den", "dem", "ein", "eine", "&"}


class CourseNotFound(Exception):
    pass


class AmbiguousCourse(Exception):
    pass


def fold(text: str) -> str:
    """Lowercase + strip diacritics for matching."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().replace("ß", "ss")


def semester_of(shortname: str, fullname: str, startdate: int | None) -> str | None:
    m = _SEM_SHORT_RE.match(shortname or "")
    if m:
        kind = m.group(1).lower()
        return f"{m.group(2)}{'W' if kind in ('ws', 'wise') else 'S'}"
    m = _SEM_FULL_RE.match(fullname or "")
    if m:
        return f"{m.group(1)}{m.group(2).upper()}"
    if startdate:
        dt = timeutil.to_dt(startdate)
        if dt.month >= 9:
            return f"{dt.year}W"
        if dt.month <= 2:
            return f"{dt.year - 1}W"
        return f"{dt.year}S"
    return None


def semester_for_date(dt: datetime) -> str:
    if dt.month >= 10:
        return f"{dt.year}W"
    if dt.month <= 2:
        return f"{dt.year - 1}W"
    return f"{dt.year}S"


def semester_key(code: str | None) -> tuple[int, int]:
    if not code:
        return (0, 0)
    m = _SEM_CODE_RE.match(code)
    if not m:
        return (0, 0)
    return (int(m.group(1)), 1 if m.group(2).upper() == "W" else 0)


def normalize_semester(code: str) -> str | None:
    s = code.strip().upper().replace(" ", "")
    m = re.match(r"^(\d{4})([WS])$", s) or re.match(r"^([WS])S?(\d{4})$", s)
    if m:
        a, b = m.groups()
        return f"{a}{b}" if a.isdigit() else f"{b}{a}"
    m = re.match(r"^(WISE|SOSE)(\d{4})$", s)
    if m:
        return f"{m.group(2)}{'W' if m.group(1) == 'WISE' else 'S'}"
    return None


def clean_title(fullname: str) -> str:
    title = clean_inline(fullname)
    title = re.sub(r"^\s*\d{4}\s*[WS]\b\s*", "", title)
    title = re.sub(r"^(\d{6}(?:-\d+)*(?:/\d{6}(?:-\d+)*)*)\s+", "", title)
    return title.strip() or clean_inline(fullname)


def acronyms(title: str) -> set[str]:
    base = re.sub(r"\(.*?\)", " ", title)
    tokens = re.findall(r"[^\W\d_]+|\d+", base)
    if not tokens:
        return set()
    full = "".join(t if t.isdigit() else t[0] for t in tokens)
    significant = "".join(t if t.isdigit() else t[0] for t in tokens if t.isdigit() or fold(t) not in _STOPWORDS)
    caps = "".join(t if t.isdigit() else t[0] for t in tokens if t.isdigit() or t[0].isupper())
    out = {fold(x) for x in (full, significant, caps) if len(x) >= 2}
    return out


@dataclass
class Course:
    id: int
    shortname: str
    fullname: str
    title: str
    semester: str | None
    lv: str | None
    startdate: int
    enddate: int
    lastaccess: int | None
    favourite: bool
    hidden: bool
    url: str
    acronyms: set[str] = field(default_factory=set)
    excluded: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def label(self) -> str:
        sem = f"{self.semester} " if self.semester else ""
        lv = f"{self.lv} " if self.lv else ""
        return f"{sem}{lv}{self.title}"

    @property
    def ref(self) -> str:
        """Short reference used in listings: 'Software Engineering 2 [471264]'."""
        return f"{self.title} [{self.id}]"


class CourseIndex:
    def __init__(self, client: MoodleClient) -> None:
        self.client = client
        self._courses: list[Course] | None = None
        self._loaded_at = 0.0

    async def all(self, refresh: bool = False) -> list[Course]:
        if self._courses is not None and not refresh and time.monotonic() - self._loaded_at < 600:
            return self._courses
        userid = await self.client.userid()
        raw = await self.client.call("core_enrol_get_users_courses", userid=userid, returnusercount=0)
        site = self.client.settings.site_url
        courses: list[Course] = []
        for c in raw or []:
            if not isinstance(c, dict):
                continue
            fullname = clean_inline(c.get("fullname") or c.get("displayname") or "")
            shortname = clean_inline(c.get("shortname") or "")
            title = clean_title(fullname)
            lv_match = _LV_RE.search(shortname) or _LV_RE.search(fullname)
            course = Course(
                id=int(c["id"]),
                shortname=shortname,
                fullname=fullname,
                title=title,
                semester=semester_of(shortname, fullname, c.get("startdate")),
                lv=lv_match.group(1) if lv_match else None,
                startdate=int(c.get("startdate") or 0),
                enddate=int(c.get("enddate") or 0),
                lastaccess=c.get("lastaccess"),
                favourite=bool(c.get("isfavourite")),
                hidden=bool(c.get("hidden")),
                url=f"{site}/course/view.php?id={c['id']}",
                raw=c,
            )
            course.acronyms = acronyms(title)
            course.excluded = course.hidden or self._is_excluded(course)
            courses.append(course)
        courses.sort(key=lambda x: (semester_key(x.semester), x.startdate, x.id), reverse=True)
        self._courses = courses
        self._loaded_at = time.monotonic()
        return courses

    def _is_excluded(self, c: Course) -> bool:
        for entry in self.client.settings.exclude_courses:
            e = fold(entry)
            if e == str(c.id) or e == (c.lv or "") or e == fold(c.shortname) or e in c.acronyms or (len(e) > 3 and e in fold(c.title)):
                return True
        return False

    async def current_semester(self) -> str:
        """Semester by date; switches early when courses of the next semester already started (or start ≤ 3 weeks)."""
        courses = await self.all()
        by_date = semester_for_date(timeutil.now())
        horizon = timeutil.now_ts() + 21 * 86400
        started = [c.semester for c in courses if c.semester and c.startdate and c.startdate <= horizon]
        latest = max(started, key=semester_key, default=None)
        if latest and semester_key(latest) > semester_key(by_date):
            return latest
        return by_date

    async def semesters(self) -> list[str]:
        sems = {c.semester for c in await self.all() if c.semester}
        return sorted(sems, key=semester_key, reverse=True)

    async def select(self, semester: str | None = "current", *, include_excluded: bool = False) -> list[Course]:
        """Courses for 'current' | 'past' | 'all' | a semester code like 2026W.

        Courses the user hid in Moodle's course overview or listed in `exclude_courses` are left out of the
        current semester unless include_excluded=True (they stay reachable by name/id).
        """
        courses = await self.all()
        sel = (semester or "current").strip().lower()
        if sel in ("current", "now", "this", "") and not include_excluded:
            courses = [c for c in courses if not c.excluded]
        if sel in ("all", "*", "any"):
            return courses
        current = await self.current_semester()
        if sel in ("current", "now", "this", ""):
            return [c for c in courses if c.semester == current]
        if sel in ("past", "previous", "old"):
            return [c for c in courses if semester_key(c.semester) < semester_key(current)]
        code = normalize_semester(sel)
        if not code:
            raise ValueError(f"Unknown semester {semester!r}. Use current, past, all or a code like 2026W / 2025S.")
        return [c for c in courses if c.semester == code]

    async def resolve(self, query: str | int) -> Course:
        courses = await self.all()
        by_id = {c.id: c for c in courses}
        q = str(query).strip()
        if not q:
            raise CourseNotFound("Empty course reference.")
        m = re.search(r"course/view\.php\?(?:.*&)?id=(\d+)", q)
        if m:
            q = m.group(1)
        if q.isdigit() and int(q) in by_id:
            return by_id[int(q)]
        if q.isdigit() and len(q) > 6:
            # A course id that the user is not enrolled in.
            raise CourseNotFound(f"You are not enrolled in a course with id {q}.")

        # Optional semester qualifier inside the query: "SE1 2025W", "Software Engineering 1 (2026S)"
        sem_filter = None
        m = re.search(r"\b((?:19|20)\d{2}\s?[WS]|[WS]S\s?(?:19|20)\d{2}|(?:WiSe|SoSe)\s?(?:19|20)\d{2})\b", q, re.I)
        if m:
            sem_filter = normalize_semester(m.group(1))
            q = (q[: m.start()] + q[m.end():]).strip(" ()-,")
        current = await self.current_semester()
        fq = fold(q)
        fq_compact = re.sub(r"[^0-9a-z]", "", fq)
        words = [w for w in re.findall(r"[0-9a-z]+", fq) if w]

        scored: list[tuple[int, Course]] = []
        for c in courses:
            if sem_filter and c.semester != sem_filter:
                continue
            title = fold(c.title)
            score = 0
            if fold(c.shortname) == fq:
                score = 95
            elif c.lv and fq_compact == c.lv:
                score = 90
            elif fq_compact and fq_compact in c.acronyms:
                score = 85
            elif title == fq:
                score = 80
            elif title.startswith(fq):
                score = 70
            elif words and all(re.search(rf"(?<![0-9a-z]){re.escape(w)}", title) for w in words):
                score = 60
            elif fq and (fq in title or fq in fold(c.fullname) or fq in fold(c.shortname)):
                score = 50
            else:
                ratio = difflib.SequenceMatcher(None, fq, title).ratio()
                if ratio >= 0.75:
                    score = int(ratio * 50)
            if score:
                scored.append((score, c))
        if not scored:
            near = difflib.get_close_matches(fq, [fold(c.title) for c in courses], n=5, cutoff=0.4)
            hint = ""
            if near:
                names = [c.ref for c in courses if fold(c.title) in near][:5]
                hint = " Did you mean: " + "; ".join(names) + "?"
            raise CourseNotFound(f"No enrolled course matches {query!r}.{hint} Use list_courses to see all courses.")

        def rank(item: tuple[int, Course]) -> tuple[int, int, tuple[int, int], int]:
            score, c = item
            return (score, 1 if c.semester == current else 0, semester_key(c.semester), c.lastaccess or 0)

        scored.sort(key=rank, reverse=True)
        best_score, best = scored[0]
        rivals = [
            c for s, c in scored[1:]
            if s == best_score and c.semester == best.semester and c.id != best.id
        ]
        if rivals:
            options = "; ".join(f"{c.label} [id {c.id}]" for c in [best, *rivals][:6])
            raise AmbiguousCourse(f"{query!r} matches several courses: {options}. Pass the course id.")
        return best

    def by_id(self, course_id: int) -> Course | None:
        for c in self._courses or []:
            if c.id == course_id:
                return c
        return None
