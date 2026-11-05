"""The deterministic checkers, on the Markdownify harness's truth bundles (read-only fixtures). No LLM."""

from __future__ import annotations

import pathlib

import pytest

from datasheet_extractor import fields as fields_module, option1, option2

ROOT = pathlib.Path(__file__).resolve().parents[1]
HARNESS = pathlib.Path("/home/bam/markdownify/harness/out")
FIELDS = fields_module.load(ROOT / "fields" / "actuator.json")
LOOKUP = fields_module.by_key(FIELDS)

needs_harness = pytest.mark.skipif(not HARNESS.is_dir(), reason="the Markdownify harness output is not on this machine")


def _bundle(case: str, arm: str = "pdf") -> option1.Bundle:
    return option1.Bundle.load(HARNESS / case / arm)


def _cell(bundle: option1.Bundle, row_label: str, column: str) -> str:
    """The id of the truth cell at (row label, column header), found by text."""
    table = next(b for b in bundle.index["blocks"] if b["type"] == "table")
    grid = option1.Grid.of(bundle, table)
    for cell in grid.cells:
        if row_label in grid.row_headers(cell) and column in " ".join(grid.column_headers(cell)):
            return cell["id"]
    raise KeyError((row_label, column))


def _check(bundle, row, part_model):
    return option1.check_value(row, LOOKUP, bundle, part_model, {})


@needs_harness
def test_option1_keeps_the_right_column_and_drops_the_wrong_one():
    bundle = _bundle("04-multi-model")
    right = _cell(bundle, "Peak torque", "KM80-9")
    wrong = _cell(bundle, "Peak torque", "KM70-10")
    kept = _check(bundle, {"key": "peak_torque", "value": "18", "unit": "N·m", "raw": "18 N·m", "id": right}, "KM80-9")
    assert kept.kept, kept.reasons
    assert kept.cite["row"] == "Peak torque" and kept.cite["column"] == "KM80-9" and kept.cite["multi_model"]
    assert kept.grounding == bundle.blocks[right]["grounding"]
    dropped = _check(bundle, {"key": "peak_torque", "value": "24.8", "unit": "N·m", "raw": "24.8 N·m", "id": wrong},
                     "KM80-9")
    assert not dropped.kept and "does not name 'KM80-9'" in dropped.reasons[0]
    no_model = _check(bundle, {"key": "peak_torque", "value": "18", "unit": "N·m", "raw": "18 N·m", "id": right}, None)
    assert not no_model.kept and "no --part-model" in no_model.reasons[0]


@needs_harness
def test_option1_drops_wrong_label_raw_outside_the_cell_and_a_whole_table():
    bundle = _bundle("04-multi-model")
    peak = _cell(bundle, "Peak torque", "KM80-9")
    as_rated = _check(bundle, {"key": "rated_torque", "value": "18", "unit": "N·m", "raw": "18 N·m", "id": peak}, "KM80-9")
    assert not as_rated.kept and "labels" in as_rated.reasons[0]
    outside = _check(bundle, {"key": "peak_torque", "value": "9", "unit": "N·m", "raw": "9 N·m", "id": peak}, "KM80-9")
    assert not outside.kept and "is not inside" in outside.reasons[0]
    table = next(b["id"] for b in bundle.index["blocks"] if b["type"] == "table")
    whole = _check(bundle, {"key": "peak_torque", "value": "18", "unit": "N·m", "raw": "18 N·m", "id": table}, "KM80-9")
    assert not whole.kept and "whole table" in whole.reasons[0]
    unknown = _check(bundle, {"key": "peak_torque", "value": "18", "unit": "N·m", "raw": "18 N·m", "id": "cell-x"}, None)
    assert not unknown.kept and "not an id" in unknown.reasons[0]


@needs_harness
def test_option1_reads_a_merged_header_and_a_row_per_model():
    bundle = _bundle("05-merged-header")
    cell = _cell(bundle, "KM80-9", "Rated")
    rows = [c for c in option1.Grid.of(bundle, bundle.table_of(cell)).column_headers(bundle.blocks[cell])]
    assert rows == ["Torque", "Rated"]
    kept = _check(bundle, {"key": "rated_torque", "value": "9", "unit": "N·m", "raw": "9 N·m", "id": cell}, "KM80-9")
    assert kept.kept, kept.reasons
    other = _check(bundle, {"key": "rated_torque", "value": "9", "unit": "N·m", "raw": "9 N·m", "id": cell}, "KM70-10")
    assert not other.kept


@needs_harness
def test_option1_units_in_headers_and_prose_binding():
    bundle = _bundle("06-units-in-headers")
    cell = _cell(bundle, "KM80-9", "Rated torque (N·m)")
    kept = _check(bundle, {"key": "rated_torque", "value": "9", "unit": "N·m", "raw": "9", "id": cell}, "KM80-9")
    assert kept.kept, kept.reasons
    prose = _bundle("01-prose-spec")
    paragraph = next(b["id"] for b in prose.index["blocks"] if "peak torque of 18" in prose.text(b))
    ok = _check(prose, {"key": "peak_torque", "value": "18", "unit": "N·m", "raw": "18 N·m", "id": paragraph}, "KM80-9")
    assert ok.kept, ok.reasons
    swapped = _check(prose, {"key": "rated_torque", "value": "18", "unit": "N·m", "raw": "18 N·m", "id": paragraph},
                     "KM80-9")
    assert not swapped.kept


def test_model_names_are_whole_designations():
    assert option1.model_named("AK80-6", ["AK80-6 KV100"])
    assert not option1.model_named("AK80-6", ["AK80-64 KV80"])
    assert option1.model_named("AK10-9 V3.0", ["AK Series Robotic Actuator", "AK10-9 V3.0 KV60"])
    assert not option1.model_named("AK10-9 V3.0", ["AK10-9 V2.0 KV60"])


def test_labels_match_like_the_vocabulary():
    speed = LOOKUP["max_output_speed"]
    assert fields_module.label_found(speed, "No-Load Speed RPM")
    assert not fields_module.label_found(LOOKUP["gear_ratio"], "Operation")
    assert fields_module.label_found(LOOKUP["rated_torque"], "额定扭矩 Rated torque")
    assert option1.header_label(LOOKUP["peak_torque"], ["Max Torque Density N·m/kg"]) is None
    assert option1.header_label(LOOKUP["kv"], ["AK10-9 V3.0 KV60"]) is None


@needs_harness
def test_option2_html_quote_checks():
    path = HARNESS / "03-simple-table" / "html" / "source.html"
    source = option2.Source(path, "text/html")
    good = option2.check_value({"key": "peak_torque", "value": "24.8", "unit": "N·m", "raw": "24.8 N·m",
                                "quote": "Peak torque 24.8 N·m", "condition": ""}, LOOKUP, source, "KM70-10")
    assert good.kept, good.reasons
    assert "source_range" in good.grounding[0]["html"]
    invented = option2.check_value({"key": "peak_torque", "value": "25", "unit": "N·m", "raw": "25 N·m",
                                    "quote": "Peak torque 25 N·m", "condition": ""}, LOOKUP, source, "KM70-10")
    assert not invented.kept and "does not occur" in invented.reasons[0]


CUBEMARS_BUNDLE = pathlib.Path.home() / ".cache/datasheet-extractor/eval/bundles/8b0930e9c42f7d7384335c7e4a876ba3cf828477be7f498111786533f5e4a2dd/bundle"


@pytest.mark.skipif(not CUBEMARS_BUNDLE.is_dir(), reason="the eval cache's CubeMars AKE40-6 bundle is not here")
def test_option1_two_pair_rows_and_the_comparison_table():
    bundle = option1.Bundle.load(CUBEMARS_BUNDLE)
    nine = next(b["id"] for b in bundle.index["blocks"] if b["type"] == "table_cell" and bundle.text(b) == "9"
                and "Backlash" in (option1.Grid.of(bundle, bundle.table_of(b["id"])).own_label(b) or ""))
    as_rated = _check(bundle, {"key": "rated_torque", "value": "9", "unit": "N·m", "raw": "9", "id": nine}, "AKE40-6 KV90")
    assert not as_rated.kept
    backlash = _check(bundle, {"key": "backlash", "value": "9", "unit": "arcmin", "raw": "9", "id": nine}, "AKE40-6 KV90")
    assert backlash.kept, backlash.reasons
    size = next(b["id"] for b in bundle.index["blocks"] if b["type"] == "table_cell" and "51×24.5" in bundle.text(b))
    for key, printed in (("outer_diameter", "51"), ("axial_length", "24.5")):
        verdict = _check(bundle, {"key": key, "value": printed, "unit": "mm", "raw": "Ø51×24.5", "id": size}, "AKE40-6 KV90")
        assert verdict.kept, verdict.reasons
    other_row = next(b["id"] for b in bundle.index["blocks"] if b["type"] == "table_cell" and "98*61.7" in bundle.text(b).replace("\\", ""))
    other = _check(bundle, {"key": "outer_diameter", "value": "98", "unit": "mm", "raw": "Ф98*61.7", "id": other_row},
                   "AKE40-6 KV90")
    assert not other.kept and "does not name" in other.reasons[0]


@needs_harness
def test_option1_footnote_marker_glued_to_a_label_is_still_the_label():
    bundle = option1.Bundle.load(HARNESS / "runs" / "mdify" / "07-footnotes" / "pdf" / "bundle")
    cell = _cell(bundle, "Rated torque2", "Value")
    kept = _check(bundle, {"key": "rated_torque", "value": "9", "unit": "N·m", "raw": "9 N·m", "id": cell}, "KM80-9")
    assert kept.kept, kept.reasons


J60_BUNDLE = pathlib.Path.home() / ".cache/datasheet-extractor/eval/bundles/f53a5470"


@pytest.mark.skipif(not list(J60_BUNDLE.parent.glob("f53a5470*")), reason="the eval cache's J60 page bundle is not here")
def test_option1_label_block_right_before_a_value_block():
    bundle = option1.Bundle.load(next(J60_BUNDLE.parent.glob("f53a5470*")) / "bundle")
    value = next(b for b in bundle.index["blocks"] if b["type"] == "paragraph" and bundle.text(b) == "19.94Nm")
    kept = _check(bundle, {"key": "peak_torque", "value": "19.94", "unit": "N·m", "raw": "19.94Nm", "id": value["id"]},
                  "J60-6")
    assert kept.kept, kept.reasons
    assert kept.cite["row"] == "Peak Torque"
    speed = next(b for b in bundle.index["blocks"] if b["type"] == "paragraph" and bundle.text(b) == "24.18rad/s")
    wrong = _check(bundle, {"key": "peak_torque", "value": "24.18", "unit": "N·m", "raw": "24.18rad/s",
                            "id": speed["id"]}, "J60-6")
    assert not wrong.kept


def _hand_cell(bundle, text, row_label):
    grid = option1.Grid.of(bundle, next(b for b in bundle.index["blocks"] if b["type"] == "table"))
    return next(c["id"] for c in grid.cells if grid.texts[c["id"]] == text and row_label in grid.row_headers(c))


def test_option1_reads_html_entities_as_the_characters_they_print():
    # Markdownify writes a merged-cell table as HTML, so '~' arrives as '&#126;'; the cell is still a range
    from test_sources_extraction import table_bundle

    table = table_bundle([["Parameter", "Value"], ["Input voltage (V)", "0 &#126; 14.6"]])
    cell = _hand_cell(table, "0 ~ 14.6", "Input voltage (V)")
    for raw in ("0 &#126; 14.6", "0 ~ 14.6"):
        high = _check(table, {"key": "voltage_max", "value": "14.6", "unit": "V", "raw": raw, "id": cell}, None)
        assert high.kept, (raw, high.reasons)
        low = _check(table, {"key": "voltage_min", "value": "0", "unit": "V", "raw": raw, "id": cell}, None)
        assert not low.kept and "126" not in " ".join(low.reasons)
        torque = _check(table, {"key": "rated_torque", "value": "14.6", "unit": "N·m", "raw": raw, "id": cell}, None)
        assert not torque.kept


def test_stored_raw_is_the_decoded_text_and_the_model_copy_stays_in_proposed():
    # WHY: raw is what the source prints (truthful rendering); the model's '&#126;' copy is kept as proposed.raw
    from test_sources_extraction import table_bundle

    table = table_bundle([["Parameter", "Value"], ["Input voltage (V)", "0 &#126; 14.6"]])
    cell = _hand_cell(table, "0 ~ 14.6", "Input voltage (V)")
    row = {"key": "voltage_max", "value": "14.6", "unit": "V", "raw": "0 &#126; 14.6", "id": cell}
    stored = _check(table, row, None).to_dict()
    assert stored["raw"] == "0 ~ 14.6" and stored["proposed"]["raw"] == "0 &#126; 14.6"
    plain_row = _check(table, dict(row, raw="0 ~ 14.6"), None).to_dict()
    assert plain_row["raw"] == "0 ~ 14.6" and plain_row["proposed"]["raw"] == "0 ~ 14.6"


def test_option1_a_model_name_that_starts_with_a_digit_is_a_row_label():
    # JMC's selection list: one row per model, specs as columns; the target model is the second row
    from test_sources_extraction import table_bundle

    jmc = table_bundle([["Model", "Rated power (W)", "Rated torque (Nm)"],
                        ["60ASM100-1024C/2500C", "100", "0.32"],
                        ["60ASM200-1024C/2500C", "200", "0.64"]])
    grid = option1.Grid.of(jmc, next(b for b in jmc.index["blocks"] if b["type"] == "table"))
    assert grid.header_cols == 1
    right = _hand_cell(jmc, "0.64", "60ASM200-1024C/2500C")
    wrong = _hand_cell(jmc, "0.32", "60ASM100-1024C/2500C")
    kept = _check(jmc, {"key": "rated_torque", "value": "0.64", "unit": "N·m", "raw": "0.64", "id": right},
                  "60ASM200-1024C/2500C")
    assert kept.kept, kept.reasons
    assert kept.cite["row"] == "60ASM200-1024C/2500C" and kept.cite["multi_model"]
    dropped = _check(jmc, {"key": "rated_torque", "value": "0.32", "unit": "N·m", "raw": "0.32", "id": wrong},
                     "60ASM200-1024C/2500C")
    assert not dropped.kept and "does not name" in " ".join(dropped.reasons)
