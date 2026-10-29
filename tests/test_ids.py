"""Every value in values.json carries a stable, unique "id" (Markdownify CONTRACT §7, §7.1). No LLM."""

from __future__ import annotations

import json
import pathlib

from datasheet_extractor import cli, fields as fields_module, values as values_module

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIELDS = fields_module.load(ROOT / "fields" / "actuator.json")


def _verdict(key, cite=None, proposed=None, reasons=()):
    return values_module.Verdict(key, 20, "N·m", "20Nm", None, cite, [], list(reasons), proposed or {})


def _ids(doc):
    rows = doc["values"] + [entry for entry in doc["part"].values() if entry]
    return [row["id"] for row in rows]


def test_ids_cite_page_and_html_forms():
    verdicts = [_verdict("rated_torque_nm", {"id": "table_cell-58"}),
                _verdict("peak_torque_nm", None, {"page": 3}),
                _verdict("mass_g", None, {}, ["dropped"])]
    doc = values_module.document({}, "option2", None, {"manufacturer": None, "model": None}, verdicts)
    assert _ids(doc) == ["rated_torque_nm@table_cell-58", "peak_torque_nm@p3", "mass_g@html"]


def test_repeated_ids_get_suffixes_in_output_order_kept_and_dropped_alike():
    verdicts = [_verdict("rated_torque_nm", {"id": "block-1"}), _verdict("rated_torque_nm", {"id": "block-1"}, None,
                                                                          ["a repeat"]),
                _verdict("rated_torque_nm", {"id": "block-1"})]
    part = {"manufacturer": {"value": "Acme", "raw": "Acme", "cite": {"id": "block-1"}, "verdict": "kept"},
            "model": None}
    doc = values_module.document({}, "option1", None, part, verdicts)
    assert _ids(doc) == ["rated_torque_nm@block-1", "rated_torque_nm@block-1#2", "rated_torque_nm@block-1#3",
                         "part.manufacturer@block-1"]
    assert doc["part"]["model"] is None


def _run_dir(tmp_path):
    source = tmp_path / "sheet.html"
    source.write_text("<html><body><p>Acme Drives</p><p>Model AX-80</p><p>Rated torque 20Nm</p>"
                      "<p>Rated torque 20Nm</p></body></html>", encoding="utf-8")
    out = tmp_path / "run"
    (out / "llm").mkdir(parents=True)
    row = {"key": "rated_torque_nm", "value": "20", "unit": "N·m", "raw": "20Nm", "condition": "",
           "quote": "Rated torque 20Nm"}
    reply = {"part": {"manufacturer": {"value": "Acme", "quote": "Acme Drives"},
                      "model": {"value": "AX-80", "quote": "Model AX-80"}}, "values": [row, row]}
    (out / "llm" / "output.json").write_text(json.dumps(reply), encoding="utf-8")
    (out / "run.json").write_text(json.dumps({"fields": str(ROOT / "fields" / "actuator.json"), "source": str(source),
                                              "option": 2, "media_type": "text/html", "part_model": None}),
                                  encoding="utf-8")
    (out / "values.json").write_text(json.dumps({"source": {}, "extractor": {"pipeline": "option2", "model": None}}),
                                     encoding="utf-8")
    return out


def test_every_value_has_a_unique_id_and_two_rejudges_agree(tmp_path):
    out = _run_dir(tmp_path)
    cli.rejudge(out)
    first = (out / "values.json").read_text(encoding="utf-8")
    cli.rejudge(out)
    assert (out / "values.json").read_text(encoding="utf-8") == first
    doc = json.loads(first)
    ids = _ids(doc)
    assert len(ids) == 4 and len(set(ids)) == len(ids)                     # 2 values + 2 part entries
    assert all(isinstance(row.get("id"), str) and "@" in row["id"] for row in doc["values"])
    assert [row["id"] for row in doc["values"]] == ["rated_torque_nm@html", "rated_torque_nm@html#2"]
    assert doc["part"]["manufacturer"]["id"] == "part.manufacturer@html"
