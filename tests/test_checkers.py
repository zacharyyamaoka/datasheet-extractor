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
