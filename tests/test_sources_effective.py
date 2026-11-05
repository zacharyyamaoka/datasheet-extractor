"""The effective record (values.json ⊕ review.json) and a product's published datasheet (CONTRACT §5): every op,
last-op-wins, and what a published datasheet leaves out. Pure functions; no disk, no LLM."""

from __future__ import annotations

from datasheet_extractor.sources import effective as eff

AT = "2026-11-05T10:00:00Z"


def _values():
    def row(key, cite, value, verdict="kept"):
        return {"id": f"{key}@{cite}", "key": key, "value": value, "unit": "N·m", "raw": f"{value}Nm",
                "condition": None, "cite": {"id": cite}, "grounding": [{"page": 1, "box": [0.1, 0.2, 0.3, 0.4]}],
                "verdict": verdict, "reasons": [] if verdict == "kept" else ["no"], "proposed": {}}
    return {"schema": "extractor/values@0",
            "source": {"sha256": "ab" * 32, "url": "https://example.com/ds.pdf", "retrieved_at": AT},
            "extractor": {"tool": "datasheet-extractor", "version": "0.1.0", "model": "m", "pipeline": "option1"},
            "part": {"manufacturer": {"id": "part.manufacturer@block-1", "value": "Acme", "raw": "Acme",
                                      "cite": {"id": "block-1"}, "grounding": [], "verdict": "kept", "reasons": []},
                     "model": None},
            "values": [row("rated_torque", "cell-1", 12), row("peak_torque", "cell-2", 30),
                       row("mass", "cell-3", 480, "dropped")]}


def _review(*ops):
    stamped = [dict(op, author=op.get("author", "zach"), at=op.get("at", f"2026-11-05T11:00:{n:02d}Z"))
               for n, op in enumerate(ops)]
    return {"schema": "extractor/review@0", "values_sha256": "x", "ops": stamped}


def _row(record, value_id):
    return next(row for row in record["values"] if row["id"] == value_id)


def test_no_review_every_value_stands_as_the_ai_wrote_it():
    record = eff.effective(_values(), None, AT)
    assert [row["id"] for row in record["values"]] == ["rated_torque@cell-1", "peak_torque@cell-2", "mass@cell-3"]
    row = _row(record, "rated_torque@cell-1")
    assert (row["value"], row["rejected"], row["human"], row["products"]) == (12, False, False, [])
    assert row["provenance"] == {"by": "ai", "at": AT} and "ai" not in row
    assert record["part"]["manufacturer"]["value"] == "Acme" and record["part"]["model"] is None


def test_edit_replaces_the_field_and_keeps_the_original_as_ai():
    record = eff.effective(_values(), _review({"op": "edit", "value_id": "rated_torque@cell-1", "field": "value",
                                               "from": 12, "to": 12.5, "note": "per sales sheet"}), AT)
    row = _row(record, "rated_torque@cell-1")
    assert row["value"] == 12.5 and row["ai"] == {"value": 12}
    assert row["provenance"] == {"by": "zach", "at": "2026-11-05T11:00:00Z"} and row["note"] == "per sales sheet"


def test_two_edits_of_one_field_keep_the_ai_original_not_the_first_edit():
    record = eff.effective(_values(), _review(
        {"op": "edit", "value_id": "rated_torque@cell-1", "field": "value", "to": 13},
        {"op": "edit", "value_id": "rated_torque@cell-1", "field": "unit", "to": "mN·m"},
        {"op": "edit", "value_id": "rated_torque@cell-1", "field": "value", "to": 14}), AT)
    row = _row(record, "rated_torque@cell-1")
    assert (row["value"], row["unit"]) == (14, "mN·m")
    assert row["ai"] == {"value": 12, "unit": "N·m"}


def test_reject_marks_and_keeps_the_row():
    record = eff.effective(_values(), _review({"op": "reject", "value_id": "peak_torque@cell-2"}), AT)
    row = _row(record, "peak_torque@cell-2")
    assert row["rejected"] is True and row["value"] == 30
    assert len(record["values"]) == 3


def test_restore_clears_a_reject():
    record = eff.effective(_values(), _review({"op": "reject", "value_id": "peak_torque@cell-2"},
                                              {"op": "restore", "value_id": "peak_torque@cell-2"}), AT)
    assert _row(record, "peak_torque@cell-2")["rejected"] is False


def test_add_appends_a_human_row_with_its_own_or_no_grounding():
    record = eff.effective(_values(), _review(
        {"op": "add", "row": {"id": "h1", "key": "mass", "value": 750, "unit": "g", "raw": None, "grounding": None},
         "note": "weighed on the bench"},
        {"op": "add", "row": {"id": "h2", "key": "kv", "value": 100, "unit": "rpm/V", "raw": "100",
                              "grounding": [{"page": 2, "box": [0, 0, 1, 1]}]}}), AT)
    first, second = record["values"][-2:]
    assert (first["id"], first["human"], first["grounding"]) == ("h1", True, None)
    assert first["note"] == "weighed on the bench"
    assert first["provenance"] == {"by": "zach", "at": "2026-11-05T11:00:00Z"} and "ai" not in first
    assert second["grounding"] == [{"page": 2, "box": [0, 0, 1, 1]}]


def test_assign_star_maps_every_row_and_named_ids_map_only_those():
    record = eff.effective(_values(), _review(
        {"op": "assign", "products": ["p-24"], "value_ids": ["*"]},
        {"op": "assign", "products": ["p-48"], "value_ids": ["rated_torque@cell-1"]}), AT)
    assert _row(record, "rated_torque@cell-1")["products"] == ["p-24", "p-48"]
    assert _row(record, "peak_torque@cell-2")["products"] == ["p-24"]
    assert record["part"]["manufacturer"]["products"] == []


def test_unassign_removes_the_mapping():
    record = eff.effective(_values(), _review(
        {"op": "assign", "products": ["p-24", "p-48"], "value_ids": ["*"]},
        {"op": "unassign", "products": ["p-48"], "value_ids": ["peak_torque@cell-2"]}), AT)
    assert _row(record, "peak_torque@cell-2")["products"] == ["p-24"]
    assert _row(record, "rated_torque@cell-1")["products"] == ["p-24", "p-48"]


def test_last_op_on_a_value_wins():
    ops = [{"op": "reject", "value_id": "peak_torque@cell-2"}, {"op": "restore", "value_id": "peak_torque@cell-2"},
           {"op": "reject", "value_id": "peak_torque@cell-2"},
           {"op": "edit", "value_id": "peak_torque@cell-2", "field": "value", "to": 31},
           {"op": "edit", "value_id": "peak_torque@cell-2", "field": "value", "to": 32},
           {"op": "assign", "products": ["p"], "value_ids": ["peak_torque@cell-2"]},
           {"op": "unassign", "products": ["p"], "value_ids": ["peak_torque@cell-2"]},
           {"op": "assign", "products": ["p"], "value_ids": ["peak_torque@cell-2"]}]
    row = _row(eff.effective(_values(), _review(*ops), AT), "peak_torque@cell-2")
    assert (row["rejected"], row["value"], row["products"]) == (True, 32, ["p"])
    restored = _row(eff.effective(_values(), _review(*ops, {"op": "restore", "value_id": "peak_torque@cell-2"}), AT),
                    "peak_torque@cell-2")
    assert restored["rejected"] is False


def test_an_op_on_a_value_not_in_this_values_json_is_unmatched_not_applied():
    record = eff.effective(_values(), _review({"op": "reject", "value_id": "gone@cell-9"}), AT)
    assert record["unmatched"] == [0]
    assert not any(row["rejected"] for row in record["values"])


def test_published_leaves_out_rejected_and_dropped_and_marks_edits():
    record = eff.effective(_values(), _review(
        {"op": "assign", "products": ["p-24"], "value_ids": ["*"]},
        {"op": "reject", "value_id": "peak_torque@cell-2"},
        {"op": "edit", "value_id": "rated_torque@cell-1", "field": "value", "to": 12.5},
        {"op": "add", "row": {"id": "h1", "key": "kv", "value": 100, "unit": "rpm/V", "raw": None,
                              "grounding": None}},
        {"op": "assign", "products": ["p-24"], "value_ids": ["h1"]}), AT)
    sheet = eff.published("p-24", record, _values()["source"])
    assert sheet["product"] == "p-24"
    assert sheet["source"] == {"sha256": "ab" * 32, "url": "https://example.com/ds.pdf", "retrieved_at": AT}
    ids = [value["id"] for value in sheet["values"]]
    assert ids == ["rated_torque@cell-1", "h1"]                    # peak rejected, mass dropped by the checker
    edited = sheet["values"][0]
    assert edited["value"] == 12.5 and edited["grounding"] == [{"page": 1, "box": [0.1, 0.2, 0.3, 0.4]}]
    assert edited["provenance"] == {"by": "zach", "at": "2026-11-05T11:00:02Z", "ai": {"value": 12}}
    assert set(edited) == {"id", "key", "value", "unit", "raw", "grounding", "provenance"}
    assert sheet["values"][1]["provenance"] == {"by": "zach", "at": "2026-11-05T11:00:03Z"}


def test_published_carries_only_the_products_rows_and_untouched_rows_as_ai():
    record = eff.effective(_values(), _review(
        {"op": "assign", "products": ["p-24"], "value_ids": ["rated_torque@cell-1"]},
        {"op": "assign", "products": ["p-48"], "value_ids": ["peak_torque@cell-2"]}), AT)
    assert eff.products_of(record) == ["p-24", "p-48"]
    sheet = eff.published("p-48", record, _values()["source"])
    assert [value["id"] for value in sheet["values"]] == ["peak_torque@cell-2"]
    assert sheet["values"][0]["provenance"] == {"by": "ai", "at": AT}


def test_check_op_rejects_unknown_ids_and_malformed_ops():
    values, review = _values(), None
    assert eff.check_op({"op": "reject", "value_id": "nope"}, values, review) is not None
    assert eff.check_op({"op": "reject", "value_id": "mass@cell-3"}, values, review) is None
    assert eff.check_op({"op": "edit", "value_id": "mass@cell-3", "field": "verdict", "to": "kept"}, values, review)
    assert eff.check_op({"op": "edit", "value_id": "mass@cell-3", "field": "value"}, values, review)
    assert eff.check_op({"op": "assign", "products": ["p"], "value_ids": ["nope"]}, values, review)
    assert eff.check_op({"op": "assign", "products": ["q"], "value_ids": ["*"]}, values, review, {"p"})
    assert eff.check_op({"op": "assign", "products": ["p"], "value_ids": ["*"]}, values, review, {"p"}) is None
    assert eff.check_op({"op": "add", "row": {"id": "rated_torque@cell-1", "key": "x"}}, values, review)
    assert eff.check_op({"op": "delete", "value_id": "mass@cell-3"}, values, review)
    added = _review({"op": "add", "row": {"id": "h1", "key": "kv", "value": 1}})
    assert eff.check_op({"op": "reject", "value_id": "h1"}, values, added) is None
    assert eff.check_op({"op": "reject", "value_id": "part.manufacturer@block-1"}, values, review) is None


def _two_models():
    """A comparison table read twice: rated torque per model, the mass printed once for both."""
    values = _values()
    values["values"][0]["part_models"] = ["LRS-350-24"]
    values["values"][1]["part_models"] = ["LRS-350-48"]
    values["values"][2].update(part_models=["LRS-350-24", "LRS-350-48"], verdict="kept")
    values["extractor"]["part_models"] = [{"model": "LRS-350-24", "products": []},
                                          {"model": "LRS-350-48", "products": []}]
    return values


def _products(record):
    return {row["id"]: row["products"] for row in record["values"]}


def test_map_assigns_the_values_read_for_that_model():
    record = eff.effective(_two_models(), _review({"op": "map", "model": "LRS-350-24", "product": "p-24"}), AT)
    assert _products(record) == {"rated_torque@cell-1": ["p-24"], "peak_torque@cell-2": [], "mass@cell-3": ["p-24"]}
    assert record["models"] == {"LRS-350-24": "p-24"}
    assert eff.products_of(record) == ["p-24"]
    assert [v["id"] for v in eff.published("p-24", record, {})["values"]] == ["rated_torque@cell-1", "mass@cell-3"]


def test_unmap_and_remap_take_back_only_what_the_map_gave():
    record = eff.effective(_two_models(), _review(
        {"op": "map", "model": "LRS-350-24", "product": "p-24"},
        {"op": "assign", "products": ["p-24"], "value_ids": ["mass@cell-3"]},
        {"op": "map", "model": "LRS-350-24", "product": None}), AT)
    assert _products(record) == {"rated_torque@cell-1": [], "peak_torque@cell-2": [], "mass@cell-3": ["p-24"]}
    assert record["models"] == {}
    record = eff.effective(_two_models(), _review(
        {"op": "map", "model": "LRS-350-24", "product": "p-24"},
        {"op": "map", "model": "LRS-350-24", "product": "p-24b"}), AT)
    assert _products(record) == {"rated_torque@cell-1": ["p-24b"], "peak_torque@cell-2": [], "mass@cell-3": ["p-24b"]}


def test_last_op_wins_between_map_and_assign():
    map_24 = {"op": "map", "model": "LRS-350-24", "product": "p-24"}
    unassign = {"op": "unassign", "products": ["p-24"], "value_ids": ["mass@cell-3"]}
    assign_other = {"op": "assign", "products": ["p-24"], "value_ids": ["peak_torque@cell-2"]}
    after = eff.effective(_two_models(), _review(map_24, unassign, assign_other), AT)      # explicit ops after win
    assert _products(after) == {"rated_torque@cell-1": ["p-24"], "peak_torque@cell-2": ["p-24"], "mass@cell-3": []}
    before = eff.effective(_two_models(), _review(unassign, map_24), AT)                  # a later map wins
    assert _products(before)["mass@cell-3"] == ["p-24"]
    unmapped = eff.effective(_two_models(), _review(map_24, assign_other, dict(map_24, product=None)), AT)
    assert _products(unmapped) == {"rated_torque@cell-1": [], "peak_torque@cell-2": ["p-24"], "mass@cell-3": []}


def test_a_map_op_is_checked():
    values = _two_models()
    assert eff.check_op({"op": "map", "model": "LRS-350-24", "product": "p-24"}, values, None, {"p-24"}) is None
    assert eff.check_op({"op": "map", "model": "LRS-350-24", "product": None}, values, None, {"p-24"}) is None
    assert "unknown product" in eff.check_op({"op": "map", "model": "LRS-350-24", "product": "ghost"}, values, None,
                                             {"p-24"})
    assert "model" in eff.check_op({"op": "map", "model": "", "product": "p-24"}, values, None, {"p-24"})
    assert "product" in eff.check_op({"op": "map", "model": "LRS-350-24"}, values, None, {"p-24"})
