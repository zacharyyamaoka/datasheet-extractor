"""Option 1: the raw source through Markdownify, then the model cites block and cell ids, and code checks them.

The model sees `document.md` with every block and table cell tagged by its index.json id, and returns
`{key, value, unit, raw, condition, id}`. Code, not the model, then:

1. finds the id in index.json (a closed set: the model cannot point at nothing) and refuses a whole table, a figure
   or a block Markdownify did not convert;
2. checks `raw` is inside the cited block's or cell's own text, and `value` is printed in `raw` as a number of its
   own (no conversion), at its key's place in the cell ('Ø51×24.5' is diameter × length, '18–52 V' a range);
3. for a table cell, reads the row header and the column header from the index's row/col grid — never from the
   model — and requires one of them to carry one of the field's labels, the unit to be printed at the number or in
   that header, and, in a table with one column (or row) per model, the other header to name `--part-model`;
4. for a prose block, holds the block's own text to robotparts' binding rule (bind.py): the label sits right before
   the number (or right after it, in prose) with no other number or quantity between them;
5. copies the grounding from index.json.

WHY cite ids (research note 2026-10-29 `2-grounding.md`): a quote grounds a string, not a table relation; in a
comparison table the model's header (which model) sits rows above the number, and small models pick the right
column only 30% of the time against 72% for the row. So the model names the cell and code names the column.

WHY the tag is `⟦id⟧` at the start of each block's and cell's range: the id is the very string index.json and
values.json carry, so no alias table can drift; the brackets never occur in a datasheet, so the checker strips them
unambiguously and the model cannot mistake them for source text; and one tag per cell keeps a GFM row on one line,
so the model still reads the table as a table.
"""

from __future__ import annotations

import dataclasses
import html
import json
import pathlib
import re
import subprocess
import time

from . import bind
from .fields import Field, by_key, label_found, unit_found, unit_only
from .values import Verdict, cross_check, normal, parse_value, squash, value_problems

MARKDOWNIFY = pathlib.Path("/home/bam/markdownify")
TAG_OPEN, TAG_CLOSE = "⟦", "⟧"
UNCITABLE = {"table", "figure", "plot", "diagram"}

# A cell that states a value: it starts with a number (after a sign, a comparison or a diameter mark).
_VALUE_CELL = re.compile(r"^[\s≤≥<>~≈±+\-−]*[" + bind.DIAMETER_MARKS + r"]?\s*\d")
# A model name that starts with a digit: the whole cell is one word, digits then two or more capitals glued to a digit
# ('60ASM200-1024C/2500C', '86HS45'). WHY: JMC's selection list has one row per model; read as a value cell, its
# model column was no header column, so no row had a label and the checker could not tell which model a row owns.
# A unit glued to its number ('48VDC', '150mVp-p', '24VAC/48VAC') never has a digit right after its capitals.
_MODEL_NAME = re.compile(r"^\d+[A-Z]{2,}\d[\w.\-/]*$")
# A model designation: one to three letters (and maybe a dash and two more) then a digit ('KM80-9', 'AK10-9',
# 'AKE40-6', 'V3.0', 'KV60', 'DM-J8009-2EC'). WHY not any word with a digit: a footnote marker glued to a label
# ('Rated torque2', 'Peak torque1') is a label, and stripping it as a designation hid the label.
_DESIGNATION = re.compile(r"(?<![\w.\-/])[A-Za-z]{1,3}(?:-?[A-Za-z]{1,2})?\d[\w.\-]*")
# A label-only block right before a value-only block ('Peak Torque' then '19.94Nm', two paragraphs): its row label.
MAX_LABEL_BLOCK = 60


def run_markdownify(source: pathlib.Path, bundle_dir: pathlib.Path) -> tuple[float, str]:
    """`uv run --directory /home/bam/markdownify markdownify <source> --out <bundle_dir>`: (seconds, stderr tail)."""
    start = time.time()
    run = subprocess.run(["uv", "run", "--directory", str(MARKDOWNIFY), "markdownify", str(pathlib.Path(source).resolve()),
                          "--out", str(bundle_dir.resolve())], capture_output=True, text=True, check=False)
    seconds = time.time() - start
    (bundle_dir.parent / "markdownify-stderr.txt").write_text(run.stderr, encoding="utf-8")
    if run.returncode != 0 or not (bundle_dir / "index.json").is_file():
        raise RuntimeError(f"markdownify failed ({run.returncode}): {run.stderr[-600:]}")
    return round(seconds, 2), run.stdout.strip()


def plain(text: str) -> str:
    """A block's markdown as the words it prints: no HTML tags, no link targets, no escapes, no emphasis marks, and
    HTML entities read as the characters they print.
    WHY entities here, once: Markdownify writes a table with merged cells as an HTML <table>, so its cells carry
    '&#126;' for '~' and '&amp;' for '&' (LRS-350); a model may copy either spelling, both mean the same printed text,
    and every checker (Option 1, open mode) reads cell, block and raw text through this one function. Tags go first,
    so an entity's '<' is text, never the start of a tag."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\\([^\w\s])", r"\1", text)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(re.escape(TAG_OPEN) + r"[^" + re.escape(TAG_CLOSE) + r"]*" + re.escape(TAG_CLOSE), " ", text)
    return normal(html.unescape(text))


def is_value_cell(text: str) -> bool:
    """A cell's plain text states a value: it starts with a number, and is not a model name that starts with one."""
    return bool(_VALUE_CELL.match(text)) and not _MODEL_NAME.match(text)


@dataclasses.dataclass
class Bundle:
    directory: pathlib.Path
    markdown: str
    index: dict
    blocks: dict

    @classmethod
    def load(cls, directory: pathlib.Path) -> "Bundle":
        markdown = (directory / "document.md").read_text(encoding="utf-8")
        index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
        return cls(directory, markdown, index, {block["id"]: block for block in index["blocks"]})

    def text(self, block: dict) -> str:
        span = block.get("range")
        return self.markdown[span["start"]:span["end"]] if span else ""

    def printed(self, block: dict) -> str:
        """The block's or cell's text as it prints (`plain` of its document.md range): what every check reads."""
        return plain(self.text(block))

    def label_before(self, block: dict) -> dict | None:
        """The block right before `block` in the markdown, when it is a short label with no number of its own."""
        prior = [b for b in self.index["blocks"] if b.get("range") and b["type"] in ("paragraph", "list_item", "heading")
                 and b["range"]["end"] <= block["range"]["start"]]
        if not prior:
            return None
        label = max(prior, key=lambda b: b["range"]["end"])
        text = self.printed(label)
        between = self.markdown[label["range"]["end"]:block["range"]["start"]]
        if len(text) > MAX_LABEL_BLOCK or re.search(r"\d", text) or between.strip():
            return None
        return label

    def table_of(self, cell_id: str) -> dict | None:
        return next((block for block in self.index["blocks"]
                     if block["type"] == "table" and cell_id in (block.get("children") or [])), None)

    def tagged(self) -> str:
        """document.md with `⟦id⟧ ` at the start of every block and cell range (outer blocks first)."""
        marks = sorted(((block["range"]["start"], -(block["range"]["end"] - block["range"]["start"]), block["id"])
                        for block in self.index["blocks"] if block.get("range")), key=lambda m: (m[0], m[1]))
        out, cursor = [], 0
        for start, _, block_id in marks:
            out.append(self.markdown[cursor:start])
            out.append(f"{TAG_OPEN}{block_id}{TAG_CLOSE} ")
            cursor = start
        out.append(self.markdown[cursor:])
        return "".join(out)


@dataclasses.dataclass
class Grid:
    """A table as index.json lays it out: (row, col) → cell, spans filled in."""
    cells: list[dict]
    texts: dict                   # cell id → plain text
    at: dict                      # (row, col) → cell
    header_rows: int
    header_cols: int

    @classmethod
    def of(cls, bundle: Bundle, table: dict) -> "Grid":
        cells = [bundle.blocks[child] for child in table.get("children") or [] if child in bundle.blocks]
        texts = {cell["id"]: bundle.printed(cell) for cell in cells}
        at = {}
        for cell in cells:
            for row in range(cell["row"], cell["row"] + cell.get("rowspan", 1)):
                for col in range(cell["col"], cell["col"] + cell.get("colspan", 1)):
                    at.setdefault((row, col), cell)
        rows = max((r for r, _ in at), default=-1) + 1
        cols = max((c for _, c in at), default=-1) + 1

        def is_value(cell):
            return is_value_cell(texts[cell["id"]])

        header_rows = 0
        for row in range(rows):
            line = [at[(row, col)] for col in range(cols) if (row, col) in at]
            if any(cell.get("kind") == "columnHeader" for cell in line) or \
                    (line and not any(is_value(cell) for cell in line if texts[cell["id"]])):
                header_rows = row + 1
            else:
                break
        header_rows = min(header_rows, rows - 1) if rows > 1 else 0
        header_cols = 0
        for col in range(cols):
            line = [at[(row, col)] for row in range(header_rows, rows) if (row, col) in at]
            if any(cell.get("kind") in ("rowHeader", "stubHead") for cell in line) or \
                    (line and not any(is_value(cell) for cell in line if texts[cell["id"]])):
                header_cols = col + 1
            else:
                break
        header_cols = min(header_cols, cols - 1) if cols > 1 else 0
        return cls(cells, texts, at, header_rows, header_cols)

    def _label_like(self, cell) -> bool:
        text = self.texts[cell["id"]]
        return bool(text) and not is_value_cell(text) and not unit_only(text)

    def own_label(self, cell: dict) -> str | None:
        """The nearest label-like cell to the left of `cell` in its row: its row label ('Weight g' in a two-pair
        row 'Weight g | 140 | Insulation Class | F', never the row's first label 'Size mm').
        WHY a cell that is only a unit is no label (SteadyWin's GIM4305-10: 'Nominal Voltage | V | 24 | 24'): a unit
        column sits between the label and the value, and the nearest non-value cell was the unit, so every row of the
        page read 'V', 'N.M', 'RPM' as its label and the checker dropped all of them. Only a unit is skipped, never a
        word: 'Noise' in 'Weight g | 140 | Noise | 60' still ends the walk, so the 60 is not the weight."""
        label = self.own_label_cell(cell)
        return self.texts[label["id"]] if label is not None else None

    def own_label_cell(self, cell: dict) -> dict | None:
        """The cell `own_label` reads, or None."""
        row, col = cell["row"], cell["col"]
        for c in range(col - 1, -1, -1):
            other = self.at.get((row, c))
            if other and other is not cell and self._label_like(other):
                return other
        return None

    def own_units(self, cell: dict) -> list[str]:
        """The unit cells of `cell`'s row that print its unit: those between it and its label on the left ('label |
        unit | value'), and the one right after it ('label | value | unit'). Whatever they print is read, not
        trusted: the unit must still be one of the field's and match what the cell says."""
        row, col = cell["row"], cell["col"]
        units = []
        for c in range(col - 1, -1, -1):
            other = self.at.get((row, c))
            if other is None or other is cell:
                continue
            if self._label_like(other):
                break
            if unit_only(self.texts[other["id"]]):
                units.append(self.texts[other["id"]])
        after = self.at.get((row, col + cell.get("colspan", 1)))
        if after is not None and unit_only(self.texts[after["id"]]):
            units.append(self.texts[after["id"]])
        return units

    def row_headers(self, cell: dict) -> list[str]:
        """Every row header of `cell`, read from the grid: its row's header columns and its own label. The model
        check reads all of them ('AK Series Robotic Actuator / AK10-9 V3.0 KV60')."""
        row, col = cell["row"], cell["col"]
        out = []
        for c in range(min(self.header_cols, col)):
            other = self.at.get((row, c))
            if other and self.texts[other["id"]] and self.texts[other["id"]] not in out:
                out.append(self.texts[other["id"]])
        own = self.own_label(cell)
        if own is not None and own not in out:
            out.append(own)
        return out

    def column_headers(self, cell: dict) -> list[str]:
        """The column header cells over `cell`, top-down (a merged 'Torque' over 'Rated')."""
        return [self.texts[other["id"]] for other in self.column_header_cells(cell)]

    def column_header_cells(self, cell: dict) -> list[dict]:
        """The cells `column_headers` reads, top-down."""
        out, seen = [], set()
        for col in range(cell["col"], cell["col"] + cell.get("colspan", 1)):
            for row in range(min(self.header_rows, cell["row"])):
                other = self.at.get((row, col))
                if other and other["id"] not in seen and self.texts[other["id"]]:
                    seen.add(other["id"])
                    out.append(other)
        return out

    def designations(self, axis: str) -> set[str]:
        """The distinct model designations along the model axis: the header row(s) across the columns
        (axis 'column') or the header column(s) down the rows (axis 'row')."""
        names = set()
        for (row, col), cell in self.at.items():
            if (axis == "column" and row < self.header_rows and col >= self.header_cols) or \
                    (axis == "row" and col < self.header_cols and row >= self.header_rows):
                text = self.texts[cell["id"]]
                names.update(_norm(m) for m in _DESIGNATION.findall(text))
                if _MODEL_NAME.match(text):
                    names.add(_norm(text))
        return names


def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z]", "", name.lower())


def model_named(part_model: str, texts: list[str]) -> bool:
    """`part_model` is named in one of `texts` as a whole designation ('AK80-6' is not inside 'AK80-64')."""
    words = [re.escape(word) for word in re.split(r"[\s\-]+", part_model.strip()) if word]
    pattern = r"(?<![a-z0-9])" + r"[\s\-]*".join(words) + r"(?![a-z0-9]|\.\d)"
    return any(re.search(pattern, text, re.IGNORECASE) for text in texts)


def _strip_designations(text: str) -> str:
    return _DESIGNATION.sub(" ", text)


def header_label(field: Field, texts: list[str]) -> str | None:
    """The header text (or merged-header path) that names the field, or None. A header that names another
    quantity besides the label ('Max Torque Density' is no peak torque) does not count; designations
    ('KV60' in 'AK10-9 V3.0 KV60') are not labels."""
    candidates = list(texts)
    if len(texts) > 1:
        candidates += [" ".join(texts), " ".join(reversed(texts))]
    for text in candidates:
        bare = _strip_designations(text)
        if not label_found(field, bare):
            continue
        rest = bare
        for pattern in field.labels + field.marks:
            rest = re.sub(pattern, " ", rest, flags=re.IGNORECASE)
        rest = bind._strip_units(bind._strip_parens(rest))
        if bind._OTHER.search(rest):
            continue
        return text
    return None


def _cell_value_problems(field: Field, value, unit: str, condition: str, raw: str, label_text: str) -> list[str]:
    """The number's place in the cell and the unit printed at it (or in its header)."""
    text = plain(raw)
    for cell in bind.cells_in(text):
        for number in cell.numbers:
            if float(value) not in number.options:
                continue
            problem = bind._place_problem(field.key, number, cell, condition or "", text)
            if problem:
                return [problem]
            printed = sorted(cell.units[cell.numbers.index(number)])
            if field.key == "gear_ratio" or unit == "":
                return []
            if printed:
                return [] if unit in printed else [f"the number prints {printed[0]!r}, not the unit {unit!r}"]
            if unit_found(unit, label_text) or unit_found(unit, text):
                return []
            return [f"the unit {unit!r} is printed neither at the number nor in its header {label_text!r}"]
    return [f"no number of raw {raw!r} is {value!r}"]


def check_value(row: dict, fields: dict[str, Field], bundle: Bundle, part_model: str | None,
                grids: dict) -> Verdict:
    key, printed = row.get("key"), str(row.get("value", "")).strip()
    unit, raw, condition = row.get("unit"), row.get("raw") or "", (row.get("condition") or "").strip() or None
    block_id = (row.get("id") or "").strip()
    reasons: list[str] = []
    cite, grounding, value = {"id": block_id}, [], None
    field = fields.get(key)
    if field is None:
        return Verdict(str(key), printed, unit, raw, condition, cite, [], [f"{key!r} is not a requested field"], row)
    try:
        value = parse_value(printed, field)
    except ValueError as error:
        reasons.append(str(error))
    if unit not in field.units:
        reasons.append(f"unit {unit!r} is not one of {list(field.units)}")
    block = bundle.blocks.get(block_id)
    if block is None:
        reasons.append(f"{block_id!r} is not an id in index.json")
        return Verdict(key, value, unit, raw, condition, cite, [], reasons, row)
    grounding = block.get("grounding") or []
    if block["type"] in UNCITABLE:
        reasons.append(f"{block_id} is a whole {block['type']}; a value cites the cell or block that prints it")
    if block.get("represented") is False:
        reasons.append(f"{block_id} was not converted by Markdownify (represented: false)")
    own = bundle.printed(block)
    if not plain(raw):
        reasons.append("empty raw")
    elif plain(raw) not in own:
        reasons.append(f"raw {raw!r} is not inside {block_id}'s text {own[:120]!r}")
    if value is not None:
        reasons += value_problems(field, value, printed, plain(raw))
    if reasons:
        return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
    if field.is_text:
        if not label_found(field, own) and not label_found(field, plain(raw)):
            reasons.append(f"{block_id}'s text carries none of {key!r}'s labels")
        return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
    if block["type"] == "table_cell":
        table = bundle.table_of(block_id)
        if table is None:
            reasons.append(f"{block_id} belongs to no table in index.json")
            return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
        grid = grids.setdefault(table["id"], Grid.of(bundle, table))
        row_texts, col_texts = grid.row_headers(block), grid.column_headers(block)
        own_label = grid.own_label(block)
        cite.update({"row": " / ".join(row_texts) or None, "column": " / ".join(col_texts) or None})
        # WHY only the nearest label on the row axis: in 'Rated Torque N·m | 1.8 | Backlash arcmin | 9' the 9's
        # label is 'Backlash arcmin'; the row's first cell names the 1.8.
        on_row = header_label(field, [own_label]) if own_label else None
        on_col = header_label(field, col_texts)
        marked = any(re.search(mark, own) for mark in field.marks)
        # WHY the label's cells are named in the cite (`label_ids`): a Stated's `as_written` is the label as the
        # source prints it (locate.attach reads it from these cells), never the checker's normalised text
        header_cells = grid.column_header_cells(block)
        own_cell = grid.own_label_cell(block)
        if on_row is not None:
            label_text, model_axes = on_row, [("column", col_texts)]
            cite["label_ids"] = [own_cell["id"]]
        elif on_col is not None:
            label_text, model_axes = on_col, [("row", row_texts)]
            one = [other["id"] for other in header_cells if grid.texts[other["id"]] == on_col]
            cite["label_ids"] = one[:1] or [other["id"] for other in header_cells]
        elif marked or (own_label and col_texts and (header_label(field, [" ".join([own_label] + col_texts)])
                                                       or header_label(field, [" ".join(col_texts + [own_label])]))):
            # WHY: a diameter sign labels its own cell ('Ø51×24.5' in a 'Size mm' row), and a label split over both
            # headers ('Stall' row, 'Torque' column) is the cell's head n-grams, as Fonduer reads them. Either way
            # neither axis is spent on the label alone, so any axis that lists models must name this one.
            label_text = " ".join([own] + row_texts + col_texts)
            model_axes = [("row", row_texts), ("column", col_texts)]
            ids = ([own_cell["id"]] if own_cell else []) + ([] if marked else [other["id"] for other in header_cells])
            if ids:
                cite["label_ids"] = ids
        else:
            reasons.append(f"neither the row label {own_label!r} nor the column header "
                           f"{' / '.join(col_texts)!r} carries one of {key!r}'s labels")
            return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
        # WHY the unit cells of the row and the column headers too: the unit sits in its own column ('N.M' between
        # 'Stall Torque' and '3.82'), after the value, or in a header row of its own, not always in the label.
        reasons += _cell_value_problems(field, value, unit, condition, raw,
                                        " ".join([label_text] + grid.own_units(block) + col_texts))
        for model_axis, model_texts in model_axes:
            if len(grid.designations(model_axis)) < 2:
                continue
            cite["multi_model"] = True
            if not part_model:
                reasons.append(f"{table['id']} has one {model_axis} per model and no --part-model was given")
            elif not model_named(part_model, model_texts):
                reasons.append(f"{table['id']} has one {model_axis} per model, and this cell's {model_axis} header "
                               f"{' / '.join(model_texts)!r} does not name {part_model!r}")
        return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
    # A prose block: robotparts' binding rule on the block's own text.
    if not label_found(field, own):
        label = bundle.label_before(block)
        # WHY: a spec sheet laid out as label and value in two blocks ('Peak Torque', then '19.94Nm', as DEEP
        # Robotics' page prints it) is a definition list; its label is the label-only block right before it. The
        # pair is read as one text, so the binding rule still holds (no other number between them).
        if label is None or not label_found(field, bundle.printed(label)):
            reasons.append(f"{block_id}'s text carries none of {key!r}'s labels, nor does a label-only block "
                           "right before it")
            return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
        cite["label_id"] = label["id"]
        cite["row"] = bundle.printed(label)
        own = cite["row"] + " " + own
        grounding = (label.get("grounding") or []) + grounding
    reasons += bind.read(key, value, unit, condition, plain(raw), own, None, tuple(fields.values()), None,
                         part_model).problems
    return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)


def check_part(row: dict | None, bundle: Bundle) -> dict | None:
    """The part's manufacturer or model as the source names it: raw must be inside the cited block."""
    if not row or not (row.get("value") or "").strip():
        return None
    block = bundle.blocks.get((row.get("id") or "").strip())
    reasons = []
    if block is None:
        reasons.append(f"{row.get('id')!r} is not an id in index.json")
    elif plain(row.get("raw") or "") not in bundle.printed(block) or not plain(row.get("raw") or ""):
        reasons.append(f"raw {row.get('raw')!r} is not inside {row.get('id')}'s text")
    elif normal(row["value"]).lower() not in plain(row.get("raw") or "").lower():
        reasons.append(f"value {row['value']!r} is not printed in raw {row.get('raw')!r}")
    return {"value": row["value"], "raw": row.get("raw"), "cite": {"id": row.get("id")},
            "grounding": block.get("grounding") if block else [], "verdict": "dropped" if reasons else "kept",
            "reasons": reasons}


SYSTEM_PROMPT = """You read manufacturers' datasheets. The datasheet below was converted to markdown, and every \
block and every table cell starts with its id in ⟦…⟧ brackets, e.g. "⟦table_cell-41⟧ 20 Nm". You point at values \
by id; a program then checks every one against the cited block or cell and throws away anything it cannot confirm. \
It reads a table cell's row and column headers itself, from the table's structure. So:

- Report only values the text states for EXACTLY the part named below. In a table with one column (or one row) per \
model, cite only the cell in this model's column (or row). When you cannot tell which model a value belongs to, leave \
it out.
- `id`: the id of the smallest block or table cell that prints the number. For a table value that is the cell \
holding the number — never the table, and never the row or column header cell.
- `raw`: the part of that block's or cell's text that states the value, copied verbatim without the ⟦…⟧ tag, e.g. \
"22Nm", "9:1", "Ф98*38.5", "18–52 V". In a cell, raw is text of that cell only.
- `value`: the number exactly as printed in `raw` (decimal point or comma as printed, no thousands separators added, \
no arithmetic, no unit conversion). A value in grams stays in grams.
- `unit`: the label from the key's list that names the unit the source uses for that number — printed in the cell, \
or in its row or column header.
- `condition`: a few words when a stated condition matters ("at 48 V", "upper bound (≤)", "with driver"), else "".
- Each key at most once. If the source gives one key at several conditions (24 V and 48 V), pick the one at the rated \
voltage and say the condition.
- When a key's definition does not match what the source means (a motor-side speed for an output-side key, a pole \
count for pole pairs), leave the key out. Leaving a value out is always better than a doubtful value.
- `part`: how the source names the part's manufacturer and model, each with the id of a block that prints it and \
the raw text; "" when the source does not say.
Reply with the JSON object only."""


def output_schema(fields: tuple[Field, ...]) -> dict:
    units = sorted({unit for field in fields for unit in field.units})
    named = {"type": "object", "additionalProperties": False, "required": ["value", "raw", "id"],
             "properties": {"value": {"type": "string"}, "raw": {"type": "string"}, "id": {"type": "string"}}}
    return {
        "type": "object", "additionalProperties": False, "required": ["part", "values"],
        "properties": {
            "part": {"type": "object", "additionalProperties": False, "required": ["manufacturer", "model"],
                     "properties": {"manufacturer": named, "model": named}},
            "values": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["key", "value", "unit", "raw", "condition", "id"],
                "properties": {
                    "key": {"type": "string", "enum": [field.key for field in fields]},
                    "value": {"type": "string"},
                    "unit": {"type": "string", "enum": units},
                    "raw": {"type": "string"},
                    "condition": {"type": "string"},
                    "id": {"type": "string"},
                }}},
        }}


def fields_prompt(fields: tuple[Field, ...]) -> list[str]:
    lines = []
    for field in fields:
        units = ", ".join(repr(unit) for unit in field.units)
        labels = ", ".join(repr(word) for word in field.words[:8])
        what = field.guidance or field.definition
        definition = f" Definition: {field.definition}" if field.definition and field.guidance else ""
        if field.is_text:
            lines.append(f"- `{field.key}` — {what}.{definition} Labels: {labels}. `value` is a comma-separated list "
                         f"of these names: {', '.join(field.allowed)}; unit ''.")
        else:
            lines.append(f"- `{field.key}` — {what}.{definition} Labels: {labels}. Unit labels: {units}.")
    return lines


def build_prompt(bundle: Bundle, fields: tuple[Field, ...], part_model: str | None) -> str:
    lines = ["# The part", f"- model: {part_model or 'the one this datasheet describes'}", "",
             "# The keys (give only those the text states, in the unit labels listed)"]
    lines += fields_prompt(fields)
    lines += ["", "# The datasheet (markdown; ⟦id⟧ starts each block and table cell)", "", bundle.tagged()]
    return "\n".join(lines) + "\n"


def judge(output: dict | None, fields: tuple[Field, ...], bundle: Bundle, part_model: str | None) -> tuple[list[Verdict], dict]:
    rows = list((output or {}).get("values") or [])
    from . import locate                          # WHY here: locate reads Bundle, so it imports this module
    lookup, grids = by_key(fields), {}
    verdicts = [check_value(row, lookup, bundle, part_model, grids) for row in rows]
    cross_check(verdicts)
    locate.attach(verdicts, bundle)
    part = (output or {}).get("part") or {}
    return verdicts, {"manufacturer": check_part(part.get("manufacturer"), bundle),
                      "model": check_part(part.get("model"), bundle)}
