#!/usr/bin/env python3
"""The bridge, end to end on recorded bundles, no model call.

For each case: replay its recorded Option 1 model reply through today's checker (values.json with raw_grounding),
put it in a scratch Sources home, take Copy as Spec's source file through the app's own code path
(`App.as_spec`, as the button and `extract as-spec` do), print it, save it beside its raw file and load it with
robot_models; then draw every `where` box on its page render and read the PDF's own text layer inside each box.

    uv run python scripts/bridge_demo.py --out DIR [--shots DIR]

Cases: the synthetic KM45-10 spec table as a PDF and as an image, a prose paragraph (tests/fixtures/bundles/), and,
when this machine still has the 2026-10-29 eval cache, DEEP Robotics' real J60 Joint PDF (a maker's datasheet: its
crops stay local, never committed). Each case saves one crop of one value: red is the value's `where`, blue dashed the
cited cell or block it was narrowed from.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import shutil
import sys
import tempfile
import unicodedata

import pdfplumber
import pypdfium2
from PIL import Image, ImageDraw, ImageFont
from source_msgs import PageBox, parse

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from datasheet_extractor import fields as fields_module  # noqa: E402
from datasheet_extractor import option1  # noqa: E402
from datasheet_extractor.sources import server, spec  # noqa: E402
from datasheet_extractor.sources.store import write_json  # noqa: E402
from datasheet_extractor.values import document  # noqa: E402

BUNDLES = ROOT / "tests" / "fixtures" / "bundles"
EVAL = pathlib.Path.home() / ".cache" / "datasheet-extractor" / "eval"
J60 = EVAL / "r0" / "aifitlab-8778268278920" / "input2" / "option1-claude"
FIELDS = fields_module.load(ROOT / "fields" / "actuator.json")
RENDER_SCALE = 4          # PDF points to pixels: 288 dpi, enough to read a 7 pt table


def fixture_case(label: str, folder: str, focus: str) -> dict:
    directory = BUNDLES / folder
    case = json.loads((directory / "case.json").read_text(encoding="utf-8"))
    made = json.loads((directory / "source.json").read_text(encoding="utf-8"))
    retrieval = made["retrievals"][-1]
    return {"label": label, "bundle": directory, "reply": json.loads((directory / "reply.json").read_text()),
            "part_model": case["part_model"], "model": case["model"], "focus": focus,
            "source": {"sha256": made["sha256"], "url": retrieval["url"], "retrieved_at": retrieval["retrieved_at"]}}


def j60_case() -> dict | None:
    if not (J60 / "llm" / "output.json").is_file():
        return None
    run = json.loads((J60 / "run.json").read_text(encoding="utf-8"))
    recorded = json.loads((J60 / "values.json").read_text(encoding="utf-8"))
    return {"label": "j60-pdf", "bundle": pathlib.Path(run["bundle"]), "part_model": run["part_model"],
            "reply": json.loads((J60 / "llm" / "output.json").read_text()), "model": run.get("llm_model"),
            "focus": "voltage_min", "source": recorded["source"]}


def replay(case: dict, home: pathlib.Path) -> tuple[str, dict, option1.Bundle]:
    """(source id, values.json, bundle): the recorded reply through the checker, saved as an extracted source."""
    bundle = option1.Bundle.load(case["bundle"])
    verdicts, part = option1.judge(case["reply"], FIELDS, bundle, case["part_model"])
    values = document(case["source"], "option1", case["model"], part, verdicts)
    source_id = case["source"]["sha256"][:12]
    folder = home / "sources" / source_id
    folder.mkdir(parents=True)
    write_json(folder / "source.json", {"id": source_id, "batch": "demo", "kind": "url", "name": case["source"]["url"],
                                        "url": case["source"]["url"], "stage": "extracted",
                                        "sha256": case["source"]["sha256"], "bundle": str(case["bundle"]),
                                        "category": "actuator"})
    write_json(folder / "values.json", values)
    return source_id, values, bundle


def wheres_of(text: str) -> dict[str, str]:
    """{key: where} of every Stated(...) in the pasted file."""
    out = {}
    for node in ast.walk(ast.parse(text)):
        pairs = []
        if isinstance(node, ast.keyword) and isinstance(node.value, ast.Call):
            pairs = [(node.arg, node.value)]
        elif isinstance(node, ast.Dict):
            pairs = [(ast.literal_eval(key), value) for key, value in zip(node.keys, node.values)]
        for key, call in pairs:
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "Stated":
                found = [ast.literal_eval(k.value) for k in call.keywords if k.arg == "where"]
                if found:
                    out[key] = found[0]
    return out


def notes_of(text: str) -> dict[str, str]:
    """{key: note} of every Stated(...) that has one."""
    out = {}
    for node in ast.walk(ast.parse(text)):
        pairs = []
        if isinstance(node, ast.keyword) and isinstance(node.value, ast.Call):
            pairs = [(node.arg, node.value)]
        elif isinstance(node, ast.Dict):
            pairs = [(ast.literal_eval(key), value) for key, value in zip(node.keys, node.values)]
        for key, call in pairs:
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == "Stated":
                out.update({key: ast.literal_eval(k.value) for k in call.keywords if k.arg == "note"})
    return out


def page_image(source: pathlib.Path, page: int) -> Image.Image:
    if source.suffix.lower() == ".pdf":
        document_ = pypdfium2.PdfDocument(str(source))
        try:
            return document_[page - 1].render(scale=RENDER_SCALE).to_pil().convert("RGB")
        finally:
            document_.close()
    return Image.open(source).convert("RGB")


def text_in(source: pathlib.Path, box: PageBox) -> str | None:
    """The PDF text layer's characters whose centre is inside `box`; None for an image or a scanned page (no text
    layer to read)."""
    if source.suffix.lower() != ".pdf":
        return None
    with pdfplumber.open(source) as pdf:
        page = pdf.pages[box.page - 1]
        if not page.chars:
            return None
        x0, y0 = box.x * page.width, box.y * page.height
        x1, y1 = (box.x + box.w) * page.width, (box.y + box.h) * page.height
        chars = [c for c in page.chars if x0 <= (c["x0"] + c["x1"]) / 2 <= x1 and y0 <= (c["top"] + c["bottom"]) / 2 <= y1]
        return "".join(c["text"] for c in sorted(chars, key=lambda c: (round(c["top"]), c["x0"])))


def words_in(bundle: option1.Bundle, box: PageBox) -> str:
    """The bundle's (OCR'd) words whose box centre is inside `box`: the check for a page with no text layer."""
    found = []
    for word in bundle.index.get("words") or []:
        grounding = word["grounding"]
        if grounding.get("page") != box.page:
            continue
        x0, y0, x1, y1 = grounding["box"]
        if box.x <= (x0 + x1) / 2 <= box.x + box.w and box.y <= (y0 + y1) / 2 <= box.y + box.h:
            found.append(option1.plain(bundle.markdown[word["range"]["start"]:word["range"]["end"]]))
    return " ".join(found)


def squash(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text or "").split())


def crop(image: Image.Image, where: PageBox, cell: list[float] | None, caption: str) -> Image.Image:
    """The value's row across the page, its `where` in red and its cited cell or block in blue dashes, captioned."""
    width, height = image.size
    top = min(where.y, cell[1] if cell else where.y) - 0.035
    bottom = max(where.y + where.h, cell[3] if cell else 0) + 0.035
    band = image.crop((0, max(0, int(top * height)), width, min(height, int(bottom * height))))
    draw = ImageDraw.Draw(band)
    offset = max(0, int(top * height))
    line = max(2, width // 600)
    if cell:
        x0, y0, x1, y1 = cell[0] * width, cell[1] * height - offset, cell[2] * width, cell[3] * height - offset
        for start in range(int(x0), int(x1), 14):
            draw.line([(start, y0 - 4), (min(start + 7, x1), y0 - 4)], fill=(40, 110, 230), width=line)
            draw.line([(start, y1 + 4), (min(start + 7, x1), y1 + 4)], fill=(40, 110, 230), width=line)
        for start in range(int(y0), int(y1), 14):
            draw.line([(x0 - 4, start), (x0 - 4, min(start + 7, y1))], fill=(40, 110, 230), width=line)
            draw.line([(x1 + 4, start), (x1 + 4, min(start + 7, y1))], fill=(40, 110, 230), width=line)
    draw.rectangle([where.x * width, where.y * height - offset, (where.x + where.w) * width,
                    (where.y + where.h) * height - offset], outline=(220, 30, 30), width=line + 1)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", max(14, width // 70))
    except OSError:
        font = ImageFont.load_default()
    bar = max(28, width // 40)
    out = Image.new("RGB", (band.width, band.height + bar), "white")
    out.paste(band, (0, bar))
    ImageDraw.Draw(out).text((10, 4), caption, fill=(20, 20, 20), font=font)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=pathlib.Path, required=True, help="where the source files and page renders go")
    parser.add_argument("--shots", type=pathlib.Path, help="where the crops go (default: --out)")
    arguments = parser.parse_args(argv)
    shots = arguments.shots or arguments.out
    arguments.out.mkdir(parents=True, exist_ok=True)
    shots.mkdir(parents=True, exist_ok=True)
    cases = [j60_case(), fixture_case("table-pdf", "12-full-datasheet-pdf", "peak_torque"),
             fixture_case("image", "12-full-datasheet-png", "mass"),
             fixture_case("prose-pdf", "01-prose-spec-pdf", "rated_torque")]
    cases = [case for case in cases if case is not None]
    failures = 0
    for number, case in enumerate(cases, 1):
        home = pathlib.Path(tempfile.mkdtemp(prefix="bridge-demo-"))
        try:
            source_id, values, bundle = replay(case, home)
            answer = server.App(home).as_spec(source_id)
        finally:
            shutil.rmtree(home, ignore_errors=True)
        folder = arguments.out / case["label"]
        (folder / "sources").mkdir(parents=True, exist_ok=True)
        py = folder / "sources" / pathlib.Path(answer["save_as"]["py"]).name
        raw = folder / "sources" / pathlib.Path(answer["save_as"]["raw"]).name
        py.write_text(answer["text"], encoding="utf-8")
        shutil.copyfile(answer["save_as"]["raw_from"], raw)
        print(f"\n===== {case['label']}: {answer['count']} kept values -> {py}\n")
        print(answer["text"])
        loaded = "robot_models not importable here"
        if spec.typed_fields_of(spec.ACTUATOR_TYPES[0]) is not None:
            from robot_models.datasheet import load_source
            source = load_source(py)
            stated = {**source.values, **source.extra}
            loaded = (f"loads with robot_models: {len(stated)} values, sha256 "
                      f"{'matches' if source.file_sha256() == source.sha256 else 'DIFFERS from'} {raw.name}")
        print(f"  {loaded}")
        rows = {row["key"]: row for row in values["values"] if row["verdict"] == "kept"}
        renders: dict[int, Image.Image] = {}
        for key, where in wheres_of(answer["text"]).items():
            selector = parse(where)
            if not isinstance(selector, PageBox):
                continue
            row = rows.get(key) or rows.get(f"spec.{key}") or {}
            printed, what = text_in(raw, selector), "text layer"
            if printed is None:
                printed, what = words_in(bundle, selector), "OCR words (no text layer)"
            wider = "prints it inside" in (notes_of(answer["text"]).get(key) or "")
            if squash(printed) == squash(option1.plain(row.get("raw") or "")):
                verdict = f"{what} inside the box = {printed!r} = raw"
            elif wider and squash(option1.plain(row.get("raw") or "")) in squash(printed):
                verdict = f"{what} inside the box = {printed!r}, which holds raw {row.get('raw')!r} (its note says so)"
            else:
                verdict = f"MISMATCH: {what} inside the box = {printed!r}, raw = {row.get('raw')!r}"
                failures += 1
            print(f"  {key:18} {where:44} {verdict}")
            image = renders.setdefault(selector.page, page_image(raw, selector.page))
            draw = ImageDraw.Draw(image)
            width, height = image.size
            draw.rectangle([selector.x * width, selector.y * height, (selector.x + selector.w) * width,
                            (selector.y + selector.h) * height], outline=(220, 30, 30), width=3)
        for page, image in renders.items():
            image.save(folder / f"page-{page}-wheres.png")
        focus = rows.get(case["focus"])
        where = wheres_of(answer["text"]).get(case["focus"])
        if focus and where:
            selector = parse(where)
            cell = (focus.get("grounding") or [{}])[0].get("box")
            label = (focus.get("cite") or {}).get("label")
            caption = (f"{case['focus']} = {focus['value']} {focus['unit']}   raw {focus['raw']!r}"
                       + (f"   label {label!r}" if label else "") + f"   where {where}")
            shot = shots / f"bridge-{number}-{case['label']}.png"
            crop(page_image(raw, selector.page), selector, cell, caption).save(shot)
            print(f"  crop: {shot}")
    print(f"\n{failures} box(es) disagree with the PDF text layer" if failures else "\nevery PDF box holds exactly its raw")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
