"""`extract serve [--port 0] [--home DIR] [--products robotparts:<repo> | file:<path>]` (CONTRACT §1, §4).

A stdlib ThreadingHTTPServer on 127.0.0.1: the page in `web/` at `/`, the API at `/api/*`. It prints the URL and
opens no browser window. WHY: Zach's no-focus-steal rule — a tool never fronts a window (as `mdify view`).

    GET  /api/fields                       categories available (fields/*.json)
    GET  /api/products                     [{id, title, provider}]
    POST /api/products {title}             a product created here (id = slug), in <home>/products.json
    GET  /api/batches                      newest first, each with its sources' {id, name, kind, stage, running,
                                           error}
    POST /api/batches                      multipart: files[], urls, products (JSON list), category, capture_keep
    GET  /api/sources/<id>                 {source, values, review, effective}
    GET  /api/sources/<id>/file/<name>     one file of the source's bundle
    POST /api/sources/<id>/review {op…}    appends one op (author and at stamped here); returns review.json
    POST /api/sources/<id>/extract {model?, category?}   sets the source's category, (re)runs extraction
    GET  /api/sources/<id>/prompt          text/plain: the prompt for any AI agent
    POST /api/sources/<id>/publish         published/<product>/<source>.json for every assigned product
"""

from __future__ import annotations

import argparse
import email.parser
import email.policy
import functools
import getpass
import hashlib
import json
import mimetypes
import pathlib
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from . import effective as effective_module
from . import extraction, jobs, prompt as prompt_module
from .products import Catalog
from .store import Store, default_home, now, parse_keep, read_json

WEB = pathlib.Path(__file__).with_name("web")
BUNDLE_FILE = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_BODY = 512 * 1024 * 1024

mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/markdown", ".md")
mimetypes.add_type("image/webp", ".webp")


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _inside(root: pathlib.Path, relative: str) -> pathlib.Path | None:
    """`relative` under `root`, or None when it is not a file there (`..` and symlinks out of it included)."""
    root = root.resolve()
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


@functools.lru_cache(maxsize=1)
def git_user_name() -> str | None:
    """`git config user.name`, or None when git or the setting is missing."""
    try:
        run = subprocess.run(["git", "config", "--get", "user.name"], capture_output=True, text=True, check=False,
                             timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return (run.stdout.strip() or None) if run.returncode == 0 else None


def author_of(home: pathlib.Path) -> str:
    """Who a review op is stamped with: `author` in <home>/config.json, else `git config user.name`, else the OS
    user. WHY git's name first: the OS user is a login ('bam' shows as "BA"); the person's name gives the provenance
    column real initials ("Zach Yamaoka" → ZY)."""
    config = read_json(pathlib.Path(home) / "config.json", {}) or {}
    return config.get("author") or git_user_name() or getpass.getuser()


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, list[str]], list[tuple[str, bytes]]]:
    """(text fields, [(file name, bytes)]) of a multipart/form-data body."""
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
    if not message.is_multipart():
        raise ApiError(400, "POST /api/batches takes multipart/form-data")
    fields: dict[str, list[str]] = {}
    files = []
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if filename is not None or name in ("files", "files[]"):
            if filename or data:
                files.append((filename or "upload", data))
        elif name:
            fields.setdefault(name, []).append(data.decode(part.get_content_charset() or "utf-8"))
    return fields, files


class App:
    """What the handlers share: the store, the products and the queue."""

    def __init__(self, home: pathlib.Path, products_spec: str | None = None, queue_options: dict | None = None):
        self.store = Store(home)
        self.products_spec = products_spec
        self.catalog = Catalog(self.store.home, products_spec)
        self.queue = jobs.Queue(self.store, self.catalog, **(queue_options or {}))

    # -- reads ------------------------------------------------------------------------------------------------------

    def fields(self) -> list[dict]:
        out = []
        for category in extraction.categories():
            if extraction.is_open(category):
                out.append({"category": category, "mode": "open", "fields": 0, "keys": [],
                            "note": "open mode: every specification the source states, keyed spec.<printed label>"})
                continue
            rows = read_json(extraction.fields_path(category), []) or []
            out.append({"category": category, "mode": "fields", "fields": len(rows),
                        "keys": [row["key"] for row in rows]})
        return out

    def batches(self) -> list[dict]:
        out = []
        for batch in self.store.batches():
            sources = []
            for source_id in batch["sources"]:
                source = self.store.source(source_id) or {"id": source_id}
                sources.append({name: source.get(name) for name in ("id", "name", "kind", "stage", "running",
                                                                     "error")})
            out.append(dict(batch, sources=sources))
        return out

    def source(self, source_id: str) -> dict:
        source = self.store.source(source_id)
        if source is None:
            raise ApiError(404, f"no source {source_id!r}")
        return source

    def detail(self, source_id: str) -> dict:
        source = self.source(source_id)
        values, review = self.store.values(source["id"]), self.store.review(source["id"])
        record = effective_module.effective(values, review, self.store.extracted_at(source)) if values else None
        return {"source": source, "values": values, "review": review, "effective": record}

    def bundle_file(self, source_id: str, name: str) -> pathlib.Path:
        source = self.source(source_id)
        found = _inside(pathlib.Path(source["bundle"]), name) if BUNDLE_FILE.match(name) else None
        if found is None:
            raise ApiError(404, f"no file {name!r} in source {source['id']}'s bundle")
        return found

    def prompt(self, source_id: str) -> str:
        source = self.source(source_id)
        batch = self.store.batch(source["batch"]) or {"id": source["batch"]}
        products = [self.catalog.get(pid) or {"id": pid, "title": pid} for pid in batch.get("products") or []]
        return prompt_module.prompt(self.store.home, source, batch, products, self.products_spec)

    # -- writes -----------------------------------------------------------------------------------------------------

    def create_product(self, body: dict) -> tuple[int, dict]:
        try:
            product, created = self.catalog.create(str(body.get("title") or ""))
        except ValueError as error:
            raise ApiError(400, str(error))
        return (201 if created else 200), product

    def create_batch(self, fields: dict[str, list[str]], files: list[tuple[str, bytes]]) -> dict:
        def one(name: str, default: str = "") -> str:
            return (fields.get(name) or [default])[0].strip()

        category = one("category")
        if category not in extraction.categories():
            raise ApiError(400, f"category is one of {extraction.categories()}, not {category!r}")
        try:
            keep = parse_keep(one("capture_keep") or None)
        except ValueError as error:
            raise ApiError(400, str(error))
        try:
            products = json.loads(one("products") or "[]")
        except ValueError:
            raise ApiError(400, "products is a JSON list of product ids")
        if not isinstance(products, list) or not all(isinstance(p, str) for p in products):
            raise ApiError(400, "products is a JSON list of product ids")
        known = {product["id"] for product in self.catalog.list()}
        unknown = [p for p in products if p not in known]
        if unknown:
            raise ApiError(400, f"unknown products {unknown} (POST /api/products creates one)")
        urls = [line.strip() for text in fields.get("urls") or [] for line in text.splitlines() if line.strip()]
        bad = [url for url in urls if not re.match(r"^https?://", url)]
        if bad:
            raise ApiError(400, f"not http(s) URLs: {bad}")
        if not urls and not files:
            raise ApiError(400, "a batch needs at least one file or URL")
        items = [{"kind": "file", "name": name, "data": data} for name, data in files]
        items += [{"kind": "url", "url": url, "capture_keep": keep} for url in urls]
        names = [name for name, _ in files] + urls
        label = one("label") or (names[0] + (f" + {len(names) - 1} more" if len(names) > 1 else ""))
        batch = self.store.create_batch(label, category, list(dict.fromkeys(products)), items)
        for source_id in batch["sources"]:
            self.queue.submit(source_id)
        return next(b for b in self.batches() if b["id"] == batch["id"])

    def review(self, source_id: str, body: dict) -> dict:
        source = self.source(source_id)
        directory = self.store.source_dir(source["id"])
        if not (directory / "values.json").is_file():
            raise ApiError(409, f"source {source['id']} has no values.json yet (stage {source['stage']})")
        op = {name: value for name, value in body.items() if name not in ("author", "at")}
        with self.store.lock:
            values, review = self.store.values(source["id"]), self.store.review(source["id"])
            if op.get("op") == "add" and isinstance(op.get("row"), dict) and not op["row"].get("id"):
                added = sum(1 for o in (review or {}).get("ops") or [] if o.get("op") == "add")
                taken = effective_module.value_ids(values, review)
                number = added + 1
                while f"h{number}" in taken:
                    number += 1
                op["row"] = dict(op["row"], id=f"h{number}")
                op["row"].setdefault("grounding", None)
            problem = effective_module.check_op(op, values, review, {p["id"] for p in self.catalog.list()})
            if problem:
                raise ApiError(400, problem)
            if op["op"] == "edit" and "from" not in op:
                record = effective_module.effective(values, review)
                row = next(r for r in record["values"] + [r for r in record["part"].values() if r]
                           if r["id"] == op["value_id"])
                op["from"] = row.get(op["field"])
            op["author"], op["at"] = author_of(self.store.home), now()
            sha = hashlib.sha256((directory / "values.json").read_bytes()).hexdigest()
            return self.store.append_op(source["id"], op, sha)

    def extract(self, source_id: str, body: dict) -> dict:
        source = self.source(source_id)
        model = body.get("model") or "claude"
        if model not in ("claude", "codex"):
            raise ApiError(400, f"model is claude or codex, not {model!r}")
        category = body.get("category")
        if category is not None and category not in extraction.categories():
            raise ApiError(400, f"category is one of {extraction.categories()}, not {category!r}")
        if category is not None:
            source = self.store.update_source(source["id"], category=category)
        effective_category = extraction.category_of(source, self.store.batch(source["batch"]) or {})
        if not jobs.bundle_complete(pathlib.Path(source["bundle"])):
            with self.queue.lock:
                pending = source["id"] in self.queue.active
            if category is not None and pending:
                # WHY accept it before conversion: the UI's per-source picker can be set while the source is still
                # queued; the job reads source.json's category when it reaches extraction.
                return {"queued": None, "category": effective_category,
                        "note": f"category saved; source {source['id']} extracts after conversion"}
            raise ApiError(409, f"source {source['id']} has no complete bundle yet (stage {source['stage']})")
        if not self.queue.submit(source["id"], re_extract=True, backend=model):
            raise ApiError(409, f"source {source['id']} already has a job running")
        return {"queued": source["id"], "model": model, "category": effective_category}

    def publish(self, source_id: str) -> dict:
        source = self.source(source_id)
        values = self.store.values(source["id"])
        if values is None:
            raise ApiError(409, f"source {source['id']} has no values.json yet (stage {source['stage']})")
        record = effective_module.effective(values, self.store.review(source["id"]), self.store.extracted_at(source))
        assigned = effective_module.products_of(record)
        if not assigned:
            raise ApiError(400, "no value is assigned to a product (an assign op maps value ids to products, a map op "
                                "a printed model's values)")
        datasheets = {product: effective_module.published(product, record, values.get("source"))
                      for product in assigned}
        paths = self.store.write_published(source["id"], datasheets)
        source = self.store.advance(source["id"], "published")
        return {"source": source, "published": [str(path) for path in paths]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: "Server"

    def log_message(self, format, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # the page polls the queue; the terminal keeps the one line that matters (the URL)

    def _send(self, status: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, path: pathlib.Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type == "application/json":
            content_type += "; charset=utf-8"
        self._send(200, path.read_bytes(), content_type)

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, f"the body is over {MAX_BODY} bytes")
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> dict:
        raw = self._body()
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(400, "the body is not JSON")
        if not isinstance(body, dict):
            raise ApiError(400, "the body is a JSON object")
        return body

    def _dispatch(self, method: str) -> None:
        path = unquote(urlsplit(self.path).path)
        app = self.server.app
        try:
            if not path.startswith("/api/"):
                if method != "GET":
                    raise ApiError(405, "the page is read-only")
                found = _inside(WEB, "index.html" if path in ("/", "") else path.lstrip("/"))
                if found is None:
                    raise ApiError(404, "not found")
                return self._file(found)
            parts = path[len("/api/"):].strip("/").split("/")
            if parts == ["fields"] and method == "GET":
                return self._json(200, app.fields())
            if parts == ["products"] and method == "GET":
                return self._json(200, app.catalog.list())
            if parts == ["products"] and method == "POST":
                return self._json(*app.create_product(self._json_body()))
            if parts == ["batches"] and method == "GET":
                return self._json(200, app.batches())
            if parts == ["batches"] and method == "POST":
                fields, files = parse_multipart(self.headers.get("Content-Type") or "", self._body())
                return self._json(201, app.create_batch(fields, files))
            if len(parts) >= 2 and parts[0] == "sources":
                source_id, rest = parts[1], parts[2:]
                if rest == [] and method == "GET":
                    return self._json(200, app.detail(source_id))
                if len(rest) == 2 and rest[0] == "file" and method == "GET":
                    return self._file(app.bundle_file(source_id, rest[1]))
                if rest == ["review"] and method == "POST":
                    return self._json(200, app.review(source_id, self._json_body()))
                if rest == ["extract"] and method == "POST":
                    return self._json(202, app.extract(source_id, self._json_body()))
                if rest == ["prompt"] and method == "GET":
                    return self._send(200, app.prompt(source_id).encode("utf-8"))
                if rest == ["publish"] and method == "POST":
                    self._body()
                    return self._json(200, app.publish(source_id))
            raise ApiError(404, f"no {method} {path}")
        except ApiError as error:
            return self._json(error.status, {"error": str(error)})
        except Exception as error:  # noqa: BLE001 - a handler bug answers 500, the server keeps serving
            return self._json(500, {"error": f"{type(error).__name__}: {error}"})

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app: App, port: int = 0) -> None:
        self.app = app
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extract serve", description="The Sources app: add datasheets, watch the "
                                     "queue, review values, publish one datasheet per product. Serves on 127.0.0.1 "
                                     "and prints the URL; opens no browser window.")
    parser.add_argument("--port", type=int, default=0, help="port to serve on (default: any free one)")
    parser.add_argument("--home", type=pathlib.Path, default=default_home(),
                        help="where the state lives (default: $DATASHEET_EXTRACTOR_HOME or "
                        "~/.local/share/datasheet-extractor/)")
    parser.add_argument("--products", help="robotparts:<repo> | file:<path>/products.json (default: "
                        "<home>/products.json)")
    arguments = parser.parse_args(argv)
    try:
        app = App(arguments.home, arguments.products)
    except ValueError as error:
        parser.error(str(error))
    try:
        server = Server(app, arguments.port)
    except OSError as error:
        print(f"extract serve: port {arguments.port}: {error.strerror or error}", file=sys.stderr)
        return 1
    app.queue.start()
    print(app.store.home)
    print(f"  {server.url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.queue.stop(wait=False)
    return 0
