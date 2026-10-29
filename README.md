# Datasheet Extractor

A field list in, checked values out: Markdownify's CONTRACT §7 seam (`fields.json` → `values.json`). It knows
nothing about robot parts; the consumer supplies the fields (`fields/actuator.json` is robotparts' vocabulary).

```bash
uv run extract <source> --fields fields/actuator.json --option 1|2 [--model claude|codex] [--part-model P] --out DIR
```

- **Option 1:** the source through Markdownify; the model cites block and cell ids; code checks them and reads the
  row and column headers from the span index (`src/datasheet_extractor/option1.py`).
- **Option 2:** one model call on the raw source only, checked like robotparts' `check.py` (`option2.py`).

Evaluation: `eval/` (`eval/RESULTS.md`). Agent skill: `skills/datasheet-extractor/SKILL.md`.
