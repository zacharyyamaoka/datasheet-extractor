"""Copy as Spec's source-file form: the kept rows as a whole robot_models `sources/<name>.py` (a `Source(...)` header
pinned by sha256, then `values = <Type>(key=Stated(value, unit, as_written, where=...))`), and robotparts' own parts
(user/parts) in the product list so a batch can target them.

The emitted file is loaded with robot_models' own loader beside the raw file it names, and every `where` is parsed
with source_msgs: a paste that would not load, or a selector robotparts' test_every_where_is_a_source_msgs_selector
would refuse, fails here first.
"""

from __future__ import annotations

import ast
import json
import pathlib
import shutil
import sys

import pytest
from source_msgs import PageBox, TextQuote, parse

from datasheet_extractor import fields as fields_module
from datasheet_extractor import option1
from datasheet_extractor.sources import effective as eff
from datasheet_extractor.sources import products as products_module
from datasheet_extractor.sources import server as server_module
from datasheet_extractor.sources import spec as spec_module
from datasheet_extractor.sources.store import write_json
from datasheet_extractor.values import document

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUNDLES = ROOT / "tests" / "fixtures" / "bundles"
FIELDS = fields_module.load(ROOT / "fields" / "actuator.json")
ROBOT_MODELS = spec_module.ROBOT_MODELS_SRC
ROBOTPARTS = pathlib.Path("/home/bam/robotparts")
needs_robot_models = pytest.mark.skipif(not (ROBOT_MODELS / "robot_models").is_dir(),
                                        reason="robot_models is not on this machine")

PART_PY = '''from robotparts import RobotPart
part = RobotPart("{id}", category="actuator", brand="Acme", model="{model}")
'''
SOURCED_PART_PY = '''from robot_models.actuators.servo import IntegratedServo
from robot_models.datasheet import Sources

from robotparts import RobotPart

sources = Sources(__file__)
part = RobotPart("{id}", category="actuator", brand="Acme", model="{model}")
datasheet = IntegratedServo.from_sources(sources, prefer=[])
'''


def _home(tmp_path: pathlib.Path, name: str, products: list[str] | None = None) -> tuple[pathlib.Path, str]:
    """A Sources home with one extracted source: the fixture's recorded reply replayed through the checker (no model
    call), assigned to `products`."""
    directory = BUNDLES / name
    case = json.loads((directory / "case.json").read_text(encoding="utf-8"))
    bundle = option1.Bundle.load(directory)
    verdicts, part = option1.judge(json.loads((directory / "reply.json").read_text(encoding="utf-8")), FIELDS, bundle,
                                   case["part_model"])
    made = json.loads((directory / "source.json").read_text(encoding="utf-8"))
    retrieval = made["retrievals"][-1]
    record = {"sha256": made["sha256"], "url": retrieval["url"], "retrieved_at": retrieval["retrieved_at"]}
    values = document(record, "option1", case["model"], part, verdicts)
    source_id = made["sha256"][:12]
    folder = tmp_path / "home" / "sources" / source_id
    folder.mkdir(parents=True)
    write_json(folder / "source.json", {"id": source_id, "batch": "b1", "kind": "url", "name": record["url"],
                                        "url": record["url"], "stage": "extracted", "sha256": made["sha256"],
                                        "bundle": str(directory), "category": "actuator"})
    write_json(folder / "values.json", values)
    if products:
        write_json(folder / "review.json", {"schema": "extractor/review@0", "values_sha256": "x", "ops": [
            {"op": "assign", "products": products, "value_ids": ["*"], "author": "zach", "at": "2026-11-24T00:00:00Z"}]})
    return tmp_path / "home", source_id


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """A robotparts-shaped repo: one catalogue part, one own part that is also in the catalogue, one own part only."""
    repo = tmp_path / "robotparts"
    for part_id, model in (("acme-km45-10", "KM45-10"), ("acme-km70-10", "KM70-10")):
        (repo / "parts" / part_id).mkdir(parents=True)
        (repo / "parts" / part_id / "part.py").write_text(PART_PY.format(id=part_id, model=model), encoding="utf-8")
    own = repo / "user" / "parts" / "actuators" / "servo" / "acme-km45-10"
    (own / "sources").mkdir(parents=True)
    (own / "acme-km45-10.py").write_text(SOURCED_PART_PY.format(id="acme-km45-10", model="KM45-10"), encoding="utf-8")
    only = repo / "user" / "parts" / "power" / "acme-psu"
    only.mkdir(parents=True)
    (only / "acme-psu.py").write_text(PART_PY.format(id="acme-psu", model="PSU-1"), encoding="utf-8")
    # a source file named like a part folder must not count as a part
    (own / "sources" / "sources.py").write_text(PART_PY.format(id="sources", model="x"), encoding="utf-8")
    return repo


def _wheres(text: str) -> dict[str, str]:
    """{key: where} of every Stated(...) in a source file's text (keywords and extra entries)."""
    out = {}
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.keyword) and isinstance(node.value, ast.Call) and \
                getattr(node.value.func, "id", None) == "Stated":
            out[node.arg] = next((ast.literal_eval(k.value) for k in node.value.keywords if k.arg == "where"), None)
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(value, ast.Call) and getattr(value.func, "id", None) == "Stated":
                    out[ast.literal_eval(key)] = next((ast.literal_eval(k.value) for k in value.keywords
                                                       if k.arg == "where"), None)
    return out


# --- the product list -------------------------------------------------------------------------------------------------

def test_the_robotparts_provider_lists_own_parts_first_with_their_folder(tmp_path):
    repo = _repo(tmp_path)
    listed = products_module.RobotParts(repo).list()
    assert [product["id"] for product in listed] == ["acme-km45-10", "acme-psu", "acme-km70-10"]
    own = {product["id"]: product for product in listed}
    assert own["acme-km45-10"]["provider"] == "robotparts user/parts"
    assert own["acme-km45-10"]["folder"] == str(repo / "user" / "parts" / "actuators" / "servo" / "acme-km45-10")
    assert own["acme-psu"]["folder"].endswith("power/acme-psu") and own["acme-psu"]["model"] == "PSU-1"
    assert own["acme-km70-10"]["provider"] == "robotparts" and "folder" not in own["acme-km70-10"]


def test_an_own_parts_type_is_read_from_its_from_sources_call_without_importing_it(tmp_path):
    repo = _repo(tmp_path)
    folder = repo / "user" / "parts" / "actuators" / "servo" / "acme-km45-10"
    assert products_module.part_type(folder) == ("robot_models.actuators.servo", "IntegratedServo")
    assert products_module.part_type(repo / "user" / "parts" / "power" / "acme-psu") is None


def test_a_batch_can_target_an_own_part(tmp_path):
    repo = _repo(tmp_path)
    app = server_module.App(tmp_path / "home", f"robotparts:{repo}")
    assert {product["id"] for product in app.catalog.list()} == {"acme-km45-10", "acme-psu", "acme-km70-10"}
    assert app.catalog.get("acme-psu")["provider"] == "robotparts user/parts"


@pytest.mark.skipif(not (ROBOTPARTS / "user" / "parts").is_dir(), reason="robotparts is not on this machine")
def test_the_real_robotparts_lists_zachs_own_parts():
    listed = {product["id"]: product for product in products_module.RobotParts(ROBOTPARTS).list()}
    own = [path.parent.name for path in (ROBOTPARTS / "user" / "parts").rglob("*.py")
           if path.parent.name == path.stem and "sources" not in path.parts]
    assert own and all(listed[part_id]["provider"] == "robotparts user/parts" for part_id in own)
    assert listed["steadywin-gim4305-10"]["folder"].endswith("actuators/servo/steadywin-gim4305-10")


# --- the source file ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["12-full-datasheet-pdf", "12-full-datasheet-png", "12-full-datasheet-html"])
def test_the_source_file_names_the_part_folder_and_every_value_has_its_where(tmp_path, name):
    repo = _repo(tmp_path)
    home, source_id = _home(tmp_path, name, ["acme-km45-10"])
    answer = server_module.App(home, f"robotparts:{repo}").as_spec(source_id)
    folder = repo / "user" / "parts" / "actuators" / "servo" / "acme-km45-10"
    assert answer["form"] == "stated" and answer["count"] == 7
    arm = name.rsplit("-", 1)[1]
    stem = {"pdf": "example_datasheet", "png": "example_image", "html": "example_page"}[arm]
    assert answer["save_as"] == {"py": str(folder / "sources" / f"{stem}.py"),
                                 "raw": str(folder / "sources" / f"{stem}.{arm}"),
                                 "raw_from": str(BUNDLES / name / f"source.{arm}"), "product": "acme-km45-10"}
    extension = f".{arm}"
    text = answer["text"]
    assert "from robot_models.actuators.servo import IntegratedServo" in text and "TODO: check the part type" not in text
    wheres = _wheres(text)
    assert set(wheres) == {"nominal_voltage", "rated_torque", "peak_torque", "rated_speed", "gear_ratio", "mass",
                           "buses"}
    kind = PageBox if extension != ".html" else TextQuote
    assert all(isinstance(parse(where), kind) for where in wheres.values()), wheres


@needs_robot_models
@pytest.mark.parametrize("name", ["12-full-datasheet-pdf", "12-full-datasheet-png", "12-full-datasheet-html",
                                  "03-simple-table-html", "01-prose-spec-pdf"])
def test_the_source_file_loads_with_robot_models_beside_its_raw_file(tmp_path, name):
    repo = _repo(tmp_path)
    home, source_id = _home(tmp_path, name, ["acme-km45-10"])
    answer = server_module.App(home, f"robotparts:{repo}").as_spec(source_id)
    py, raw = pathlib.Path(answer["save_as"]["py"]), pathlib.Path(answer["save_as"]["raw"])
    py.write_text(answer["text"], encoding="utf-8")
    shutil.copyfile(answer["save_as"]["raw_from"], raw)
    if str(ROBOT_MODELS) not in sys.path:
        sys.path.append(str(ROBOT_MODELS))
    from robot_models.datasheet import load_source
    source = load_source(py)
    assert source.file_sha256() == source.sha256                     # the header pins the raw file beside it
    stated = {**source.values, **source.extra}
    assert stated and all(value.anchor for value in stated.values())
    for value in stated.values():
        parse(value.anchor)                                           # robotparts' where rule: a source_msgs selector
    if name.startswith("12-"):
        assert stated["peak_torque"].value == 4.8 and stated["peak_torque"].unit == "N·m"
        assert stated["peak_torque"].as_written == "Peak torque" and stated["nominal_voltage"].as_written == "Rated voltage"


def test_a_part_with_no_type_of_its_own_gets_the_best_covering_actuator_type_and_a_todo(tmp_path):
    home, source_id = _home(tmp_path, "12-full-datasheet-pdf")
    answer = server_module.App(home).as_spec(source_id)
    text = answer["text"]
    if spec_module.typed_fields_of(spec_module.ACTUATOR_TYPES[0]) is None:
        pytest.skip("robot_models is not importable here")
    assert "from robot_models.actuators.servo import IntegratedServo" in text
    assert "# TODO: check the part type: IntegratedServo is a guess" in text
    assert answer["save_as"]["py"].startswith("<part folder>/sources/")


def test_a_value_the_type_has_no_field_for_goes_in_extra_and_a_generic_type_takes_all():
    rows = [{"id": "a", "key": "peak_torque", "value": 4.8, "unit": "N·m", "raw": "4.8 N·m", "cite": {"label": "Peak"}},
            {"id": "b", "key": "spec.Noise", "value": 60, "unit": "dB", "raw": "60 dB", "cite": {}}]
    typed = spec_module.as_stated(rows, source_url="https://acme.example/km.pdf", sha256="ab" * 32,
                                  manufacturer="Acme", part_type=("robot_models.actuators.servo", "IntegratedServo"),
                                  typed_fields={"peak_torque"}, extension=".pdf")
    assert 'peak_torque=Stated(4.8, "N·m", "Peak")' in typed and '"Noise": Stated(60, "dB")' in typed
    generic = spec_module.as_stated(rows, source_url="https://acme.example/km.pdf", sha256="ab" * 32,
                                    manufacturer="Acme", extension=".pdf")
    assert "values = Datasheet(" in generic and '"peak_torque": Stated(4.8' in generic
    assert "from robot_models.datasheet import Datasheet, Source, Stated" in generic
    compile(typed, "<typed>", "exec") and compile(generic, "<generic>", "exec")


def test_a_value_marked_only_by_its_cell_says_so_in_its_note():
    from datasheet_extractor import locate
    rows = [{"id": "a", "key": "buses", "value": ["can"], "unit": "", "raw": "CAN", "cite": {}}]
    wheres = {"a": locate.Where("page=1&xywh=percent:10,20,30,2", "cell",
                                "the cited block prints 'CAN' 2 times, so the block is marked, not one of them")}
    text = spec_module.as_stated(rows, source_url=None, sha256="ab" * 32, wheres=wheres, extension=".pdf")
    assert ('"buses": Stated("can", None, where="page=1&xywh=percent:10,20,30,2", note="the cited block prints '
            "'CAN' 2 times, so the block is marked, not one of them\")") in text
    assert "url=" not in text                                         # a saved file with no web address has none


def test_the_spec_form_carries_where_in_its_note_until_spec_has_a_where_field(tmp_path):
    home, source_id = _home(tmp_path, "12-full-datasheet-pdf")
    answer = server_module.App(home).as_spec(source_id, form="spec")
    line = next(line for line in answer["text"].splitlines() if '"peak_torque"' in line)
    note = ast.literal_eval(line.split("note=", 1)[1].rsplit(")", 1)[0])
    where = note.split("where ", 1)[1].split(";", 1)[0]
    assert isinstance(parse(where), PageBox) and answer["save_as"] is None


def test_an_unknown_form_is_refused(tmp_path):
    home, source_id = _home(tmp_path, "12-full-datasheet-pdf")
    with pytest.raises(server_module.ApiError) as error:
        server_module.App(home).as_spec(source_id, form="yaml")
    assert error.value.status == 400


def test_the_cli_prints_the_source_file_and_says_where_to_save_it(tmp_path, capsys):
    from datasheet_extractor import cli
    home, source_id = _home(tmp_path, "12-full-datasheet-html")
    assert cli.main(["as-spec", source_id, "--home", str(home)]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith('"""KM45-10, as the web page on example.com prints it.')
    assert "save as <part folder>/sources/example_page.py" in captured.err
    compile(captured.out, "<pasted>", "exec")


# --- review edits ------------------------------------------------------------------------------------------------------

def test_an_edit_to_raw_clears_the_located_words_and_publish_carries_them_otherwise(tmp_path):
    home, source_id = _home(tmp_path, "12-full-datasheet-pdf")
    values = json.loads((home / "sources" / source_id / "values.json").read_text(encoding="utf-8"))
    ops = [{"op": "assign", "products": ["p"], "value_ids": ["*"], "author": "z", "at": "t"},
           {"op": "edit", "value_id": "mass@table_cell-14", "field": "raw", "to": "185g", "author": "z", "at": "t"}]
    record = eff.effective(values, {"ops": ops})
    rows = {row["key"]: row for row in record["values"]}
    assert rows["mass"]["raw_grounding"] is None and rows["peak_torque"]["raw_grounding"]
    published = {row["key"]: row for row in eff.published("p", record, values["source"])["values"]}
    assert "raw_grounding" not in published["mass"] and published["peak_torque"]["raw_grounding"]
