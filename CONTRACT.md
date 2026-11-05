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
                                   running: "capture"|"convert"|"extract"|null (the step a job is in now),
                                   bundle: "<abs path of the Markdownify bundle>", history: [{stage, at}]}
sources/<source id>/values.json   the extractor's output (CONTRACT §7), never rewritten after extraction
sources/<source id>/review.json   human ops (CONTRACT §7.1), append-only
published/<product id>/<source id>.json   the effective datasheet for one product (§5)
products.json                     products created here (generic provider), [{id, title, created_at}]
```
`source id` = first 12 hex of the source sha256 once captured (`pending-<n>` before).

## 3. Stages (the queue)

`added → captured → converted → extracted → published`, plus `failed` with an error. A source never skips a stage
silently; each step appends `{stage, at}` to `history`. Capture = `mdify capture --keep <capture_keep>` for URLs
(default `html`; the 5 levels of markdownify CONTRACT §2.1), the file's bytes for files. Convert = `mdify` pipeline A.
Extract = Option 1 with the logged-in CLI (default claude), the batch's category fields, every product of the batch.
`running` names the step a job is in (a re-extraction keeps the stage `extracted` while it runs) and is cleared when
the step ends or fails; `GET /api/batches` and `GET /api/sources/<id>` return it. A restart clears a `running` left by
the dead process and appends `failed` to the history for that step (a source it resumes then runs the step again).

## 4. HTTP API (JSON unless noted)

| method + path | does |
|---|---|
| `GET /api/fields` | categories available (`fields/*.json`) |
| `GET /api/products` | `[{id, title, provider}]` from the provider + `products.json` |
| `POST /api/products` `{title}` | creates a product here (id = slug); returns it |
| `GET /api/batches` | batches, newest first, each with its sources' `{id, name, kind, stage, error}` |
| `POST /api/batches` (multipart: `files[]`, `urls` newline text, `products` JSON list, `category`, `capture_keep`) | creates the batch, queues every source, returns it |
| `GET /api/sources/<id>` | `{source, values, review, effective}` — `effective` = values ⊕ review (§5) |
| `GET /api/sources/<id>/file/<name>` | a bundle file (`source.<ext>`, `index.json`, `document.md`, `snapshot.jpg`) |
| `POST /api/sources/<id>/review` `{op…}` | appends one §7.1 op; the server stamps `author` (`<home>/config.json`'s `author`, else `git config user.name`, else the OS user) and `at`; returns review.json |
| `POST /api/sources/<id>/extract` `{model?, category?}` | (re)runs extraction as a background job; `category` is saved to source.json first (a category with no `fields/<category>.json` runs open mode: every specification the source states, keyed `spec.<printed label>`) |
| `GET /api/sources/<id>/prompt` (text) | a prompt any AI agent can run: the command line for this machine, then the source path/URL, the fields, and where to write values.json |
| `POST /api/sources/<id>/publish` | writes `published/<product>/<source>.json` for every assigned product; stage → published |

## 5. Effective record and publishing

values.json with review.json applied in order (last op on a value wins): edit replaces a field and keeps `ai` = the
original; reject marks `rejected: true`; restore clears it; add appends a human row (`human: true`, its own grounding
or `null`); assign/unassign set `products`; map `{model, product|null}` assigns the values read for that model
(`part_models`, or a cite row/column naming it) to the product — per value and product the last op wins, and an
unmap takes back only what the map gave (the record's `models` lists the current mappings). A published datasheet is one product's view:
`{product, source: {sha256, url, retrieved_at}, values: [{id, key, value, unit, raw, grounding, provenance: {by:
"ai"|"<author>", at, ai?: <original>}}]}` — rejected values left out, every value still pointing at its source.

## 6. Review tab (the UI)

The splice recommended from the 2026-10-29 review-view round (AI ranking fragile: V5 93, V3 90; Zach did not pick, so
the recommendation is the default): **V5 Stream** as the base — a provenance column on every row (faint "AI · date" on
untouched rows, bold initials · time where you acted), keyboard j/k move, type to replace, x strike/restore, a add,
p products, and a version log docked at the bottom; **V4**'s "this datasheet covers N models → map to your parts"
step on top; **V3**'s per-product columns as a view toggle. Source on the left via the Source Highlighter; click a row
→ its source spotlights. Edit in place, no Fix button, no modal; untouched = accepted; struck stays visible.
Every pane boundary drags and is remembered; the log and the mapping step fold.
