---
name: datasheet-extractor
description: Read the values a datasheet states for a list of fields (a PDF, a page image or a saved web page), each checked against the source and pointing at the block or table cell it came from. Use when an agent must fill a part's specs from its maker's datasheet. Stub (v0, 2026-10-29).
---

# Datasheet Extractor (stub)

Zach (2026-10-29): "it will likely be a skill for the AI agent". This is that skill's first cut: how an agent runs
the extractor and what it gets back. The code and its evaluation are `/home/bam/datasheet-extractor`.

## Run it

```bash
uv run --directory /home/bam/datasheet-extractor extract <source> --fields /home/bam/datasheet-extractor/fields/actuator.json --option 1 --part-model "<part number>" --out <dir>
```

- `<source>`: a local file — PDF, PNG/JPEG of a digital page, or a saved HTML page. Fetch a URL first (Markdownify's
  `mdify capture`, or robotparts' ingest cache) so the bytes a value cites are kept.
- `--fields`: what to read (CONTRACT §7 `fields.json`: key, labels in English and Chinese, units, kind, per_model).
  `fields/actuator.json` is robotparts' actuator vocabulary; write another for another kind of part.
- `--option 1` (default choice): Markdownify converts the source, the model cites block and cell ids, and code reads
  the row and column headers from the span index. **Use it.** `--option 2` (one shot on the raw source) is the
  comparison baseline; see `eval/RESULTS.md`.
- `--part-model`: always pass the part number when the page may compare several models — a value from a table with
  one column (or row) per model is kept only from the column that names it.
- `--model claude|codex`: which logged-in CLI (no API keys). `--bundle <dir>` reuses a Markdownify bundle already
  made from this very source.

## What `values.json` holds (for the standard view)

```jsonc
{"schema": "extractor/values@0",
 "source": {"sha256": "…", "url": "…", "retrieved_at": "…"},       // the exact bytes every value cites
 "extractor": {"tool": "datasheet-extractor", "version": "0.1.0", "model": "claude-opus-5-5 via …", "pipeline": "option1"},
 "part": {"manufacturer": {…}, "model": {"value": "AK80-9", "raw": "…", "cite": {"id": "heading-2"}, "verdict": "kept"}},
 "values": [{"key": "rated_torque", "value": 9, "unit": "N·m", "raw": "9 N·m", "condition": null,
             "cite": {"id": "table_cell-14", "row": "Rated torque", "column": "KM80-9", "multi_model": true},
             "grounding": [{"page": 1, "box": [0.41, 0.19, 0.46, 0.20]}],   // or {"html": {"source_range": …}}
             "verdict": "kept", "reasons": [], "proposed": {…the model's own row…}}]}
```

- Show only `verdict: "kept"` values as the part's specs. Each carries `value` in the source's own unit (never
  converted), `raw` (the printed text), and `grounding` — page + box for a PDF/image, a source range for HTML — which
  the Source Highlighter opens as "Go to source".
- `cite.row` / `cite.column` are the headers code read from the table structure, not the model's words; show them
  beside the value ("Rated torque · KM80-9").
- `verdict: "dropped"` values are kept in the file with their `reasons` (e.g. "this cell's column header 'KM70-10'
  does not name 'KM80-9'"). Show them collapsed, for review — never as specs.
- `DIR/run.json`: seconds, LLM calls, where the raw event stream is (`DIR/llm/events.jsonl`).

## Not yet

- No fetching, no batching, no write into robotparts' part.json (its citation shape is RobotParts' call, CONTRACT §8).
- Values read off plots are not requested; a cited block Markdownify did not convert (`represented: false`) is dropped.
