#!/usr/bin/env python3
"""Score every run eval/run.py made: eval/results/*.json and the table in eval/RESULTS.md.

    uv run python eval/score.py

The KPI is not Markdownify's (all of the source) but the requested fields only: every requested field the source
states, found, right, and pointing at the right place. Per option × model × set (and, for the synthetic set, per
rendered arm):

- **precision:** kept values that equal a truth value (same key, same value, same unit) ÷ kept values. A kept value
  that equals an `optional` truth row (a debatable reading, e.g. the 48 V a torque is measured at as a rated
  voltage) counts on neither side.
- **coverage (recall):** truth values found and kept ÷ required truth values.
- **location correct:** of the kept true values, those whose grounding contains the value's own word box (PDF/PNG,
  1% slack) or source range (HTML, 2 characters slack) in the harness truth index, and lies within the true block or
  cell (2% / 8 characters slack) — the cited cell is the right cell, not the table around it.
- **dropped:** proposed values the checker dropped, with reasons grouped; `dropped_true` counts drops that were
  in fact right (what the checker's strictness costs).
- **seconds per document** (Option 1 includes Markdownify's time on that source), **LLM calls**.
- `proposed_*`: the same scores before the checker, to show what the model alone got right.

R0 (robotparts' 20 ingested parts, 217 quote-verified values) has no hand truth, so it is scored against
robotparts: agreement (same key, same value and unit), recall of robotparts' values, conflicts, and values robotparts
did not have — split by whether Option 1 read them from a comparison table (one column or row per model), which
robotparts' prompt skipped — with a fixed-seed sample of 15 for a person to check.
"""

from __future__ import annotations

import collections
import json
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

from datasheet_extractor import option1  # noqa: E402
from datasheet_extractor.values import number_forms, normal  # noqa: E402
from run import EVAL, HARNESS, robotparts_parts  # noqa: E402

RESULTS = ROOT / "eval" / "results"
BOX_SLACK, BLOCK_SLACK = 0.01, 0.02
RANGE_SLACK, BLOCK_RANGE_SLACK = 2, 8
REASONS = [
    (r"another number .* sits between|row of values|not tied to a row label", "label not tied to the number (another number between: a row of values)"),
    (r"no text layer", "no text layer to check against (image)"),
    (r"does not name", "cited the wrong model's column/row"),
    (r"no --part-model", "multi-model table without --part-model"),
    (r"is not inside", "raw not inside the cited block/cell"),
    (r"carries none of|neither the row label", "no field label in the headers/quote"),
    (r"quote does not occur|not a page of", "quote not on the page"),
    (r"text layer inside the box", "box does not contain raw"),
    (r"unit", "unit not printed / wrong unit"),
    (r"repeat of", "a repeat of a kept value"),
    (r"ambiguous", "ambiguous: two different values for one key"),
    (r"not an id", "id not in index.json"),
    (r"whole table", "cited a whole table"),
    (r"not printed as a number|cannot be read in raw|no number printed|no number of raw", "value not printed in raw"),
    (r"part of the word|model designation", "number belongs to a designation"),
    (r"is a range|states sizes|ratio|place|first|second|low|high end", "number not at its key's place in the cell"),
]


def reason_group(reason: str) -> str:
    for pattern, name in REASONS:
        if re.search(pattern, reason):
            return name
    return "other: " + reason[:70]


def same_value(row: dict, truth: dict) -> bool:
    if row["key"] != truth["key"]:
        return False
    if isinstance(truth["value"], list):
        return isinstance(row["value"], list) and sorted(row["value"]) == sorted(truth["value"])
    if not isinstance(row["value"], (int, float)) or isinstance(row["value"], bool):
        return False
    return abs(float(row["value"]) - float(truth["value"])) < 1e-9 and (row.get("unit") or "") == (truth["unit"] or "")


# --- truth locations ------------------------------------------------------------------------------------

_truth_bundles: dict = {}


def truth_bundle(case: str, arm: str) -> option1.Bundle:
    key = (case, arm)
    if key not in _truth_bundles:
        _truth_bundles[key] = option1.Bundle.load(HARNESS / case / arm)
    return _truth_bundles[key]


def _fold(text: str) -> str:
    return normal(text).casefold()


def truth_places(truth: dict, case: str, arm: str) -> list[tuple[dict, dict]]:
    """(true block, true word grounding) for each place the truth row says the value is printed."""
    bundle = truth_bundle(case, arm)
    places = []
    for at in truth["at"]:
        block = None
        if "row" in at:
            for table in [b for b in bundle.index["blocks"] if b["type"] == "table"]:
                grid = option1.Grid.of(bundle, table)
                for cell in grid.cells:
                    rows = [_fold(t) for t in grid.row_headers(cell)]
                    column = _fold(" ".join(grid.column_headers(cell)))
                    if any(_fold(at["row"]) in t for t in rows) and _fold(at["column"]) in column \
                            and cell["row"] >= grid.header_rows and cell["col"] >= grid.header_cols:
                        block = cell
                        break
                if block:
                    break
        else:
            hits = [b for b in bundle.index["blocks"] if b["type"] not in ("table", "table_cell") and b.get("range")
                    and _fold(at["text"]) in _fold(option1.plain(bundle.text(b)))]
            block = min(hits, key=lambda b: b["range"]["end"] - b["range"]["start"]) if hits else None
        if block is None:
            continue
        word = _value_word(truth, bundle, block)
        places.append((block, word))
    return places


def _value_word(truth: dict, bundle: option1.Bundle, block: dict) -> dict | None:
    words = [w for w in bundle.index["words"]
             if block["range"]["start"] <= w["range"]["start"] < block["range"]["end"]]
    if isinstance(truth["value"], list):
        wanted = [name.lower() for name in truth["value"]]
        for word in words:
            if any(name in bundle.markdown[word["range"]["start"]:word["range"]["end"]].lower() for name in wanted):
                return word["grounding"]
        return None
    forms = number_forms(truth["value"])
    for word in words:
        text = bundle.markdown[word["range"]["start"]:word["range"]["end"]]
        if any(re.search(rf"(?<![\d.]){re.escape(form)}(?![\d])", text) for form in forms):
            return word["grounding"]
    return None


def _contains(outer, inner, slack) -> bool:
    return (outer[0] - slack <= inner[0] and outer[1] - slack <= inner[1]
            and inner[2] <= outer[2] + slack and inner[3] <= outer[3] + slack)


def location_correct(row: dict, truth: dict, case: str, arm: str) -> bool:
    for block, word in truth_places(truth, case, arm):
        if word is None:
            continue
        block_ground = (block.get("grounding") or [{}])[0]
        for ground in row.get("grounding") or []:
            if arm == "html":
                predicted = (ground.get("html") or {}).get("source_range")
                true_range = (word.get("html") or {}).get("source_range")
                block_range = (block_ground.get("html") or {}).get("source_range")
                if not (predicted and true_range and block_range):
                    continue
                if predicted["start"] - RANGE_SLACK <= true_range["start"] and true_range["end"] <= predicted["end"] + RANGE_SLACK \
                        and block_range["start"] - BLOCK_RANGE_SLACK <= predicted["start"] \
                        and predicted["end"] <= block_range["end"] + BLOCK_RANGE_SLACK:
                    return True
            else:
                box = ground.get("box")
                if not (isinstance(box, list) and len(box) == 4) or ground.get("page") != word.get("page"):
                    continue
                if _contains(box, word["box"], BOX_SLACK) and _contains(block_ground.get("box", [0, 0, 1, 1]), box,
                                                                       BLOCK_SLACK):
                    return True
    return False


# --- runs -------------------------------------------------------------------------------------------------

def load_runs(set_name: str) -> list[dict]:
    documents = json.loads((EVAL / "documents.json").read_text(encoding="utf-8"))
    runs = []
    for doc in documents:
        if doc["set"] != set_name:
            continue
        base = EVAL / set_name / doc["id"]
        if not base.is_dir():
            continue
        for directory in sorted(base.glob("option*-*")):
            option, backend = directory.name.split("-", 1)
            run = {"doc": doc, "option": option, "backend": backend, "dir": directory}
            if (directory / "values.json").is_file():
                run["values"] = json.loads((directory / "values.json").read_text(encoding="utf-8"))
                run["run"] = json.loads((directory / "run.json").read_text(encoding="utf-8"))
                bundle = run["run"].get("bundle")
                if bundle:
                    mdify = pathlib.Path(bundle).parent / "mdify.json"
                    run["mdify_seconds"] = json.loads(mdify.read_text())["seconds"] if mdify.is_file() else None
                else:
                    run["mdify_seconds"] = run["run"].get("mdify_seconds")
            else:
                run["failed"] = json.loads((directory / "failed.json").read_text()) if (directory / "failed.json").is_file() else {}
            runs.append(run)
    return runs


def _seconds(run: dict) -> float | None:
    if "run" not in run:
        return None
    return round(run["run"].get("seconds", 0) + (run.get("mdify_seconds") or 0), 2)


def _ratio(a, b):
    return round(a / b, 3) if b else None


def score_synthetic() -> dict:
    truths = {path.name.split(".")[0]: json.loads(path.read_text(encoding="utf-8"))
              for path in (ROOT / "eval" / "synthetic").glob("*.values.json")}
    groups: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    per_document = []
    for run in load_runs("synthetic"):
        doc = run["doc"]
        truth_rows = truths[doc["case"]]["models"][doc["part_model"]]
        required = [t for t in truth_rows if not t.get("optional")]
        record = {"id": doc["id"], "case": doc["case"], "arm": doc["arm"], "part_model": doc["part_model"],
                  "option": run["option"], "backend": run["backend"], "required": len(required)}
        if "values" not in run:
            record["error"] = run.get("failed", {}).get("error", "no values.json")
        else:
            rows = run["values"]["values"]
            for stage, chosen in (("kept", [r for r in rows if r["verdict"] == "kept"]), ("proposed", rows)):
                tp = fp = optional = located = 0
                found = set()
                for row in chosen:
                    match = next((t for t in truth_rows if same_value(row, t)), None)
                    if match is None:
                        fp += 1
                    elif match.get("optional"):
                        optional += 1
                    else:
                        tp += 1
                        found.add(match["key"])
                        if location_correct(row, match, doc["case"], doc["arm"]):
                            located += 1
                record[stage] = {"n": len(chosen), "tp": tp, "fp": fp, "optional": optional, "located": located,
                                 "found": len(found)}
            dropped = [r for r in rows if r["verdict"] == "dropped"]
            record["dropped"] = len(dropped)
            record["dropped_true"] = sum(1 for r in dropped if any(same_value(r, t) and not t.get("optional")
                                                                    for t in truth_rows))
            record["reasons"] = [reason_group(r["reasons"][0]) for r in dropped if r["reasons"]]
            record["false_kept"] = [{k: r.get(k) for k in ("key", "value", "unit", "raw", "cite")}
                                    for r in rows if r["verdict"] == "kept"
                                    and not any(same_value(r, t) for t in truth_rows)]
            record["missed"] = [t["key"] for t in required
                                if not any(r["verdict"] == "kept" and same_value(r, t) for r in rows)]
            record["seconds"] = _seconds(run)
            record["llm_calls"] = run["run"].get("llm_calls", 0)
            record["llm_error"] = run["run"].get("llm_error")
        per_document.append(record)
        groups[(run["option"], run["backend"])]["all"].append(record)
        groups[(run["option"], run["backend"])][doc["arm"]].append(record)
    summary = {}
    for (option, backend), by_arm in sorted(groups.items()):
        for arm, records in sorted(by_arm.items()):
            summary[f"{option}/{backend}/{arm}"] = _summarise(records)
    return {"summary": summary, "documents": per_document}


def _summarise(records: list[dict]) -> dict:
    ok = [r for r in records if "kept" in r]
    total = lambda stage, field: sum(r[stage][field] for r in ok)  # noqa: E731
    required = sum(r["required"] for r in ok)
    reasons = collections.Counter(reason for r in ok for reason in r["reasons"])
    seconds = [r["seconds"] for r in ok if r.get("seconds") is not None]
    kept_scored = total("kept", "n") - total("kept", "optional")
    proposed_scored = total("proposed", "n") - total("proposed", "optional")
    return {
        "documents": len(records), "failed": len(records) - len(ok),
        "precision": _ratio(total("kept", "tp"), kept_scored),
        "coverage": _ratio(total("kept", "tp"), required),
        "location_correct": _ratio(total("kept", "located"), total("kept", "tp")),
        "kept": total("kept", "n"), "true_kept": total("kept", "tp"), "required": required,
        "proposed": total("proposed", "n"),
        "proposed_precision": _ratio(total("proposed", "tp"), proposed_scored),
        "proposed_coverage": _ratio(total("proposed", "tp"), required),
        "proposed_location_correct": _ratio(total("proposed", "located"), total("proposed", "tp")),
        "dropped": sum(r["dropped"] for r in ok), "dropped_true": sum(r["dropped_true"] for r in ok),
        "dropped_reasons": dict(reasons.most_common()),
        "seconds_per_document": round(sum(seconds) / len(seconds), 1) if seconds else None,
        "llm_calls": sum(r["llm_calls"] for r in ok),
        "llm_errors": sum(1 for r in ok if r.get("llm_error")),
    }


# --- R0 ---------------------------------------------------------------------------------------------------

def _canonical(spec_key: str, atlas: str | None, registry: dict) -> str | None:
    if spec_key in registry["keys"]:
        return spec_key
    for key, entry in registry["keys"].items():
        if atlas and entry["atlas"].get(atlas) == spec_key:
            return key
    return None


def score_r0() -> dict:
    registry = json.loads(pathlib.Path("/home/bam/robotparts/schemas/registry.json").read_text(encoding="utf-8"))
    parts = {part["id"]: part for part in robotparts_parts()}
    theirs = {}
    for part_id, part in parts.items():
        theirs[part_id] = {}
        for spec_key, spec in part["record"]["specs"].items():
            if "quote" not in spec:
                continue
            key = _canonical(spec_key, part["atlas"], registry)
            theirs[part_id][key] = spec
    reference_total = sum(len(v) for v in theirs.values())
    ours: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    stats: dict = collections.defaultdict(lambda: {"seconds": [], "llm_calls": 0, "dropped": 0, "reasons": collections.Counter(),
                                                   "proposed": 0, "kept": 0, "documents": 0, "failed": 0,
                                                   "llm_errors": 0})
    for run in load_runs("r0"):
        config = f"{run['option']}/{run['backend']}"
        stat = stats[config]
        stat["documents"] += 1
        if "values" not in run:
            stat["failed"] += 1
            continue
        stat["seconds"].append(_seconds(run))
        stat["llm_calls"] += run["run"].get("llm_calls", 0)
        stat["llm_errors"] += 1 if run["run"].get("llm_error") else 0
        for row in run["values"]["values"]:
            stat["proposed"] += 1
            if row["verdict"] == "kept":
                stat["kept"] += 1
                ours[config][run["doc"]["part"]].append(dict(row, source_url=run["doc"]["url"],
                                                             run=str(run["dir"])))
            else:
                stat["dropped"] += 1
                stat["reasons"][reason_group(row["reasons"][0]) if row["reasons"] else "none"] += 1
    summary, details, new_pool = {}, {}, {}
    for config in sorted(stats):
        agree, conflicts, missing, new = [], [], [], []
        for part_id, reference in theirs.items():
            mine = ours[config].get(part_id, [])
            by_key = collections.defaultdict(list)
            for row in mine:
                by_key[row["key"]].append(row)
            for key, spec in reference.items():
                rows = by_key.get(key, [])
                if any(isinstance(r["value"], (int, float)) and abs(float(r["value"]) - float(spec["value"])) < 1e-9
                       and r["unit"] == spec["unit"] for r in rows):
                    agree.append((part_id, key))
                elif rows:
                    conflicts.append({"part": part_id, "key": key,
                                      "robotparts": {"value": spec["value"], "unit": spec["unit"], "raw": spec["raw"],
                                                     "quote": spec.get("quote")},
                                      "ours": [{k: r.get(k) for k in ("value", "unit", "raw", "cite", "proposed")}
                                               for r in rows]})
                else:
                    missing.append({"part": part_id, "key": key, "robotparts": {"value": spec["value"],
                                                                                  "unit": spec["unit"],
                                                                                  "quote": spec.get("quote")}})
            for key, rows in by_key.items():
                if key in reference:
                    continue
                for row in rows[:1]:
                    multi = bool((row.get("cite") or {}).get("multi_model"))
                    item = {"part": part_id, "model": parts[part_id]["model"], "key": key, "value": row["value"],
                            "unit": row["unit"], "raw": row["raw"], "condition": row.get("condition"),
                            "cite": row.get("cite"), "quote": (row.get("proposed") or {}).get("quote"),
                            "source_url": row["source_url"], "multi_model_table": multi}
                    new.append(item)
                    pool_key = (part_id, key, json.dumps(row["value"]), row["unit"])
                    new_pool.setdefault(pool_key, dict(item, configs=[]))["configs"].append(config)
        stat = stats[config]
        summary[config] = {
            "documents": stat["documents"], "failed": stat["failed"], "robotparts_values": reference_total,
            "agree": len(agree), "conflicts": len(conflicts), "missing": len(missing),
            "recall_of_robotparts": _ratio(len(agree), reference_total),
            "agreement_where_both": _ratio(len(agree), len(agree) + len(conflicts)),
            "new_values": len(new), "new_from_multi_model_tables": sum(1 for n in new if n["multi_model_table"]),
            "kept": stat["kept"], "proposed": stat["proposed"], "dropped": stat["dropped"],
            "dropped_reasons": dict(stat["reasons"].most_common()),
            "seconds_per_document": round(sum(stat["seconds"]) / len(stat["seconds"]), 1) if stat["seconds"] else None,
            "llm_calls": stat["llm_calls"], "llm_errors": stat["llm_errors"],
        }
        details[config] = {"conflicts": conflicts, "missing": missing, "new": new}
    pool = sorted(new_pool.values(), key=lambda item: (item["part"], item["key"], str(item["value"])))
    sample = random.Random(20261029).sample(pool, min(15, len(pool)))
    for item in sample:
        item["human_check"] = None  # right | wrong | not this part — for a person to fill in
    return {"summary": summary, "details": details, "new_sample": sample, "new_distinct": len(pool)}


def table(rows: list[tuple]) -> str:
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "|".join("---" for _ in rows[0]) + "|"]
    out += ["| " + " | ".join("" if cell is None else str(cell) for cell in row) + " |" for row in rows[1:]]
    return "\n".join(out)


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    synthetic = score_synthetic()
    r0 = score_r0()
    (RESULTS / "synthetic.json").write_text(json.dumps(synthetic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RESULTS / "r0.json").write_text(json.dumps({k: r0[k] for k in ("summary", "details", "new_distinct")},
                                                ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RESULTS / "r0-new-sample.json").write_text(json.dumps(r0["new_sample"], ensure_ascii=False, indent=2) + "\n",
                                                encoding="utf-8")
    rows = [("option × model × arm", "docs", "precision", "coverage", "location", "kept", "dropped (true)",
             "proposed prec / cov", "s/doc", "LLM calls")]
    for name, s in synthetic["summary"].items():
        rows.append((name, s["documents"], s["precision"], s["coverage"], s["location_correct"], s["kept"],
                     f"{s['dropped']} ({s['dropped_true']})", f"{s['proposed_precision']} / {s['proposed_coverage']}",
                     s["seconds_per_document"], s["llm_calls"]))
    print(table(rows))
    rows = [("option × model", "docs", "agree", "recall of 217", "agreement where both", "conflicts", "new (multi-model)",
             "kept", "dropped", "s/doc", "LLM calls")]
    for name, s in r0["summary"].items():
        rows.append((name, s["documents"], s["agree"], s["recall_of_robotparts"], s["agreement_where_both"],
                     s["conflicts"], f"{s['new_values']} ({s['new_from_multi_model_tables']})", s["kept"], s["dropped"],
                     s["seconds_per_document"], s["llm_calls"]))
    print()
    print(table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
