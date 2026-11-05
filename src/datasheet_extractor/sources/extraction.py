"""The queue's extract step: Option 1 over a source's Markdownify bundle, the batch's category fields, and every
product of the batch (CONTRACT §3).

A product is a part model when one of its names (products.model_candidates) is printed in a cell of one of the
bundle's tables — the header of a column or row per model. Then Option 1 runs once per such model with it as
`--part-model`, and the runs merge into one values.json; a row two runs agree on (same key, cite, value, unit,
verdict) is written once. When no product names a table, Option 1 runs once with no part model. Each row a part-model
run produced carries `part_models`, and `extractor.part_models` maps each model to the products that named it, so a
reviewer's "this datasheet covers N models" step can assign them.

Also a command, the one the prompt hands to any AI agent (prompt.py):

    uv run --directory /home/bam/datasheet-extractor python -m datasheet_extractor.sources.extraction \\
        --home <home> <source id> [--model claude|codex] [--llm-model M]

It writes `<home>/sources/<id>/values.json` (never over an existing one) and touches nothing else the queue owns;
the running server's watcher sees the file and moves the source to `extracted`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

from .. import fields as fields_module
from .. import llm, option1
from ..values import document
from . import products as products_module
from .store import Store, default_home, now, read_json, write_json

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIELDS_DIR = ROOT / "fields"


def fields_path(category: str) -> pathlib.Path:
    return FIELDS_DIR / f"{category}.json"


def categories() -> list[str]:
    return sorted(path.stem for path in FIELDS_DIR.glob("*.json"))


def bundle_source(bundle_dir: pathlib.Path) -> pathlib.Path:
    """The bundle's `source.<ext>`: the bytes values cite."""
    found = sorted(path for path in bundle_dir.glob("source.*") if path.suffix != ".json")
    found = [path for path in found if path.suffix in (".pdf", ".html", ".png", ".jpg", ".jpeg", ".webp")]
    if not found:
        raise FileNotFoundError(f"{bundle_dir} has no source.<ext>")
    return found[0]


def source_record(source: dict, bundle_dir: pathlib.Path) -> dict:
    """values.json's `source`: the bundle's sha256, the URL (the source's own, else the last retrieval with one,
    else the bundle's file) and when it was retrieved."""
    record = read_json(bundle_dir / "source.json", {}) or {}
    retrievals = record.get("retrievals") or []
    url, at = source.get("url"), None
    matching = [r for r in retrievals if url and url in (r.get("url"), r.get("final_url"))]
    with_url = matching or [r for r in retrievals if r.get("url")]
    if with_url:
        url, at = with_url[-1]["url"], with_url[-1].get("retrieved_at")
    elif retrievals:
        at = retrievals[-1].get("retrieved_at")
    return {"sha256": record.get("sha256"), "url": url or bundle_source(bundle_dir).resolve().as_uri(),
            "retrieved_at": at}


def table_texts(bundle: option1.Bundle) -> list[str]:
    """The text of every cell of every table: where a comparison table prints its model names.
    WHY every cell, not only the header cells Grid finds: a model name that starts with a digit ('60ASM400-1024C')
    reads as a value cell to Grid's header heuristic, so a table with one row per such model has no header column."""
    texts = []
    for table in bundle.index["blocks"]:
        if table["type"] != "table":
            continue
        grid = option1.Grid.of(bundle, table)
        texts += [text for text in grid.texts.values() if text]
    return list(dict.fromkeys(texts))


def part_models(bundle: option1.Bundle, products: list[dict]) -> list[tuple[str | None, list[str]]]:
    """[(part model, [product ids that are it])], in product order; [(None, [])] when no product names a table."""
    headers = table_texts(bundle)
    found: dict[str, list[str]] = {}
    for product in products:
        for candidate in products_module.model_candidates(product):
            if option1.model_named(candidate, headers):
                found.setdefault(candidate, []).append(product["id"])
                break
    return list(found.items()) or [(None, [])]


def _merge_key(verdict) -> tuple:
    return (verdict.key, (verdict.cite or {}).get("id"), repr(verdict.value), verdict.unit, verdict.kept)


def extract(bundle_dir: pathlib.Path, category: str, products: list[dict], source: dict, out: pathlib.Path,
            backend: str = "claude", llm_model: str | None = None) -> tuple[dict, dict]:
    """(values.json document, run summary). Model calls are logged under `out/llm/<time>/<n>/`. Raises RuntimeError
    when a model call returns nothing."""
    start = time.time()
    fields = fields_module.load(fields_path(category))
    bundle = option1.Bundle.load(bundle_dir)
    models = part_models(bundle, products)
    merged: list[list] = []                          # [verdict, [part models]]
    seen: dict[tuple, int] = {}
    part, model_name, calls = None, None, []
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for number, (part_model, product_ids) in enumerate(models, 1):
        prompt = option1.build_prompt(bundle, fields, part_model)
        result = llm.extract(backend, option1.SYSTEM_PROMPT, prompt, option1.output_schema(fields),
                             out / "llm" / stamp / str(number), llm_model)
        calls.append({"part_model": part_model, "products": product_ids, "seconds": result.seconds,
                      "model": result.model, "error": result.error, "run_dir": str(result.run_dir)})
        if result.output is None:
            raise RuntimeError(f"the {backend} call for part model {part_model!r} returned no values: {result.error}")
        model_name = model_name or (f"{result.model} via {result.cli_version}" if result.model else None)
        verdicts, run_part = option1.judge(result.output, fields, bundle, part_model)
        for verdict in verdicts:
            key = _merge_key(verdict)
            if key in seen:
                if part_model and part_model not in merged[seen[key]][1]:
                    merged[seen[key]][1].append(part_model)
                continue
            seen[key] = len(merged)
            merged.append([verdict, [part_model] if part_model else []])
        kept_part = sum(1 for entry in run_part.values() if entry and entry["verdict"] == "kept")
        if part is None or kept_part > sum(1 for entry in part.values() if entry and entry["verdict"] == "kept"):
            part = run_part
    values = document(source_record(source, bundle_dir), "option1", model_name, part or {}, [v for v, _ in merged])
    for row, (_, models_of_row) in zip(values["values"], merged):
        if models_of_row:
            row["part_models"] = models_of_row
    values["extractor"]["part_models"] = [{"model": m, "products": ids} for m, ids in models if m]
    kept = sum(1 for verdict, _ in merged if verdict.kept)
    run = {"category": category, "fields": str(fields_path(category)), "backend": backend, "calls": calls,
           "proposed": len(merged), "kept": kept, "dropped": len(merged) - kept,
           "seconds": round(time.time() - start, 2), "at": now()}
    return values, run


def run_for(store: Store, catalog: products_module.Catalog, source_id: str, backend: str = "claude",
            llm_model: str | None = None) -> dict:
    """Extract one source of the store and write its values.json (refusing to overwrite one) and run.json."""
    source = store.source(source_id)
    if source is None:
        raise KeyError(source_id)
    directory = store.source_dir(source["id"])
    if (directory / "values.json").exists():
        raise FileExistsError(f"{directory / 'values.json'} exists; values.json is never rewritten "
                              "(POST /api/sources/<id>/extract re-runs and keeps the old one aside)")
    batch = store.batch(source["batch"]) or {}
    products = [catalog.get(product_id) or {"id": product_id, "title": product_id}
                for product_id in batch.get("products") or []]
    values, run = extract(pathlib.Path(source["bundle"]), batch.get("category"), products, source, directory,
                          backend, llm_model)
    write_json(directory / "run.json", run)
    write_json(directory / "values.json", values)
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m datasheet_extractor.sources.extraction",
                                     description="Extract one queued source (Option 1, every product of its batch) "
                                     "and write its values.json for the queue to pick up.")
    parser.add_argument("source", help="the source id")
    parser.add_argument("--home", type=pathlib.Path, default=default_home())
    parser.add_argument("--products", help="the server's --products (robotparts:<repo> | file:<path>)")
    parser.add_argument("--model", choices=("claude", "codex"), default="claude", help="which logged-in CLI")
    parser.add_argument("--llm-model", help="override the CLI's model")
    arguments = parser.parse_args(argv)
    store = Store(arguments.home)
    catalog = products_module.Catalog(store.home, arguments.products)
    try:
        run = run_for(store, catalog, arguments.source, arguments.model, arguments.llm_model)
    except (KeyError, FileExistsError, FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"extract: {arguments.source}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
