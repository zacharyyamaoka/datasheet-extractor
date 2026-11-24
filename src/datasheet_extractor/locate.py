"""Where a value's own words are: the index.json words of its cited block or cell that print its `raw`.

Option 1 cites a block or a table cell by id, and its `grounding` is that whole block's box or range, so a prose value
lit a whole paragraph (535 characters for one J60 page value). This narrows it to the words that print `raw`, from
index.json's word locations inside the block's range: no model call, nothing guessed. `raw` must be found among the
block's own words exactly once; printed twice there, or not at all, the value is *not located* and keeps only the
block's location (the same rule as the Source Highlighter's: several matches are reported, never picked from).

    raw_grounding(bundle, block, raw)  -> [Location, ...] (one per line of the value's words) | None
    where(bundle, row)                 -> Where: a source_msgs compact selector for the value, and how it was found
    label_text(bundle, block_ids)      -> the label as the source prints it (a Stated's `as_written`)

WHY (Zach, 2026-11-24): "when I click that I want to basically be taken directly not just to the page, but see
highlighted the exact text that you got that information from. That is how you create a fully linked datasheet all the
way back to the source."
"""

from __future__ import annotations

import codecs
import dataclasses
import html
import math
import pathlib
import re
from html.parser import HTMLParser

from source_msgs import PageBox, TextPosition, TextQuote, XPath

from . import option1

# A value's words on one line overlap vertically by at least this share of the shorter word's height.
SAME_LINE = 0.5
# Words of context tried on each side of an HTML quote before it falls back to the pinned byte range (char=).
MAX_CONTEXT_WORDS = 12


@dataclasses.dataclass(frozen=True)
class Where:
    """A value's location as one source_msgs compact selector, and what it marks.

    - `selector`: `page=N&xywh=percent:x,y,w,h` (PDF, image), `text=prefix-,exact,-suffix` (a web page), `char=a,b`
      (a web page whose quote is not unique: code points into the pinned bytes), `xpath=…` (a web page's cited cell),
      or None (nothing to point at).
    - `marks`: `"words"` (exactly the words that print raw), `"cell"` (the cited cell or block only: its words were
      not located), or `"none"`.
    - `note`: why it is not the words, or what to know about them, in plain words; None when there is nothing to say.
    """

    selector: str | None
    marks: str
    note: str | None = None


# --- index.json words -------------------------------------------------------------------------------------------

def _grounding(word: dict) -> dict:
    """A word's grounding is one Location (CONTRACT §3.2); a list is read as its first."""
    found = word.get("grounding")
    if isinstance(found, list):
        found = found[0] if found else {}
    return found or {}


def block_words(bundle: option1.Bundle, block: dict) -> list[dict]:
    """index.json's words inside `block`'s markdown range, in markdown order."""
    span = block.get("range")
    if not span:
        return []
    return [word for word in _words(bundle) if span["start"] <= word["range"]["start"] and word["range"]["end"] <= span["end"]]


def _words(bundle: option1.Bundle) -> list[dict]:
    cached = bundle.__dict__.get("_sorted_words")
    if cached is None:
        cached = sorted(bundle.index.get("words") or [], key=lambda word: word["range"]["start"])
        bundle.__dict__["_sorted_words"] = cached
    return cached


def _glued(text: str, start: int, end: int) -> bool:
    """The match [start, end) is part of a longer number or word ('05' in '1.05', 'V' in 'kV'), not text of its own."""
    def joined(a: str, b: str) -> bool:
        return (a.isdigit() and b.isdigit()) or (a.isascii() and b.isascii() and a.isalpha() and b.isalpha())
    first, last = text[start], text[end - 1]
    if start > 0 and joined(text[start - 1], first):
        return True
    if end < len(text) and joined(last, text[end]):
        return True
    if first.isdigit() and start >= 2 and text[start - 1] in ".," and text[start - 2].isdigit():
        return True
    return last.isdigit() and end + 1 < len(text) and text[end] in ".," and text[end + 1].isdigit()


def occurrences(bundle: option1.Bundle, block: dict, raw: str) -> list[list[dict]]:
    """Each place the block's words print `raw` (read as every checker reads it: `option1.plain`), as the list of
    words it covers. A word that holds raw only in part ('Ø51×24.5' for raw '51') is covered whole."""
    needle = option1.plain(raw or "")
    words = block_words(bundle, block)
    if not needle or not words:
        return []
    text, owner = "", []
    for index, word in enumerate(words):
        printed = option1.plain(bundle.markdown[word["range"]["start"]:word["range"]["end"]])
        if not printed:
            continue
        if text:
            text += " "
            owner.append(None)
        text += printed
        owner += [index] * len(printed)
    found = []
    for match in re.finditer(re.escape(needle), text):
        if _glued(text, match.start(), match.end()):
            continue
        covered = sorted({owner[at] for at in range(match.start(), match.end()) if owner[at] is not None})
        found.append([words[index] for index in covered])
    return found


# --- paged sources (PDF, image) -----------------------------------------------------------------------------------

def _same_line(a: list[float], b: list[float]) -> bool:
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    return overlap >= SAME_LINE * min(a[3] - a[1], b[3] - b[1])


def _line_boxes(words: list[dict]) -> list[dict]:
    """One `{page, box}` per line the words sit on, each the union of that line's word boxes."""
    lines: list[dict] = []
    for word in words:
        location = _grounding(word)
        page, box = location.get("page"), location.get("box")
        if page is None or not box:
            continue
        if lines and lines[-1]["page"] == page and _same_line(lines[-1]["box"], box):
            old = lines[-1]["box"]
            lines[-1]["box"] = [min(old[0], box[0]), min(old[1], box[1]), max(old[2], box[2]), max(old[3], box[3])]
        else:
            lines.append({"page": page, "box": list(box)})
    return lines


def _page_box(boxes: list[dict]) -> PageBox:
    """The PageBox around `boxes` (all on the first one's page), rounded outward to 0.01 % so it still covers every
    word. WHY outward: a box rounded to the nearest 0.01 % can cut a glyph's edge off."""
    page = boxes[0]["page"]
    on_page = [location["box"] for location in boxes if location["page"] == page]
    x0, y0 = min(box[0] for box in on_page), min(box[1] for box in on_page)
    x1, y1 = max(box[2] for box in on_page), max(box[3] for box in on_page)
    x0, y0 = math.floor(x0 * 1e4) / 1e4, math.floor(y0 * 1e4) / 1e4
    x1, y1 = min(1.0, math.ceil(x1 * 1e4) / 1e4), min(1.0, math.ceil(y1 * 1e4) / 1e4)
    return PageBox(page, max(0.0, x0), max(0.0, y0), x1 - max(0.0, x0), y1 - max(0.0, y0))


# --- web pages ----------------------------------------------------------------------------------------------------

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
_TAG_OR_COMMENT = re.compile(r"<!--.*?-->|<[^>]*>", re.S)


def _charset(data: bytes) -> str:
    """BOM, then <meta charset> in the first 4 KiB, then UTF-8. WHY markdownify's very order (markdownify/html.py
    `_charset`): every `source_range` counts code points in the string it decoded, so this must decode the same."""
    for bom, name in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(bom):
            return name
    match = _META_CHARSET.search(data[:4096])
    if match:
        name = match.group(1).decode("ascii", "replace")
        try:
            codecs.lookup(name)
            return name
        except LookupError:
            pass
    return "utf-8"


def text_of_range(decoded: str, source_range: dict) -> str:
    """The text at `source_range` as a reader sees it: tags and comments out, entities decoded, whitespace squashed
    (markdownify's `text_of_range`)."""
    fragment = decoded[source_range["start"]:source_range["end"]]
    return " ".join(html.unescape(_TAG_OR_COMMENT.sub("", fragment)).split())


class _TextNodes(HTMLParser):
    """The body's text nodes concatenated, scripts, styles and the head left out: the string the Source
    Highlighter's `pageText` searches a quote in (js/source-highlighter.js), so a quote unique here is unique there."""

    SKIP = {"script", "style", "noscript", "template", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping.append(tag)
        elif tag == "body":
            self.skipping = []

    def handle_endtag(self, tag):
        if tag in self.skipping:
            while self.skipping and self.skipping.pop() != tag:
                pass

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


@dataclasses.dataclass
class Page:
    """A web page's decoded HTML and its rendered text, for building and checking text quotes."""

    decoded: str
    text: str

    @classmethod
    def load(cls, path: pathlib.Path) -> "Page":
        data = pathlib.Path(path).read_bytes()
        decoded = data.decode(_charset(data), errors="replace")
        parser = _TextNodes()
        parser.feed(decoded)
        parser.close()
        return cls(decoded, "".join(parser.parts))

    def count(self, exact: str, prefix: str = "", suffix: str = "") -> int:
        """How many places print `exact` with this prefix and suffix around it, whitespace-insensitive in the context
        (the highlighter's `quoteMatches`)."""
        before, after = re.sub(r"\s+", "", prefix), re.sub(r"\s+", "", suffix)
        found, at = 0, self.text.find(exact)
        while at != -1:
            if (not before or _nonspace_before(self.text, at, len(before)).endswith(before)) and \
                    (not after or _nonspace_after(self.text, at + len(exact), len(after)).startswith(after)):
                found += 1
            at = self.text.find(exact, at + 1)
        return found


def _nonspace_before(text: str, at: int, count: int) -> str:
    collected = []
    index = at - 1
    while index >= 0 and len(collected) < count:
        if not text[index].isspace():
            collected.append(text[index])
        index -= 1
    return "".join(reversed(collected))


def _nonspace_after(text: str, at: int, count: int) -> str:
    collected = []
    index = at
    while index < len(text) and len(collected) < count:
        if not text[index].isspace():
            collected.append(text[index])
        index += 1
    return "".join(collected)


def source_file(bundle: option1.Bundle) -> pathlib.Path | None:
    """The bundle's `source.<ext>` (CONTRACT §1), or None."""
    found = sorted(bundle.directory.glob("source.*"))
    return next((path for path in found if path.suffix != ".json"), None)


def _page(bundle: option1.Bundle) -> Page | None:
    """The bundle's web page, loaded once per bundle; None when the bundle kept no HTML."""
    if "_page" not in bundle.__dict__:
        path = source_file(bundle)
        bundle.__dict__["_page"] = Page.load(path) if path is not None and path.suffix.lower() in (".html", ".htm") else None
    return bundle.__dict__["_page"]


def _quote_of(word: dict, bundle: option1.Bundle) -> dict:
    found = (_grounding(word).get("html") or {}).get("text_quote") or {}
    if found.get("exact"):
        return found
    return {"exact": option1.plain(bundle.markdown[word["range"]["start"]:word["range"]["end"]]), "prefix": "", "suffix": ""}


def _html_location(bundle: option1.Bundle, block: dict, words: list[dict]) -> tuple[dict | None, bool]:
    """One `html` Location for the value's words, and whether its text quote was checked to match exactly one place
    on the page. The Location holds the byte range from the first word's start to the last word's end, the xpath they
    share (else the cited cell's), and a text quote whose prefix/suffix are grown, word by word across the page, until
    it matches exactly one place. A quote that cannot be made unique is left out (the highlighter would refuse it as
    ambiguous); the byte range still marks the words in the saved file."""
    first, last = (_grounding(word).get("html") or {} for word in (words[0], words[-1]))
    if not first.get("source_range") or not last.get("source_range"):
        return None, False
    source_range = {"start": first["source_range"]["start"], "end": last["source_range"]["end"]}
    xpaths = {(_grounding(word).get("html") or {}).get("xpath") for word in words}
    cell = next((location.get("html", {}).get("xpath") for location in block.get("grounding") or []
                 if isinstance(location, dict) and location.get("html")), None)
    xpath = next(iter(xpaths)) if len(xpaths) == 1 and None not in xpaths else cell
    location = {"html": {"source_range": source_range, **({"xpath": xpath} if xpath else {})}}
    page = _page(bundle)
    own = [_quote_of(word, bundle)["exact"] for word in words]
    quote = {"exact": " ".join(own), "prefix": _quote_of(words[0], bundle).get("prefix", ""),
             "suffix": _quote_of(words[-1], bundle).get("suffix", "")}
    if page is None:
        # no saved page to check against: markdownify's own quote, unchecked (the byte range wins when resolved)
        location["html"]["text_quote"] = quote
        return location, False
    # WHY checked word by word: a page decoded differently from markdownify's string would shift every range;
    # then the words' stored quotes, not the bytes, are what is trusted
    if all(text_of_range(page.decoded, (_grounding(word).get("html") or {})["source_range"]) == text
           for word, text in zip(words, own)):
        quote["exact"] = text_of_range(page.decoded, source_range)
    unique = page.count(quote["exact"], quote["prefix"], quote["suffix"]) == 1
    if not unique:
        # WHY across the cell's edge: a table value's quote has no context inside its own cell ('24 V' alone), and
        # the same '24 V' is printed in the intro and in every model column; the row label before it and the cells
        # after it are what make it one place
        order = sorted((word for word in _words(bundle) if (_grounding(word).get("html") or {}).get("source_range")),
                       key=lambda word: _grounding(word)["html"]["source_range"]["start"])
        position = {id(word): index for index, word in enumerate(order)}
        if id(words[0]) in position and id(words[-1]) in position:
            at, to = position[id(words[0])], position[id(words[-1])]
            texts = [_quote_of(word, bundle)["exact"] for word in order]
            for count in range(1, MAX_CONTEXT_WORDS + 1):
                prefix = " ".join(texts[max(0, at - count):at])
                suffix = " ".join(texts[to + 1:to + 1 + count])
                if page.count(quote["exact"], prefix, suffix) == 1:
                    quote.update({"prefix": prefix + " " if prefix else "", "suffix": " " + suffix if suffix else ""})
                    unique = True
                    break
    if unique:
        location["html"]["text_quote"] = quote
    return location, unique


# --- the value's words -----------------------------------------------------------------------------------------------

def paged(bundle: option1.Bundle) -> bool:
    return bool(bundle.index.get("pages"))


def locate(bundle: option1.Bundle, block: dict | None, raw: str | None) -> tuple[list[dict] | None, bool]:
    """(`raw_grounding`, whether a web page's text quote was checked unique on the saved page)."""
    if block is None:
        return None, False
    found = occurrences(bundle, block, raw or "")
    if len(found) != 1:
        return None, False
    if paged(bundle):
        return _line_boxes(found[0]) or None, False
    location, unique = _html_location(bundle, block, found[0])
    return ([location] if location else None), unique


def raw_grounding(bundle: option1.Bundle, block: dict | None, raw: str | None) -> list[dict] | None:
    """The Locations of the words that print `raw` in `block`: for a PDF or an image one `{page, box}` per line they
    sit on, for a web page one `html` Location. None when raw is not among the block's words, or printed there more
    than once (which one the value is cannot be told, so neither is marked)."""
    return locate(bundle, block, raw)[0]


def not_located(bundle: option1.Bundle | None, block: dict | None, raw: str | None) -> str:
    """Why a value's own words are not marked, in plain words."""
    if bundle is None or block is None:
        return "its words were not located (no index of the source's words)"
    count = len(occurrences(bundle, block, raw or ""))
    kind = "cell" if block.get("type") == "table_cell" else "block"
    if count > 1:
        return f"the cited {kind} prints {raw!r} {count} times, so the {kind} is marked, not one of them"
    if not block_words(bundle, block):
        return f"the source has no word index here, so the cited {kind} is marked"
    return f"{raw!r} was not found among the cited {kind}'s words, so the {kind} is marked"


def selector(locations: list[dict], quote_checked: bool = False) -> Where:
    """The value's words as one compact selector (`raw_grounding`'s Locations, all one occurrence). A web page's words
    are a text quote only when it was checked to match one place on the saved page (`quote_checked`), else their byte
    range in the saved file (exact on the bytes the source file's sha256 pins)."""
    if "html" in locations[0]:
        location = locations[0]["html"]
        quote = location.get("text_quote") or {}
        if quote.get("exact") and quote_checked:
            return Where(TextQuote(quote["exact"], quote.get("prefix", ""), quote.get("suffix", "")).compact(), "words")
        span = location["source_range"]
        return Where(TextPosition(span["start"], span["end"]).compact(), "words",
                     "its words are printed elsewhere on the page too, so it is pinned by position in the saved file")
    lines = [location for location in locations if location.get("page") == locations[0].get("page")]
    note = f"its words wrap over {len(lines)} lines; the box spans them" if len(lines) > 1 else None
    return Where(_page_box(lines).compact(), "words", note)


def cell_selector(grounding: list | dict | None) -> str | None:
    """The cited block's own location as a compact selector: its box (all its pieces on the first page) or its xpath."""
    grounding = grounding if isinstance(grounding, list) else [grounding] if grounding else []
    boxes = [location for location in grounding if isinstance(location, dict) and location.get("box")]
    if boxes:
        return _page_box(boxes).compact()
    for location in grounding:
        if isinstance(location, dict) and (location.get("html") or {}).get("xpath"):
            return XPath(location["html"]["xpath"]).compact()
        if isinstance(location, dict) and (location.get("html") or {}).get("source_range"):
            span = location["html"]["source_range"]
            return TextPosition(span["start"], span["end"]).compact()
    return None


def where(row: dict, bundle: option1.Bundle | None = None) -> Where:
    """Where one values.json (or effective-record) row is printed: its own words when they were located (the row's
    `raw_grounding`, else found now in `bundle`), else its cited block, else nothing. A human-added row with its own
    grounding counts as its words."""
    cite_id = (row.get("cite") or {}).get("id")
    block = bundle.blocks.get(cite_id) if bundle is not None and cite_id else None
    locations, checked = row.get("raw_grounding"), False
    if block is not None and not row.get("human"):
        # WHY found again when the bundle is here: the row's raw may have been edited since extraction, and only the
        # saved page can say whether a quote is unique
        locations, checked = locate(bundle, block, row.get("raw"))
    if locations:
        return selector(locations, checked)
    if row.get("human"):
        own = cell_selector(row.get("grounding"))
        return Where(own, "words") if own else Where(None, "none", "added by hand with no location")
    cell = cell_selector(row.get("grounding"))
    if cell is None:
        return Where(None, "none", "no location was recorded")
    return Where(cell, "cell", not_located(bundle, block, row.get("raw")))


# --- labels -----------------------------------------------------------------------------------------------------------

def _unescape(text: str) -> str:
    """A markdown word as the source prints it: escapes and entities undone, no NFKC (a Stated's `as_written` is the
    source's own spelling)."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\\([^\w\s])", r"\1", text).replace("**", "").replace("`", "")
    return html.unescape(text)


def verbatim(bundle: option1.Bundle, block: dict) -> str:
    """The block's text as the source prints it: a web page's own characters (the words' text quotes), a PDF's or an
    image's words as read; the block's printed text when the bundle has no word index."""
    words = block_words(bundle, block)
    if not words:
        return bundle.printed(block)
    if not paged(bundle):
        pieces, previous = [], None
        for word in words:
            location = _grounding(word).get("html") or {}
            text = _quote_of(word, bundle)["exact"]
            span = location.get("source_range")
            glued = previous is not None and span is not None and span["start"] == previous
            pieces.append(("" if glued or not pieces else " ") + text)
            previous = span["end"] if span else None
        return " ".join("".join(pieces).split())
    return " ".join(" ".join(_unescape(bundle.markdown[w["range"]["start"]:w["range"]["end"]]) for w in words).split())


def label_text(bundle: option1.Bundle, block_ids: list[str]) -> str | None:
    """The label as printed, from the cells or block the checker read it in (a header path joined by ' / ')."""
    texts = [verbatim(bundle, bundle.blocks[block_id]) for block_id in block_ids if block_id in bundle.blocks]
    texts = [text for text in texts if text]
    return " / ".join(texts) or None


def attach(verdicts: list, bundle: option1.Bundle) -> None:
    """Give each kept verdict its own words' Locations (`raw_grounding`, None when not located) and its label as
    printed (`cite["label"]`), read from the cells the checker named (`cite["label_ids"]`, or a prose `label_id`)."""
    for verdict in verdicts:
        cite = verdict.cite or {}
        ids = cite.get("label_ids") or ([cite["label_id"]] if cite.get("label_id") else [])
        if ids and not cite.get("label"):
            label = label_text(bundle, ids)
            if label:
                cite["label"] = label
        if verdict.kept:
            verdict.raw_grounding = raw_grounding(bundle, bundle.blocks.get(cite.get("id")), verdict.raw)
            verdict.located = True
