"""Shared data access: course contents, modules, files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .app import app
from .client import MoodleError
from .courses import Course
from .html import clean_inline
from .toolkit import UserError

_CMID_URL_RE = re.compile(r"/mod/([a-z0-9_]+)/[a-z_]+\.php\?(?:[^#]*&)?id=(\d+)")
_SKIP_BOOK_FILES = {"index.html"}


@dataclass
class Mod:
    cmid: int
    modname: str
    name: str
    instance: int
    course_id: int
    section_num: int
    section_name: str
    raw: dict[str, Any] = field(repr=False)

    @property
    def url(self) -> str:
        return self.raw.get("url") or app.mod_url(self.modname, self.cmid)

    @property
    def dates(self) -> list[tuple[str, int]]:
        out = []
        for d in self.raw.get("dates") or []:
            ts = d.get("timestamp")
            if ts:
                out.append((clean_inline(d.get("label") or "").rstrip(":") or "Date", int(ts)))
        return out

    @property
    def files(self) -> list[dict[str, Any]]:
        out = []
        for c in self.raw.get("contents") or []:
            if c.get("type") != "file":
                continue
            if self.modname in ("page", "book") and c.get("filename") in _SKIP_BOOK_FILES:
                continue
            out.append(c)
        return out

    @property
    def user_visible(self) -> bool:
        return bool(self.raw.get("uservisible", True))

    @property
    def hidden(self) -> bool:
        return self.raw.get("visible") == 0 or self.raw.get("visibleoncoursepage") == 0

    @property
    def description(self) -> str:
        return self.raw.get("description") or ""


@dataclass
class FileRef:
    course_id: int
    cmid: int
    modname: str
    module_name: str
    section_num: int
    section_name: str
    filename: str
    filepath: str
    size: int
    mtime: int
    url: str
    mimetype: str

    @property
    def is_video(self) -> bool:
        return self.mimetype.startswith("video/") or self.filename.lower().endswith((".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"))

    @property
    def ext(self) -> str:
        return self.filename.rsplit(".", 1)[-1].lower() if "." in self.filename else ""


def section_title(section: dict[str, Any]) -> str:
    name = clean_inline(section.get("name") or "")
    if name:
        return name
    num = section.get("section", 0)
    return "General" if num == 0 else f"Section {num}"


async def contents(course_id: int) -> list[dict[str, Any]]:
    data = await app.client.call("core_course_get_contents", courseid=course_id)
    return data if isinstance(data, list) else []


async def contents_many(course_ids: list[int]) -> dict[int, list[dict[str, Any]] | MoodleError]:
    results = await app.client.batch([("core_course_get_contents", {"courseid": cid}) for cid in course_ids])
    return {cid: (res if isinstance(res, (list, MoodleError)) else []) for cid, res in zip(course_ids, results)}


def mods_from_contents(course_id: int, sections: list[dict[str, Any]]) -> list[Mod]:
    out = []
    for s in sections:
        sname = section_title(s)
        snum = int(s.get("section") or 0)
        for m in s.get("modules") or []:
            out.append(
                Mod(
                    cmid=int(m["id"]),
                    modname=m.get("modname") or "?",
                    name=clean_inline(m.get("name") or ""),
                    instance=int(m.get("instance") or 0),
                    course_id=course_id,
                    section_num=snum,
                    section_name=sname,
                    raw=m,
                )
            )
    return out


async def mods(course_id: int) -> list[Mod]:
    return mods_from_contents(course_id, await contents(course_id))


def parse_cmid(ref: int | str) -> int:
    if isinstance(ref, int):
        return ref
    s = str(ref).strip()
    if s.isdigit():
        return int(s)
    m = _CMID_URL_RE.search(s)
    if m:
        return int(m.group(2))
    m = re.search(r"[?&]cmid=(\d+)", s)
    if m:
        return int(m.group(1))
    raise UserError(f"Cannot find an activity id (cmid) in {ref!r}. Pass the cmid or a /mod/<type>/view.php?id=… URL.")


async def get_mod(ref: int | str) -> tuple[Course | None, Mod]:
    """Resolve a cmid (or activity URL) to its course and full module record."""
    cmid = parse_cmid(ref)
    try:
        info = await app.client.call("core_course_get_course_module", cmid=cmid)
    except MoodleError as exc:
        raise UserError(f"Activity {cmid} not found or not accessible ({exc.message}).") from exc
    cm = info.get("cm") or {}
    course_id = int(cm.get("course") or 0)
    await app.courses.all()
    course = app.courses.by_id(course_id)
    for m in await mods(course_id):
        if m.cmid == cmid:
            return course, m
    # Not on the course page (e.g. stealth) — build a minimal record.
    raw = {"id": cmid, "modname": cm.get("modname"), "name": cm.get("name"), "instance": cm.get("instance"),
           "uservisible": cm.get("uservisible", True), "visible": cm.get("visible", 1)}
    return course, Mod(cmid, cm.get("modname") or "?", clean_inline(cm.get("name") or ""), int(cm.get("instance") or 0),
                       course_id, int(cm.get("sectionnum") or 0), f"Section {cm.get('sectionnum') or 0}", raw)


async def course_files(course: Course, *, include_assign: bool = True) -> list[FileRef]:
    sections = await contents(course.id)
    out: list[FileRef] = []
    seen: set[str] = set()
    for m in mods_from_contents(course.id, sections):
        for f in m.files:
            url = f.get("fileurl") or ""
            if not url or url in seen or f.get("isexternalfile"):
                continue
            seen.add(url)
            out.append(
                FileRef(
                    course_id=course.id, cmid=m.cmid, modname=m.modname, module_name=m.name,
                    section_num=m.section_num, section_name=m.section_name,
                    filename=f.get("filename") or "file", filepath=f.get("filepath") or "/",
                    size=int(f.get("filesize") or 0), mtime=int(f.get("timemodified") or 0), url=url,
                    mimetype=f.get("mimetype") or "",
                )
            )
    if include_assign:
        assign_mods = {m.instance: m for m in mods_from_contents(course.id, sections) if m.modname == "assign"}
        if assign_mods:
            data = await app.client.try_call("mod_assign_get_assignments", courseids=[course.id])
            if isinstance(data, dict):
                for c in data.get("courses") or []:
                    for a in c.get("assignments") or []:
                        m = assign_mods.get(int(a.get("id") or 0))
                        if m is None:
                            continue
                        for f in a.get("introattachments") or []:
                            url = f.get("fileurl") or ""
                            if not url or url in seen:
                                continue
                            seen.add(url)
                            out.append(
                                FileRef(
                                    course_id=course.id, cmid=m.cmid, modname="assign", module_name=m.name,
                                    section_num=m.section_num, section_name=m.section_name,
                                    filename=f.get("filename") or "file", filepath=f.get("filepath") or "/",
                                    size=int(f.get("filesize") or 0), mtime=int(f.get("timemodified") or 0), url=url,
                                    mimetype=f.get("mimetype") or "",
                                )
                            )
    return out
