"""Page text from the source bytes. The LLM reads this text (Option 2 on HTML), and the checker re-derives it
from the same bytes with the same reader, so a quote is confirmed against exactly what was read.

Copied from robotparts tools/ingestor/text.py (2026-10-29). Added here: `html_offsets`, which also records where in
the decoded HTML each character of the squashed text came from, so an Option 2 value on a web page gets a
`source_range` grounding (CONTRACT §3.3) the scorer can compare with the harness truth, and `pdf_words`, the
text layer's words with their boxes (pdfplumber), against which an Option 2 box is checked.

- PDF: `pdftotext -layout -enc UTF-8` (poppler), split on its form feeds: page N of the list is page N of
  the PDF (1-based in part.json). Layout mode keeps a table row on one line, so a quote can carry the row
  label and its value together.
- HTML: a stdlib reader that drops script/style/template/svg, puts block elements on their own lines and
  table cells side by side with a tab. The whole page is page 1 (SCHEMA2.md §1.2).

Quotes are compared whitespace-normalised (`squash`), as the frozen part.json check does.

`html_zones` reads the same HTML once more for where each character of that squashed text sat (`layout_zones`
does the little a PDF's layout text allows): in the site's
navigation (`<nav>`, `<header>`, `<footer>`, `<aside>`, `<menu>`, or an ARIA navigation/menu/banner/contentinfo
role), and/or in a link's text (`<a href>`). WHY (review V2-1): CubeMars prints its whole catalogue in its menu
('- AKE90-8 KV35') and links to related products, and the text alone cannot tell a menu entry from a spec row.
The zones never change the text the LLM and the checker read; they only tell the checker where a quote came from.
"""

from __future__ import annotations

import functools
import html.parser
import re
import subprocess
import tempfile

PDFTOTEXT = "pdftotext"
# WHY not "head": a page that never closes <head> would lose its whole body; head's scripts and styles are
# skipped one by one anyway.
_SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "object"}
_BLOCK = {"address", "article", "aside", "blockquote", "br", "caption", "dd", "details", "div", "dl", "dt",
          "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
          "hr", "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "tfoot",
          "thead", "title", "tr", "ul"}
_CELL = {"td", "th"}
# Where a page's navigation sits: HTML's sectioning elements for it, and the ARIA roles that say the same.
_NAVIGATION_TAGS = {"nav", "header", "footer", "aside", "menu"}
_NAVIGATION_ROLES = {"navigation", "menu", "menubar", "menuitem", "banner", "contentinfo"}
NAVIGATION, LINK, BREAK = 1, 2, 4  # zone flags, OR-ed per character; BREAK marks a space that ends a cell or line
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def squash(text: str) -> str:
    return " ".join(text.split())


@functools.lru_cache(maxsize=1)
def pdftotext_version() -> str:
    run = subprocess.run([PDFTOTEXT, "-v"], capture_output=True, text=True, check=False)
    first = (run.stderr or run.stdout).splitlines()
    return first[0].strip() if first else "pdftotext (unknown version)"


def pdf_pages(data: bytes) -> list[str]:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
        handle.write(data)
        handle.flush()
        run = subprocess.run([PDFTOTEXT, "-layout", "-enc", "UTF-8", handle.name, "-"], capture_output=True, check=False)
    if run.returncode != 0:
        raise ValueError(f"pdftotext failed ({run.returncode}): {run.stderr.decode('utf-8', 'replace')[:300]}")
    text = run.stdout.decode("utf-8", errors="replace")
    pages = text.split("\f")
    if pages and pages[-1].strip() == "":
        pages = pages[:-1]
    return pages


class _TextReader(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.zones: list[int] = []        # per part: the zone flags it was read in
        self.starts: list[int] = []       # per part: (line, column) in the decoded HTML, or -1 for a break
        self.skip_depth = 0
        self.stack: list[str] = []
        self.open: list[tuple[str, int]] = []  # the open elements outside skipped ones, with their own zone flags

    @property
    def zone(self) -> int:
        flags = 0
        for _, own in self.open:
            flags |= own
        return flags

    def _add(self, text: str, flags: int = 0, data: bool = False) -> None:
        self.parts.append(text)
        self.zones.append(self.zone | flags)
        self.starts.append(self.getpos() if data else -1)

    def _close(self, tag: str) -> None:
        for index in range(len(self.open) - 1, -1, -1):
            if self.open[index][0] == tag:
                del self.open[index:]
                return

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if not self.skip_depth and tag not in _SKIP and tag not in _VOID:
            values = dict(attrs)
            own = NAVIGATION if tag in _NAVIGATION_TAGS or (values.get("role") or "").strip().lower() in _NAVIGATION_ROLES else 0
            if tag == "a":
                self._close("a")  # WHY: an <a> inside an open <a> ends it, as a browser reads it, so an unclosed
                # link cannot turn the rest of the page into link text
                own |= LINK if values.get("href") is not None else 0
            self.open.append((tag, own))
        if tag in _SKIP:
            if tag not in _VOID:
                self.skip_depth += 1
                self.stack.append(tag)
            return
        if self.skip_depth:
            if tag not in _VOID:
                self.stack.append(tag)
            return
        if tag in _BLOCK:
            self._add("\n", BREAK)
        elif tag in _CELL:
            self._add("\t", BREAK)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if not self.skip_depth:
            self._close(tag)
        if tag in self.stack:
            while self.stack:
                popped = self.stack.pop()
                if popped in _SKIP:
                    self.skip_depth = max(0, self.skip_depth - 1)
                if popped == tag:
                    break
            return
        if not self.skip_depth and (tag in _BLOCK or tag in _CELL):
            self._add("\n" if tag in _BLOCK else "\t", BREAK)

    def handle_data(self, data):
        if not self.skip_depth:
            self._add(data, data=True)


def _charset(data: bytes, content_type: str | None) -> str:
    match = re.search(r"charset=([A-Za-z0-9_.:-]+)", content_type or "")
    if not match:
        match = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_.:-]+)", data[:4096], re.IGNORECASE)
        if match:
            return match.group(1).decode("ascii", "replace")
        return "utf-8"
    return match.group(1)


def decode(data: bytes, content_type: str | None = None) -> str:
    try:
        return data.decode(_charset(data, content_type), errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _read(data: bytes, content_type: str | None) -> _TextReader:
    decoded = decode(data, content_type)
    reader = _TextReader()
    reader.feed(decoded)
    reader.close()
    return reader


def html_text(data: bytes, content_type: str | None = None) -> str:
    reader = _read(data, content_type)
    lines = []
    for line in "".join(reader.parts).split("\n"):
        cells = [" ".join(cell.split()) for cell in line.split("\t")]
        cells = [cell for cell in cells if cell]
        if cells:
            lines.append("\t".join(cells))
    return "\n".join(lines) + "\n"


def html_zones(data: bytes, content_type: str | None = None) -> tuple[str, bytes]:
    """The squashed page text (exactly `squash(html_text(...))`) and, per character, its zone flags
    (NAVIGATION, LINK; 0 is the page's own content). A space between two words takes the flags they share, plus
    BREAK where an element's edge (a block, a table cell) sits in it."""
    reader = _read(data, content_type)
    out: list[str] = []
    flags = bytearray()
    pending, in_word, word_zone, edge = False, False, 0, 0
    for part, zone in zip(reader.parts, reader.zones):
        for char in part:
            if char.isspace():
                if in_word:
                    pending, in_word, edge = True, False, 0
                edge |= zone & BREAK
                continue
            if pending:
                out.append(" ")
                flags.append((word_zone & zone & ~BREAK) | edge)
                pending = False
            out.append(char)
            flags.append(zone & ~BREAK)
            in_word, word_zone = True, zone
    return "".join(out), bytes(flags)


def html_offsets(data: bytes, content_type: str | None = None) -> tuple[str, list[int]]:
    """The squashed page text (exactly `squash(html_text(...))`) and, per character, its code-point offset in the
    decoded HTML (-1 for a space the reader put in). WHY approximate inside an entity: a data chunk's characters
    are counted from the chunk's start, so a '&amp;' before them shifts them by four; the harness pages have none,
    and a CubeMars page's spec cells rarely do."""
    decoded = decode(data, content_type)
    line_starts = [0]
    for match in re.finditer("\n", decoded):
        line_starts.append(match.end())
    reader = _read(data, content_type)
    out: list[str] = []
    offsets: list[int] = []
    pending, in_word = False, False
    for part, start in zip(reader.parts, reader.starts):
        base = -1 if start == -1 else line_starts[start[0] - 1] + start[1]
        for index, char in enumerate(part):
            if char.isspace():
                if in_word:
                    pending, in_word = True, False
                continue
            if pending:
                out.append(" ")
                offsets.append(-1)
                pending = False
            out.append(char)
            offsets.append(-1 if base == -1 else base + index)
            in_word = True
    return "".join(out), offsets


def pdf_words(path) -> list[list[dict]]:
    """Per page, the text layer's words: {text, box: [x0, y0, x1, y1] as 0-1 of the page, origin top-left}."""
    import pdfplumber

    pages = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            width, height = float(page.width), float(page.height)
            pages.append([{"text": word["text"], "box": [word["x0"] / width, word["top"] / height,
                                                          word["x1"] / width, word["bottom"] / height]}
                          for word in page.extract_words(keep_blank_chars=False, use_text_flow=True)])
    return pages


def layout_zones(page: str) -> bytes:
    """Zone flags for a page read from layout text (pdftotext -layout): no navigation or links to know of, only
    BREAK on a space that stood for a line end, a tab or a column gap (two spaces or more)."""
    flags = bytearray()
    for index, match in enumerate(re.finditer(r"(\s*)(\S+)", page.lstrip())):
        gap = match.group(1)
        if index:
            flags.append(BREAK if ("\n" in gap or "\t" in gap or "\f" in gap or "\r" in gap or len(gap) > 1) else 0)
        flags.extend(b"\0" * len(match.group(2)))
    return bytes(flags)


def pages_of(data: bytes, media_type: str, content_type: str | None = None) -> list[str]:
    """The page texts of one input, page 1 first."""
    if media_type == "application/pdf":
        return pdf_pages(data)
    if media_type == "text/html":
        return [html_text(data, content_type)]
    raise ValueError(f"no text reader for {media_type}")


def reader_name(media_type: str) -> str:
    return f"{pdftotext_version()} -layout" if media_type == "application/pdf" else "robotparts-ingest html reader (stdlib html.parser)"
