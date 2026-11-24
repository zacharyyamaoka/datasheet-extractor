"""Where an OCR line's words really are: read off the page image's ink, never off character counts.

A page image, or a PDF with no text layer, is read by OCR (RapidOCR through docling: Markdownify's image path and its
path for an image-only PDF). OCR boxes whole lines, not words, so Markdownify gives each word "its slice of the line's
box, in proportion to its characters" (markdownify/image.py). The line's box is measured; the cuts between its words
are estimates, and on 2026-11-24 12 of 116 such word boxes in the eval cache missed a glyph ('CAN H' in
'Yellow: CAN H' lost its 'C'). Here the cuts are measured instead:

    split(image, line_box, slices, masks)  -> one (x0, x1) per slice: the ink each word really covers, or None
    extent(image, line_box, masks)         -> (x0, x1): the whole line's ink, or None
    load_page(path, page), has_text_layer(path)

`split` reads the ink in the line's band (ruling lines and other OCR lines' words inside it left out), finds its blank
columns, and splits the ink at the widest gaps into exactly as many runs as the line has words. It answers only when
that split is clean: every gap it cuts at is a word space (wider than letter spacing, alike the line's other spaces,
and clearly wider than every gap it does not cut at), no run sits more than one word away from where OCR's slice put
it, and the line's ink ends inside the line's box with nothing glued on from outside. Otherwise None: the words could
not be told apart, and the caller marks the line or the cell and says so. Nothing here is guessed: a box edge is
always where the page image's ink ends.

Measured on 2026-11-24 against the twin PDF the harness printed from the same layout (its text layer, an oracle apart
from OCR): of the 279 OCR lines in the eval cache's 12 synthetic page images, 275 split cleanly into 883 words, and
880 of those boxes held exactly their own characters. The other 3: two plot labels OCR misread ('l-od' boxes the
printed 'No-load'), and a footnote mark OCR never read, printed touching its word ('torque¹'). The 4 refused lines:
two of Chinese without spaces, one OCR misread, a lone footnote mark.

WHY (Zach, 2026-11-24): "see highlighted the exact text that you got that information from"; a box that cuts a
glyph off, or takes one from the next word, is not that text.
"""

from __future__ import annotations

import dataclasses
import pathlib
import statistics

# Ink differs from the paper (the median grey around the line) by more than this share of the band's widest difference,
# and by at least MIN_CONTRAST grey levels. WHY the median and an absolute difference: a band is mostly background
# whatever its polarity, and DEEP Robotics' J60 sheet prints white text on near-black.
INK_SHARE = 0.4
MIN_CONTRAST = 40
# A row inked without a break across more than this share of the band is a ruling line (a table border inside the OCR
# box), not text; so is a column inked over RULE_COLUMN of the band and RULE_REACH × its height above and below (a
# cell's side border: no glyph reaches that far out of its line).
RULE_SHARE = 0.6
RULE_COLUMN = 0.95
RULE_REACH = 0.3
# Ink is looked for this far (× the line's height) beyond each end of the line's box: a glyph may spill a little past
# OCR's box, and ink that runs on to there is someone else's or a ruling, so the line is not clean.
MARGIN = 0.5
# A gap the split cuts at is a word space: at least this × the line's height, at least MIN_GAP_PX pixels, and at least
# SAME_SPACE × the line's lower-middle cut gap (one font's word spaces are alike; the lower middle, because a line OCR
# read across two table cells also cuts at the much wider gap between them)…
WORD_GAP = 0.1
MIN_GAP_PX = 2
SAME_SPACE = 0.6
# …and every gap it leaves uncut is at most this share of the narrowest one it cuts.
CLEAR = 0.75


@dataclasses.dataclass(frozen=True)
class _Band:
    """The ink columns of one line: `columns[i]` is True when column `left + i` of the page image has ink."""

    columns: list[bool]
    left: int
    window: tuple[int, int]          # the line box's own columns, as indices into `columns`
    height: int
    width: int                       # the page image's width in pixels
    image_edges: tuple[bool, bool]   # the band reaches the page image's left / right edge


def _band(image, line_box: list[float], masks: list[list[float]] = ()) -> _Band | None:
    """The line's ink columns. `masks`: boxes of other OCR words printed inside the line's band (a footnote mark
    after a word is its own OCR line), whose ink is not the line's."""
    width, height = image.size
    x0, y0, x1, y1 = line_box
    top, bottom = max(0, int(y0 * height)), min(height, int(round(y1 * height + 0.5)))
    line_height = bottom - top
    if line_height < 4:
        return None
    margin = max(2, int(MARGIN * line_height))
    reach = max(1, int(RULE_REACH * line_height))
    window_left, window_right = int(x0 * width), min(width - 1, int(round(x1 * width + 0.5)))
    left, right = max(0, window_left - margin), min(width, window_right + margin + 1)
    if right - left < 2:
        return None
    outer_top, outer_bottom = max(0, top - reach), min(height, bottom + reach)
    crop = image.crop((left, outer_top, right, outer_bottom))
    count = right - left
    band_rows = range(top - outer_top, bottom - outer_top)
    pixels = list(crop.tobytes())      # 8-bit grey: one byte per pixel
    band_pixels = [pixels[row * count + column] for row in band_rows for column in range(count)]
    # WHY the median of the band and the rows above and below it: on a line of heavy print the band alone can be more
    # ink than paper; with the space between lines it is paper again
    background = statistics.median_low(pixels)
    for box in masks:
        mx0, my0 = max(left, int(box[0] * width)), max(outer_top, int(box[1] * height))
        mx1, my1 = min(right, int(round(box[2] * width + 0.5))), min(outer_bottom, int(round(box[3] * height + 0.5)))
        for row in range(my0 - outer_top, my1 - outer_top):
            for column in range(mx0 - left, mx1 - left):
                pixels[row * count + column] = background
    spread = max(abs(value - background) for value in band_pixels)
    if spread < MIN_CONTRAST:
        return None
    threshold = max(MIN_CONTRAST, INK_SHARE * spread)
    rows = [[abs(value - background) > threshold for value in pixels[row * count:(row + 1) * count]]
            for row in range(outer_bottom - outer_top)]
    text_rows = [rows[row] for row in band_rows if _longest(rows[row]) <= RULE_SHARE * count]
    columns = []
    for column in range(count):
        tall = sum(1 for row in rows if row[column]) >= RULE_COLUMN * len(rows)
        columns.append(not tall and any(row[column] for row in text_rows))
    return _Band(columns, left, (window_left - left, window_right - left), line_height, width,
                 (left == 0, right == width))


def _longest(row: list[bool]) -> int:
    """The longest unbroken run of ink in a row."""
    longest = current = 0
    for inked in row:
        current = current + 1 if inked else 0
        longest = max(longest, current)
    return longest


def _runs(columns: list[bool]) -> list[tuple[int, int]]:
    """Maximal runs of inked columns, as (first, last) indices."""
    runs, start = [], None
    for index, inked in enumerate(columns):
        if inked and start is None:
            start = index
        elif not inked and start is not None:
            runs.append((start, index - 1))
            start = None
    if start is not None:
        runs.append((start, len(columns) - 1))
    return runs


def _line_runs(band: _Band) -> list[tuple[int, int]] | None:
    """The runs of ink that belong to the line: those that overlap its box. None when the line's ink is not clean:
    it runs on to the edge of the margin looked at, or other ink sits closer to it than a word space."""
    runs = _runs(band.columns)
    first, last = band.window
    inside = [run for run in runs if run[1] >= first and run[0] <= last]
    if not inside:
        return None
    space = max(MIN_GAP_PX, WORD_GAP * band.height)
    if inside[0][0] == 0 and not band.image_edges[0]:
        return None
    if inside[-1][1] == len(band.columns) - 1 and not band.image_edges[1]:
        return None
    before = [run for run in runs if run[1] < inside[0][0]]
    after = [run for run in runs if run[0] > inside[-1][1]]
    if before and inside[0][0] - before[-1][1] - 1 < space:
        return None
    if after and after[0][0] - inside[-1][1] - 1 < space:
        return None
    return inside


def extent(image, line_box: list[float], masks: list[list[float]] = ()) -> tuple[float, float] | None:
    """The line's ink from its first inked column to its last, as fractions of the page's width; None when the line's
    ink is not clean (see `_line_runs`)."""
    band = _band(image, line_box, masks)
    runs = _line_runs(band) if band is not None else None
    if not runs:
        return None
    return ((band.left + runs[0][0]) / band.width, (band.left + runs[-1][1] + 1) / band.width)


def split(image, line_box: list[float], slices: list[list[float]],
          masks: list[list[float]] = ()) -> list[tuple[float, float]] | None:
    """The ink of each of the line's words, as (x0, x1) fractions of the page's width, in the order of `slices` (the
    words' estimated boxes, left to right); None when the line's ink does not split cleanly into that many words."""
    band = _band(image, line_box, masks)
    runs = _line_runs(band) if band is not None else None
    count = len(slices)
    if not runs or len(runs) < count or count == 0:
        return None
    gaps = [runs[index + 1][0] - runs[index][1] - 1 for index in range(len(runs) - 1)]
    widest = sorted(range(len(gaps)), key=lambda index: -gaps[index])
    cut, kept = sorted(widest[:count - 1]), widest[count - 1:]
    if cut:
        sizes = sorted(gaps[index] for index in cut)
        narrowest = sizes[0]
        if narrowest < max(MIN_GAP_PX, WORD_GAP * band.height, SAME_SPACE * sizes[(len(sizes) - 1) // 2]):
            return None
        if kept and max(gaps[index] for index in kept) > CLEAR * narrowest:
            return None
    groups, start = [], 0
    for index in cut:
        groups.append((runs[start][0], runs[index][1]))
        start = index + 1
    groups.append((runs[start][0], runs[-1][1]))
    found = [((band.left + first) / band.width, (band.left + last + 1) / band.width) for first, last in groups]
    # WHY a loose check, never "each slice overlaps its own run": a slice drifts as the line's narrow and wide letters
    # add up (by 18 px, more than the word '9' is wide, late in a 21-word line), so a correct split can sit one word
    # off its slices; it can never sit further, or the split and OCR do not read the same words
    for index, (x0, x1) in enumerate(found):
        low = slices[max(0, index - 1)][0]
        high = slices[min(count - 1, index + 1)][2]
        if not low <= (x0 + x1) / 2 <= high:
            return None
    return found


# --- the page image -----------------------------------------------------------------------------------------------

# A PDF page is rendered this many pixels across its longer side: about 290 dpi for A4 or Letter, enough to see the
# space between two words of 6 pt text.
RENDER_PIXELS = 3400


def load_page(path: pathlib.Path, page: int):
    """Page `page` (1-based) of an image or a PDF, as an 8-bit grey PIL image; None when it cannot be read."""
    path = pathlib.Path(path)
    try:
        if path.suffix.lower() == ".pdf":
            import pypdfium2

            document = pypdfium2.PdfDocument(str(path))
            try:
                if not 1 <= page <= len(document):
                    return None
                pdf_page = document[page - 1]
                scale = RENDER_PIXELS / max(pdf_page.get_size())
                return pdf_page.render(scale=scale).to_pil().convert("L")
            finally:
                document.close()
        from PIL import Image

        if page != 1:
            return None
        with Image.open(path) as picture:
            return picture.convert("L")
    except Exception:          # a damaged or missing file: no image, so nothing is measured on it
        return None


def has_text_layer(path: pathlib.Path) -> bool | None:
    """Whether any page of a PDF has text-layer characters (Markdownify's own rule, `pdf.has_text_layer`: one page
    with text and the whole PDF is read from its text layer, never OCR'd); None when the PDF cannot be read."""
    import pypdfium2

    try:
        document = pypdfium2.PdfDocument(str(path))
    except Exception:          # a damaged or missing file: the caller falls back to the bundle's own record
        return None
    try:
        for index in range(len(document)):
            text_page = document[index].get_textpage()
            try:
                if text_page.count_chars() > 0:
                    return True
            finally:
                text_page.close()
        return False
    finally:
        document.close()
