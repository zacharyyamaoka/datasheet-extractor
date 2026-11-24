"""Open mode ("every spec"): a category with no `fields/<category>.json` asks the model for every specification the
source states, `{label (as printed), value, unit, raw, condition, id}`, and the key is the printed label's slug under
`spec.` ('RIPPLE & NOISE (max.)' → `spec.ripple_noise_max`).

WHY: RobotParts' vocabulary defines only `actuator` today, so a power supply checked against actuator fields kept 1
value of 4 (LRS-350, 2026-11-05). In open mode nothing the source states is lost meanwhile, and RobotParts can map
`spec.*` keys onto its vocabulary later.

The checker is option1.py's structure without the field list: the id must be a block or cell of index.json (not a
whole table or figure, and converted); `raw` must be inside its text and the value readable in `raw` (a number as a
number of its own, a text value as a substring); the printed label must be the cell's row or column header as the
index lays them out (for prose: in the block, or in a label-only block right before it); a unit, when given, must be
printed in `raw` or in that header; and in a table that names the part model (or has one column or row per model),
the cell's other header must name it.
"""

from __future__ import annotations

import re

from .. import locate, option1
from ..values import Verdict, cross_check, normal, printed_numbers

SYSTEM_PROMPT = """You read manufacturers' datasheets. The datasheet below was converted to markdown, and every \
block and every table cell starts with its id in ⟦…⟧ brackets, e.g. "⟦table_cell-41⟧ 350.4W". You point at values by \
id; a program then checks every one against the cited block or cell and throws away anything it cannot confirm. It \
reads a table cell's row and column headers itself, from the table's structure. So:

- Report EVERY specification the text states for EXACTLY the part named below: electrical, mechanical, thermal, \
environmental, protections, certifications — one row per stated value. In a table with one column (or one row) per \
model, cite only the cell in this model's column (or row). When you cannot tell which model a value belongs to, leave \
it out.
- `label`: the specification's name exactly as printed — the row header or column header of the cell (one of them), \
or the words right before the value in prose, e.g. "RATED POWER", "RIPPLE & NOISE (max.)".
- `id`: the id of the smallest block or table cell that prints the value — never the table, never a header cell.
- `raw`: the part of that block's or cell's text that states the value, copied verbatim without the ⟦…⟧ tag.
- `value`: a number exactly as printed in `raw` (no arithmetic, no unit conversion), or, for a value that is not one \
number ("90 ~ 132VAC", "Forced air cooling"), the words as printed.
- `unit`: the unit as printed at the number or in its header ("W", "VAC", "mVp-p"), or "" for none.
- `condition`: a few words when a stated condition matters ("at 230VAC", "max."), else "".
- `part`: how the source names the part's manufacturer and model, each with the id of a block that prints it and \
the raw text; "" when the source does not say.
Reply with the JSON object only."""


def key_of(label: str) -> str:
    """`spec.` + the printed label as a slug: letters and digits of any script, runs of anything else as `_`."""
    slug = re.sub(r"[^\w]+", "_", normal(label).lower()).strip("_")
    return f"spec.{slug or 'unnamed'}"


def output_schema() -> dict:
    named = {"type": "object", "additionalProperties": False, "required": ["value", "raw", "id"],
             "properties": {"value": {"type": "string"}, "raw": {"type": "string"}, "id": {"type": "string"}}}
    return {
        "type": "object", "additionalProperties": False, "required": ["part", "values"],
        "properties": {
            "part": {"type": "object", "additionalProperties": False, "required": ["manufacturer", "model"],
                     "properties": {"manufacturer": named, "model": named}},
            "values": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["label", "value", "unit", "raw", "condition", "id"],
                "properties": {name: {"type": "string"} for name in ("label", "value", "unit", "raw", "condition",
                                                                      "id")}}},
        }}


def build_prompt(bundle: option1.Bundle, part_model: str | None) -> str:
    lines = ["# The part", f"- model: {part_model or 'the one this datasheet describes'}", "",
             "# The datasheet (markdown; ⟦id⟧ starts each block and table cell)", "", bundle.tagged()]
    return "\n".join(lines) + "\n"


def _value(printed: str):
    """A number as printed (int or float), else the text as printed."""
    printed = printed.strip()
    if re.fullmatch(r"\d+", printed):
        return int(printed)
    if re.fullmatch(r"\d+\.\d+", printed):
        return float(printed)
    return printed


def _readable(value, printed: str, raw: str) -> list[str]:
    text = option1.plain(raw)
    if isinstance(value, (int, float)):
        if printed.strip() not in [token for token, _ in printed_numbers(text)]:
            return [f"value {printed!r} is not printed as a number of its own in raw {raw!r}"]
        return []
    if not printed.strip() or option1.plain(printed).lower() not in text.lower():
        return [f"value {printed!r} is not printed in raw {raw!r}"]
    return []


def _named(label: str, texts: list[str]) -> bool:
    return bool(label) and any(option1.plain(label).lower() in option1.plain(text).lower() for text in texts if text)


def check_value(row: dict, bundle: option1.Bundle, part_model: str | None, grids: dict) -> Verdict:
    label, printed = (row.get("label") or "").strip(), str(row.get("value", "")).strip()
    unit, raw = (row.get("unit") or "").strip(), row.get("raw") or ""
    condition = (row.get("condition") or "").strip() or None
    block_id = (row.get("id") or "").strip()
    key, value = key_of(label), _value(printed)
    cite, reasons = {"id": block_id}, []
    if not label:
        reasons.append("no label")
    block = bundle.blocks.get(block_id)
    if block is None:
        reasons.append(f"{block_id!r} is not an id in index.json")
        return Verdict(key, value, unit, raw, condition, cite, [], reasons, row)
    grounding = block.get("grounding") or []
    if block["type"] in option1.UNCITABLE:
        reasons.append(f"{block_id} is a whole {block['type']}; a value cites the cell or block that prints it")
    if block.get("represented") is False:
        reasons.append(f"{block_id} was not converted by Markdownify (represented: false)")
    own = bundle.printed(block)
    if not option1.plain(raw):
        reasons.append("empty raw")
    elif option1.plain(raw) not in own:
        reasons.append(f"raw {raw!r} is not inside {block_id}'s text {own[:120]!r}")
    else:
        reasons += _readable(value, printed, raw)
    if reasons:
        return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
    if block["type"] == "table_cell":
        table = bundle.table_of(block_id)
        if table is None:
            reasons.append(f"{block_id} belongs to no table in index.json")
            return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
        grid = grids.setdefault(table["id"], option1.Grid.of(bundle, table))
        row_texts, col_texts = grid.row_headers(block), grid.column_headers(block)
        cite.update({"row": " / ".join(row_texts) or None, "column": " / ".join(col_texts) or None})
        own_label = grid.own_label(block)
        if _named(label, [own_label] if own_label else []):
            label_texts, model_axes = [own_label], [("column", col_texts)]
            cite["label_ids"] = [grid.own_label_cell(block)["id"]]
        elif _named(label, col_texts):
            label_texts, model_axes = col_texts, [("row", row_texts)]
            cite["label_ids"] = [other["id"] for other in grid.column_header_cells(block)]
        else:
            reasons.append(f"the label {label!r} is neither the row label {own_label!r} nor the column header "
                           f"{' / '.join(col_texts)!r}")
            return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
        # WHY the unit column and the column headers too: as option1.check_value, the unit may sit in its own cell
        if unit and not _named(unit, [raw] + label_texts + grid.own_units(block) + col_texts):
            reasons.append(f"the unit {unit!r} is printed neither in raw {raw!r} nor in its header")
        if part_model and option1.model_named(part_model, list(grid.texts.values())):
            # WHY beside the designation rule: Grid's designations miss 'LRS-350-24' (a digit after the dash), so
            # a table that names the part model anywhere must name it
            # on this cell's model axis too: its column header, or (label in a column header) its own row.
            line = col_texts if model_axes[0][0] == "column" else \
                [grid.texts[other["id"]] for (r, _), other in sorted(grid.at.items()) if r == block["row"]]
            if not option1.model_named(part_model, line):
                cite["multi_model"] = True
                reasons.append(f"{table['id']} names {part_model!r}, but not on this cell's "
                               f"{model_axes[0][0]} ({' / '.join(t for t in line if t)!r})")
        for model_axis, model_texts in model_axes:
            if len(grid.designations(model_axis)) < 2 or reasons:
                continue
            cite["multi_model"] = True
            if not part_model:
                reasons.append(f"{table['id']} has one {model_axis} per model and no part model was given")
            elif not option1.model_named(part_model, model_texts):
                reasons.append(f"{table['id']} has one {model_axis} per model, and this cell's {model_axis} header "
                               f"{' / '.join(model_texts)!r} does not name {part_model!r}")
        return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
    texts = [own]
    if not _named(label, texts):
        before = bundle.label_before(block)
        if before is None or not _named(label, [bundle.text(before)]):
            reasons.append(f"the label {label!r} is not in {block_id}'s text, nor in a label-only block right "
                           "before it")
            return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)
        cite["label_id"], cite["row"] = before["id"], bundle.printed(before)
        grounding = (before.get("grounding") or []) + grounding
        texts.append(cite["row"])
    if unit and not _named(unit, [raw] + texts):
        reasons.append(f"the unit {unit!r} is not printed in raw {raw!r}")
    return Verdict(key, value, unit, raw, condition, cite, grounding, reasons, row)


def judge(output: dict | None, bundle: option1.Bundle, part_model: str | None) -> tuple[list[Verdict], dict]:
    grids: dict = {}
    verdicts = [check_value(row, bundle, part_model, grids) for row in (output or {}).get("values") or []]
    cross_check(verdicts)
    locate.attach(verdicts, bundle)
    part = (output or {}).get("part") or {}
    return verdicts, {"manufacturer": option1.check_part(part.get("manufacturer"), bundle),
                      "model": option1.check_part(part.get("model"), bundle)}
