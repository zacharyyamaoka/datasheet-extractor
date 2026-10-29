#!/usr/bin/env python3
"""Run both options over both evaluation sets; every run keeps its values.json, run.json and raw event stream.

    uv run python eval/run.py [--set synthetic,r0] [--option 1,2] [--model claude,codex] [--jobs 6] [--only SUBSTR]

- **synthetic:** the 12 Markdownify harness cases, each rendered as PDF, PNG and HTML
  (/home/bam/markdownify/harness/out/<case>/<arm>/source.<arm>), once per model the truth names (a comparison table
  is extracted once for each of its models).
- **r0:** robotparts' 20 ingested parts (/home/bam/robotparts/parts/*/part.json with an `ingest` block), each input
  read from its cached bytes (~/.cache/robotparts-ingest/blobs/<sha256>), with the part's atlas model as
  `--part-model`.

Runs go to ~/.cache/datasheet-extractor/eval/ (or $DATASHEET_EXTRACTOR_CACHE/eval), outside git. WHY: they hold
copies of makers' datasheets (Markdownify bundles carry the source), which are mostly not redistributable — the same
rule as robotparts' ingest cache. A run whose values.json exists is skipped, so a rerun only fills gaps.

Markdownify runs once per source (`bundles/<sha256>/`, with its wall time in `mdify.json`) and every Option 1 run
of that source reuses the bundle; the scorer charges the mdify time to each Option 1 document.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import pathlib
import subprocess
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from datasheet_extractor import cli, option1  # noqa: E402
from datasheet_extractor.values import sha256_of  # noqa: E402

HARNESS = pathlib.Path("/home/bam/markdownify/harness/out")
ROBOTPARTS = pathlib.Path("/home/bam/robotparts")
BLOBS = pathlib.Path.home() / ".cache" / "robotparts-ingest" / "blobs"
CACHE = pathlib.Path(os.environ.get("DATASHEET_EXTRACTOR_CACHE", pathlib.Path.home() / ".cache" / "datasheet-extractor"))
EVAL = CACHE / "eval"
FIELDS = ROOT / "fields" / "actuator.json"
ARMS = ("pdf", "png", "html")
_bundle_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def synthetic_documents() -> list[dict]:
    docs = []
    for truth_path in sorted((ROOT / "eval" / "synthetic").glob("*.values.json")):
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        for arm in ARMS:
            source = HARNESS / truth["case"] / arm / f"source.{arm}"
            for model in truth["models"]:
                docs.append({"set": "synthetic", "id": f"{truth['case']}/{arm}/{model}", "case": truth["case"],
                             "arm": arm, "source": str(source), "part_model": model})
    return docs


def robotparts_parts() -> list[dict]:
    """Each ingested part with its model as the atlas (or its own part.json) names it."""
    sys.dont_write_bytecode = True  # WHY: robotparts is read-only to this project
    sys.path.insert(0, str(ROBOTPARTS / "tools"))
    from ingestor.atlas import atlas_part  # noqa: E402

    parts = []
    for path in sorted((ROBOTPARTS / "parts").glob("*/part.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if "ingest" not in record:
            continue
        atlas = atlas_part(record["id"])
        identity = atlas.identity if atlas else {}
        parts.append({"id": record["id"], "atlas": atlas.atlas if atlas else None,
                      "brand": identity.get("brand") or record.get("brand"),
                      "model": identity.get("model") or record.get("model"), "record": record})
    return parts


def r0_documents() -> list[dict]:
    docs = []
    for part in robotparts_parts():
        for number, item in enumerate(part["record"]["ingest"]["inputs"], 1):
            docs.append({"set": "r0", "id": f"{part['id']}/input{number}", "part": part["id"],
                         "source": str(BLOBS / item["sha256"]), "media_type": item["media_type"],
                         "url": item["url"], "part_model": part["model"]})
    return docs


def bundle_for(source: pathlib.Path) -> pathlib.Path:
    """The shared Markdownify bundle of a source, made once (thread-safe per source)."""
    sha = sha256_of(source)
    with _locks_guard:
        lock = _bundle_locks.setdefault(sha, threading.Lock())
    with lock:
        directory = EVAL / "bundles" / sha
        bundle = directory / "bundle"
        if (bundle / "index.json").is_file():
            return bundle
        directory.mkdir(parents=True, exist_ok=True)
        seconds, summary = option1.run_mdify(source, bundle)
        (directory / "mdify.json").write_text(json.dumps({"seconds": seconds, "summary": summary,
                                                          "source": str(source)}, indent=2) + "\n", encoding="utf-8")
        return bundle


def run_one(doc: dict, option: int, backend: str, force: bool = False) -> dict:
    out = EVAL / doc["set"] / doc["id"] / f"option{option}-{backend}"
    if not force and (out / "values.json").is_file() and (out / "run.json").is_file():
        return json.loads((out / "run.json").read_text(encoding="utf-8"))
    source = pathlib.Path(doc["source"])
    try:
        bundle = bundle_for(source) if option == 1 else None
        run = cli.extract(source, FIELDS, option, backend, doc["part_model"], out, bundle=bundle)
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        out.mkdir(parents=True, exist_ok=True)
        run = {"error": f"{type(error).__name__}: {error}", "llm_calls": 0}
        (out / "failed.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    print(f"{doc['id']} option{option}-{backend}: proposed {run.get('proposed')}, kept {run.get('kept')}, "
          f"{run.get('seconds')} s{'  ERROR ' + str(run.get('llm_error') or run.get('error')) if run.get('llm_error') or run.get('error') else ''}",
          flush=True)
    return run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", default="synthetic,r0")
    parser.add_argument("--option", default="1,2")
    parser.add_argument("--model", default="claude,codex")
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--only", help="run only documents whose id contains this")
    parser.add_argument("--force", action="store_true", help="re-run even where values.json exists (the old run's "
                        "files are overwritten in place)")
    arguments = parser.parse_args()
    docs = []
    if "synthetic" in arguments.set:
        docs += synthetic_documents()
    if "r0" in arguments.set:
        docs += r0_documents()
    if arguments.only:
        docs = [doc for doc in docs if arguments.only in doc["id"]]
    (EVAL).mkdir(parents=True, exist_ok=True)
    (EVAL / "documents.json").write_text(json.dumps(synthetic_documents() + r0_documents(), indent=2) + "\n",
                                         encoding="utf-8")
    # Bundles first, a few at a time: docling loads its models per process.
    if "1" in arguments.option:
        sources = sorted({doc["source"] for doc in docs})
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda s: bundle_for(pathlib.Path(s)), sources))
    jobs = [(doc, int(option), backend) for doc in docs for option in arguments.option.split(",")
            for backend in arguments.model.split(",")]
    print(f"{len(jobs)} runs over {len(docs)} documents", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=arguments.jobs) as pool:
        list(pool.map(lambda job: run_one(*job, force=arguments.force), jobs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
