"""`extract <source> --fields fields/actuator.json --option 1|2 [--model claude|codex] [--part-model P] --out DIR`

Writes DIR/values.json (CONTRACT §7) and DIR/run.json (timings, LLM calls, where the raw event stream is). Every
proposed value is in values.json: `verdict: kept`, or `verdict: dropped` with the checker's reasons — never silently
left out.

- Option 1: `uv run --directory /home/bam/markdownify mdify <source> --out DIR/bundle`, then one model call over the
  tagged markdown, then the id checker (option1.py).
- Option 2: one model call on the raw source only (page images for a PDF or an image, robotparts' page text for
  HTML), then robotparts' quote checker (option2.py).

DIR/llm/ keeps the call's prompt, schema, argv, raw events.jsonl and the parsed output.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

from . import fields as fields_module
from . import llm, option1, option2
from .values import document, source_record


def extract(source: pathlib.Path, fields_path: pathlib.Path, option: int, backend: str, part_model: str | None,
            out: pathlib.Path, llm_model: str | None = None, bundle: pathlib.Path | None = None) -> dict:
    """One source, one option, one model call. `bundle` (Option 1): a Markdownify bundle already made from this
    very source, reused instead of running mdify again (its sha256 must match)."""
    start = time.time()
    out.mkdir(parents=True, exist_ok=True)
    fields = fields_module.load(fields_path)
    record = source_record(source)
    media_type = option2.media_type_of(source)
    run = {"source": str(source), "media_type": media_type, "option": option, "backend": backend,
           "part_model": part_model, "fields": str(fields_path), "llm_calls": 0, "mdify_seconds": None}
    if option == 1:
        if bundle is not None:
            made = json.loads((bundle / "source.json").read_text(encoding="utf-8"))
            if made.get("sha256") != record["sha256"]:
                raise ValueError(f"{bundle} was made from {made.get('sha256')}, not this source ({record['sha256']})")
            run["bundle"] = str(bundle)
            bundle_dir = bundle
        else:
            bundle_dir = out / "bundle"
            run["mdify_seconds"], run["mdify"] = option1.run_mdify(source, bundle_dir)
        bundle = option1.Bundle.load(bundle_dir)
        prompt = option1.build_prompt(bundle, fields, part_model)
        result = llm.extract(backend, option1.SYSTEM_PROMPT, prompt, option1.output_schema(fields), out / "llm",
                             llm_model)
        run["llm_calls"] += 1
        verdicts, part = option1.judge(result.output, fields, bundle, part_model)
        pipeline = "option1"
    elif option == 2:
        checked = option2.Source(source, media_type)
        if media_type == "text/html":
            images, page_text = [], checked.pages[0]
        elif media_type == "application/pdf":
            images, page_text = option2.render_pages(source, out / "pages"), None
        else:
            images, page_text = [source.resolve()], None
        prompt = option2.build_prompt(fields, part_model, page_text, len(images))
        result = llm.extract(backend, option2.SYSTEM_PROMPT, prompt, option2.output_schema(fields, bool(images)),
                             out / "llm", llm_model, images)
        run["llm_calls"] += 1
        verdicts, part = option2.judge(result.output, fields, checked, part_model)
        pipeline = "option2"
    else:
        raise ValueError(f"--option is 1 or 2, not {option!r}")
    model_name = f"{result.model} via {result.cli_version}" if result.model else None
    values = document(record, pipeline, model_name, part, verdicts, checked if option == 2 else None)
    (out / "values.json").write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    kept = sum(1 for verdict in verdicts if verdict.kept)
    run.update({"llm_seconds": result.seconds, "llm_error": result.error, "llm_model": result.model,
                "events": str(result.run_dir / "events.jsonl"), "proposed": len(verdicts), "kept": kept,
                "dropped": len(verdicts) - kept, "seconds": round(time.time() - start, 2)})
    (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extract", description="A datasheet and a field list in, checked values "
                                     "out (Markdownify CONTRACT §7).")
    parser.add_argument("source", type=pathlib.Path, help="a local PDF, PNG/JPEG or HTML file")
    parser.add_argument("--fields", type=pathlib.Path, required=True, help="fields.json (CONTRACT §7)")
    parser.add_argument("--option", type=int, choices=(1, 2), required=True,
                        help="1: Markdownify bundle + cited ids; 2: single shot on the raw source")
    parser.add_argument("--model", choices=("claude", "codex"), default="claude", help="which logged-in CLI")
    parser.add_argument("--llm-model", help="override the CLI's model (default: claude-opus-5-5 / gpt-6.1-sol)")
    parser.add_argument("--part-model", help="the part number to pick in a table with one column (or row) per model")
    parser.add_argument("--out", type=pathlib.Path, required=True, help="output directory")
    parser.add_argument("--bundle", type=pathlib.Path, help="Option 1: reuse a Markdownify bundle of this source")
    arguments = parser.parse_args(argv)
    run = extract(arguments.source, arguments.fields, arguments.option, arguments.model, arguments.part_model,
                  arguments.out, arguments.llm_model, arguments.bundle)
    print(json.dumps(run, ensure_ascii=False, indent=2))
    return 0 if run["llm_error"] is None else 1


if __name__ == "__main__":
    sys.exit(main())


def rejudge(out: pathlib.Path) -> dict:
    """Re-run the checker on a finished run's stored model reply (DIR/llm/output.json): no new LLM call. WHY: a
    checker fix must be measurable on the very replies it was judged against, as robotparts' `ingest rejudge` does."""
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    values = json.loads((out / "values.json").read_text(encoding="utf-8"))
    output = json.loads((out / "llm" / "output.json").read_text(encoding="utf-8"))
    fields = fields_module.load(pathlib.Path(run["fields"]) if pathlib.Path(run["fields"]).is_absolute()
                                else pathlib.Path(__file__).resolve().parents[2] / run["fields"])
    source = pathlib.Path(run["source"])
    checked = None
    if run["option"] == 1:
        bundle = option1.Bundle.load(pathlib.Path(run.get("bundle") or out / "bundle"))
        verdicts, part = option1.judge(output, fields, bundle, run["part_model"])
    else:
        checked = option2.Source(source, run["media_type"])
        verdicts, part = option2.judge(output, fields, checked, run["part_model"])
    fresh = document(values["source"], values["extractor"]["pipeline"], values["extractor"]["model"], part, verdicts,
                     checked)
    (out / "values.json").write_text(json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    kept = sum(1 for verdict in verdicts if verdict.kept)
    run.update({"proposed": len(verdicts), "kept": kept, "dropped": len(verdicts) - kept, "rejudged": True})
    (out / "run.json").write_text(json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run
