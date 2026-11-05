"""Product providers: where a batch's products come from (CONTRACT §1 `--products`, §4 `/api/products`).

- `robotparts:<repo>`: every `<repo>/parts/*/part.json`, read-only (id, title, model). This module is the only place
  that knows RobotParts exists.
- `file:<path>`: a products.json of `[{id, title, created_at}]`. The default is `<home>/products.json`.

Products created here (`POST /api/products`) are written to `<home>/products.json` only, whatever the provider.
WHY: the Sources app is generic, RobotParts one consumer of it; a product made while reviewing a datasheet must never
appear in RobotParts' tree uninvited.
"""

from __future__ import annotations

import json
import pathlib
import re
import threading

from .store import now, read_json, write_json


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")


class RobotParts:
    def __init__(self, repo: pathlib.Path) -> None:
        self.repo = pathlib.Path(repo)
        self.name = "robotparts"

    def list(self) -> list[dict]:
        products = []
        for path in sorted((self.repo / "parts").glob("*/part.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not record.get("id"):
                continue
            title = record.get("title") or " ".join(filter(None, (record.get("brand"), record.get("model")))) \
                or record["id"]
            products.append({"id": record["id"], "title": title, "model": record.get("model"),
                             "provider": self.name})
        return products


class FileProducts:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = pathlib.Path(path)
        self.name = "file"

    def list(self) -> list[dict]:
        return [{"id": row["id"], "title": row.get("title") or row["id"], "provider": self.name}
                for row in read_json(self.path, []) or [] if row.get("id")]


def provider(spec: str | None, home: pathlib.Path):
    """`robotparts:<repo>` | `file:<path>` | None (= `file:<home>/products.json`)."""
    if not spec:
        return FileProducts(pathlib.Path(home) / "products.json")
    kind, _, where = spec.partition(":")
    if kind == "robotparts" and where:
        return RobotParts(pathlib.Path(where).expanduser())
    if kind == "file":
        return FileProducts(pathlib.Path(where).expanduser() if where else pathlib.Path(home) / "products.json")
    raise ValueError(f"--products is robotparts:<repo> or file:<path>, not {spec!r}")


class Catalog:
    """The provider's products plus the ones created here."""

    def __init__(self, home: pathlib.Path, spec: str | None = None) -> None:
        self.home = pathlib.Path(home)
        self.local = FileProducts(self.home / "products.json")
        self.provider = provider(spec, self.home)
        self.lock = threading.Lock()

    def list(self) -> list[dict]:
        out, seen = [], set()
        sources = [self.provider]
        if not (isinstance(self.provider, FileProducts) and self.provider.path.resolve() == self.local.path.resolve()):
            sources.append(self.local)
        for source in sources:
            for product in source.list():
                if product["id"] not in seen:
                    seen.add(product["id"])
                    out.append(product)
        return out

    def get(self, product_id: str) -> dict | None:
        return next((product for product in self.list() if product["id"] == product_id), None)

    def create(self, title: str) -> tuple[dict, bool]:
        """(the product, created?) — an existing id is returned as it is."""
        product_id = slug(title)
        if not product_id:
            raise ValueError("a product needs a title with at least one letter or digit")
        with self.lock:
            existing = self.get(product_id)
            if existing is not None:
                return existing, False
            rows = read_json(self.local.path, []) or []
            row = {"id": product_id, "title": title.strip(), "created_at": now()}
            write_json(self.local.path, rows + [row])
            return {"id": product_id, "title": row["title"], "provider": self.local.name}, True


def model_candidates(product: dict) -> list[str]:
    """The strings that may name this product in a table's model header: its model, its title, and each word of
    the title that carries a digit ('Mean Well LRS-350-24' → 'LRS-350-24')."""
    out = []
    for text in (product.get("model"), product.get("title")):
        if text and text.strip() and text.strip() not in out:
            out.append(text.strip())
    for word in (product.get("title") or "").split():
        if re.search(r"\d", word) and len(word) >= 3 and word not in out:
            out.append(word)
    return out
