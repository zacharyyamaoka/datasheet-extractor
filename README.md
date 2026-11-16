# Datasheet Extractor

A field list in, checked values out: Markdownify's CONTRACT §7 seam (`fields.json` → `values.json`). It knows
nothing about robot parts; the consumer supplies the fields (`fields/actuator.json` is robotparts' vocabulary).

```bash
uv run extract <source> --fields fields/actuator.json --option 1|2 [--model claude|codex] [--part-model P] --out DIR
```

- **Option 1:** the source through Markdownify; the model cites block and cell ids; code checks them and reads the
  row and column headers from the span index (`src/datasheet_extractor/option1.py`).
- **Option 2:** one model call on the raw source only, checked like robotparts' `check.py` (`option2.py`).

**Copy as Spec:** the Sources app's review tab has a Copy as Spec button (beside Publish) that copies the kept rows as paste-ready `"key": Spec(...)` lines for a robotparts part file's `specs={...}`; the same block from the command line: `uv run extract as-spec <source id> [--home DIR] [--product ID]`. Its `source_kind` comes from robotparts' `makers.toml` (read at each call): a host on a maker's domain is `manufacturer`, a known shop (aifitlab, alibaba, aliexpress, omc-stepperonline) a `retailer`, anything else keeps a `# TODO`.

Evaluation: `eval/` (`eval/RESULTS.md`). Agent skill: `skills/datasheet-extractor/SKILL.md`.
