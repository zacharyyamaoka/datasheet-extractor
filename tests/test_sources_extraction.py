"""Which part model a batch's product is in a comparison table (extraction.part_models), and the open mode's checker
(open_mode.py), on tiny hand-built bundles. No LLM."""

from __future__ import annotations

from datasheet_extractor import option1
from datasheet_extractor.sources import extraction, open_mode


def table_bundle(rows: list[list[str]], prose: str = "") -> option1.Bundle:
    """A bundle holding one GFM table (first row = header) and, after it, one paragraph."""
    markdown, blocks, cell_ids, number = "", [], [], 1
    table_start = 0
    for r, row in enumerate(rows):
        markdown += "|"
        for c, text in enumerate(row):
            markdown += " "
            number += 1
            start = len(markdown)
            markdown += text
            blocks.append({"id": f"table_cell-{number}", "type": "table_cell", "row": r, "col": c,
                           "kind": "columnHeader" if r == 0 else None, "range": {"start": start, "end": len(markdown)},
                           "grounding": [{"page": 1, "box": [0, 0, 1, 1]}]})
            cell_ids.append(f"table_cell-{number}")
            markdown += " |"
        markdown += "\n"
        if r == 0:
            markdown += "|" + " --- |" * len(row) + "\n"
    blocks.insert(0, {"id": "table-1", "type": "table", "children": cell_ids,
                      "range": {"start": table_start, "end": len(markdown)}})
    if prose:
        markdown += "\n"
        start = len(markdown)
        markdown += prose
        blocks.append({"id": f"paragraph-{number + 1}", "type": "paragraph", "range": {"start": start,
                                                                                        "end": len(markdown)},
                       "grounding": [{"page": 1, "box": [0, 0, 1, 1]}]})
    index = {"blocks": blocks}
    return option1.Bundle(None, markdown, index, {block["id"]: block for block in blocks})


JMC = table_bundle([["Model", "Rated power (W)", "Rated torque (Nm)"],
                    ["60ASM100-1024C/2500C", "100", "0.32"],
                    ["60ASM200-1024C/2500C", "200", "0.64"]])


def test_a_product_names_its_row_when_the_table_drops_an_option_segment():
    # JMC's model code 60ASM200-5-1024C carries the pole pairs (5); its selection list prints 60ASM200-1024C/2500C
    product = {"id": "jmc-60asm200-5-1024c", "title": "JMC 60ASM200-5-1024C", "model": "60ASM200-5-1024C"}
    assert extraction.part_models(JMC, [product]) == [("60ASM200-1024C/2500C", ["jmc-60asm200-5-1024c"])]


def test_rows_that_differ_only_by_model_map_each_product_to_its_own_row():
    products = [{"id": "a", "title": "60ASM100-5-1024C"}, {"id": "b", "title": "60ASM200-5-1024C"}]
    assert extraction.part_models(JMC, products) == [("60ASM100-1024C/2500C", ["a"]),
                                                     ("60ASM200-1024C/2500C", ["b"])]


def test_a_neighbouring_model_is_never_taken_for_the_product():
    table = table_bundle([["Model", "AK80-64", "AK80-8"], ["Rated torque (Nm)", "48", "10"]])
    assert extraction.part_models(table, [{"id": "x", "title": "AK80-9"}]) == [(None, [])]
    other = {"id": "y", "title": "60ASM400-5-1024C"}
    assert extraction.part_models(JMC, [other]) == [(None, [])]


def test_an_exact_name_wins():
    table = table_bundle([["MODEL", "LRS-350-12", "LRS-350-24"], ["RATED POWER", "348W", "350.4W"]])
    assert extraction.part_models(table, [{"id": "p", "title": "Mean Well LRS-350-24"}]) == [("LRS-350-24", ["p"])]


# -- open mode -------------------------------------------------------------------------------------------------------

PSU = table_bundle([["MODEL", "LRS-350-12", "LRS-350-24"],
                    ["RATED POWER", "348W", "350.4W"],
                    ["RIPPLE & NOISE (max.)", "150mVp-p", "150mVp-p"]],
                   prose="Cooling Forced air cooling by built-in DC fan")


def _cell(bundle, text, row_label):
    grid = option1.Grid.of(bundle, next(b for b in bundle.index["blocks"] if b["type"] == "table"))
    return next(c["id"] for c in grid.cells if grid.texts[c["id"]] == text and row_label in grid.row_headers(c))


def test_open_mode_keeps_a_value_its_cell_and_headers_confirm():
    output = {"values": [{"label": "RATED POWER", "value": "350.4", "unit": "W", "raw": "350.4W", "condition": "",
                          "id": _cell(PSU, "350.4W", "RATED POWER")}],
              "part": {"manufacturer": {"value": "", "raw": "", "id": ""}, "model": {"value": "", "raw": "", "id": ""}}}
    verdicts, _ = open_mode.judge(output, PSU, "LRS-350-24")
    assert [(v.key, v.value, v.unit, v.reasons) for v in verdicts] == [("spec.rated_power", 350.4, "W", [])]
    assert verdicts[0].cite["column"] == "LRS-350-24" and verdicts[0].cite["row"] == "RATED POWER"


def test_open_mode_drops_what_the_structure_does_not_confirm():
    wrong_column = _cell(PSU, "348W", "RATED POWER")
    prose = next(b["id"] for b in PSU.index["blocks"] if b["type"] == "paragraph")
    rows = [
        {"label": "RATED POWER", "value": "348", "unit": "W", "raw": "348W", "id": wrong_column},          # not 24
        {"label": "RATED POWER", "value": "350", "unit": "W", "raw": "350.4W",
         "id": _cell(PSU, "350.4W", "RATED POWER")},  # unreadable
        {"label": "Efficiency", "value": "350.4", "unit": "W", "raw": "350.4W",
         "id": _cell(PSU, "350.4W", "RATED POWER")},                                                         # no label
        {"label": "Cooling", "value": "Forced air", "unit": "", "raw": "Forced air cooling", "id": prose},
        {"label": "Cooling", "value": "fan", "unit": "", "raw": "liquid cooling", "id": prose},  # raw absent
        {"label": "Weight", "value": "1", "unit": "kg", "raw": "1kg", "id": "table_cell-999"},  # no such id
    ]
    verdicts, _ = open_mode.judge({"values": [dict(row, condition="") for row in rows]}, PSU, "LRS-350-24")
    kept = [(v.key, v.value) for v in verdicts if v.kept]
    assert kept == [("spec.cooling", "Forced air")]
    reasons = [" ".join(v.reasons) for v in verdicts]
    assert "LRS-350-24" in reasons[0] and "350" in reasons[1] and "Efficiency" in reasons[2]
    assert "not inside" in reasons[4] and "not an id" in reasons[5]


def test_open_mode_keys_are_slugs_of_the_printed_label():
    assert open_mode.key_of("RIPPLE & NOISE (max.)") == "spec.ripple_noise_max"
    assert open_mode.key_of("额定功率") == "spec.额定功率"
