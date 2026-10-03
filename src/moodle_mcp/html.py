"""HTML → Markdown / plain text tuned for Moodle content."""

from __future__ import annotations

import html as htmllib
import re
import warnings
from urllib.parse import unquote

from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
from markdownify import MarkdownConverter

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)

_MLANG_RE = re.compile(r"\{mlang\s+([^}]+)\}(.*?)\{mlang\}", re.S | re.I)
_SPAN_MLANG_RE = re.compile(r'<span[^>]*class="multilang"[^>]*lang="([^"]+)"[^>]*>(.*?)</span>', re.S | re.I)
_HIDDEN_CLASSES = {"accesshide", "sr-only", "visually-hidden", "hidden"}
_preferred_lang = "en"


def set_language(lang: str) -> None:
    global _preferred_lang
    _preferred_lang = (lang or "en").lower()


def resolve_mlang(text: str) -> str:
    """Pick one language out of {mlang xx}…{mlang} / span.multilang blocks (fallback if the server didn't)."""
    if "{mlang" not in text and "multilang" not in text:
        return text

    def pick(blocks: list[tuple[str, str]]) -> str:
        chosen = None
        for langs, content in blocks:
            codes = [c.strip().lower() for c in langs.split(",")]
            if _preferred_lang in codes:
                return content
            if chosen is None and ("other" in codes or "en" in codes):
                chosen = content
        return chosen if chosen is not None else blocks[0][1]

    for regex in (_MLANG_RE, _SPAN_MLANG_RE):
        # Group adjacent blocks into runs and replace each run with the picked variant.
        def replace_run(match: re.Match[str], regex: re.Pattern[str] = regex) -> str:
            return pick(regex.findall(match.group(0)))

        run = re.compile(f"(?:{regex.pattern}\\s*)+", re.S | re.I)
        text = run.sub(replace_run, text)
    return text


def clean_inline(text: str | None) -> str:
    """For names/titles: resolve mlang, strip tags, unescape entities, collapse whitespace."""
    if not text:
        return ""
    text = resolve_mlang(str(text))
    if "<" in text:
        text = re.sub(r"<[^>]+>", " ", text)
    text = htmllib.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


class _Converter(MarkdownConverter):
    def convert_img(self, el, text, parent_tags):  # type: ignore[override]
        alt = (el.get("alt") or el.get("title") or "").strip()
        src = (el.get("src") or "").strip()
        if "pluginfile.php" in src:
            # Moodle-hosted image: keep the link so read_file(url=…) can show it to the model.
            src = re.sub(r"([?&])token=[^&]+&?", r"\1", src).rstrip("?&").replace("/webservice/pluginfile.php", "/pluginfile.php")
            name = alt or unquote(src.rsplit("/", 1)[-1])
            return f"![{name}]({src})"
        if src.startswith("http") and alt:
            return f"[image: {alt}]"
        return f"[image: {alt}]" if alt else ""

    def convert_a(self, el, text, parent_tags):  # type: ignore[override]
        href = el.get("href") or ""
        text = (text or "").strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            return text
        href = re.sub(r"([?&])token=[^&]+&?", r"\1", href).rstrip("?&")
        href = href.replace("/webservice/pluginfile.php", "/pluginfile.php")
        if not text:
            return f"<{href}>"
        if text == href or text.rstrip("/") == href.rstrip("/"):
            return f"<{href}>"
        return f"[{text}]({href})"


def _prepare(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript", "template", "button"]):
        tag.decompose()
    for tag in soup.find_all(class_=True):
        classes = set(tag.get("class") or [])
        if classes & _HIDDEN_CLASSES:
            tag.decompose()


def to_markdown(html: str | None, *, max_chars: int | None = None) -> str:
    if not html:
        return ""
    html = resolve_mlang(str(html))
    if "<" not in html:
        text = htmllib.unescape(html).strip()
    else:
        soup = BeautifulSoup(html, "html.parser")
        _prepare(soup)
        text = _Converter(heading_style="ATX", bullets="-", escape_underscores=False, escape_asterisks=False).convert_soup(soup)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_chars is not None:
        text = truncate(text, max_chars)
    return text


def to_text(html: str | None, *, max_chars: int | None = None) -> str:
    """Plain single-spaced text (for snippets and search)."""
    if not html:
        return ""
    html = resolve_mlang(str(html))
    if "<" in html:
        soup = BeautifulSoup(html, "html.parser")
        _prepare(soup)
        text = soup.get_text(" ")
    else:
        text = html
    text = htmllib.unescape(text).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if max_chars is not None:
        text = truncate(text, max_chars)
    return text


def truncate(text: str, max_chars: int, hint: str = "") -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Prefer cutting at a line/sentence boundary.
    for sep in ("\n", ". ", " "):
        pos = cut.rfind(sep)
        if pos > max_chars * 0.7:
            cut = cut[: pos + (1 if sep != "\n" else 0)]
            break
    rest = len(text) - len(cut)
    suffix = f" … [+{rest} chars{'; ' + hint if hint else ''}]"
    return cut.rstrip() + suffix
