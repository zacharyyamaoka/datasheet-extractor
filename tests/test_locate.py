"""A value's own words (locate.py) and the `where` Copy as Spec writes for them, on recorded model replies replayed
through the checker (no model call) over real Markdownify bundles of the synthetic datasheets
(tests/fixtures/bundles/README.md): a PDF table, an image of the same table, a web page with the table, a web page
whose '24 V' is printed twice, and a prose paragraph.

Every `where` must parse with source_msgs, and what it marks must be exactly the value's words:
- a PDF box is checked against the PDF's own text layer (pdfplumber, an oracle independent of Markdownify's index);
- an image box against the bundle's word boxes (the image has no text layer);
- a web page quote against the saved page's text, read here with a separate tag-stripping reader.
"""

from __future__ import annotations

import html
import json
import pathlib
import re
import unicodedata

import pdfplumber
import pytest
from source_msgs import PageBox, TextQuote, parse

from datasheet_extractor import fields as fields_module
from datasheet_extractor import locate, option1

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUNDLES = ROOT / "tests" / "fixtures" / "bundles"
FIELDS = fields_module.load(ROOT / "fields" / "actuator.json")


def _replayed(name: str):
    """(bundle, kept verdicts) of a fixture: its recorded model reply through today's checker."""
    directory = BUNDLES / name
    case = json.loads((directory / "case.json").read_text(encoding="utf-8"))
    bundle = option1.Bundle.load(directory)
    reply = json.loads((directory / "reply.json").read_text(encoding="utf-8"))
    verdicts, _ = option1.judge(reply, FIELDS, bundle, case["part_model"])
    return bundle, [verdict for verdict in verdicts if verdict.kept]


def _squash(text: str) -> str:
    """Compared as printed glyphs: NFKC, no whitespace."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _pdf_text_in(pdf_path: pathlib.Path, box: PageBox) -> str:
    """The PDF text layer's characters whose centre lies inside the box, in reading order."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[box.page - 1]
        x0, y0, x1, y1 = (box.x * page.width, box.y * page.height, (box.x + box.w) * page.width,
                          (box.y + box.h) * page.height)
        chars = [char for char in page.chars
                 if x0 <= (char["x0"] + char["x1"]) / 2 <= x1 and y0 <= (char["top"] + char["bottom"]) / 2 <= y1]
        return "".join(char["text"] for char in sorted(chars, key=lambda c: (round(c["top"]), c["x0"])))


def _words_in(bundle: option1.Bundle, box: PageBox) -> list[str]:
    """The bundle's words whose box centre lies inside `box`, in markdown order."""
    out = []
    for word in bundle.index["words"]:
        grounding = word["grounding"]
        if grounding.get("page") != box.page:
            continue
        x0, y0, x1, y1 = grounding["box"]
        if box.x <= (x0 + x1) / 2 <= box.x + box.w and box.y <= (y0 + y1) / 2 <= box.y + box.h:
            out.append(bundle.markdown[word["range"]["start"]:word["range"]["end"]])
    return out


def _where(bundle, verdict) -> locate.Where:
    return locate.where(verdict.to_dict(), bundle)


# --- a PDF table value -----------------------------------------------------------------------------------------------

def test_a_pdf_table_value_is_boxed_on_exactly_its_own_printed_characters():
    bundle, kept = _replayed("12-full-datasheet-pdf")
    table_values = [verdict for verdict in kept if verdict.cite["id"].startswith("table_cell")]
    assert len(table_values) == 6
    for verdict in table_values:
        where = _where(bundle, verdict)
        assert where.marks == "words" and where.note is None
        box = parse(where.selector)
        assert isinstance(box, PageBox) and box.page == 1
        assert _squash(_pdf_text_in(BUNDLES / "12-full-datasheet-pdf" / "source.pdf", box)) == _squash(verdict.raw), \
            (verdict.key, where.selector)
        # within the cited cell's column, and its centre on the cell's line (a glyph box may rise above the cell's)
        x0, y0, x1, y1 = bundle.blocks[verdict.cite["id"]]["grounding"][0]["box"]
        assert x0 - 1e-4 <= box.x and box.x + box.w <= x1 + 1e-4 and y0 <= box.y + box.h / 2 <= y1


def test_the_label_is_the_row_label_as_the_pdf_prints_it():
    bundle, kept = _replayed("12-full-datasheet-pdf")
    labels = {verdict.key: verdict.cite.get("label") for verdict in kept}
    assert labels["nominal_voltage"] == "Rated voltage" and labels["mass"] == "Weight"
    # the footnote mark is printed after 'Rated torque', and as_written keeps what is printed
    assert labels["rated_torque"].startswith("Rated torque")


# --- an image value --------------------------------------------------------------------------------------------------

def test_an_image_value_is_boxed_on_exactly_its_own_words():
    bundle, kept = _replayed("12-full-datasheet-png")
    table_values = [verdict for verdict in kept if verdict.cite["id"].startswith("table_cell")]
    assert len(table_values) == 6
    for verdict in table_values:
        where = _where(bundle, verdict)
        box = parse(where.selector)
        assert isinstance(box, PageBox) and where.marks == "words"
        assert _squash("".join(_words_in(bundle, box))) == _squash(verdict.raw), (verdict.key, where.selector)


# --- a web page value ------------------------------------------------------------------------------------------------

def _page_text(path: pathlib.Path) -> str:
    """The body's text, read independently of locate.Page: scripts, styles and tags stripped by pattern, entities
    decoded. Every tag counts as nothing (the highlighter compares a quote's context without whitespace)."""
    document = path.read_text(encoding="utf-8")
    body = re.search(r"<body[^>]*>(.*)</body>", document, re.S | re.I)
    body = body.group(1) if body else document
    body = re.sub(r"<(script|style|noscript|template)\b.*?</\1\s*>", "", body, flags=re.S | re.I)
    return html.unescape(re.sub(r"<!--.*?-->|<[^>]*>", "", body, flags=re.S))


def _places(text: str, quote: TextQuote) -> int:
    """Places `quote.exact` is printed with its prefix right before and its suffix right after, whitespace aside."""
    flat = re.sub(r"\s+", "", text)
    needle = re.sub(r"\s+", "", quote.prefix + quote.exact + quote.suffix)
    return sum(1 for at in range(len(flat)) if flat.startswith(needle, at))


@pytest.mark.parametrize("name", ["12-full-datasheet-html", "03-simple-table-html"])
def test_a_web_page_value_is_one_quote_of_its_own_words_printed_once_on_the_page(name):
    bundle, kept = _replayed(name)
    text = _page_text(BUNDLES / name / "source.html")
    table_values = [verdict for verdict in kept if verdict.cite["id"].startswith("table_cell")]
    assert len(table_values) >= 6
    for verdict in table_values:
        where = _where(bundle, verdict)
        quote = parse(where.selector)
        assert isinstance(quote, TextQuote) and where.marks == "words", (verdict.key, where)
        assert _squash(quote.exact) == _squash(verdict.raw)
        assert _places(text, quote) == 1, (verdict.key, where.selector)
        # the stored Location's byte range holds the same words in the saved file
        span = verdict.raw_grounding[0]["html"]["source_range"]
        decoded = (BUNDLES / name / "source.html").read_text(encoding="utf-8")
        assert locate.text_of_range(decoded, span) == quote.exact


def test_a_value_printed_twice_on_the_page_gets_the_context_that_makes_it_one_place():
    # '24 V' is in the intro ('All values at 24 V and 25 °C') and in the Rated voltage row
    bundle, kept = _replayed("03-simple-table-html")
    text = _page_text(BUNDLES / "03-simple-table-html" / "source.html")
    assert _places(text, TextQuote("24 V")) == 2
    voltage = next(verdict for verdict in kept if verdict.key == "nominal_voltage")
    quote = parse(_where(bundle, voltage).selector)
    assert quote.exact == "24 V" and (quote.prefix or quote.suffix) and _places(text, quote) == 1
    assert "voltage" in quote.prefix.lower() or "rated" in quote.suffix.lower()


# --- prose ----------------------------------------------------------------------------------------------------------

def test_a_prose_value_is_narrowed_from_its_paragraph_to_its_words():
    bundle, kept = _replayed("01-prose-spec-pdf")
    torque = next(verdict for verdict in kept if verdict.key == "rated_torque")
    paragraph = bundle.blocks[torque.cite["id"]]
    assert paragraph["type"] == "paragraph"
    box = parse(_where(bundle, torque).selector)
    assert _squash(_pdf_text_in(BUNDLES / "01-prose-spec-pdf" / "source.pdf", box)) == _squash(torque.raw)
    x0, y0, x1, y1 = paragraph["grounding"][0]["box"]
    assert box.w * box.h < 0.25 * (x1 - x0) * (y1 - y0)        # a phrase, not the paragraph


# --- not located: said plainly, never guessed -------------------------------------------------------------------------

def _tiny_bundle(text: str) -> option1.Bundle:
    """One paragraph, one word per whitespace-separated token, each with its own box on page 1."""
    words, blocks, start = [], [], 0
    for index, token in enumerate(text.split(" ")):
        words.append({"range": {"start": start, "end": start + len(token)},
                      "grounding": {"page": 1, "box": [0.1 + 0.05 * index, 0.2, 0.14 + 0.05 * index, 0.22]}})
        start += len(token) + 1
    blocks.append({"id": "paragraph-1", "type": "paragraph", "range": {"start": 0, "end": len(text)},
                   "grounding": [{"page": 1, "box": [0.1, 0.2, 0.9, 0.22]}]})
    return option1.Bundle(pathlib.Path("."), text, {"pages": [{"page": 1}], "blocks": blocks, "words": words},
                          {block["id"]: block for block in blocks})


def test_raw_printed_twice_in_its_block_marks_the_block_and_says_why():
    bundle = _tiny_bundle("Communication: CAN 2.0B, 通信接口: CAN 2.0B")
    block = bundle.blocks["paragraph-1"]
    assert locate.raw_grounding(bundle, block, "CAN 2.0B") is None
    row = {"id": "buses@paragraph-1", "key": "buses", "raw": "CAN 2.0B", "cite": {"id": "paragraph-1"},
           "grounding": block["grounding"]}
    where = locate.where(row, bundle)
    assert where.marks == "cell" and "2 times" in where.note
    assert parse(where.selector) == PageBox(1, 0.1, 0.2, 0.8, 0.02)


def test_a_number_inside_a_longer_number_is_not_found():
    bundle = _tiny_bundle("Rated torque 1.05 N·m")
    block = bundle.blocks["paragraph-1"]
    assert locate.raw_grounding(bundle, block, "05") is None
    located = locate.raw_grounding(bundle, block, "1.05 N·m")
    assert len(located) == 1 and located[0]["box"] == pytest.approx([0.2, 0.2, 0.29, 0.22])


def test_a_value_with_no_location_at_all_has_no_selector():
    where = locate.where({"id": "x", "key": "kv", "raw": "100", "cite": {"id": "gone"}, "grounding": []}, None)
    assert where.selector is None and where.marks == "none"


def test_a_human_row_with_its_own_box_is_marked_by_it():
    row = {"id": "h1", "key": "kv", "raw": "100", "human": True, "grounding": [{"page": 2, "box": [0.1, 0.1, 0.2, 0.2]}]}
    assert locate.where(row, None) == locate.Where("page=2&xywh=percent:10,10,10,10", "words")


def test_a_values_json_written_before_raw_grounding_is_located_from_its_bundle():
    """values.json files extracted before 2026-11-24 have no raw_grounding: Copy as Spec finds the words then."""
    directory = BUNDLES / "12-full-datasheet-pdf"
    recorded = json.loads((directory / "values.json").read_text(encoding="utf-8"))
    bundle = option1.Bundle.load(directory)
    row = next(row for row in recorded["values"] if row["key"] == "peak_torque")
    assert "raw_grounding" not in row
    box = parse(locate.where(row, bundle).selector)
    assert _squash(_pdf_text_in(directory / "source.pdf", box)) == _squash(row["raw"])


def test_values_json_carries_the_words_beside_the_cell():
    bundle, kept = _replayed("12-full-datasheet-pdf")
    row = next(verdict for verdict in kept if verdict.key == "mass").to_dict()
    assert row["grounding"] and row["raw_grounding"] and row["raw_grounding"] != row["grounding"]
    assert list(row).index("raw_grounding") == list(row).index("grounding") + 1
