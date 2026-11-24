"""A value's own words (locate.py) and the `where` Copy as Spec writes for them, on recorded model replies replayed
through the checker (no model call) over real Markdownify bundles of the synthetic datasheets
(tests/fixtures/bundles/README.md): a PDF table, an image of the same table, an image of a spec list, a web page with
the table, a web page whose '24 V' is printed twice, and a prose paragraph.

Every `where` must parse with source_msgs, and what it marks must be exactly the value's words:
- a PDF box is checked against the PDF's own text layer (pdfplumber, an oracle independent of Markdownify's index);
- an image box against its twin PDF's text layer (the harness printed the PDF and took the image from one layout) and
  against the image's own ink (no edge runs through a glyph); the bundle's word boxes are OCR's estimates, so they
  are no oracle for it;
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


# --- an image value inside an OCR line: measured on the page, never a character-count slice -------------------------
#
# OCR boxes whole lines, and Markdownify gives each word its slice of the line in proportion to its characters: an
# estimate that cut the 'C' off 'CAN H' in 'Yellow: CAN H' (independent check, 2026-11-24). Two oracles here, neither
# of them Markdownify's index: the same page's PDF text layer (the harness printed the PDF and took the PNG from one
# layout, so their 0-1 coordinates agree: tests/fixtures/bundles/README.md), and the PNG's own ink columns.

def _ink_columns(image_path: pathlib.Path, box: PageBox) -> tuple[list[bool], int]:
    """(inked?, per column across the whole page width, in the box's rows) and the page width: a dark pixel (< 128)
    on the synthetic sheets' white paper is ink."""
    from PIL import Image
    with Image.open(image_path) as picture:
        grey = picture.convert("L")
        width, height = grey.size
        top, bottom = int(box.y * height), int((box.y + box.h) * height) + 1
        pixels = grey.crop((0, top, width, bottom)).tobytes()
    rows = bottom - top
    return [any(pixels[row * width + column] < 128 for row in range(rows)) for column in range(width)], width


def _cuts_a_glyph(image_path: pathlib.Path, box: PageBox) -> list[str]:
    """Which edge of the box runs through ink: the column just inside it and the one just outside both inked."""
    columns, width = _ink_columns(image_path, box)
    left, right = int(box.x * width), int((box.x + box.w) * width)
    cut = []
    if left > 0 and columns[left] and columns[left - 1]:
        cut.append("left")
    if right < width - 1 and columns[right] and columns[right - 1]:
        cut.append("right")
    return cut


def test_a_value_inside_an_ocr_line_is_boxed_on_its_own_ink():
    bundle, kept = _replayed("12-full-datasheet-png")
    buses = next(verdict for verdict in kept if verdict.key == "buses")
    assert buses.raw == "CAN H" and bundle.printed(bundle.blocks[buses.cite["id"]]).endswith("Yellow: CAN H")
    where = _where(bundle, buses)
    box = parse(where.selector)
    assert isinstance(box, PageBox) and where.marks == "words", where
    twin = BUNDLES / "12-full-datasheet-pdf" / "source.pdf"
    assert _squash(_pdf_text_in(twin, box)) == "CANH", (where.selector, _pdf_text_in(twin, box))
    assert _cuts_a_glyph(BUNDLES / "12-full-datasheet-png" / "source.png", box) == [], where.selector
    # values.json keeps the same measured box
    assert parse(locate.selector(buses.raw_grounding).selector) == box


def test_every_image_value_is_boxed_on_exactly_the_characters_the_page_prints():
    """Every kept value of the image, table cells and list item alike, against the twin PDF's text layer."""
    bundle, kept = _replayed("12-full-datasheet-png")
    twin = BUNDLES / "12-full-datasheet-pdf" / "source.pdf"
    for verdict in kept:
        if verdict.key.startswith("part."):
            continue
        where = _where(bundle, verdict)
        box = parse(where.selector)
        assert where.marks == "words", (verdict.key, where)
        assert _squash(_pdf_text_in(twin, box)) == _squash(verdict.raw), (verdict.key, where.selector)
        assert _cuts_a_glyph(BUNDLES / "12-full-datasheet-png" / "source.png", box) == [], (verdict.key, where.selector)


TWINS = {"12-full-datasheet-png": BUNDLES / "12-full-datasheet-pdf" / "source.pdf",
         "02-spec-list-png": BUNDLES / "02-spec-list-png" / "twin.pdf"}


@pytest.mark.parametrize("name", ["02-spec-list-png", "12-full-datasheet-png"])
def test_no_image_value_box_misses_or_borrows_a_glyph(name):
    """02-spec-list's values sit inside OCR lines ('Rated torque: 3 N·m'): before the ink was read, 5 of its 10 boxes
    cut a glyph ('76 mm' marked '6 mm')."""
    bundle, kept = _replayed(name)
    values = [verdict for verdict in kept if not verdict.key.startswith("part.")]
    assert len(values) >= 7
    for verdict in values:
        where = _where(bundle, verdict)
        box = parse(where.selector)
        assert where.marks == "words", (verdict.key, where)
        printed = _squash(_pdf_text_in(TWINS[name], box))
        if where.note and "prints it inside" in where.note:
            # raw is part of a longer printed word ('CAN 2.0B,'): that whole word, no more
            assert _squash(verdict.raw) in printed and printed in _squash(where.note), (verdict.key, printed, where.note)
        else:
            assert printed == _squash(verdict.raw).replace("–", "-") or printed == _squash(verdict.raw), \
                (verdict.key, where.selector, printed)
        assert _cuts_a_glyph(BUNDLES / name / "source.png", box) == [], (verdict.key, where.selector)


def test_a_scanned_pdf_is_measured_on_its_rendered_page(tmp_path):
    """A PDF with no text layer (DEEP Robotics' J60 sheet is one) is OCR'd like an image: the same bundle with its
    page saved as an image-only PDF is measured on the PDF's render, and 'CAN H' keeps its 'C'."""
    import shutil
    from PIL import Image
    folder = tmp_path / "scanned"
    shutil.copytree(BUNDLES / "12-full-datasheet-png", folder)
    with Image.open(folder / "source.png") as picture:
        picture.convert("RGB").save(folder / "source.pdf", resolution=192.0)       # 1632 px at 192 dpi: 612 pt
    (folder / "source.png").unlink()
    index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
    index["source"]["media_type"] = "application/pdf"
    (folder / "index.json").write_text(json.dumps(index), encoding="utf-8")
    bundle = option1.Bundle.load(folder)
    assert locate.ocr(bundle)
    assert not locate.ocr(option1.Bundle.load(BUNDLES / "12-full-datasheet-pdf"))
    _, kept = _replayed("12-full-datasheet-png")
    buses = next(verdict for verdict in kept if verdict.key == "buses")
    where = locate.where(buses.to_dict(), bundle)
    box = parse(where.selector)
    assert where.marks == "words" and _squash(_pdf_text_in(TWINS["12-full-datasheet-png"], box)) == "CANH", where
    assert _cuts_a_glyph(BUNDLES / "12-full-datasheet-png" / "source.png", box) == []


def test_the_oracles_catch_a_character_count_slice(monkeypatch):
    """The rulers above can fail: with the ink not read (the slice handed on as before), 'CAN H' is caught by both."""
    bundle, kept = _replayed("12-full-datasheet-png")
    buses = next(verdict for verdict in kept if verdict.key == "buses")
    monkeypatch.setattr(locate, "ocr", lambda bundle: False)
    box = parse(_where(bundle, buses).selector)
    assert _squash(_pdf_text_in(TWINS["12-full-datasheet-png"], box)) == "ANH"
    assert _cuts_a_glyph(BUNDLES / "12-full-datasheet-png" / "source.png", box) == ["left"]


# --- an OCR line whose words cannot be told apart: the line, said plainly ------------------------------------------

def _drawn_bundle(tmp_path: pathlib.Path, glyphs: list[tuple[int, int]], text: str, *, image: bool = True):
    """A one-line OCR'd image: black blocks at `glyphs` (x0, x1 in px) on a white 1000 x 200 page, and an index whose
    words are Markdownify's character-count slices of the line's box [100, 900] x [80, 120] for `text`."""
    from PIL import Image, ImageDraw
    if image:
        picture = Image.new("L", (1000, 200), 255)
        draw = ImageDraw.Draw(picture)
        for x0, x1 in glyphs:
            draw.rectangle([x0, 88, x1, 112], fill=0)
        picture.save(tmp_path / "source.png")
    words, start, length = [], 0, len(text)
    for token in text.split(" "):
        end = start + len(token)
        words.append({"range": {"start": start, "end": end},
                      "grounding": {"page": 1, "box": [round((100 + 800 * start / length) / 1000, 5), 0.4,
                                                       round((100 + 800 * end / length) / 1000, 5), 0.6]}})
        start = end + 1
    block = {"id": "paragraph-1", "type": "paragraph", "range": {"start": 0, "end": len(text)},
             "grounding": [{"page": 1, "box": [0.09, 0.38, 0.91, 0.62]}]}
    index = {"source": {"media_type": "image/png"}, "pages": [{"page": 1, "width": 1000, "height": 200, "unit": "px"}],
             "blocks": [block], "words": words}
    return option1.Bundle(tmp_path, text, index, {"paragraph-1": block})


def _row(bundle, raw):
    block = bundle.blocks["paragraph-1"]
    return {"id": f"x@{block['id']}", "key": "x", "raw": raw, "cite": {"id": block["id"]}, "grounding": block["grounding"]}


def test_drawn_words_are_found_on_their_ink_not_their_slices(tmp_path):
    # three words whose ink does not sit where equal-width characters would put it: 'WWWW' wide, 'ii' narrow
    bundle = _drawn_bundle(tmp_path, [(100, 140), (146, 186), (192, 232), (238, 278), (330, 336), (342, 348),
                                      (400, 470), (478, 548), (556, 626)], "WWWW ii 9xx")
    where = locate.where(_row(bundle, "ii"), bundle)
    assert where == locate.Where("page=1&xywh=percent:33,40,1.9,20", "words")    # columns 330-348, inclusive
    assert locate.where(_row(bundle, "9xx"), bundle).selector == "page=1&xywh=percent:40,40,22.7,20"


def test_a_line_whose_words_cannot_be_told_apart_is_marked_as_the_line_and_says_why(tmp_path):
    # every gap the same width: the ink does not say where one word ends
    bundle = _drawn_bundle(tmp_path, [(100 + 60 * index, 150 + 60 * index) for index in range(13)],
                           "AAA BBB CCC DDD EE")
    where = locate.where(_row(bundle, "CCC"), bundle)
    assert where.marks == "line" and where.selector == "page=1&xywh=percent:10,40,77.1,20"    # columns 100-870
    assert where.note == ("OCR boxes whole lines, not single words, and this line's words could not be told apart on "
                          "the page image, so the line is marked")
    assert locate.raw_grounding(bundle, bundle.blocks["paragraph-1"], "CCC") is None


@pytest.mark.parametrize("case, text, glyphs", [
    # a letter gap (30 px) almost as wide as the one word space (34 px): which gap is the space is not clear
    ("unclear", "AB CD", [(100, 180), (211, 290), (325, 405), (416, 496)]),
    # one gap the split must cut (13 px) is unlike the line's other word spaces (88 px)
    ("unlike", "A B C D E", [(100, 189), (278, 367), (456, 544), (558, 722), (811, 900)]),
    # the only gap to cut is 2 px: letter spacing on a 40 px line, not a word space
    ("narrow", "AB CD", [(100, 189), (191, 420), (423, 600), (602, 900)]),
    # four clean words of ink, all at the left end of a line OCR read across its whole width: not where it read them
    ("elsewhere", "AA BB CC DD", [(100, 140), (160, 200), (220, 260), (280, 320)]),
])
def test_ink_that_does_not_clearly_split_into_the_ocr_words_marks_the_line(tmp_path, case, text, glyphs):
    bundle = _drawn_bundle(tmp_path, glyphs, text)
    raw = text.split(" ")[1]
    where = locate.where(_row(bundle, raw), bundle)
    assert where.marks == "line" and "could not be told apart" in where.note, (case, where)


def test_with_no_page_image_a_value_inside_an_ocr_line_marks_its_cell(tmp_path):
    bundle = _drawn_bundle(tmp_path, [], "Rated torque: 3 N·m", image=False)
    where = locate.where(_row(bundle, "3 N·m"), bundle)
    assert where.marks == "cell" and where.selector == "page=1&xywh=percent:9,38,82,24"
    assert "page image was not here" in where.note and where.note.endswith("so the cell is marked")
    # a value that is the whole OCR line keeps the line's own box, which OCR measured
    whole = _drawn_bundle(tmp_path, [], "3 N·m", image=False)
    assert locate.where(_row(whole, "3 N·m"), whole) == locate.Where("page=1&xywh=percent:10,40,80,20", "words")


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


def test_raw_inside_a_longer_printed_word_marks_that_word_and_says_so():
    # DEEP Robotics' J60 sheet is scanned: OCR reads '76.5mm*72.5mm' as one word, so the diameter cannot have a box of
    # its own; the whole word is marked (never a guessed part of its box) and the note says so
    bundle = _tiny_bundle("76.5mm*72.5mm")
    row = {"id": "outer_diameter@paragraph-1", "key": "outer_diameter", "raw": "76.5mm", "cite": {"id": "paragraph-1"},
           "grounding": bundle.blocks["paragraph-1"]["grounding"]}
    where = locate.where(row, bundle)
    assert where.marks == "words" and where.note == "the source prints it inside '76.5mm*72.5mm', so that whole text is marked"
    assert parse(where.selector) == PageBox(1, 0.1, 0.2, 0.04, 0.02)


def test_a_web_page_row_read_without_its_bundle_is_pinned_by_position_and_says_why():
    bundle, kept = _replayed("12-full-datasheet-html")
    row = next(verdict for verdict in kept if verdict.key == "mass").to_dict()
    where = locate.where(row, None)                   # no bundle: the stored quote cannot be checked against the page
    span = row["raw_grounding"][0]["html"]["source_range"]
    assert where.selector == f"char={span['start']},{span['end']}" and "no saved page" in where.note
