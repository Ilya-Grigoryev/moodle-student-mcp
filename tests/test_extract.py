"""Text extraction from generated documents."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from moodle_mcp.extract import extract


def _zip(path: Path, files: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def _pdf_bytes(pages: list[str]) -> bytes:
    """A tiny valid PDF with one text line per page."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", ""]
    kids = []
    font_id = 3
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        content_id = len(objects) + 1
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        page_id = len(objects) + 1
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
                       f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>")
        kids.append(f"{page_id} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{obj}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def test_pdf_pages(tmp_path: Path):
    p = tmp_path / "slides.pdf"
    p.write_bytes(_pdf_bytes(["First page text", "Second page text", "Third page text"]))
    res = extract(p, "slides.pdf")
    assert res.kind == "pdf" and "3 pages" in res.note
    assert "--- page 1/3 ---\nFirst page text" in res.text
    res2 = extract(p, "slides.pdf", pages="2-3")
    assert "First page" not in res2.text and "Third page text" in res2.text


def test_docx(tmp_path: Path):
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    doc = (f'<w:document {W}><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Title</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Hello </w:t></w:r><w:r><w:t>world</w:t></w:r></w:p>'
           '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
           '</w:body></w:document>')
    p = _zip(tmp_path / "a.docx", {"word/document.xml": doc})
    text = extract(p, "a.docx").text
    assert "# Title" in text and "Hello world" in text and "| A | B |" in text


def test_pptx(tmp_path: Path):
    A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    slide = lambda t: f'<p:sld xmlns:p="x" {A}><a:p><a:r><a:t>{t}</a:t></a:r></a:p></p:sld>'  # noqa: E731
    p = _zip(tmp_path / "s.pptx", {"ppt/slides/slide1.xml": slide("One"), "ppt/slides/slide2.xml": slide("Two"),
                                    "ppt/slides/slide10.xml": slide("Ten")})
    res = extract(p, "s.pptx")
    assert res.text.index("One") < res.text.index("Two") < res.text.index("Ten")
    assert "slide 3/3" in res.text


def test_ipynb_and_text(tmp_path: Path):
    nb = {"cells": [{"cell_type": "markdown", "source": ["# Task"]},
                    {"cell_type": "code", "source": ["print(1)"], "outputs": [{"text": ["1\n"]}]}],
          "metadata": {"kernelspec": {"language": "python"}}}
    p = tmp_path / "n.ipynb"
    p.write_text(json.dumps(nb))
    text = extract(p, "n.ipynb").text
    assert "# Task" in text and "```python\nprint(1)\n```" in text and "1" in text
    t = tmp_path / "Main.java"
    t.write_text("class Main {}")
    assert extract(t, "Main.java").text == "class Main {}"


def test_unknown_binary(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(bytes(range(256)) * 10)
    res = extract(p, "x.bin")
    assert not res.units and "Cannot extract" in res.note
