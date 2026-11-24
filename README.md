# Datasheet Extractor

A field list in, checked values out: Markdownify's CONTRACT §7 seam (`fields.json` → `values.json`). It knows
nothing about robot parts; the consumer supplies the fields (`fields/actuator.json` is robotparts' vocabulary).

```bash
uv run extract <source> --fields fields/actuator.json --option 1|2 [--model claude|codex] [--part-model P] --out DIR
```

- **Option 1:** the source through Markdownify; the model cites block and cell ids; code checks them and reads the
  row and column headers from the span index (`src/datasheet_extractor/option1.py`).
- **Option 2:** one model call on the raw source only, checked like robotparts' `check.py` (`option2.py`).

**Where each value is printed:** Option 1 (and open mode) narrow a kept value from its cited cell or paragraph to the
words that print its `raw`, from the bundle's word index (`src/datasheet_extractor/locate.py`, no model call), and
values.json keeps both: `grounding` (the cell) and `raw_grounding` (the words: a `{page, box}` per line, or a web page's
byte range + a text quote checked to match one place on the saved page). Printed twice in its cell, or not found, a
value keeps `raw_grounding: null` and is marked by its cell, which its note says. On an image or a scanned PDF the
word boxes are OCR's character-count estimates, so the box is read off the page's ink instead (`ink.py`); a line whose
words the ink cannot tell apart is marked as the line, and its note says so.

**Copy as Spec:** the Sources app's review tab copies the kept rows paste-ready (`uv run extract as-spec <source id>
[--home DIR] [--product ID] [--spec]` prints the same text):

- **Copy as Spec** (default): a whole robot_models source file for robotparts `user/parts/<id>/sources/<name>.py`: a
  `Source(...)` header pinning the raw file by sha256, then `values = <Type>(key=Stated(value, unit, "label as
  printed", where="page=1&xywh=percent:…" | "text=prefix-,exact,-suffix"), ...)`. The type is the one the part's own
  file resolves with (`<Type>.from_sources`), else the actuator type covering most fields (marked `# TODO`). The
  answer names the file to save and the bundle file to copy beside it as the raw file.
- **Spec lines** (`--spec`): `"key": Spec(...)` lines for a catalogue `parts/<id>` file's `specs={...}`. robotparts'
  `Spec` has no `where` field yet, so the selector rides in the note (`where page=1&xywh=…`) until it does.

Every `where` is written with source_msgs' own selectors (source-highlighter PEP 0001), so it parses wherever robotparts
checks one. `source_kind` comes from robotparts' `makers.toml` (read at each call): a host on a maker's domain is
`manufacturer`, a known shop (aifitlab, alibaba, aliexpress, omc-stepperonline) a `retailer`, anything else keeps a
`# TODO`. With `--products robotparts:/home/bam/robotparts` the product list holds Zach's own parts (`user/parts`, read
only, listed first) as well as the catalogue, so a batch can target them.

Proof on recorded bundles (no model call): `uv run python scripts/bridge_demo.py --out DIR` prints the source files
and draws every `where` box on its page render.

Evaluation: `eval/` (`eval/RESULTS.md`). Agent skill: `skills/datasheet-extractor/SKILL.md`.
