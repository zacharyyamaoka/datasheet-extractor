"""The Sources app (CONTRACT.md): where datasheets enter — batches of files and URLs, the queue that captures,
converts and extracts each one, the human review layer over its values, and one published datasheet per product.

- `store.py`: the state on disk (§2) and the stage history (§3).
- `products.py`: the product providers (`robotparts:<repo>`, `file:`).
- `extraction.py`: Option 1 over a source's bundle for every product of its batch (also a command, for the prompt).
- `jobs.py`: the queue — a background pool of two, resume on restart, and the values.json watcher.
- `effective.py`: values.json ⊕ review.json, and a product's published datasheet (§5); pure functions.
- `prompt.py`: the text any AI agent can run for one source.
- `server.py`: `extract serve` — the HTTP API (§4) and the page in `web/`.
"""
