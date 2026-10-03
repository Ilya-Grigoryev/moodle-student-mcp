"""Extract readable text from downloaded files (PDF, Office, OpenDocument, notebooks, HTML, text, ZIP listing)."""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .html import to_markdown

log = logging.getLogger(__name__)

TEXT_EXTENSIONS = {
    "txt", "md", "markdown", "csv", "tsv", "json", "xml", "yaml", "yml", "toml", "ini", "cfg", "log", "tex", "bib",
    "py", "java", "c", "h", "cpp", "hpp", "cc", "cs", "js", "ts", "jsx", "tsx", "go", "rs", "rb", "php", "kt", "swift",
    "scala", "hs", "ml", "r", "m", "jl", "sql", "sh", "bash", "zsh", "ps1", "bat", "pl", "lua", "dart", "css", "scss",
    "gradle", "properties", "dockerfile", "makefile", "cmake", "asm", "s", "vhd", "v", "proto", "graphql", "puml", "dot",
}
IMAGE_TYPES = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "webp": "webp"}


@dataclass
class Extracted:
    kind: str
    units: list[tuple[str, str]] = field(default_factory=list)  # (label, text) per page/slide/sheet
    note: str = ""

    @property
    def text(self) -> str:
        if len(self.units) == 1 and not self.units[0][0]:
            return self.units[0][1]
        return "\n\n".join(f"--- {label} ---\n{text}".rstrip() for label, text in self.units)


def parse_ranges(spec: str | None, total: int) -> list[int]:
    """'1-3,7,10-' → [1,2,3,7,10,…,total] (1-based)."""
    if not spec:
        return list(range(1, total + 1))
    pages: list[int] = []
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)|(\d+)", part)
        if not m:
            raise ValueError(f"Invalid page range {spec!r}; use e.g. '1-5,8,10-'.")
        if m.group(3):
            a = b = int(m.group(3))
        else:
            a = int(m.group(1) or 1)
            b = int(m.group(2) or total)
        a, b = max(a, 1), min(b, total)
        pages.extend(p for p in range(a, b + 1) if p not in pages)
    return pages


def _pdf(data: bytes, pages: str | None) -> Extracted:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            return Extracted("pdf", note=f"PDF is encrypted ({exc}).")
    total = len(reader.pages)
    wanted = parse_ranges(pages, total)
    units = []
    chars = 0
    for n in wanted:
        try:
            text = reader.pages[n - 1].extract_text() or ""
        except Exception as exc:  # broken page streams happen
            text = f"(could not extract page: {exc})"
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        chars += len(text)
        units.append((f"page {n}/{total}", text))
    note = f"{total} pages"
    if wanted and chars / len(wanted) < 40:
        note += " · very little text — probably scanned images or slides with pictures; download the file to view it"
    return Extracted("pdf", units, note)


def _xml_texts(xml: bytes, para_tag: str, text_tag: str) -> list[str]:
    root = ET.fromstring(xml)
    out = []
    for p in root.iter(para_tag):
        text = "".join(t.text or "" for t in p.iter(text_tag)).strip()
        if text:
            out.append(text)
    return out


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _docx(data: bytes) -> Extracted:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    lines: list[str] = []
    body = root.find(f"{W}body")
    for block in list(body) if body is not None else []:
        if block.tag == f"{W}p":
            text = "".join(t.text or "" for t in block.iter(f"{W}t")).strip()
            style = block.find(f"{W}pPr/{W}pStyle")
            if text:
                level = None
                if style is not None:
                    m = re.search(r"(\d)", style.get(f"{W}val", ""))
                    if "heading" in style.get(f"{W}val", "").lower() or "überschrift" in style.get(f"{W}val", "").lower():
                        level = int(m.group(1)) if m else 1
                lines.append(("#" * min(level, 6) + " " + text) if level else text)
        elif block.tag == f"{W}tbl":
            for row in block.iter(f"{W}tr"):
                cells = ["".join(t.text or "" for t in c.iter(f"{W}t")).strip() for c in row.iter(f"{W}tc")]
                lines.append("| " + " | ".join(cells) + " |")
    return Extracted("docx", [("", "\n\n".join(lines))])


def _pptx(data: bytes, pages: str | None) -> Extracted:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(
            (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.findall(r"\d+", n)[-1]),
        )
        total = len(names)
        units = []
        for n in parse_ranges(pages, total):
            name = names[n - 1]
            texts = _xml_texts(zf.read(name), f"{A}p", f"{A}t")
            notes_name = name.replace("slides/slide", "notesSlides/notesSlide")
            notes = []
            if notes_name in zf.namelist():
                notes = [t for t in _xml_texts(zf.read(notes_name), f"{A}p", f"{A}t") if not t.isdigit()]
            text = "\n".join(texts)
            if notes:
                text += "\n[notes] " + " ".join(notes)
            units.append((f"slide {n}/{total}", text))
    return Extracted("pptx", units, f"{total} slides")


def _xlsx(data: bytes) -> Extracted:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.iter(f"{ns}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
        sheets = sorted((n for n in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                        key=lambda n: int(re.findall(r"\d+", n)[-1]))
        units = []
        for i, name in enumerate(sheets, 1):
            root = ET.fromstring(zf.read(name))
            rows = []
            for row in root.iter(f"{ns}row"):
                cells = []
                for c in row.iter(f"{ns}c"):
                    v = c.find(f"{ns}v")
                    value = v.text if v is not None else ""
                    if c.get("t") == "s" and value:
                        value = shared[int(value)] if int(value) < len(shared) else value
                    elif c.get("t") == "inlineStr":
                        value = "".join(t.text or "" for t in c.iter(f"{ns}t"))
                    cells.append(value or "")
                if any(cells):
                    rows.append(" | ".join(cells))
            units.append((f"sheet {i}", "\n".join(rows)))
    return Extracted("xlsx", units)


def _odf(data: bytes, kind: str) -> Extracted:
    ns_text = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        root = ET.fromstring(zf.read("content.xml"))
    lines = []
    for el in root.iter():
        if el.tag in (f"{ns_text}p", f"{ns_text}h"):
            text = "".join(el.itertext()).strip()
            if text:
                lines.append(text)
    return Extracted(kind, [("", "\n".join(lines))])


def _ipynb(data: bytes) -> Extracted:
    nb = json.loads(data.decode("utf-8", "replace"), strict=False)
    lang = (nb.get("metadata", {}).get("kernelspec", {}) or {}).get("language", "python")
    parts = []
    for cell in nb.get("cells") or []:
        src = "".join(cell.get("source") or [])
        if cell.get("cell_type") == "markdown":
            parts.append(src)
        elif cell.get("cell_type") == "code":
            block = f"```{lang}\n{src}\n```"
            outs = []
            for o in cell.get("outputs") or []:
                text = "".join(o.get("text") or []) or "".join((o.get("data") or {}).get("text/plain") or [])
                if text:
                    outs.append(text.strip()[:1500])
            if outs:
                block += "\nOutput:\n```\n" + "\n".join(outs) + "\n```"
            parts.append(block)
    return Extracted("ipynb", [("", "\n\n".join(parts))])


def _zip_listing(data: bytes) -> Extracted:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        entries = [f"- {i.filename} ({i.file_size} bytes)" for i in zf.infolist() if not i.is_dir()]
    return Extracted("zip", [("", "\n".join(entries))], f"ZIP archive with {len(entries)} files (contents not extracted; download it)")


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def extract(path: Path, filename: str, mimetype: str = "", pages: str | None = None) -> Extracted:
    data = path.read_bytes()
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mt = (mimetype or "").lower()
    try:
        if ext == "pdf" or mt == "application/pdf" or data[:5] == b"%PDF-":
            return _pdf(data, pages)
        if ext == "docx":
            return _docx(data)
        if ext == "pptx":
            return _pptx(data, pages)
        if ext == "xlsx":
            return _xlsx(data)
        if ext in ("odt", "odp", "ods"):
            return _odf(data, ext)
        if ext == "ipynb":
            return _ipynb(data)
        if ext in ("html", "htm") or mt == "text/html":
            return Extracted("html", [("", to_markdown(_decode(data)))])
        if ext == "zip" or mt == "application/zip":
            return _zip_listing(data)
        if ext in TEXT_EXTENSIONS or mt.startswith("text/") or filename.lower() in ("makefile", "dockerfile", "readme"):
            return Extracted("text", [("", _decode(data))])
        if ext in ("doc", "ppt", "xls"):
            return Extracted(ext, note=f"Legacy binary .{ext} format — text extraction not supported; download the file.")
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as exc:
        if isinstance(exc, ValueError) and "page range" in str(exc):
            raise
        note = f"Could not parse the file ({type(exc).__name__}: {exc})"
        if ext in ("ipynb", "json", "html", "htm", "xml"):
            return Extracted("text", [("", _decode(data))], note + " — showing raw text")
        return Extracted(ext or "file", note=note + ".")
    # Unknown: maybe it's text anyway.
    sample = data[:4000]
    if sample and sum(b < 9 or 13 < b < 32 for b in sample) / len(sample) < 0.01:
        return Extracted("text", [("", _decode(data))])
    return Extracted(ext or "binary", note=f"Cannot extract text from this file type ({ext or mimetype or 'unknown'}). Use download_files.")
