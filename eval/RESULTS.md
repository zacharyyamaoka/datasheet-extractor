# Results — Datasheet Extractor v0, Option 1 vs Option 2 (2026-10-29)

**Option 1 (Markdownify bundle + cited ids) beats Option 2 (single shot on the raw source) on the synthetic set at
equal precision (1.0 both): coverage 0.968 vs 0.25–0.27, location 1.0 vs 0.91–0.94.** On robotparts' real pages (R0)
Option 2 reproduces robotparts slightly better (recall 0.98–1.0 vs 0.96–0.98) — robotparts' values were made by this
very Option 2 pipeline — while Option 1 adds 39–41 values robotparts did not have, 15 of them from comparison tables.
So "strictly better" holds on coverage and location, not on R0 recall.

Made by 340 runs over 85 documents (340 LLM calls: synthetic 63 docs × 2 options × 2 models, R0 22 inputs × 2 × 2)
then | option × model × arm | docs | precision | coverage | location | kept | dropped (true) | proposed prec / cov | s/doc | LLM calls |
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
| option2/claude/png | 21 |  | 0.0 |  | 0 | 115 (114) | 1.0 / 1.0 | 9.1 | 21 |
| option2/codex/all | 63 | 1.0 | 0.266 | 0.912 | 93 | 161 (156) | 1.0 / 0.722 | 18.3 | 63 |
| option2/codex/html | 21 | 1.0 | 0.404 | 0.913 | 47 | 18 (18) | 1.0 / 0.561 | 14.1 | 21 |
| option2/codex/pdf | 21 | 1.0 | 0.395 | 0.911 | 46 | 44 (42) | 1.0 / 0.763 | 20.7 | 21 |
| option2/codex/png | 21 |  | 0.0 |  | 0 | 99 (96) | 1.0 / 0.842 | 20.1 | 21 |

| option × model | docs | agree | recall of 217 | agreement where both | conflicts | new (multi-model) | kept | dropped | s/doc | LLM calls |
|---|---|---|---|---|---|---|---|---|---|---|
| option1/claude | 22 | 212 | 0.977 | 0.991 | 2 | 41 (15) | 257 | 8 | 14.4 | 22 |
| option1/codex | 22 | 208 | 0.959 | 0.995 | 1 | 39 (15) | 250 | 10 | 22.9 | 22 |
| option2/claude | 22 | 213 | 0.982 | 1.0 | 0 | 19 (0) | 232 | 25 | 15.0 | 22 |
| option2/codex | 22 | 217 | 1.0 | 1.0 | 0 | 22 (0) | 239 | 18 | 26.9 | 22 |. Per-run files (values.json, run.json, raw ):
. Scores: .

## Synthetic set (12 Markdownify harness cases × PDF/PNG/HTML; multi-model cases once per model; 119 truth rows)

Precision and coverage count only the requested fields (fields/actuator.json); "location" = the kept value's grounding
holds its true word box (or HTML source range) and lies inside its true cell or block. "dropped (true)": checker drops,
and how many of them were in fact right. "proposed": before the checker.

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
| option2/claude/png | 21 |  | 0.0 |  | 0 | 115 (114) | 1.0 / 1.0 | 9.1 | 21 |
| option2/codex/all | 63 | 1.0 | 0.266 | 0.912 | 93 | 161 (156) | 1.0 / 0.722 | 18.3 | 63 |
| option2/codex/html | 21 | 1.0 | 0.404 | 0.913 | 47 | 18 (18) | 1.0 / 0.561 | 14.1 | 21 |
| option2/codex/pdf | 21 | 1.0 | 0.395 | 0.911 | 46 | 44 (42) | 1.0 / 0.763 | 20.7 | 21 |
| option2/codex/png | 21 |  | 0.0 |  | 0 | 99 (96) | 1.0 / 0.842 | 20.1 | 21 |

## R0 — robotparts' 20 ingested parts (22 inputs, 217 quote-verified values)

| option × model | docs | agree | recall of 217 | agreement where both | conflicts | new (multi-model) | kept | dropped | s/doc | LLM calls |
|---|---|---|---|---|---|---|---|---|---|---|
| option1/claude | 22 | 212 | 0.977 | 0.991 | 2 | 41 (15) | 257 | 8 | 14.4 | 22 |
| option1/codex | 22 | 208 | 0.959 | 0.995 | 1 | 39 (15) | 250 | 10 | 22.9 | 22 |
| option2/claude | 22 | 213 | 0.982 | 1.0 | 0 | 19 (0) | 232 | 25 | 15.0 | 22 |
| option2/codex | 22 | 217 | 1.0 | 1.0 | 0 | 22 (0) | 239 | 18 | 26.9 | 22 |

New values (distinct over all four configurations: see  ) include  on every atlas
part (robotparts asks for buses only on folder parts) and comparison-table values (rated power, rated voltage) that
robotparts' prompt skipped. : 15 sampled,  to fill in.

## Where each option won (concrete)

1. **04-multi-model PDF, KM80-9, peak_torque.** Option 1 cited ; code read row "Peak torque", column
   "KM80-9" from the index → kept, box on the right cell. Option 2 (Claude) proposed 18 N·m with a correct box and the
   quote "Peak torque 9 N·m 24.8 N·m 18 N·m" → dropped: "another number (9) sits between them" (robotparts' binding
   rule refuses a row of values). Codex did not even propose it.
2. **CubeMars AKE40-6 KV90 (real), rated_power.** Option 1 kept 50 W from the comparison table (row "AKE40-6 KV90",
   column "Rated Power (W)", multi_model). Option 2 proposed 50 W off "Max Continuous Power W 50" → dropped, no rated-
   power label (right to drop). Same page, outer_diameter: Option 1 kept 51 mm (Ф mark, column "Size (diameter
   *length) MM"); Option 2 dropped "Size mm Ø51×24.5": unit printed in neither place the binder looks.
3. **Option 2 won — DEEP Robotics J60-10 (real), max_output_speed 15.49 rad/s.** The page prints label and value as
   separate paragraphs and covers both J60 models; Option 2's quote "Peak Speed 15.49rad/s" passed, Option 1's model
   proposed nothing for J60-10 on that page (both backends). Also: on PNG, Markdownify's OCR read "No-load" as "l-od"
   (05-merged-header), so Option 1 dropped every max_output_speed there.

## Checker drop reasons (synthetic, all arms)

- Option 1: no field label in the cited cell's headers / block (Claude 14, Codex 9: OCR "l-od", '9:1 … gearbox'
  after 'pole pairs' in prose, '10:1 planetary stage' with no label at all); label not tied to the number (3).
- Option 2: image with no text layer (Claude 115 / Codex 99 — every PNG value); row of values (70 / 40); no label in
  the quote, e.g. a merged header (49 / 14); unit not printed where the binder looks (16 / 0); quote not on the page
  (0 / 7); box does not contain raw (5 / 1).

## Notes

- Option 2 on a PNG can never keep a value: nothing to check the quote or box against (no OCR in the checker),
  which is robotparts' rule for an image-only PDF. Its pre-checker scores () show the model read them right.
- Checker changes after the first run were applied with  (stored replies, no new calls):
  footnote markers ('Rated torque2') no longer read as designations; a label-only block right before a value block
  ('Peak Torque' / '19.94Nm') is the value's label. The location ruler's block slack is 3% (a list item's Markdownify
  box includes its bullet).
- Seconds per document for Option 1 include Markdownify's time (≈10 s docling model load on PDF/PNG).
