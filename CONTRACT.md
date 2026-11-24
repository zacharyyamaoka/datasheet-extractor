# Datasheet Extractor — Sources app contract v0 (2026-11-05)

The Sources app is where datasheets enter: add sources, watch the queue, review each source's values beside the
source, publish one reviewed datasheet per product. Generic: RobotParts is one consumer, plugged in through a product
provider, never imported. Value and review formats are /home/bam/markdownify/CONTRACT.md §7 (values.json, value ids)
and §7.1 (review.json ops); locations are /home/bam/source-highlighter/CONTRACT.md §2.
WHY (Zach, 2026-10-29): "I should be able to drag and drop any number of files and also add any number of urls … set
the initial products … if they don't exist it can create new ones … this single upload becomes an 'upload', grouped
together, added to the queue … once source per tab … source rendered in the left and the extracted values on the
right … copy a prompt that you can paste into any AI agent … from a single source you can create multiple
'datasheets'."

## 1. Command

`extract serve [--port 0] [--home DIR] [--products robotparts:/home/bam/robotparts | file:DIR/products.json]`
Serves on 127.0.0.1, prints the URL, opens no window. State lives in `--home` (default
`$DATASHEET_EXTRACTOR_HOME` or `~/.local/share/datasheet-extractor/`), never in the checkout.

## 2. State on disk (`<home>/`)

```
batches/<batch id>/batch.json     {id, created_at, label, category, products: [product ids], sources: [source id…]}
sources/<source id>/source.json   {id, batch, kind: "file"|"url", name, url?, capture_keep, stage, error?, sha256?,
                                   category? (default: the batch's; a batch can mix a servo and a power supply),
                                   running: "capture"|"convert"|"extract"|null (the step a job is in now), note?,
                                   bundle: "<abs path of the Markdownify bundle>", history: [{stage, at}]}
sources/<source id>/values.json   the extractor's output (CONTRACT §7), never rewritten after extraction
sources/<source id>/review.json   human ops (CONTRACT §7.1), append-only
published/<product id>/<source id>.json   the effective datasheet for one product (§5)
products.json                     products created here (generic provider), [{id, title, created_at}]
```
`source id` = first 12 hex of the source sha256 once captured (`pending-<n>` before).

## 3. Stages (the queue)

`added → captured → converted → extracted → published`, plus `failed` with an error. A source never skips a stage
silently; each step appends `{stage, at}` to `history`. Capture = `markdownify capture --keep <capture_keep>` for URLs
(default `html`; the 5 levels of markdownify CONTRACT §2.1), the file's bytes for files. Convert = `markdownify` pipeline A.
Extract = Option 1 with the logged-in CLI (default claude), the batch's category fields, every product of the batch.
A URL with `capture_keep` = `link` alone keeps only the source record: it stops at `captured` with `note: "link only:
nothing stored to read"` (no convert, no extract, never `failed`).
`running` names the step a job is in (a re-extraction keeps the stage `extracted` while it runs) and is cleared when
the step ends or fails; `GET /api/batches` and `GET /api/sources/<id>` return it. A restart clears a `running` left by
the dead process and appends `failed` to the history for that step (a source it resumes then runs the step again).

## 4. HTTP API (JSON unless noted)

| method + path | does |
|---|---|
| `GET /api/fields` | categories available (`fields/*.json`), each with `labels: {key: label}` (a field's first label as written; `{}` in open mode) |
| `GET /api/products` | `[{id, title, provider}]` from the provider + `products.json` |
| `POST /api/products` `{title}` | creates a product here (id = slug); returns it |
| `GET /api/batches` | batches, newest first, each with its sources' `{id, name, kind, stage, running, error, note}` |
| `POST /api/batches` (multipart: `files[]`, `urls` newline text, `products` JSON list, `category`, `capture_keep`) | creates the batch, queues every source, returns it |
| `GET /api/sources/<id>` | `{source, values, review, effective}` — `effective` = values ⊕ review (§5) |
| `GET /api/sources/<id>/file/<name>` | a bundle file (`source.<ext>`, `index.json`, `document.md`, `snapshot.jpg`) |
| `POST /api/sources/<id>/review` `{op…}` | appends one §7.1 op; the server stamps `author` (`<home>/config.json`'s `author`, else `git config user.name`, else the OS user) and `at`; returns review.json |
| `POST /api/sources/<id>/extract` `{model?, category?}` | (re)runs extraction as a background job; `category` is saved to source.json first (a category with no `fields/<category>.json` runs open mode: every specification the source states, keyed `spec.<printed label>`) |
| `GET /api/sources/<id>/prompt` (text) | a prompt any AI agent can run: the command line for this machine, then the source path/URL, the fields, and where to write values.json |
| `GET /api/sources/<id>/spec[?product=&form=stated\|spec]` | `{text, count, form, save_as}`: the kept rows paste-ready (Copy as Spec; `extract as-spec <id> [--spec]` prints the same text). `stated` (default): a robot_models source file, `Stated(value, unit, as_written, where=…)` per value (§7); `spec`: `"key": Spec(...)` lines for a catalogue part file's `specs={...}`, the `where` in the note. `save_as`: `{py, raw, raw_from, product}`, the user/parts folder's `sources/<name>.py` and raw file when the product has one |
| `POST /api/sources/<id>/publish` | writes `published/<product>/<source>.json` for every assigned product; stage → published |

## 5. Effective record and publishing

values.json with review.json applied in order (last op on a value wins): edit replaces a field and keeps `ai` = the
original; reject marks `rejected: true`; restore clears it; add appends a human row (`human: true`, its own grounding
or `null`); assign/unassign set `products`; map `{model, product|null}` assigns the values read for that model
(`part_models`, or a cite row/column naming it) to the product — per value and product the last op wins, and an
unmap takes back only what the map gave (the record's `models` lists the current mappings). A published datasheet is one product's view:
`{product, source: {sha256, url, retrieved_at}, values: [{id, key, value, unit, raw, grounding, raw_grounding?,
provenance: {by: "ai"|"<author>", at, ai?: <original>}}]}` — rejected values left out, every value still pointing at
its source. An edit to `raw` or `grounding` clears `raw_grounding` (it marked the old text).

## 6. Review tab (the UI)

The splice recommended from the 2026-10-29 review-view round (AI ranking fragile: V5 93, V3 90; Zach did not pick, so
the recommendation is the default): **V5 Stream** as the base — a provenance column on every row (faint "AI · date" on
untouched rows, bold initials · time where you acted), keyboard j/k move, type to replace, x strike/restore, a add,
p products, and a version log docked at the bottom; **V4**'s "this datasheet covers N models → map to your parts"
step on top; **V3**'s per-product columns as a view toggle. Source on the left via the Source Highlighter; click a row
→ its source spotlights. Edit in place, no Fix button, no modal; untouched = accepted; struck stays visible.
Every pane boundary drags and is remembered; the log and the mapping step fold.

## 7. A value's own words, and its `where` (2026-11-24)

values.json rows from Option 1 and open mode carry, beside Markdownify's `grounding` (the cited cell or block):

- `raw_grounding`: the Locations (source-highlighter CONTRACT §2) of the words that print `raw` inside the cited block,
  from index.json's words: one `{page, box}` per line for a PDF or image; for a web page one
  `{html: {source_range, xpath, text_quote}}`, whose quote context is grown across the page until it matches exactly
  one place on the saved page (left out when it cannot be). `null` when raw is not among the block's words or is
  printed there more than once: neither is marked (the highlighter's rule: several matches are reported, never
  picked from). Rows written before this have no key; Copy as Spec then finds the words in the bundle.
- `cite.label_ids` / `cite.label`: the cells the checker read the field's label in, and that label as the source
  prints it (no NFKC), a Stated's `as_written`.

Copy as Spec writes each value's `where` as one source_msgs compact selector: `page=N&xywh=percent:x,y,w,h` (the
words' box, rounded outward to 0.01 %), `text=prefix-,exact,-suffix` (a web page's checked quote), `char=a,b` (a web
page's words by code points into the pinned bytes, when no quote is unique), or the cited cell's box or `xpath=` with
a note saying the words were not located.
WHY (Zach, 2026-11-24): "when I click that I want to basically be taken directly not just to the page, but see
highlighted the exact text that you got that information from."
