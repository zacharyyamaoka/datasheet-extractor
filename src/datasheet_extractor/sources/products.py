"""Product providers: where a batch's products come from (CONTRACT §1 `--products`, §4 `/api/products`).

- `robotparts:<repo>`: every `<repo>/parts/*/part.json` and `parts/*/part.py`, and every own part
  `<repo>/user/parts/**/<id>/<id>.py`, read-only (id, title, model). An id with a `user/parts` folder carries it
  (`folder`) and says so in `provider`, so Copy as Spec can name the file to paste into. This module is the only place
  that knows RobotParts exists.
- `file:<path>`: a products.json of `[{id, title, created_at}]`. The default is `<home>/products.json`.

Products created here (`POST /api/products`) are written to `<home>/products.json` only, whatever the provider.
WHY: the Sources app is generic, RobotParts one consumer of it; a product made while reviewing a datasheet must never
appear in RobotParts' tree uninvited.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import threading

from .store import now, read_json, write_json


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")


def part_py(path: pathlib.Path) -> dict | None:
    """id, brand, model and title of a RobotParts part.py: the literal arguments of its `RobotPart(...)` call.
    WHY an `ast` read and never an import: a part.py is code from a pull request (RobotParts' own build reads it the
    same way, build/robotparts2/partpy.py), and this provider needs four strings, not the part."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "RobotPart":
            record = {}
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                record["id"] = node.args[0].value
            for keyword in node.keywords:
                if keyword.arg in ("id", "brand", "model", "title") and isinstance(keyword.value, ast.Constant):
                    record[keyword.arg] = keyword.value.value
            return record
    return None


def part_type(folder: pathlib.Path) -> tuple[str, str] | None:
    """(module, class) of the robot_models type an own part's file resolves its sources with:
    `IntegratedServo.from_sources(...)` imported `from robot_models.actuators.servo import IntegratedServo` gives
    ("robot_models.actuators.servo", "IntegratedServo"). None when the file has no `<Type>.from_sources` (a part still
    on `specs={...}`). Read with `ast`, never imported, as `part_py`."""
    folder = pathlib.Path(folder)
    try:
        tree = ast.parse((folder / f"{folder.name}.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return None
    name = next((node.func.value.id for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == "from_sources"
                 and isinstance(node.func.value, ast.Name)), None)
    if name is None:
        return None
    module = next((node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
                   and any((alias.asname or alias.name) == name for alias in node.names)), None)
    return (module, name) if module else None


class RobotParts:
    # WHY "user/parts" in the provider's name: the picker shows it beside each product, and it is the folder the
    # pasted source file goes in, a path rather than a coined word
    USER = "robotparts user/parts"

    def __init__(self, repo: pathlib.Path) -> None:
        self.repo = pathlib.Path(repo)
        self.name = "robotparts"

    def user_parts(self) -> dict[str, dict]:
        """{id: {id, title, model, folder}} for every own part: a folder under `user/parts/` holding `<folder>.py`
        whose `RobotPart(...)` id is the folder's name (robotparts' own rule, user/__init__.py). Read-only.
        WHY (2026-11-24): Zach's own parts live there since 11-16, and a batch must be able to target them."""
        root = self.repo / "user" / "parts"
        if not root.is_dir():
            return {}
        found = {}
        for path in sorted(root.rglob("*.py")):
            if path.parent.name != path.stem or "sources" in path.relative_to(root).parts:
                continue
            record = part_py(path)
            if not record or record.get("id") != path.parent.name or record["id"] in found:
                continue
            title = record.get("title") or " ".join(filter(None, (record.get("brand"), record.get("model")))) \
                or record["id"]
            found[record["id"]] = {"id": record["id"], "title": title, "model": record.get("model"),
                                   "folder": str(path.parent)}
        return found

    def list(self) -> list[dict]:
        """Own parts first (the ones Zach builds with), then the catalogue; an id in both is listed once, as his."""
        user = self.user_parts()
        products = [dict(part, provider=self.USER) for part in user.values()]
        directory_root = self.repo / "parts"
        catalogue = sorted(path for path in directory_root.iterdir() if path.is_dir()) if directory_root.is_dir() else []
        for directory in catalogue:
            record = None
            if (directory / "part.py").is_file():
                record = part_py(directory / "part.py")
            elif (directory / "part.json").is_file():
                try:
                    record = json.loads((directory / "part.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    record = None
            if not record or not record.get("id") or record["id"] in user:
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
