"""Copy as Spec: the kept rows of a source as paste-ready `Spec(...)` dict entries for a robotparts part file. Pure
function tests on a fixture (a numeric value, a string, a two-column variant table, a value with a quote character),
the block exec'd inside `specs={...}` with robotparts' own Spec, and the CLI against a temp --home."""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys

import pytest

from datasheet_extractor.sources import effective as eff
from datasheet_extractor.sources import spec as spec_module
from datasheet_extractor.sources.store import write_json

ROBOTPARTS = pathlib.Path("/home/bam/robotparts/python")
needs_robotparts = pytest.mark.skipif(not (ROBOTPARTS / "robotparts").is_dir(), reason="robotparts is not on this machine")

SHA = "ab" * 32
URL = "https://www.steadywin.cn/en/h-pd-9.html"
AT = "2026-11-16T09:30:00Z"


def _row(key, value, unit, raw, cell, column=None, row_label=None, verdict="kept", page=None, condition=None):
    cite = {"id": cell}
    if column:
        cite["column"] = column
    if row_label:
        cite["row"] = row_label
    return {"id": f"{key}@{cell}", "key": key, "value": value, "unit": unit, "raw": raw, "condition": condition,
            "cite": cite, "grounding": [{"page": page, "box": [0, 0, 1, 1]}] if page else [], "verdict": verdict,
            "reasons": [] if verdict == "kept" else ["no"], "proposed": {}}


def _values():
    return {"schema": "extractor/values@0", "source": {"sha256": SHA, "url": URL, "retrieved_at": AT},
            "part": {"manufacturer": {"id": "part.manufacturer@b1", "value": "SteadyWin", "raw": "SteadyWin",
                                      "cite": {"id": "b1"}, "grounding": [], "verdict": "kept", "reasons": []},
                     "model": None},
            "values": [
                _row("rated_torque", 1.05, "N·m", "1.05", "c1", "GDZ34", "Rated torque"),
                _row("torque_constant", 0.62, "N·m/A", "0.62", "c2", "GDS34", "Torque constant"),
                _row("drivetrain", "geared", "", "Geared", "c3"),
                _row("mass", 0.14, "kg", "140 g", "c4", condition="without driver"),
                _row("model_note", 5, "", 'the "5" mark', "c5"),
                _row("peak_torque", 3.82, "N·m", "3.82", "c6", "GDZ34", "Peak torque", verdict="dropped"),
                _row("backlash", 15, "arcmin", "15", "c7", "GDZ34", "Backlash"),
            ]}


def _record(*ops):
    review = {"schema": "extractor/review@0", "values_sha256": "x",
              "ops": [dict(op, author="zach", at="2026-11-16T10:00:00Z") for op in ops]}
    return eff.effective(_values(), review, AT)


def _block(record=None, **overrides):
    record = record or _record()
    options = dict(source_url=URL, sha256=SHA, retrieved_at=AT, manufacturer="SteadyWin")
    options.update(overrides)
    return spec_module.as_spec(spec_module.kept_rows(record), **options)


def _entries(block):
    """The block parsed as the body of a dict: {key: ast.Call}."""
    tree = ast.parse("{\n" + block + "\n}", mode="eval").body
    return {key.value: call for key, call in zip(tree.keys, tree.values)}


def _kwargs(call):
    return {keyword.arg: ast.literal_eval(keyword.value) for keyword in call.keywords}


def test_kept_rows_leave_out_rejected_and_checker_dropped_rows():
    record = _record({"op": "reject", "value_id": "backlash@c7"})
    assert [row["key"] for row in spec_module.kept_rows(record)] == ["rated_torque", "torque_constant", "drivetrain",
                                                                    "mass", "model_note"]


def test_kept_rows_can_be_one_product_and_a_restored_row_returns():
    record = _record({"op": "assign", "products": ["gim"], "value_ids": ["rated_torque@c1", "mass@c4"]},
                     {"op": "reject", "value_id": "mass@c4"}, {"op": "restore", "value_id": "mass@c4"})
    assert [row["key"] for row in spec_module.kept_rows(record, "gim")] == ["rated_torque", "mass"]
    assert spec_module.kept_rows(record, "other") == []


def test_a_numeric_value_is_a_number_with_its_unit_source_raw_and_a_note():
    call = _entries(_block())["mass"]
    args = [ast.literal_eval(a) for a in call.args]
    kwargs = _kwargs(call)
    assert args == [0.14, "kg"]
    assert kwargs["source_url"] == URL and kwargs["source_kind"] == "manufacturer" and kwargs["raw"] == "140 g"
    assert kwargs["note"] == f"without driver; captured 2026-11-16 sha256 {SHA[:12]}"


def test_a_string_value_stays_a_string_and_a_unitless_value_has_an_empty_unit():
    call = _entries(_block())["drivetrain"]
    assert [ast.literal_eval(a) for a in call.args] == ["geared", ""]


def test_a_two_column_table_names_the_column_in_every_line_that_has_one():
    entries = _entries(_block())
    assert _kwargs(entries["rated_torque"])["note"].startswith("GDZ34 column; ")
    assert _kwargs(entries["torque_constant"])["note"].startswith("GDS34 column; ")
    assert "column" not in _kwargs(entries["drivetrain"])["note"]


def test_a_column_is_named_by_what_tells_it_from_the_others_not_the_shared_header_path():
    rows = [dict(row, cite=dict(row["cite"], column=f"GIM4305-10 / Y / {row['cite']['column']}"))
            for row in spec_module.kept_rows(_record()) if row["cite"].get("column")]
    entries = _entries(spec_module.as_spec(rows, source_url=URL, sha256=SHA, manufacturer="SteadyWin"))
    assert _kwargs(entries["rated_torque"])["note"].startswith("GDZ34 column; ")
    assert _kwargs(entries["torque_constant"])["note"].startswith("GDS34 column; ")


def test_one_column_only_is_not_a_variant_table():
    record = _record()
    only = [row for row in spec_module.kept_rows(record) if row["key"] in ("rated_torque", "mass")]
    block = spec_module.as_spec(only, source_url=URL, sha256=SHA, retrieved_at=AT, manufacturer="SteadyWin")
    assert "column" not in _kwargs(_entries(block)["rated_torque"])["note"]


def test_a_quote_character_in_raw_is_escaped():
    block = _block()
    call = _entries(block)["model_note"]
    assert _kwargs(call)["raw"] == 'the "5" mark' and _kwargs(call)["quote"] == 'the "5" mark'


def test_the_quote_is_the_cell_text_from_the_bundle_with_its_row_label_when_the_cell_lacks_it():
    block = _block(texts={"c1": "1.05 N·m"})
    assert _kwargs(_entries(block)["rated_torque"])["quote"] == "Rated torque 1.05 N·m"
    assert _kwargs(_entries(block)["mass"])["quote"] == "140 g"          # no bundle text: raw is the quote


def test_page_only_for_a_paged_source():
    record = _record()
    rows = [dict(row, grounding=[{"page": 3, "box": [0, 0, 1, 1]}]) for row in spec_module.kept_rows(record)]
    paged = spec_module.as_spec(rows, source_url=URL, sha256=SHA, paged=True, manufacturer="SteadyWin")
    flat = spec_module.as_spec(rows, source_url=URL, sha256=SHA, paged=False, manufacturer="SteadyWin")
    assert _kwargs(_entries(paged)["mass"])["page"] == 3
    assert "page" not in _kwargs(_entries(flat)["mass"])


def test_source_kind_is_manufacturer_on_the_makers_host_and_retailer_elsewhere():
    assert _kwargs(_entries(_block())["mass"])["source_kind"] == "manufacturer"
    shop = "https://aifitlab.com/collections/steadywin/products/steadywin-gim4305"
    block = _block(source_url=shop)
    assert _kwargs(_entries(block)["mass"])["source_kind"] == "retailer" and "TODO" not in block


def test_source_kind_is_a_todo_when_the_maker_or_the_url_is_unknown():
    # WHY example.com and not the maker's own host: a host on a makers.toml domain is no longer a guess
    for options in ({"manufacturer": None, "source_url": "https://example.com/gim4305"},
                    {"source_url": "file:///tmp/sheet.pdf"}):
        block = _block(**options)
        assert _kwargs(_entries(block)["mass"])["source_kind"] == "manufacturer"
        assert block.splitlines()[0].lstrip().startswith("# TODO") and "source_kind" in block.splitlines()[0]


MAKERS = """
[steadywin]
name = "SteadyWin"
domains = ["steadywin.cn", "14180476.s21i.faiusr.com"]

[stepperonline]
name = "StepperOnline (OMC Corporation)"
domains = ["omc-stepperonline.com"]

[cubemars]
name = "CubeMars"
domains = ["cubemars.com"]
"""


@pytest.fixture
def makers_toml(tmp_path, monkeypatch):
    path = tmp_path / "makers.toml"
    path.write_text(MAKERS, encoding="utf-8")
    monkeypatch.setattr(spec_module, "MAKERS_TOML", path)
    return path


def _kind(source_url, manufacturer=None):
    block = _block(source_url=source_url, manufacturer=manufacturer)
    return _kwargs(_entries(block)["mass"])["source_kind"], block.lstrip().startswith("# TODO")


def test_a_host_on_a_makers_domain_is_the_manufacturer_even_with_no_maker_named(makers_toml):
    # the GIM4305-10 page names no maker in a block the extractor can cite: the domain alone says it
    assert _kind("https://www.steadywin.cn/en/h-pd-9.html") == ("manufacturer", False)
    assert _kind("https://14180476.s21i.faiusr.com/x.pdf") == ("manufacturer", False)
    assert _kind("https://www.cubemars.com/goods.php?id=1", "Some Other Name") == ("manufacturer", False)
    assert _kind("https://notsteadywin.cn/") == ("manufacturer", True)         # a sibling host is not a subdomain


def test_a_known_shop_is_a_retailer_unless_the_maker_printed_is_the_shop(makers_toml):
    for shop in ("https://aifitlab.com/p/gim4305", "https://www.alibaba.com/product-detail/x.html",
                 "https://www.aliexpress.com/item/1.html"):
        assert _kind(shop) == ("retailer", False), shop
        assert _kind(shop, "SteadyWin") == ("retailer", False), shop
    stepper = "https://www.omc-stepperonline.com/gearbox/ad60-g10"
    assert _kind(stepper) == ("retailer", False)
    assert _kind(stepper, "SteadyWin") == ("retailer", False)
    assert _kind(stepper, "StepperOnline") == ("manufacturer", False)
    assert _kind(stepper, "OMC Corporation") == ("manufacturer", False)


def test_an_unlisted_host_keeps_the_name_guess_and_the_todo(makers_toml):
    assert _kind("https://example.com/sheet", None) == ("manufacturer", True)
    assert _kind("https://example.com/sheet", "SteadyWin") == ("retailer", False)
    assert _kind("https://www.steadywin.example/sheet", "SteadyWin") == ("manufacturer", False)


def test_a_missing_or_broken_makers_file_falls_back_to_the_name_guess(tmp_path, monkeypatch):
    monkeypatch.setattr(spec_module, "MAKERS_TOML", tmp_path / "nowhere.toml")
    assert _kind("https://www.steadywin.cn/en/h-pd-9.html", "SteadyWin") == ("manufacturer", False)
    assert _kind("https://www.steadywin.cn/en/h-pd-9.html") == ("manufacturer", True)
    assert _kind("https://aifitlab.com/p/x", "SteadyWin") == ("retailer", False)
    broken = tmp_path / "broken.toml"
    broken.write_text("[steadywin\ndomains = ", encoding="utf-8")
    monkeypatch.setattr(spec_module, "MAKERS_TOML", broken)
    assert _kind("https://www.steadywin.cn/en/h-pd-9.html") == ("manufacturer", True)


def test_makers_toml_is_read_at_every_call_not_copied(makers_toml):
    assert _kind("https://www.example-maker.com/x") == ("manufacturer", True)
    makers_toml.write_text(MAKERS + '\n[example]\nname = "Example"\ndomains = ["example-maker.com"]\n', encoding="utf-8")
    assert _kind("https://www.example-maker.com/x") == ("manufacturer", False)


@needs_robotparts
def test_the_real_robotparts_registry_knows_steadywin_and_the_shops():
    assert spec_module.MAKERS_TOML == pathlib.Path("/home/bam/robotparts/makers.toml")
    if spec_module.MAKERS_TOML.is_file():
        assert _kind("https://www.steadywin.cn/en/h-pd-9.html") == ("manufacturer", False)
        assert _kind("https://aifitlab.com/p/x") == ("retailer", False)


def test_a_repeated_key_keeps_the_first_and_comments_out_the_rest():
    record = _record({"op": "add", "row": {"id": "h1", "key": "mass", "value": 0.15, "unit": "kg", "raw": "150 g"}})
    block = _block(record)
    assert list(_entries(block)) == ["rated_torque", "torque_constant", "drivetrain", "mass", "model_note", "backlash"]
    assert any(line.lstrip().startswith("# ") and '"mass"' in line and "duplicate" in line
               for line in block.splitlines())


def test_a_human_row_without_raw_or_grounding_still_makes_a_line():
    record = _record({"op": "add", "row": {"id": "h1", "key": "kv", "value": 100, "unit": "rpm/V"}, "note": "bench"})
    kwargs = _kwargs(_entries(_block(record))["kv"])
    assert "raw" not in kwargs and "quote" not in kwargs and kwargs["note"].startswith("bench; captured")


def test_a_list_value_of_one_name_is_that_string_and_an_edit_to_text_is_numeric_when_it_parses():
    record = _record({"op": "edit", "value_id": "drivetrain@c3", "field": "value", "to": ["geared"]},
                     {"op": "edit", "value_id": "mass@c4", "field": "value", "to": "0.150"})
    entries = _entries(_block(record))
    assert ast.literal_eval(entries["drivetrain"].args[0]) == "geared"
    assert ast.literal_eval(entries["mass"].args[0]) == 0.15


def test_nothing_kept_is_an_empty_block():
    assert spec_module.as_spec([], source_url=URL, sha256=SHA) == ""


@needs_robotparts
def test_the_block_runs_inside_a_specs_dict_with_robotparts_own_spec():
    namespace: dict = {}
    code = ("from robotparts import Spec\nspecs = {\n" + _block(texts={"c1": "1.05 N·m"}) + "\n}\n")
    exec(compile(code, "<paste>", "exec"), _with_path(namespace))
    specs = namespace["specs"]
    assert set(specs) == {"rated_torque", "torque_constant", "drivetrain", "mass", "model_note", "backlash"}
    assert specs["rated_torque"].value == 1.05 and specs["rated_torque"].quote == "Rated torque 1.05 N·m"
    assert specs["model_note"].raw == 'the "5" mark'


def _with_path(namespace):
    sys.path.insert(0, str(ROBOTPARTS))
    return namespace


# -- the CLI ----------------------------------------------------------------------------------------------------------

def _home(tmp_path, with_bundle=False):
    directory = tmp_path / "sources" / SHA[:12]
    directory.mkdir(parents=True)
    bundle = directory / "bundle"
    bundle.mkdir()
    source = {"id": SHA[:12], "batch": "b1", "kind": "url", "name": URL, "url": URL, "stage": "extracted",
              "sha256": SHA, "bundle": str(bundle)}
    write_json(directory / "source.json", source)
    write_json(directory / "values.json", _values())
    return tmp_path


def test_cli_as_spec_prints_the_block_for_a_source(tmp_path, capsys):
    from datasheet_extractor import cli
    home = _home(tmp_path)
    # WHY --spec (2026-11-24): the default form is now the robot_models source file; Spec lines sit behind the flag
    assert cli.main(["as-spec", SHA[:12], "--home", str(home), "--spec"]) == 0
    out = capsys.readouterr().out
    assert list(_entries(out)) == ["rated_torque", "torque_constant", "drivetrain", "mass", "model_note", "backlash"]
    assert "peak_torque" not in out and f'source_url="{URL}"' in out


def test_the_app_answers_the_same_block_and_a_count_for_the_page(tmp_path):
    from datasheet_extractor.sources import server as server_module
    app = server_module.App(_home(tmp_path))
    answer = app.as_spec(SHA[:12], form="spec")          # WHY form: the source file is the default since 11-24
    assert answer["count"] == 6 and list(_entries(answer["text"])) == [
        "rated_torque", "torque_constant", "drivetrain", "mass", "model_note", "backlash"]
    with pytest.raises(server_module.ApiError) as error:
        app.as_spec(SHA[:12], "no-such-product")
    assert error.value.status == 400


def test_cli_as_spec_names_the_known_sources_when_the_id_is_wrong(tmp_path, capsys):
    from datasheet_extractor import cli
    home = _home(tmp_path)
    assert cli.main(["as-spec", "nope", "--home", str(home)]) == 1
    assert SHA[:12] in capsys.readouterr().err


def test_cli_as_spec_with_nothing_kept_says_so_and_exits_1(tmp_path, capsys):
    from datasheet_extractor import cli
    home = _home(tmp_path)
    ops = [{"op": "reject", "value_id": row["id"], "author": "z", "at": AT} for row in _values()["values"]]
    write_json(home / "sources" / SHA[:12] / "review.json", {"schema": "extractor/review@0", "values_sha256": "x",
                                                            "ops": ops})
    assert cli.main(["as-spec", SHA[:12], "--home", str(home)]) == 1
    assert "no kept rows" in capsys.readouterr().err
