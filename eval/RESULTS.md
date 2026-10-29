# Results — Datasheet Extractor v0, Option 1 vs Option 2 (2026-10-29)

**Option 1 (Markdownify bundle + cited ids) beats Option 2 (single shot on the raw source) on the synthetic set at
equal precision (1.0 both): coverage 0.968 vs 0.25–0.27, location 1.0 vs 0.91–0.94.** On robotparts' real pages (R0)
Option 2 reproduces robotparts slightly better (recall 0.98–1.0 vs 0.96–0.98) — robotparts' values were made by this
very Option 2 pipeline — while Option 1 adds 39–41 values robotparts did not have, 15 of them from comparison tables.
So "strictly better" holds on coverage and location, not on R0 recall.

Made by `uv run python eval/run.py` (340 LLM calls: synthetic 63 docs × 2 options × 2 models, R0 22 inputs × 2 × 2),
then `uv run python eval/score.py`. Per-run files (values.json, run.json, the raw `llm/events.jsonl`) are in
`~/.cache/datasheet-extractor/eval/`; scores in `eval/results/synthetic.json`, `r0.json`, `r0-new-sample.json`.

## Synthetic set (12 harness cases × PDF/PNG/HTML; multi-model cases once per model; 119 truth rows)

Precision and coverage count only the requested fields (`fields/actuator.json`). Location: the kept value's grounding
holds its true word box (HTML: source range) from the harness truth index and lies inside its true cell or block.
"dropped (true)": checker drops, and how many of those were in fact right. "proposed": before the checker.

| option × model × arm | docs | precision | coverage | location | kept | dropped (true) | proposed prec / cov | s/doc | LLM calls |
|---|---|---|---|---|---|---|---|---|---|
| option1/claude/all | 63 | 1.0 | 0.968 | 1.0 | 334 | 17 (11) | 1.0 / 1.0 | 16.2 | 63 |
| option1/claude/html | 21 | 1.0 | 0.982 | 1.0 | 113 | 4 (2) | 1.0 / 1.0 | 7.4 | 21 |
| option1/claude/pdf | 21 | 1.0 | 0.982 | 1.0 | 113 | 4 (2) | 1.0 / 1.0 | 19.1 | 21 |
| option1/claude/png | 21 | 1.0 | 0.939 | 1.0 | 108 | 9 (7) | 1.0 / 1.0 | 22.2 | 21 |
| option1/codex/all | 63 | 1.0 | 0.968 | 1.0 | 334 | 12 (6) | 1.0 / 0.985 | 18.7 | 63 |
| option1/codex/html | 21 | 1.0 | 0.982 | 1.0 | 113 | 4 (2) | 1.0 / 1.0 | 9.7 | 21 |
| option1/codex/pdf | 21 | 1.0 | 0.982 | 1.0 | 113 | 4 (2) | 1.0 / 1.0 | 21.4 | 21 |
| option1/codex/png | 21 | 1.0 | 0.939 | 1.0 | 108 | 4 (2) | 1.0 / 0.956 | 25.1 | 21 |
| option2/claude/all | 63 | 1.0 | 0.249 | 0.941 | 87 | 258 (254) | 0.991 / 0.991 | 8.8 | 63 |
| option2/claude/html | 21 | 1.0 | 0.395 | 0.933 | 46 | 69 (68) | 0.991 / 0.991 | 8.6 | 21 |
| option2/claude/pdf | 21 | 1.0 | 0.351 | 0.95 | 41 | 74 (72) | 0.982 / 0.982 | 8.6 | 21 |
| option2/claude/png | 21 | — | 0.0 | — | 0 | 115 (114) | 1.0 / 1.0 | 9.1 | 21 |
| option2/codex/all | 63 | 1.0 | 0.266 | 0.912 | 93 | 161 (156) | 1.0 / 0.722 | 18.3 | 63 |
| option2/codex/html | 21 | 1.0 | 0.404 | 0.913 | 47 | 18 (18) | 1.0 / 0.561 | 14.1 | 21 |
| option2/codex/pdf | 21 | 1.0 | 0.395 | 0.911 | 46 | 44 (42) | 1.0 / 0.763 | 20.7 | 21 |
| option2/codex/png | 21 | — | 0.0 | — | 0 | 99 (96) | 1.0 / 0.842 | 20.1 | 21 |

## R0 — robotparts' 20 ingested parts (22 inputs, 217 quote-verified values)

| option × model | docs | agree | recall of 217 | agreement where both | conflicts | new (multi-model) | kept | dropped | s/doc | LLM calls |
|---|---|---|---|---|---|---|---|---|---|---|
| option1/claude | 22 | 212 | 0.977 | 0.991 | 2 | 41 (15) | 257 | 8 | 14.4 | 22 |
| option1/codex | 22 | 208 | 0.959 | 0.995 | 1 | 39 (15) | 250 | 10 | 22.9 | 22 |
| option2/claude | 22 | 213 | 0.982 | 1.0 | 0 | 19 (0) | 232 | 25 | 15.0 | 22 |
| option2/codex | 22 | 217 | 1.0 | 1.0 | 0 | 22 (0) | 239 | 18 | 26.9 | 22 |

- 51 distinct new values over all four configurations. Many are `buses` (robotparts asks for buses only on parts in
  no atlas) and comparison-table values robotparts' prompt skipped (rated power, rated voltage, size).
  `eval/results/r0-new-sample.json`: 15 sampled with a fixed seed, `human_check: null` to fill in.
- Option 1's conflicts are not misreads: J60-10 voltage_min is 18 V on the maker's web page and 12 V in its PDF
  (image-only; robotparts could not read it, Markdownify OCR'd it); AKE40-6 gear_ratio 6 is `""` in robotparts
  ("Reduction Ratio 6") and `:1` here ("6:1" in the comparison table).
- Multi-model "new" values are counted for Option 1 only: Option 2 has no table structure to tell them apart.

## Where each option won (concrete)

1. **04-multi-model PDF, KM80-9, peak_torque.** Option 1 cited `table_cell-19`; code read row "Peak torque" and
   column "KM80-9" from the index → kept, box on the right cell. Option 2 (Claude) proposed 18 N·m with a correct box
   and the quote "Peak torque 9 N·m 24.8 N·m 18 N·m" → dropped: "another number (9) sits between them" (robotparts'
   binding rule refuses a row of values). Codex did not propose it at all.
2. **CubeMars AKE40-6 KV90 (real), rated_power and outer_diameter.** Option 1 kept 50 W from the comparison table
   (row "AKE40-6 KV90", column "Rated Power (W)") and 51 mm from "φ51×24.5" (column "Size (diameter *length) MM").
   Option 2 proposed 50 W from "Max Continuous Power W 50" → dropped, no rated-power label (a correct drop), and
   "Size mm Ø51×24.5" → dropped, the unit is printed where the binder does not look.
3. **Option 2 won — DEEP Robotics J60-10 (real), max_output_speed 15.49 rad/s.** The page covers both J60 models and
   prints each label and value as two paragraphs; Option 2's quote "Peak Speed 15.49rad/s" passed, while Option 1's
   model proposed nothing for J60-10 on that page (both backends). On PNG, Markdownify's OCR read "No-load" as "l-od"
   (05-merged-header), so Option 1 drops every max_output_speed there.

## Checker drop reasons (synthetic, all arms)

- Option 1 (Claude / Codex): no field label in the cited cell's headers or block, 14 / 9 (the OCR "l-od"; "10:1
  planetary stage" has no label at all); label not tied to the number, 3 / 3 ("9:1 planetary gearbox" comes right
  after "21 pole pairs", so the binder ties it to pole pairs).
- Option 2 (Claude / Codex): image with no text layer, 115 / 99 (every PNG value); row of values, 70 / 40; no label
  in the quote (merged or units-in-header tables), 49 / 14; unit not printed where the binder looks, 16 / 0; quote
  not on the page, 0 / 7; box does not contain raw, 5 / 1.

## Notes

- Option 2 on a PNG can never keep a value: there is nothing to check its quote or box against (no OCR in the
  checker), robotparts' rule for an image-only PDF. Its `proposed` scores show the model read them right.
- Checker fixes after the first run were applied with `eval/run.py --rejudge` (stored replies, no new calls):
  footnote markers ('Rated torque2') are no longer read as model designations; a label-only block right before a
  value block ('Peak Torque' / '19.94Nm') is that value's label. The location ruler's block slack is 3%, because a
  list item's Markdownify box includes its bullet glyph.
- Seconds per document for Option 1 include Markdownify's time (about 10 s of docling model load on PDF/PNG).
