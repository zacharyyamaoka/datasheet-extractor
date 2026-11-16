"""Which cell of a row is its label, in the layouts a spec table comes in (option1.Grid.own_label): SteadyWin's real
GIM4305-10 table (label | unit | value | value, a second driver variant in the last column) and the sibling layouts,
on tiny hand-built bundles. The model's reply is hand-written against the real cell ids (the extraction could not be
rerun live: the Claude accounts were at their usage limit). No LLM."""

from __future__ import annotations

import pathlib

import pytest

from datasheet_extractor import fields as fields_module, option1
from datasheet_extractor.sources import spec as spec_module

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "steadywin-gim4305-10"
FIELDS = fields_module.load(ROOT / "fields" / "actuator.json")
LOOKUP = fields_module.by_key(FIELDS)
PART = "GIM4305-10"
VARIANT_COLUMN = {"GDZ34": 2, "GDS34": 3}

# The model's reply: (key, unit, row label as the page prints it, value in the GDZ34 column, value in the GDS34 column)
# and the text each cell prints. WHY these keys: the ones the actuator field list can read off this table.
REPLY = [
    ("nominal_voltage", "V", "Nominal Voltage", "24", "24"),
    ("voltage_max", "V", "Voltage Range", "40", "36"),
    ("voltage_min", "V", "Voltage Range", "12", "12"),
    ("rated_torque", "N·m", "Nominal Torque", "1.05", "1"),
    ("peak_torque", "N·m", "Stall Torque", "3.82", "3.47"),
    ("rated_speed", "rpm", "Nominal Speed after Reduce", "312", "300"),
    ("max_output_speed", "rpm", "Max Speed after Reduce", "402", "366"),
    ("backlash", "arcmin", "Reducer Gear Backlash", "15", "15"),
    ("kv", "rpm/V", "Speed Constant", "16.75", "15.25"),
    ("pole_pairs", "", "Number of Pole Pairs", "14", "14"),
    ("resistance_line_to_line", "Ω", "Phase to Phase Resistance", "1.11", "1.15"),
]
RAW = {("Voltage Range", "GDZ34"): "12~40", ("Voltage Range", "GDS34"): "12~36"}


@pytest.fixture(scope="module")
def gim() -> option1.Bundle:
    return option1.Bundle.load(FIXTURE)


def _row_cell(bundle: option1.Bundle, label: str, variant: str) -> dict:
    """The value cell of the row whose label cell prints `label` (the English half), in the variant's column."""
    cells = [b for b in bundle.index["blocks"] if b["type"] == "table_cell"]
    start = next(c for c in cells if c["col"] == 0 and bundle.printed(c).endswith(f"({label})"))
    return next(c for c in cells if c["row"] == start["row"] and c["col"] == VARIANT_COLUMN[variant])


def _reply(bundle: option1.Bundle, variants: list[str]) -> dict:
    """The model's JSON: each key once (a repeat of a key is dropped by the cross-check), in the variant column
    `variants[i]` names for the i-th key."""
    rows = []
    for (key, unit, label, gdz, gds), variant in zip(REPLY, variants):
        cell = _row_cell(bundle, label, variant)
        value = gdz if variant == "GDZ34" else gds
        rows.append({"key": key, "value": value, "unit": unit, "raw": RAW.get((label, variant), value),
                     "condition": "", "id": cell["id"]})
    return {"part": {}, "values": rows}


MIXED = ["GDZ34" if number % 2 == 0 else "GDS34" for number in range(len(REPLY))]


@pytest.mark.parametrize("variants", [MIXED, ["GDZ34"] * len(REPLY), ["GDS34"] * len(REPLY)],
                         ids=["mixed", "all GDZ34", "all GDS34"])
def test_the_real_steadywin_table_keeps_every_proposed_row_in_its_own_variant_column(gim, variants):
    output = _reply(gim, variants)
    verdicts, _ = option1.judge(output, FIELDS, gim, PART)
    assert len(verdicts) == len(REPLY)
    for verdict, row, (key, unit, label, *_), variant in zip(verdicts, output["values"], REPLY, variants):
        assert verdict.kept, (key, row["id"], verdict.reasons)
        # the cell the model named is the cell the checker read, and its column is that cell's own variant
        assert verdict.cite["id"] == _row_cell(gim, label, variant)["id"]
        assert verdict.cite["column"].split(" / ")[-1] == variant, (key, verdict.cite)
        assert label in verdict.cite["row"], (key, verdict.cite)


def test_a_gds34_value_never_reads_the_gdz34_column(gim):
    # a GDS34 cell resolves to the GDS34 column and carries GDS34's number, a GDZ34 cell to GDZ34's
    power_gdz = _row_cell(gim, "Stall Torque", "GDZ34")
    power_gds = _row_cell(gim, "Stall Torque", "GDS34")
    for variant, cell, number in (("GDZ34", power_gdz, 3.82), ("GDS34", power_gds, 3.47)):
        verdict = option1.check_value({"key": "peak_torque", "value": str(number), "unit": "N·m", "raw": str(number),
                                       "id": cell["id"]}, LOOKUP, gim, PART, {})
        assert verdict.kept and verdict.cite["column"].endswith(variant) and verdict.value == number, verdict.reasons
    # and the GDZ34 number cited on the GDS34 cell is no row at all
    wrong = {"key": "peak_torque", "value": "3.82", "unit": "N·m", "raw": "3.82", "condition": "", "id": power_gds["id"]}
    verdict = option1.check_value(wrong, LOOKUP, gim, PART, {})
    assert not verdict.kept and "is not inside" in verdict.reasons[0]


def test_the_unit_column_is_not_the_label_and_still_proves_the_unit(gim):
    cell = _row_cell(gim, "Stall Torque", "GDZ34")
    grid = option1.Grid.of(gim, gim.table_of(cell["id"]))
    assert grid.own_label(cell) == "堵转扭矩(Stall Torque)"
    # the number 3.82 prints no unit; the unit column prints N.M and that is its proof
    kept = option1.check_value({"key": "peak_torque", "value": "3.82", "unit": "N·m", "raw": "3.82", "id": cell["id"]},
                               LOOKUP, gim, PART, {})
    assert kept.kept, kept.reasons
    # a unit the unit column does not print is still refused
    wrong = option1.check_value({"key": "peak_torque", "value": "3.82", "unit": "mN·m", "raw": "3.82", "id": cell["id"]},
                                LOOKUP, gim, PART, {})
    assert not wrong.kept and "printed neither at the number nor in its header" in wrong.reasons[0]


def test_copy_as_spec_on_the_real_table_quotes_the_cell_and_names_the_column(gim):
    verdicts, _ = option1.judge(_reply(gim, MIXED), FIELDS, gim, PART)
    rows = [verdict.to_dict() | {"products": []} for verdict in verdicts]
    texts = {block["id"]: gim.printed(block) for block in gim.index["blocks"]}
    text = spec_module.as_spec(rows, source_url="https://www.steadywin.cn/en/h-pd-9.html", sha256="ab" * 32,
                               manufacturer="SteadyWin", texts=texts)
    lines = {line.split('"')[1]: line for line in text.splitlines() if line.strip().startswith('"')}
    # MIXED: stall torque (index 4) is a GDZ34 row, rated torque (index 3) a GDS34 row
    assert "3.82" in lines["peak_torque"] and "GDZ34 column" in lines["peak_torque"]
    assert "GDS34" not in lines["peak_torque"]
    assert "1," in lines["rated_torque"] and "GDS34 column" in lines["rated_torque"]
    assert "GDZ34" not in lines["rated_torque"]
    assert 'quote="堵转扭矩(Stall Torque)' in lines["peak_torque"]


# ---- the sibling layouts, hand-built -------------------------------------------------------------------------------

def grid_bundle(rows: list[list[str]], header_rows: int = 0) -> option1.Bundle:
    """One GFM-ish table, no separator row; the first `header_rows` rows are flagged columnHeader, as Markdownify
    does for a <th> row."""
    markdown, blocks, ids, number = "", [], [], 1
    for r, row in enumerate(rows):
        markdown += "|"
        for c, text in enumerate(row):
            markdown += " "
            number += 1
            start = len(markdown)
            markdown += text
            blocks.append({"id": f"table_cell-{number}", "type": "table_cell", "row": r, "col": c,
                           "kind": "columnHeader" if r < header_rows else None,
                           "range": {"start": start, "end": len(markdown)}, "grounding": [{"page": 1, "box": [0, 0, 1, 1]}]})
            ids.append(f"table_cell-{number}")
            markdown += " |"
        markdown += "\n"
    blocks.insert(0, {"id": "table-1", "type": "table", "children": ids, "range": {"start": 0, "end": len(markdown)}})
    return option1.Bundle(None, markdown, {"blocks": blocks}, {block["id"]: block for block in blocks})


def _id(bundle: option1.Bundle, text: str, nth: int = 0) -> str:
    return [b["id"] for b in bundle.index["blocks"] if b["type"] == "table_cell" and bundle.printed(b) == text][nth]


def _check(bundle, key, value, unit, raw, cell, part_model=None):
    return option1.check_value({"key": key, "value": value, "unit": unit, "raw": raw, "id": cell}, LOOKUP, bundle,
                               part_model, {})


def test_label_value_unit():
    table = grid_bundle([["Rated torque", "1.8", "N·m"], ["Backlash", "9", "arcmin"], ["Rated speed", "300", "rpm"]])
    kept = _check(table, "rated_torque", "1.8", "N·m", "1.8", _id(table, "1.8"))
    assert kept.kept, kept.reasons
    assert kept.cite["row"] == "Rated torque"
    back = _check(table, "backlash", "9", "arcmin", "9", _id(table, "9"))
    assert back.kept, back.reasons
    # the unit written to the right is the unit: not another unit
    wrong = _check(table, "rated_torque", "1.8", "mN·m", "1.8", _id(table, "1.8"))
    assert not wrong.kept
    # and the wrong label is still refused
    assert not _check(table, "rated_torque", "9", "N·m", "9", _id(table, "9")).kept


def test_label_unit_value():
    table = grid_bundle([["Rated torque", "N·m", "1.8"], ["Backlash", "arcmin", "9"], ["Rated speed", "rpm", "300"],
                         ["Mass", "g", "140"], ["Resistance", "Ω", "1.11"]])
    for key, unit, value in (("rated_torque", "N·m", "1.8"), ("backlash", "arcmin", "9"), ("rated_speed", "rpm", "300"),
                             ("mass", "g", "140")):
        verdict = _check(table, key, value, unit, value, _id(table, value))
        assert verdict.kept, (key, verdict.reasons)
    # the unit column is no label, so a unit's name cannot make a row's value another key
    assert not _check(table, "rated_speed", "1.8", "rpm", "1.8", _id(table, "1.8")).kept
    assert not _check(table, "mass", "9", "g", "9", _id(table, "9")).kept
    # nor can the unit column vouch for a unit it does not print
    assert not _check(table, "rated_torque", "1.8", "mN·m", "1.8", _id(table, "1.8")).kept


def test_label_with_unit_in_it_then_value():
    table = grid_bundle([["Rated torque (N·m)", "1.8"], ["Backlash (arcmin)", "9"], ["Weight g", "140"]])
    for key, unit, value in (("rated_torque", "N·m", "1.8"), ("backlash", "arcmin", "9"), ("mass", "g", "140")):
        verdict = _check(table, key, value, unit, value, _id(table, value))
        assert verdict.kept, (key, verdict.reasons)
    assert not _check(table, "backlash", "1.8", "arcmin", "1.8", _id(table, "1.8")).kept


def test_label_then_value_with_its_unit():
    table = grid_bundle([["Rated torque", "1.8 N·m"], ["Backlash", "9 arcmin"], ["Mass", "140 g"]])
    for key, unit, value, raw in (("rated_torque", "N·m", "1.8", "1.8 N·m"), ("backlash", "arcmin", "9", "9 arcmin"),
                                  ("mass", "g", "140", "140 g")):
        verdict = _check(table, key, value, unit, raw, _id(table, raw))
        assert verdict.kept, (key, verdict.reasons)
    assert not _check(table, "mass", "1.8", "g", "1.8 N·m", _id(table, "1.8 N·m")).kept


def test_a_header_row_names_each_variant_column():
    table = grid_bundle([["Parameter", "Unit", "GDZ34", "GDS34"], ["Rated torque", "N·m", "1.05", "1"],
                         ["Rated speed", "rpm", "312", "300"]], header_rows=1)
    gds = _check(table, "rated_speed", "300", "rpm", "300", _id(table, "300"), "GDS34")
    assert gds.kept, gds.reasons
    assert gds.cite["column"] == "GDS34" and gds.cite["row"].startswith("Rated speed")
    # the GDZ34 number is not GDS34's: the part model names the column the checker reads, never the model's say-so
    gdz = _check(table, "rated_speed", "312", "rpm", "312", _id(table, "312"), "GDS34")
    assert not gdz.kept and "does not name 'GDS34'" in " ".join(gdz.reasons)
    assert _check(table, "rated_speed", "312", "rpm", "312", _id(table, "312"), "GDZ34").kept
    # two variants and no part model: still refused, as for any comparison table
    assert not _check(table, "rated_speed", "300", "rpm", "300", _id(table, "300")).kept


def test_units_in_their_own_header_row():
    # models across the top, then a row of units, then rows: the row labels are the first column, no unit column
    table = grid_bundle([["Parameter", "KM80-9", "KM70-10"], ["", "N·m", "N·m"], ["Rated torque", "9", "7"],
                         ["Peak torque", "18", "15"]], header_rows=2)
    grid = option1.Grid.of(table, table.blocks["table-1"])
    assert grid.header_rows == 2
    kept = _check(table, "rated_torque", "9", "N·m", "9", _id(table, "9"), "KM80-9")
    assert kept.kept, kept.reasons
    assert kept.cite["column"] == "KM80-9 / N·m"
    other = _check(table, "rated_torque", "7", "N·m", "7", _id(table, "7"), "KM80-9")
    assert not other.kept and "does not name 'KM80-9'" in " ".join(other.reasons)


def test_two_label_pairs_on_a_row_each_value_reads_its_own_label_across_a_unit_cell():
    # 'Rated torque | N·m | 1.8 | Backlash | arcmin | 9': the 9's nearest label-like cell to the left is 'arcmin'
    table = grid_bundle([["Rated torque", "N·m", "1.8", "Backlash", "arcmin", "9"]])
    assert _check(table, "backlash", "9", "arcmin", "9", _id(table, "9")).kept
    assert not _check(table, "rated_torque", "9", "N·m", "9", _id(table, "9")).kept
    assert _check(table, "rated_torque", "1.8", "N·m", "1.8", _id(table, "1.8")).kept
    assert not _check(table, "backlash", "1.8", "arcmin", "1.8", _id(table, "1.8")).kept


def test_a_short_word_that_is_no_unit_is_still_a_label():
    # WHY: skipping unit cells must not walk past 'Noise' to the 'Weight g' pair before it: the 60 is Noise's
    table = grid_bundle([["Weight g", "140", "Noise", "60"]])
    assert _check(table, "mass", "140", "g", "140", _id(table, "140")).kept
    assert not _check(table, "mass", "60", "g", "60", _id(table, "60")).kept


def test_open_mode_reads_the_same_label_and_unit_layouts():
    from datasheet_extractor.sources import open_mode

    table = grid_bundle([["Nominal Voltage", "V", "24", "24"], ["Stall Torque", "N.M", "3.82", "3.47"],
                         ["Pole Pairs", "Pairs", "14", "14"]])
    rows = [{"label": "Nominal Voltage", "value": "24", "unit": "V", "raw": "24", "condition": "", "id": _id(table, "24")},
            {"label": "Stall Torque", "value": "3.47", "unit": "N.M", "raw": "3.47", "condition": "", "id": _id(table, "3.47")},
            {"label": "Pole Pairs", "value": "14", "unit": "Pairs", "raw": "14", "condition": "", "id": _id(table, "14", 1)}]
    verdicts, _ = open_mode.judge({"values": rows}, table, None)
    assert all(verdict.kept for verdict in verdicts), [verdict.reasons for verdict in verdicts]
    # a label the row does not carry is still refused: the unit column is not a second label
    wrong = dict(rows[1], label="Nominal Voltage")
    assert not open_mode.judge({"values": [wrong]}, table, None)[0][0].kept
