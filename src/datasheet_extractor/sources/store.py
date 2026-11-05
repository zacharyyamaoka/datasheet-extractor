"""The Sources app's state on disk (CONTRACT §2) and the stage history every source keeps (§3).

    <home>/batches/<batch id>/batch.json
    <home>/sources/<source id>/source.json, values.json, review.json   (+ upload/, bundle/, llm/ of the queue's own)
    <home>/published/<product id>/<source id>.json
    <home>/products.json

Every write is a temp file renamed over the target, so a reader (or a restart) never sees half a file. A source is
`pending-<n>` until its bytes are known, then `<first 12 hex of its sha256>`: the directory is renamed and the batch's
list updated under one lock; the old name stays in `pending_id` so a client still holding it finds the source.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone

STAGES = ("added", "captured", "converted", "extracted", "published")
FAILED = "failed"
CAPTURE_LEVELS = ("link", "html", "snapshot", "pdf", "archive")   # Markdownify CONTRACT §2.1
DEFAULT_KEEP = "html"
SOURCE_ID = re.compile(r"^(?:[0-9a-f]{12}|pending-\d+)$")
BATCH_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


def default_home() -> pathlib.Path:
    env = os.environ.get("DATASHEET_EXTRACTOR_HOME")
    return pathlib.Path(env) if env else pathlib.Path.home() / ".local" / "share" / "datasheet-extractor"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: pathlib.Path, data) -> None:
    """Atomically: a temp file beside the target, then a rename over it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def read_json(path: pathlib.Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def parse_keep(text: str | None) -> str:
    """`capture_keep` as Markdownify's levels, in its order. `html` is required: a value cites the HTML bytes."""
    chosen = [name.strip() for name in (text or DEFAULT_KEEP).split(",") if name.strip()]
    unknown = [name for name in chosen if name not in CAPTURE_LEVELS]
    if unknown:
        raise ValueError(f"unknown capture level {', '.join(unknown)} (levels: {', '.join(CAPTURE_LEVELS)})")
    if "html" not in chosen:
        raise ValueError("capture_keep must include html: values are read from, and cite, the page's HTML bytes")
    return ",".join(name for name in CAPTURE_LEVELS if name in chosen)


def safe_name(name: str) -> str:
    """An uploaded file's name as a single path component."""
    name = pathlib.PurePosixPath(name.replace("\\", "/")).name.strip()
    name = re.sub(r"[^\w.\- ()+]", "_", name)
    return name.lstrip(".") or "upload"


class Store:
    def __init__(self, home: pathlib.Path) -> None:
        self.home = pathlib.Path(home).expanduser().resolve()
        for name in ("batches", "sources", "published"):
            (self.home / name).mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._pending = self._max_pending()

    # -- paths ------------------------------------------------------------------------------------------------------

    def source_dir(self, source_id: str) -> pathlib.Path:
        return self.home / "sources" / source_id

    def batch_dir(self, batch_id: str) -> pathlib.Path:
        return self.home / "batches" / batch_id

    def _max_pending(self) -> int:
        top = 0
        for path in (self.home / "sources").glob("*/source.json"):
            record = read_json(path, {}) or {}
            for name in (record.get("id"), record.get("pending_id")):
                if name and name.startswith("pending-") and name[len("pending-"):].isdigit():
                    top = max(top, int(name[len("pending-"):]))
        return top

    # -- reads ------------------------------------------------------------------------------------------------------

    def source(self, source_id: str) -> dict | None:
        """source.json by id, or by the `pending-<n>` it had before its bytes were known."""
        if not SOURCE_ID.match(source_id or ""):
            return None
        record = read_json(self.source_dir(source_id) / "source.json")
        if record is not None:
            return record
        if source_id.startswith("pending-"):
            for path in (self.home / "sources").glob("*/source.json"):
                record = read_json(path, {}) or {}
                if record.get("pending_id") == source_id:
                    return record
        return None

    def sources(self) -> list[dict]:
        return [record for path in sorted((self.home / "sources").glob("*/source.json"))
                if (record := read_json(path))]

    def batch(self, batch_id: str) -> dict | None:
        if not BATCH_ID.match(batch_id or ""):
            return None
        return read_json(self.batch_dir(batch_id) / "batch.json")

    def batches(self) -> list[dict]:
        found = [record for path in (self.home / "batches").glob("*/batch.json") if (record := read_json(path))]
        return sorted(found, key=lambda record: (record["created_at"], record["id"]), reverse=True)

    def values(self, source_id: str) -> dict | None:
        return read_json(self.source_dir(source_id) / "values.json")

    def review(self, source_id: str) -> dict | None:
        return read_json(self.source_dir(source_id) / "review.json")

    def extracted_at(self, source: dict) -> str | None:
        """When the current values.json was written: the last `extracted` in the history."""
        return next((entry["at"] for entry in reversed(source.get("history") or []) if entry["stage"] == "extracted"),
                    None)

    # -- writes -----------------------------------------------------------------------------------------------------

    def create_batch(self, label: str, category: str, products: list[str], items: list[dict]) -> dict:
        """`items`: [{kind: "file", name, data} | {kind: "url", url, capture_keep}]. Every source starts `added`."""
        with self.lock:
            batch_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
            while self.batch_dir(batch_id).exists():
                batch_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
            created = now()
            source_ids = []
            for item in items:
                self._pending += 1
                source_id = f"pending-{self._pending}"
                directory = self.source_dir(source_id)
                directory.mkdir(parents=True)
                record = {"id": source_id, "batch": batch_id, "kind": item["kind"]}
                if item["kind"] == "file":
                    name = safe_name(item["name"])
                    (directory / "upload").mkdir()
                    (directory / "upload" / name).write_bytes(item["data"])
                    record.update({"name": name, "capture_keep": None})
                else:
                    record.update({"name": item["url"], "url": item["url"], "capture_keep": item["capture_keep"]})
                record.update({"stage": "added", "bundle": str(directory / "bundle"),
                               "history": [{"stage": "added", "at": created}]})
                write_json(directory / "source.json", record)
                source_ids.append(source_id)
            batch = {"id": batch_id, "created_at": created, "label": label, "category": category,
                     "products": list(products), "sources": source_ids}
            write_json(self.batch_dir(batch_id) / "batch.json", batch)
            return batch

    def update_source(self, source_id: str, **fields) -> dict:
        with self.lock:
            path = self.source_dir(source_id) / "source.json"
            record = read_json(path)
            if record is None:
                raise KeyError(source_id)
            for name, value in fields.items():
                if value is None:
                    record.pop(name, None)
                else:
                    record[name] = value
            write_json(path, record)
            return record

    def advance(self, source_id: str, stage: str, error: str | None = None, **fields) -> dict:
        """Move a source to `stage`, appending `{stage, at}` to its history (and `error` when it failed)."""
        if stage not in STAGES and stage != FAILED:
            raise ValueError(stage)
        with self.lock:
            record = self.source(source_id)
            if record is None:
                raise KeyError(source_id)
            history = list(record.get("history") or []) + [{"stage": stage, "at": now()}]
            return self.update_source(record["id"], stage=stage, history=history,
                                      error=error[-2000:] if error and stage == FAILED else None, **fields)

    def identify(self, source_id: str, sha256: str) -> str:
        """The bytes are known: rename `pending-<n>` to the sha256's first 12 hex and fix the batch's list. Raises
        FileExistsError when another source already holds these bytes."""
        new_id = sha256[:12]
        with self.lock:
            if new_id == source_id:
                return new_id
            target = self.source_dir(new_id)
            if target.exists():
                other = read_json(target / "source.json", {}) or {}
                raise FileExistsError(f"the same bytes are already source {new_id} (batch {other.get('batch')})")
            old = self.source_dir(source_id)
            os.rename(old, target)
            record = read_json(target / "source.json")
            record.update({"id": new_id, "pending_id": source_id, "sha256": sha256, "bundle": str(target / "bundle")})
            write_json(target / "source.json", record)
            batch = self.batch(record["batch"])
            if batch is not None:
                batch["sources"] = [new_id if sid == source_id else sid for sid in batch["sources"]]
                write_json(self.batch_dir(batch["id"]) / "batch.json", batch)
            return new_id

    def append_op(self, source_id: str, op: dict, values_sha256: str) -> dict:
        """Append one op to review.json (created on the first op), atomically."""
        with self.lock:
            path = self.source_dir(source_id) / "review.json"
            review = read_json(path) or {"schema": "extractor/review@0", "values_sha256": values_sha256, "ops": []}
            review["ops"].append(op)
            write_json(path, review)
            return review

    def archive_values(self, source_id: str, sha256: str) -> pathlib.Path | None:
        """Before a re-extraction: values.json moves aside intact as values-<sha12>.json (it is never rewritten)."""
        path = self.source_dir(source_id) / "values.json"
        if not path.is_file():
            return None
        target = path.with_name(f"values-{sha256[:12]}.json")
        os.replace(path, target)
        return target

    def write_published(self, source_id: str, datasheets: dict[str, dict]) -> list[pathlib.Path]:
        """published/<product>/<source>.json for each product; a product no longer assigned loses its file."""
        with self.lock:
            written = []
            for product_id, datasheet in datasheets.items():
                path = self.home / "published" / product_id / f"{source_id}.json"
                write_json(path, datasheet)
                written.append(path)
            for stale in (self.home / "published").glob(f"*/{source_id}.json"):
                if stale.parent.name not in datasheets:
                    stale.unlink()
            return written

    def remove_tree(self, path: pathlib.Path) -> None:
        if path.exists():
            shutil.rmtree(path)
