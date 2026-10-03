"""File tools: list_files, read_file, download_files."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote, urlsplit

from mcp.server.mcpserver import Image
from pydantic import Field

from .. import data, fmt, timeutil
from ..app import app
from ..client import MoodleClient, MoodleError
from ..courses import Course, fold
from ..extract import IMAGE_TYPES, extract
from ..toolkit import UserError, moodle_tool
from .courses import CourseArg

MAX_READ_BYTES = 150 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
CACHE_LIMIT_BYTES = 1024 * 1024 * 1024
MANIFEST = ".moodle-sync.json"


# ---------------------------------------------------------------- helpers

def _matches_filters(f: data.FileRef, include_videos: bool, extensions: list[str] | None) -> bool:
    if not include_videos and f.is_video:
        return False
    if extensions:
        exts = {e.lower().lstrip(".") for e in extensions}
        return f.ext in exts
    return True


def _section_filter(files: list[data.FileRef], section: str | int | None) -> list[data.FileRef]:
    if section is None:
        return files
    s = str(section).strip()
    if s.isdigit():
        return [f for f in files if f.section_num == int(s)]
    fs = fold(s)
    return [f for f in files if fs in fold(f.section_name)]


async def _mod_files(m: data.Mod) -> list[dict[str, Any]]:
    if m.modname == "assign":
        res = await app.client.try_call("mod_assign_get_assignments", courseids=[m.course_id])
        if isinstance(res, dict):
            for c in res.get("courses") or []:
                for a in c.get("assignments") or []:
                    if int(a.get("cmid") or 0) == m.cmid:
                        return list(a.get("introattachments") or [])
        return []
    return m.files


def _cache_root() -> Path:
    return app.settings.cache_dir / "files"


def _prune_cache() -> None:
    root = _cache_root()
    if not root.exists():
        return
    files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in files)
    for p in files:
        if total <= CACHE_LIMIT_BYTES:
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)


async def fetch_cached(url: str, filename: str, mtime: int | None) -> Path:
    key = hashlib.sha1(f"{MoodleClient.public_url(url)}|{mtime or ''}".encode()).hexdigest()[:20]
    safe = re.sub(r"[^\w.\-]+", "_", filename)[:120] or "file"
    path = _cache_root() / key / safe
    if path.exists() and (mtime or time.time() - path.stat().st_mtime < 6 * 3600):
        return path
    await app.client.download(url, path, max_bytes=MAX_READ_BYTES)
    _prune_cache()
    return path


def _filename_from_url(url: str) -> str:
    return unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "file"


# ---------------------------------------------------------------- list_files

@moodle_tool(title="List course files")
async def list_files(
    course: CourseArg,
    section: Annotated[str | int | None, Field(description="Only this section (number or name part)")] = None,
    include_videos: Annotated[bool, Field(description="Include video files")] = True,
    extensions: Annotated[list[str] | None, Field(description="Only these extensions, e.g. ['pdf','zip']")] = None,
) -> str:
    """All files of a course (resources, folders, pages, books, assignment attachments) with section,
    activity cmid, size and modification date. Read one with read_file(cmid, filename)."""
    c = await app.courses.resolve(course)
    files = [f for f in _section_filter(await data.course_files(c), section) if _matches_filters(f, include_videos, extensions)]
    if not files:
        return f"No files in {c.label}{f' section {section}' if section is not None else ''} matching the filters."
    total = sum(f.size for f in files)
    videos = [f for f in files if f.is_video]
    head = f"# Files — {c.label} [{c.id}]: {len(files)} files, {fmt.size(total)}"
    if videos:
        head += f" ({len(videos)} videos, {fmt.size(sum(v.size for v in videos))})"
    rows = []
    for f in files:
        path = f.filepath.strip("/")
        name = f"{path}/{f.filename}" if path else f.filename
        rows.append([f"{f.section_num} {f.section_name}", f"{f.module_name} · cmid {f.cmid}", name, fmt.size(f.size),
                     timeutil.fmt_short(f.mtime)])
    return fmt.cap(head + "\n\n" + fmt.table(["Section", "Activity", "File", "Size", "Modified"], rows))


# ---------------------------------------------------------------- read_file

@moodle_tool(title="Read a file")
async def read_file(
    cmid: Annotated[str | int | None, Field(description="Activity cmid holding the file (resource, folder, assign, page…)")] = None,
    filename: Annotated[str | None, Field(description="File name (or part of it) when the activity has several files")] = None,
    url: Annotated[str | None, Field(description="Alternatively a Moodle file URL (…/pluginfile.php/…) e.g. from a post or page")] = None,
    pages: Annotated[str | None, Field(description="PDF pages / PPTX slides to read, e.g. '1-5,9' (default all)")] = None,
    offset: Annotated[int, Field(ge=0, description="Character offset to continue a long text")] = 0,
    max_chars: Annotated[int, Field(ge=500, le=200_000, description="Max characters to return")] = 30_000,
) -> Any:
    """Download a Moodle file and return its text: PDF (page markers), PPTX (slides + notes), DOCX, XLSX,
    ODT/ODP, Jupyter notebooks, HTML, source code/text. Images are returned as images. Long texts are paged."""
    if url is None and cmid is None:
        raise UserError("Pass `cmid` (+ `filename` if needed) or a Moodle file `url`.")
    mimetype = ""
    mtime: int | None = None
    size: int | None = None
    if url is not None:
        if not app.client.is_site_url(url):
            raise UserError("Only files hosted on your Moodle site can be read.")
        fname = filename or _filename_from_url(url)
        source = MoodleClient.public_url(url)
    else:
        _, m = await data.get_mod(cmid)  # type: ignore[arg-type]
        files = await _mod_files(m)
        if not files:
            raise UserError(f"Activity {m.cmid} ({m.modname} «{m.name}») has no files. Try get_activity({m.cmid}).")
        chosen = files
        if filename:
            ff = fold(filename)
            exact = [f for f in files if fold(f.get("filename") or "") == ff]
            chosen = exact or [f for f in files if ff in fold(f.get("filename") or "")]
        if not chosen:
            names = ", ".join(f.get("filename") or "" for f in files)
            raise UserError(f"No file matching {filename!r} in «{m.name}». Files: {names}")
        if len(chosen) > 1:
            names = "\n".join(f"- {f.get('filename')} ({fmt.size(f.get('filesize'))})" for f in chosen)
            raise UserError(f"«{m.name}» has {len(chosen)} files — pass `filename`:\n{names}")
        f = chosen[0]
        url = f.get("fileurl") or ""
        fname = f.get("filename") or "file"
        mimetype = f.get("mimetype") or ""
        mtime = int(f.get("timemodified") or 0) or None
        size = int(f.get("filesize") or 0) or None
        source = f"cmid {m.cmid} «{m.name}»"
    if size and size > MAX_READ_BYTES:
        raise UserError(f"{fname} is {fmt.size(size)} — too large to read here. Use download_files.")
    path = await fetch_cached(url, fname, mtime)
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    header = f"# {fname}\n{source} · {fmt.size(path.stat().st_size)}"
    if ext in IMAGE_TYPES:
        if path.stat().st_size > MAX_IMAGE_BYTES:
            return header + "\nImage is too large to show inline; use download_files."
        return [header, Image(data=path.read_bytes(), format=IMAGE_TYPES[ext])]
    if ext in ("mp4", "mov", "mkv", "webm", "mp3", "m4a", "wav", "avi"):
        return header + "\nAudio/video file — no text to extract. Use download_files to save it."
    try:
        result = await asyncio.to_thread(extract, path, fname, mimetype, pages)
    except ValueError as exc:
        raise UserError(str(exc)) from exc
    if result.note:
        header += f" · {result.note}"
    text = result.text
    if not text.strip():
        return header + "\n\n(no extractable text)"
    total = len(text)
    chunk = text[offset : offset + max_chars]
    out = header + (f"\nshowing chars {offset}–{offset + len(chunk)} of {total}" if total > max_chars or offset else "")
    out += "\n\n" + chunk
    if offset + max_chars < total:
        out += f"\n\n… {total - offset - max_chars} more chars — call read_file again with offset={offset + max_chars}"
        if result.kind in ("pdf", "pptx"):
            out += " or choose `pages`"
    return out


# ---------------------------------------------------------------- download_files

def _sanitize(name: str, spaces: bool) -> str:
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "file"
    if spaces:
        name = re.sub(r"\s+", "_", name)
    return name[:180]


def _default_dest(c: Course) -> Path:
    return app.settings.download_dir / _sanitize(f"{c.semester or ''} {c.title}".strip(), spaces=False)


def _plan_paths(files: list[data.FileRef], layout: str, sanitize_names: bool) -> dict[str, str]:
    """Map file URL → relative local path, resolving name collisions deterministically."""
    plan: dict[str, str] = {}
    used: dict[str, str] = {}
    for f in files:
        sub = f.filepath.strip("/")
        name = _sanitize(f.filename, sanitize_names)
        parts: list[str] = []
        if layout == "sections":
            parts.append(_sanitize(f"{f.section_num:02d} {f.section_name}", sanitize_names))
            if f.modname in ("folder", "assign") or len([g for g in files if g.cmid == f.cmid]) > 1:
                parts.append(_sanitize(f.module_name, sanitize_names))
        if sub:
            parts.extend(_sanitize(p, sanitize_names) for p in sub.split("/") if p)
        rel = "/".join([*parts, name])
        if rel.lower() in used and used[rel.lower()] != f.url:
            stem, dot, ext = name.rpartition(".")
            tag = _sanitize(f.module_name, sanitize_names)[:40]
            alt = f"{stem}__{tag}.{ext}" if dot else f"{name}__{tag}"
            rel = "/".join([*parts, alt])
            n = 2
            while rel.lower() in used and used[rel.lower()] != f.url:
                rel = "/".join([*parts, f"{stem}__{tag}_{n}.{ext}" if dot else f"{name}__{tag}_{n}"])
                n += 1
        used[rel.lower()] = f.url
        plan[f.url] = rel
    return plan


@moodle_tool(title="Download course files", read_only=False, idempotent=True)
async def download_files(
    course: CourseArg,
    dest: Annotated[str | None, Field(description="Target folder, absolute or relative to your home directory (created if missing). Default: <download_dir>/<semester> <course>")] = None,
    section: Annotated[str | int | None, Field(description="Only this section (number or name part)")] = None,
    activity: Annotated[str | int | None, Field(description="Only files of this activity (cmid)")] = None,
    include_videos: Annotated[bool, Field(description="Also download videos (can be large)")] = False,
    extensions: Annotated[list[str] | None, Field(description="Only these extensions, e.g. ['pdf']")] = None,
    layout: Annotated[str, Field(description="'flat' (all files in one folder) or 'sections' (subfolder per section)")] = "flat",
    sanitize_names: Annotated[bool, Field(description="Replace spaces in file names with '_'")] = True,
    dry_run: Annotated[bool, Field(description="Only show what would be downloaded")] = False,
) -> str:
    """Sync a course's files into a local folder: downloads new and changed files, skips unchanged ones
    (tracked in .moodle-sync.json), never deletes anything. Videos are skipped unless requested."""
    if layout not in ("flat", "sections"):
        raise UserError("layout must be 'flat' or 'sections'.")
    c = await app.courses.resolve(course)
    all_files = await data.course_files(c)
    files = _section_filter(all_files, section)
    if activity is not None:
        cm = data.parse_cmid(activity)
        files = [f for f in files if f.cmid == cm]
    skipped_filter = [f for f in files if not _matches_filters(f, include_videos, extensions)]
    files = [f for f in files if _matches_filters(f, include_videos, extensions)]
    target = Path(dest).expanduser() if dest else _default_dest(c)
    if not target.is_absolute():
        target = Path.home() / target
    manifest_path = target / MANIFEST
    try:
        man = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    except (OSError, ValueError):
        man = {}
    entries: dict[str, Any] = man.get("files") or {}
    plan = _plan_paths(files, layout, sanitize_names)
    # Keep paths of files we downloaded before, so renaming rules never move existing files.
    for f in files:
        key = MoodleClient.public_url(f.url)
        if key in entries and entries[key].get("path"):
            plan[f.url] = entries[key]["path"]

    new, updated, unchanged, adopted = [], [], [], []
    for f in files:
        key = MoodleClient.public_url(f.url)
        rel = plan[f.url]
        local = target / rel
        entry = entries.get(key)
        if local.exists():
            same_size = local.stat().st_size == f.size
            if entry and entry.get("mtime") == f.mtime and same_size:
                unchanged.append(f)
                continue
            if not entry and same_size:
                adopted.append(f)  # downloaded earlier by hand — just start tracking it
                continue
            updated.append(f)
        else:
            new.append(f)
    todo = new + updated
    total = sum(f.size for f in todo)
    lines = [f"# Download — {c.label} → {target}"]
    if dry_run:
        lines.append(f"DRY RUN: would download {len(new)} new + {len(updated)} changed files ({fmt.size(total)}); "
                     f"{len(unchanged) + len(adopted)} already present.")
        for f in todo:
            lines.append(f"- {'new' if f in new else 'changed'}: {plan[f.url]} ({fmt.size(f.size)})")
    else:
        failed: list[tuple[data.FileRef, str]] = []
        sem = asyncio.Semaphore(4)

        async def get(f: data.FileRef) -> None:
            async with sem:
                try:
                    await app.client.download(f.url, target / plan[f.url])
                except MoodleError as exc:
                    failed.append((f, exc.message))

        if todo:
            target.mkdir(parents=True, exist_ok=True)
            await asyncio.gather(*(get(f) for f in todo))
        failed_urls = {f.url for f, _ in failed}
        now = timeutil.now_ts()
        for f in files:
            if f.url in failed_urls:
                continue
            key = MoodleClient.public_url(f.url)
            if f in todo or f in adopted or key not in entries:
                entries[key] = {"path": plan[f.url], "mtime": f.mtime, "size": f.size, "cmid": f.cmid,
                                "synced": now if (f in todo or key not in entries) else entries.get(key, {}).get("synced", now)}
        if files:
            target.mkdir(parents=True, exist_ok=True)
            man = {"course_id": c.id, "course": c.label, "site": app.settings.site_url, "updated": now, "files": entries}
            manifest_path.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
        ok_new = [f for f in new if f.url not in failed_urls]
        ok_upd = [f for f in updated if f.url not in failed_urls]
        lines.append(f"Downloaded {len(ok_new)} new + {len(ok_upd)} updated files "
                     f"({fmt.size(sum(f.size for f in ok_new + ok_upd))}); {len(unchanged) + len(adopted)} unchanged.")
        for f in ok_new:
            lines.append(f"- new: {plan[f.url]} ({fmt.size(f.size)})")
        for f in ok_upd:
            lines.append(f"- updated: {plan[f.url]} ({fmt.size(f.size)})")
        for f, err in failed:
            lines.append(f"- FAILED: {plan[f.url]} — {err}")
    if skipped_filter:
        vids = [f for f in skipped_filter if f.is_video]
        rest = len(skipped_filter) - len(vids)
        bits = []
        if vids:
            bits.append(f"{len(vids)} videos ({fmt.size(sum(v.size for v in vids))}; include_videos=true to get them)")
        if rest:
            bits.append(f"{rest} files filtered by extension")
        lines.append("Skipped: " + ", ".join(bits))
    if not files:
        lines.append("No files matched.")
    return fmt.cap("\n".join(lines))
