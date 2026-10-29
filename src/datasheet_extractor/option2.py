"""Option 2: one shot of the model on the raw source only, checked like robotparts' check.py.

- **HTML:** the model reads the page text robotparts' reader makes (text.py: block elements on their own lines,
  table cells side by side) and returns `{key, value, unit, raw, condition, quote}`. The checker re-derives the same
  text from the same bytes: the quote must occur on the page (whitespace-normalised, otherwise character for
  character), raw inside the quote, the value printed in raw, a field label in the quote tied to the number by
  robotparts' binding rule (bind.py, with the page's navigation/link zones and `--part-model`), the unit printed.
  Grounding: the raw's `source_range` in the decoded HTML, plus the quote as `text_quote`.
- **PDF:** the model sees the pages as images and returns `{…, page, box, quote}`. The checker reads the PDF's text
  layer: the quote must occur on that page's `pdftotext -layout` text (robotparts' reader) and pass the same rules,
  and the text-layer words inside the box must spell `raw`. Grounding: the model's own page + box.
- **Image (PNG/JPEG):** as a PDF, but an image has no text layer, so nothing the model says can be checked against
  the source: every value is written `dropped` with that reason (robotparts' rule for an image-only PDF), and the
  proposal is kept beside it for the scorer.

WHY the prompt is robotparts' SYSTEM_PROMPT minus one line ("A value whose label sits only in a table's header row
… leave it out"): that line made the model hide every comparison-table value before the checker saw it, so the recall
it cost was invisible (research note 2026-10-29, `2-grounding.md`). Here the model proposes them and the checker's
drop reason says why each one fails.

WHY a quote on PDFs and images too, beyond the page + box the spec names: robotparts' checker confirms a label
against the value through a verbatim quote; without it, a box alone can say where a number is but not what it is.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

from . import bind, text as text_reader
from .fields import Field, by_key, label_found, unit_found
from .option1 import fields_prompt
from .values import Verdict, cross_check, normal, parse_value, squash, value_problems

MAX_QUOTE = 500
BOX_SLACK = 0.01        # a box may miss the raw's words by 1% of the page
RENDER_DPI = 110


def media_type_of(path: pathlib.Path) -> str:
    head = pathlib.Path(path).read_bytes()[:2048]
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if re.search(rb"<(!doctype html|html|head|body|meta|div|title)", head, re.IGNORECASE):
        return "text/html"
    raise ValueError(f"{path}: not a PDF, PNG, JPEG, WebP or HTML file")


def render_pages(pdf: pathlib.Path, out_dir: pathlib.Path) -> list[pathlib.Path]:
    """Every page as a PNG (`pdftoppm -r 110`), page 1 first."""
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftoppm", "-r", str(RENDER_DPI), "-png", str(pdf), str(out_dir / "page")], check=True,
                   capture_output=True)
    return sorted(out_dir.glob("page-*.png"), key=lambda p: int(re.search(r"(\d+)\.png$", p.name).group(1)))


SYSTEM_PROMPT = """You read manufacturers' datasheets for an open library of parts where every value cites its \
source. You point at values the source states; a program then checks every one of them against the same source, \
character for character, and throws away anything it cannot confirm. So:

- Report only values the source states for EXACTLY the part named below. A table or page that covers several models: \
use only this model's column or block. When you cannot tell which model a value belongs to, leave it out.
- `quote`: copy a short, contiguous span of the source text verbatim (spaces and line breaks may differ, nothing \
else): it must contain the row label and the value, and the unit when the source prints one. Prefer under 200 \
characters; never over 400.
- The row label must stand right before the value, as the source prints it ("Rated Torque (Nm) 18", "Peak Torque \
53Nm"), or right after it in a sentence ("a 10:1 gear reduction").
- `raw`: the part of the quote that states the value, verbatim, e.g. "22Nm" or "9:1" or "Ф98*38.5mm".
- `value`: the number exactly as printed in `raw` (decimal point or comma as printed, no thousands separators \
added, no arithmetic, no unit conversion). A value in grams stays in grams.
- `unit`: the label from the key's list that names the unit the source uses for that number.
- `condition`: a few words when something matters (e.g. "upper bound (≤)", "at 48 V", "with driver"), else "".
- Each key at most once. If the source gives one key at several conditions (24 V and 48 V), pick the one at the \
rated voltage and say the condition.
- When a key's definition does not match what the source means (a motor-side speed for an output-side key, a pole \
count for pole pairs), leave the key out. Leaving a value out is always better than a doubtful value.
- `part`: how the source names the part's manufacturer and model, verbatim, with a quote that prints it; "" when \
the source does not say.
Reply with the JSON object only."""

IMAGE_RULES = """
# Where (the source is page images)
- `page`: the page number (1 = the first image).
- `box`: [x0, y0, x1, y1] around the printed `raw` on that page, each a fraction 0–1 of the page's width (x) and \
height (y), origin at the top-left. Tight around the value's own characters, not the whole row."""


def output_schema(fields: tuple[Field, ...], images: bool) -> dict:
    units = sorted({unit for field in fields for unit in field.units})
    named = {"type": "object", "additionalProperties": False, "required": ["value", "quote"],
             "properties": {"value": {"type": "string"}, "quote": {"type": "string"}}}
    value = {"key": {"type": "string", "enum": [field.key for field in fields]}, "value": {"type": "string"},
             "unit": {"type": "string", "enum": units}, "raw": {"type": "string"}, "condition": {"type": "string"},
             "quote": {"type": "string"}}
    if images:
        value.update({"page": {"type": "integer"},
                      "box": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}})
    return {"type": "object", "additionalProperties": False, "required": ["part", "values"],
            "properties": {
                "part": {"type": "object", "additionalProperties": False, "required": ["manufacturer", "model"],
                         "properties": {"manufacturer": named, "model": named}},
                "values": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                      "required": sorted(value), "properties": value}}}}


def build_prompt(fields: tuple[Field, ...], part_model: str | None, page_text: str | None, pages: int) -> str:
    lines = ["# The part", f"- model: {part_model or 'the one this datasheet describes'}", "",
             "# The keys (give only those the source states, in the unit labels listed)"]
    lines += fields_prompt(fields)
    if page_text is None:
        lines += [IMAGE_RULES, "", f"# The source: the {pages} attached page image(s), page 1 first. Read the values off the images."]
    else:
        lines += ["", "# The source: the web page's text (an HTML page is page 1)", "", page_text.rstrip()]
    return "\n".join(lines) + "\n"


class Source:
    """The raw source as the checker re-derives it: page texts, zones, offsets, text-layer words."""

    def __init__(self, path: pathlib.Path, media_type: str):
        self.path, self.media_type = path, media_type
        self.pages: list[str] = []
        self.squashed: list[str] = []
        self.zones: list = []
        self.offsets: list[int] = []
        self.words: list[list[dict]] = []
        data = path.read_bytes()
        if media_type == "text/html":
            self.pages = [text_reader.html_text(data)]
            self.squashed = [squash(self.pages[0])]
            flat, flags = text_reader.html_zones(data)
            self.zones = [flags if flat == self.squashed[0] else None]
            flat, self.offsets = text_reader.html_offsets(data)
            if flat != self.squashed[0]:
                self.offsets = []
        elif media_type == "application/pdf":
            self.pages = text_reader.pdf_pages(data)
            self.squashed = [squash(page) for page in self.pages]
            zones = [text_reader.layout_zones(page) for page in self.pages]
            self.zones = [flags if len(flags) == len(text) else None for flags, text in zip(zones, self.squashed)]
            self.words = text_reader.pdf_words(path)

    @property
    def has_text(self) -> bool:
        return any(page.strip() for page in self.squashed)


def _location_problems(row: dict, raw: str, quote: str, source: Source) -> tuple[list[str], int | None]:
    """robotparts check.py rule 3: the quote is on the page; raw is inside the quote."""
    p = []
    page = 1 if source.media_type == "text/html" else row.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= len(source.squashed):
        return [f"page {page!r} is not a page of the source (1..{len(source.squashed)})"], None
    squashed_quote, squashed_raw = squash(quote), squash(raw)
    if not squashed_quote:
        p.append("empty quote")
    elif len(squashed_quote) > MAX_QUOTE:
        p.append(f"quote is {len(squashed_quote)} characters, over {MAX_QUOTE}")
    elif squashed_quote not in source.squashed[page - 1]:
        elsewhere = [n for n, text in enumerate(source.squashed, 1) if squashed_quote in text]
        p.append(f"quote does not occur on page {page}" + (f" (it is on page {elsewhere[0]})" if elsewhere else ""))
    if not squashed_raw:
        p.append("empty raw")
    elif squashed_raw not in squashed_quote:
        p.append("raw does not occur in the quote")
    return p, page


def _unit_problems(field: Field, unit, raw: str, quote: str) -> list[str]:
    if unit not in field.units:
        return [f"unit {unit!r} is not one of {list(field.units)}"]
    if field.key == "gear_ratio" or field.is_text or unit == "":
        return []
    if unit_found(unit, raw) or unit_found(unit, quote):
        return []
    return [f"neither raw {raw!r} nor the quote prints the unit {unit!r}"]


def _box_problems(row: dict, raw: str, page: int, source: Source) -> list[str]:
    """The text-layer words inside the box (with 1% slack) spell raw."""
    box = row.get("box")
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
        return [f"box {box!r} is not [x0, y0, x1, y1]"]
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return [f"box {box!r} is not inside the page as 0–1 fractions"]
    inside = [w["text"] for w in source.words[page - 1]
              if x0 - BOX_SLACK <= (w["box"][0] + w["box"][2]) / 2 <= x1 + BOX_SLACK
              and y0 - BOX_SLACK <= (w["box"][1] + w["box"][3]) / 2 <= y1 + BOX_SLACK]
    spelled = normal("".join(inside)).replace(" ", "")
    if normal(raw).replace(" ", "") not in spelled:
        return [f"the text layer inside the box reads {' '.join(inside)[:80]!r}, not raw {raw!r}"]
    return []


def _html_grounding(raw: str, quote: str, source: Source) -> list[dict]:
    page = source.squashed[0]
    at = page.find(squash(quote))
    if at < 0 or not source.offsets:
        return [{"html": {"text_quote": {"exact": squash(quote)}}}]
    inner = squash(quote).find(squash(raw))
    start = at + max(inner, 0)
    end = start + len(squash(raw)) if inner >= 0 else at + len(squash(quote))
    real = [o for o in source.offsets[start:end] if o >= 0]
    grounding = {"text_quote": {"exact": squash(quote), "prefix": page[max(0, at - 32):at],
                                "suffix": page[at + len(squash(quote)):at + len(squash(quote)) + 32]}}
    if real:
        grounding = {"source_range": {"start": real[0], "end": real[-1] + 1}, **grounding}
    return [{"html": grounding}]


def check_value(row: dict, fields: dict[str, Field], source: Source, part_model: str | None) -> Verdict:
    key, printed = row.get("key"), str(row.get("value", "")).strip()
    unit, raw, quote = row.get("unit"), row.get("raw") or "", row.get("quote") or ""
    condition = (row.get("condition") or "").strip() or None
    field = fields.get(key)
    images = source.media_type != "text/html"
    grounding = [{"page": row.get("page"), "box": row.get("box")}] if images else []
    if field is None:
        return Verdict(str(key), printed, unit, raw, condition, None, grounding, [f"{key!r} is not a requested field"],
                       row)
    reasons, value = [], None
    try:
        value = parse_value(printed, field)
    except ValueError as error:
        reasons.append(str(error))
    if images and not source.has_text:
        reasons.append("the source has no text layer, so neither the quote nor the box can be checked against it "
                       "(no OCR in the checker)")
        return Verdict(key, value, unit, raw, condition, None, grounding, reasons, row)
    where, page = _location_problems(row, raw, quote, source)
    reasons += where
    if value is not None:
        reasons += value_problems(field, value, printed, raw)
    reasons += _unit_problems(field, unit, raw, quote)
    if not label_found(field, quote):
        reasons.append(f"the quote carries none of {key!r}'s labels")
    elif value is not None and not where and not field.is_text:
        page_text = source.squashed[page - 1]
        zones = source.zones[page - 1] if page - 1 < len(source.zones) else None
        reading = bind.read(key, value, unit, condition, raw, quote, page_text, tuple(fields.values()), zones,
                            part_model)
        reasons += reading.problems
    if images and page is not None and not where:
        reasons += _box_problems(row, raw, page, source)
    if not images and not where:
        grounding = _html_grounding(raw, quote, source)
    return Verdict(key, value, unit, raw, condition, None, grounding, reasons, row)


def check_part(row: dict | None, source: Source) -> dict | None:
    if not row or not (row.get("value") or "").strip():
        return None
    quote = squash(row.get("quote") or "")
    reasons = []
    if not source.has_text:
        reasons.append("the source has no text layer to check the quote against")
    elif not quote or not any(quote in page for page in source.squashed):
        reasons.append("the quote does not occur in the source text")
    elif squash(row["value"]).lower() not in quote.lower():
        reasons.append(f"value {row['value']!r} is not printed in the quote")
    return {"value": row["value"], "raw": row["value"], "quote": row.get("quote"),
            "verdict": "dropped" if reasons else "kept", "reasons": reasons}


def judge(output: dict | None, fields: tuple[Field, ...], source: Source, part_model: str | None) -> tuple[list[Verdict], dict]:
    lookup = by_key(fields)
    verdicts = [check_value(row, lookup, source, part_model) for row in (output or {}).get("values") or []]
    cross_check(verdicts)
    part = (output or {}).get("part") or {}
    return verdicts, {"manufacturer": check_part(part.get("manufacturer"), source),
                      "model": check_part(part.get("model"), source)}
