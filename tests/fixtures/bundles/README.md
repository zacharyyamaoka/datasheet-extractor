# Recorded bundles (tests/test_locate.py, tests/test_stated.py)

Five real Markdownify conversions (pipeline A, 2026-10-29) of Markdownify's own synthetic datasheets
(`/home/bam/markdownify/harness/cases/<case>/source.md`, rendered by its harness as PDF, PNG and HTML), each with the
model reply Option 1 recorded for it (`reply.json`, Claude, eval run of 2026-10-29) and the values.json that run wrote
(`values.json`, rejudged 2026-11-05, written before `raw_grounding` existed). The tests replay `reply.json` through
today's checker: no model call.

| folder | what it holds |
|---|---|
| `12-full-datasheet-pdf` | a PDF spec table (KM45-10): table values, a list item, a footnote mark on a label |
| `12-full-datasheet-png` | the same page as an image: word boxes from OCR, no text layer |
| `12-full-datasheet-html` | the same page as HTML: words with byte ranges and text quotes |
| `03-simple-table-html` | a web page that prints "24 V" twice (intro and table): a quote needs context to be one place |
| `01-prose-spec-pdf` | values inside a paragraph: the case that narrows a whole block to the value's words |

Changed from the cache copies (`~/.cache/datasheet-extractor/eval/`): `index.json` minified; `source.json` keeps
only `sha256`, `bytes`, `media_type` and one retrieval whose URL is `https://example.com/km-series/<case>.<ext>`
(the cache recorded local paths); `values.json`'s `source.url` is that URL too. `case.json` names the part model and
where the run came from.
