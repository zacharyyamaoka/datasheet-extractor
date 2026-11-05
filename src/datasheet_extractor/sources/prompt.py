"""`GET /api/sources/<id>/prompt`: text a person pastes into any AI agent to extract one source (CONTRACT §4).

It carries the command for this machine first, then everything needed without it — the source's path and URL, the
bundle, the fields file, the part models — and the one path values.json must land at, which the queue watches.
"""

from __future__ import annotations

import pathlib
import shlex
import socket

from .. import option1
from . import extraction

REPO = extraction.ROOT


def prompt(home: pathlib.Path, source: dict, batch: dict, products: list[dict], products_spec: str | None) -> str:
    directory = pathlib.Path(home) / "sources" / source["id"]
    bundle = pathlib.Path(source["bundle"])
    fields = extraction.fields_path(batch.get("category") or "")
    target = directory / "values.json"
    command = ["uv", "run", "--directory", str(REPO), "python", "-m", "datasheet_extractor.sources.extraction",
               "--home", str(home), source["id"]]
    if products_spec:
        command += ["--products", products_spec]
    try:
        local = str(extraction.bundle_source(bundle))
    except FileNotFoundError:
        local = str(directory / "upload" / source["name"]) if source["kind"] == "file" else None
    models = None
    if (bundle / "index.json").is_file():
        models = extraction.part_models(option1.Bundle.load(bundle), products)
    lines = [
        f"Extract datasheet values from one source of the Datasheet Extractor's queue (source {source['id']}, "
        f"batch {batch.get('id')}, category {batch.get('category')}).",
        "",
        f"1. Run this on {socket.gethostname()} (it calls the logged-in `claude` CLI; add `--model codex` for Codex):",
        "",
        "    " + shlex.join(command),
        "",
        f"   It writes {target}; the queue watches that path and moves the source to `extracted` within seconds.",
        "",
        "2. Or extract it yourself and write the same file:",
        f"- Source: {source.get('url') or source['name']}",
        f"- Its bytes: {local or '(not captured yet)'}",
        f"- Markdownify bundle: {bundle} (document.md, index.json — cite a block or table-cell id from index.json)"
        + ("" if (bundle / "index.json").is_file()
           else f" — not made yet; the source is at stage `{source['stage']}`"),
        f"- Fields: {fields}",
        f"- Products: {', '.join(p.get('title') or p['id'] for p in products) or 'none'}",
    ]
    if models is not None:
        named = [f"{model} ({', '.join(ids)})" for model, ids in models if model]
        lines.append(f"- Part models (one column or row per model): {'; '.join(named) if named else 'none — the '
                     'datasheet describes one part'}")
    lines += [
        f"- Write values.json to: {target}",
        "  Format: /home/bam/markdownify/CONTRACT.md §7 (schema extractor/values@0; every value with an id "
        "\"<key>@<cite id>\", verdict kept or dropped with reasons). The single-model equivalent of step 1 is",
        "",
        "    " + shlex.join(["uv", "run", "--directory", str(REPO), "extract", local or "<source file>", "--fields",
                             str(fields), "--option", "1", "--bundle", str(bundle), "--out", str(directory)]
                            + (["--part-model", models[0][0]] if models and models[0][0] else [])),
        "",
        "Do not overwrite an existing values.json; ask the queue to re-extract instead "
        f"(POST /api/sources/{source['id']}/extract).",
    ]
    return "\n".join(lines) + "\n"
