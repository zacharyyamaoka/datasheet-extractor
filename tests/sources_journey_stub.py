"""A stand-in for `extract serve` (CONTRACT §1–§5) so the Sources app's web/ can be built and measured before the real
server lands. It serves src/datasheet_extractor/sources/web/ at / and the §4 API over state kept in --home exactly as
§2 lays it out. Nothing is extracted: the pipeline is simulated, one stage per --step seconds, and only sources whose
bytes Markdownify has already bundled get that far.

    python tests/sources_journey_stub.py --home DIR [--port 0] [--step 1.0]

Canned from real data:
- the LRS-350 bundle (~/.cache/markdownify/bundles/ac3e7b8d…) and its values, /home/bam/clank-markdownify/fixtures/
  lrs-350-24.values.json, given ids by datasheet_extractor.values.assign_ids (CONTRACT §7);
- the CubeMars AK80-9 page bundle (04e9b6e6…), captured and converted but with no canned values, so it fails at
  extraction and says why;
- products: robotparts' part ids (/home/bam/robotparts/parts/) plus products.json.
Prints its URL on the first line, opens no window; stop it by its pid.
"""

from __future__ import annotations

import argparse
import copy
import datetime
import email.parser
import email.policy
import getpass
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from datasheet_extractor.values import _place, assign_ids  # noqa: E402

WEB = REPO / "src" / "datasheet_extractor" / "sources" / "web"
BUNDLES = pathlib.Path(os.environ.get("MARKDOWNIFY_CACHE") or pathlib.Path.home() / ".cache" / "markdownify") / "bundles"
LRS_SHA = "ac3e7b8d27dcd72277b6bdbb208586a2343a635edf1ff578fffd131a31713f6f"
CUBEMARS_SHA = "04e9b6e63a10d733533f0677b8daa6c8b4f490573a233e4eaae6259e1d5b11f2"
LRS_VALUES = pathlib.Path("/home/bam/clank-markdownify/fixtures/lrs-350-24.values.json")
ROBOTPARTS = pathlib.Path("/home/bam/robotparts/parts")
# The URLs whose captures are already bundled (the LRS PDF's own retrieval URL; the CubeMars page's canonical URL).
KNOWN_URLS = {
    "https://www.meanwell.com/Upload/PDF/LRS-350/LRS-350-SPEC.PDF": LRS_SHA,
    "https://www.cubemars.com/product/ak80-9-v3-0-robotic-actuator.html": CUBEMARS_SHA,
}
STAGES = ["added", "captured", "converted", "extracted", "published"]
CAPTURE_LEVELS = ("link", "html", "snapshot", "pdf", "archive")  # Markdownify CONTRACT §2.1, the order capture_keep keeps
OPS = {"edit", "reject", "restore", "add", "assign", "unassign"}
MEDIA_EXT = {"application/pdf": "pdf", "text/html": "html", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "product"


def read(path: pathlib.Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback


def write(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def canned_values() -> dict:
    """The LRS-350-24 values with ids, as the extractor writes them (values.document → assign_ids)."""
    doc = json.loads(LRS_VALUES.read_text(encoding="utf-8"))
    rows = [(row["key"], row, _place(row.get("cite"), None)) for row in doc["values"]]
    rows += [(f"part.{name}", entry, _place(entry.get("cite"), None)) for name, entry in doc["part"].items() if entry]
    assign_ids(rows)
    return doc


def effective(values: dict | None, review: dict) -> dict | None:
    """CONTRACT §5: values.json with review.json applied in order; the last op on a value wins."""
    if values is None:
        return None
    doc = copy.deepcopy(values)
    rows = {row["id"]: row for row in doc["values"]}
    for row in rows.values():
        row["products"] = []
    for op in review["ops"]:
        kind = op["op"]
        if kind == "add":
            row = dict(op["row"], human=True, products=[])
            doc["values"].append(row)
            rows[row["id"]] = row
            continue
        if kind in ("assign", "unassign"):
            targets = list(rows) if "*" in op["value_ids"] else op["value_ids"]
            for value_id in targets:
                row = rows.get(value_id)
                if row is None:
                    continue
                for product in op["products"]:
                    if kind == "assign" and product not in row["products"]:
                        row["products"].append(product)
                    if kind == "unassign" and product in row["products"]:
                        row["products"].remove(product)
            continue
        row = rows.get(op.get("value_id"))
        if row is None:
            continue
        if kind == "edit":
            if "ai" not in row and not row.get("human"):
                row["ai"] = {key: row[key] for key in ("value", "unit", "raw", "grounding") if key in row}
            row[op["field"]] = op["to"]
        elif kind == "reject":
            row["rejected"] = True
        elif kind == "restore":
            row.pop("rejected", None)
    return doc


class Home:
    def __init__(self, root: pathlib.Path, step: float):
        self.root = root
        self.step = step
        self.lock = threading.RLock()
        self.counter = 0
        for name in ("batches", "sources", "published"):
            (root / name).mkdir(parents=True, exist_ok=True)

    # ---- categories (the real server's GET /api/fields shape) -------------------------------------------------------
    def fields(self) -> list[dict]:
        out = []
        for path in sorted((REPO / "fields").glob("*.json")):
            rows = json.loads(path.read_text(encoding="utf-8"))
            out.append({"category": path.stem, "mode": "fields", "fields": len(rows), "keys": [row["key"] for row in rows]})
        out.append({"category": "psu", "mode": "open", "fields": 0, "keys": [],
                    "note": "open mode: every specification the source states, keyed spec.<printed label>"})
        return out

    # ---- products ------------------------------------------------------------------------------------------------
    def products(self) -> list[dict]:
        here = [dict(p, provider="here") for p in read(self.root / "products.json", [])]
        provided = []
        if ROBOTPARTS.is_dir():
            provided = [{"id": d.name, "title": d.name, "provider": "robotparts"} for d in sorted(ROBOTPARTS.iterdir()) if d.is_dir()]
        return here + provided

    def create_product(self, title: str) -> dict:
        with self.lock:
            known = read(self.root / "products.json", [])
            taken = {p["id"] for p in self.products()}
            base = slug(title)
            product_id, n = base, 1
            while product_id in taken:
                n += 1
                product_id = f"{base}-{n}"
            product = {"id": product_id, "title": title, "created_at": now()}
            write(self.root / "products.json", known + [product])
            return dict(product, provider="here")

    # ---- batches and sources -------------------------------------------------------------------------------------
    def source_dir(self, source_id: str) -> pathlib.Path:
        return self.root / "sources" / source_id

    def source(self, source_id: str) -> dict | None:
        return read(self.source_dir(source_id) / "source.json")

    def save_source(self, record: dict) -> None:
        write(self.source_dir(record["id"]) / "source.json", record)

    def batches(self) -> list[dict]:
        out = []
        for path in (self.root / "batches").glob("*/batch.json"):
            batch = read(path)
            batch["sources"] = [self.summary(source_id) for source_id in batch["sources"]]
            out.append(batch)
        return sorted(out, key=lambda batch: batch["created_at"] + batch["id"], reverse=True)

    def summary(self, source_id: str) -> dict:
        record = self.source(source_id) or {"id": source_id, "name": "?", "kind": "?", "stage": "failed", "error": "missing"}
        return {key: record.get(key) for key in ("id", "name", "kind", "stage", "error")}

    def create_batch(self, files: list[tuple[str, bytes]], urls: list[str], products: list[str], category: str,
                     capture_keep: str) -> dict:
        with self.lock:
            created = now()
            self.counter += 1
            batch_id = f"{created.replace('-', '').replace(':', '').rstrip('Z')}-{self.counter}"
            ids, jobs = [], []
            for name, data in files:
                record = self.new_source(batch_id, "file", name, None, capture_keep)
                (self.source_dir(record["id"]) / "upload.bin").write_bytes(data)
                ids.append(record["id"])
                jobs.append(record["id"])
            for url in urls:
                name = urllib.parse.urlparse(url).netloc + (urllib.parse.urlparse(url).path or "/")
                record = self.new_source(batch_id, "url", name, url, capture_keep)
                ids.append(record["id"])
                jobs.append(record["id"])
            label = ", ".join(self.source(i)["name"] for i in ids[:2]) + (f" +{len(ids) - 2}" if len(ids) > 2 else "")
            batch = {"id": batch_id, "created_at": created, "label": label, "category": category, "products": products,
                     "sources": ids}
            write(self.root / "batches" / batch_id / "batch.json", batch)
        for job in jobs:
            threading.Thread(target=self.pipeline, args=(batch_id, job), daemon=True).start()
        return dict(batch, sources=[self.summary(i) for i in ids])

    def new_source(self, batch_id, kind, name, url, capture_keep) -> dict:
        n = len(list((self.root / "sources").glob("pending-*"))) + 1
        while self.source_dir(f"pending-{n}").exists():
            n += 1
        record = {"id": f"pending-{n}", "batch": batch_id, "kind": kind, "name": name, "capture_keep": capture_keep,
                  "stage": "added", "history": [{"stage": "added", "at": now()}]}
        if url:
            record["url"] = url
        self.source_dir(record["id"]).mkdir(parents=True)
        self.save_source(record)
        return record

    def advance(self, source_id: str, stage: str, **fields) -> dict:
        with self.lock:
            record = self.source(source_id)
            record.update(fields)
            record["stage"] = stage
            record["history"].append({"stage": stage, "at": now()})
            self.save_source(record)
            return record

    def fail(self, source_id: str, error: str) -> None:
        self.advance(source_id, "failed", error=error)

    def rename(self, batch_id: str, old: str, new: str) -> str:
        """`source id` = the first 12 hex of the sha256 once captured (§2). A source seen before keeps its record."""
        with self.lock:
            batch_path = self.root / "batches" / batch_id / "batch.json"
            batch = read(batch_path)
            batch["sources"] = [new if i == old else i for i in batch["sources"]]
            write(batch_path, batch)
            if self.source_dir(new).exists():
                shutil.rmtree(self.source_dir(old))
                return new
            self.source_dir(old).rename(self.source_dir(new))
            record = self.source(new)
            record["id"] = new
            self.save_source(record)
            return new

    def pipeline(self, batch_id: str, source_id: str) -> None:
        time.sleep(self.step)
        record = self.source(source_id)
        if record["kind"] == "file":
            data = (self.source_dir(source_id) / "upload.bin").read_bytes()
            sha = hashlib.sha256(data).hexdigest()
        else:
            sha = KNOWN_URLS.get(record["url"])
            if sha is None:
                self.fail(source_id, "stub server: no capture canned for this URL (the stub only knows the LRS-350 "
                                     "PDF and the CubeMars AK80-9 page)")
                return
        if self.source_dir(sha[:12]).exists() and sha[:12] != source_id:
            self.rename(batch_id, source_id, sha[:12])
            return
        source_id = self.rename(batch_id, source_id, sha[:12])
        (self.source_dir(source_id) / "upload.bin").unlink(missing_ok=True)
        bundle = BUNDLES / sha
        self.advance(source_id, "captured", sha256=sha, bundle=str(bundle))
        time.sleep(self.step)
        if not (bundle / "index.json").is_file():
            self.fail(source_id, "stub server: Markdownify has no bundle for these bytes (only canned bundles convert)")
            return
        self.advance(source_id, "converted")
        time.sleep(self.step)
        self.extract(source_id)

    def extract(self, source_id: str) -> None:
        record = self.source(source_id)
        if record.get("sha256") != LRS_SHA:
            self.fail(source_id, "stub server: no canned values for this source (only the LRS-350 values are canned)")
            return
        values_path = self.source_dir(source_id) / "values.json"
        if not values_path.exists():  # §2: values.json is never rewritten after extraction
            write(values_path, canned_values())
        self.advance(source_id, "extracted", error=None)

    # ---- review ----------------------------------------------------------------------------------------------------
    def review(self, source_id: str) -> dict:
        values_path = self.source_dir(source_id) / "values.json"
        values_sha = hashlib.sha256(values_path.read_bytes()).hexdigest() if values_path.exists() else None
        return read(self.source_dir(source_id) / "review.json",
                    {"schema": "extractor/review@0", "values_sha256": values_sha, "ops": []})

    def full(self, source_id: str) -> dict | None:
        record = self.source(source_id)
        if record is None:
            return None
        values = read(self.source_dir(source_id) / "values.json")
        review = self.review(source_id)
        return {"source": record, "values": values, "review": review, "effective": effective(values, review)}

    def append_op(self, source_id: str, op: dict) -> dict:
        if op.get("op") not in OPS:
            raise ValueError(f"unknown op {op.get('op')!r}")
        with self.lock:
            review = self.review(source_id)
            stamped = {key: value for key, value in op.items() if key not in ("author", "at")}
            stamped.update(author=getpass.getuser(), at=now())
            review["ops"].append(stamped)
            write(self.source_dir(source_id) / "review.json", review)
            return review

    def prompt(self, source_id: str) -> str:
        record = self.source(source_id)
        batch = read(self.root / "batches" / record["batch"] / "batch.json")
        bundle = pathlib.Path(record.get("bundle") or "")
        media = (read(bundle / "index.json", {}) or {}).get("source", {}).get("media_type", "")
        source_file = bundle / f"source.{MEDIA_EXT.get(media, 'bin')}"
        out = self.source_dir(source_id)
        return "\n".join([
            "# Extract this datasheet's values (Datasheet Extractor, CONTRACT §7)",
            "",
            "Run on this machine:",
            f"  uv run --directory {REPO} extract {source_file} --fields {REPO}/fields/{batch['category']}.json "
            f"--option 1 --bundle {bundle} --out {out}",
            "",
            f"Source: {source_file}" + (f"  ({record['url']})" if record.get("url") else ""),
            f"Fields: {REPO}/fields/{batch['category']}.json",
            f"Products: {', '.join(batch['products']) or '(none yet)'}",
            f"Write values.json to: {out / 'values.json'}",
            "",
            "(stub server prompt — the real `extract serve` writes its own)",
        ]) + "\n"

    def publish(self, source_id: str) -> dict:
        state = self.full(source_id)
        record = state["source"]
        values = state["effective"]
        products = sorted({p for row in values["values"] for p in row.get("products", [])})
        written = []
        for product in products:
            rows = []
            for row in values["values"]:
                if row.get("rejected") or row.get("verdict") == "dropped" or product not in row.get("products", []):
                    continue
                last = next((op for op in reversed(state["review"]["ops"])
                             if op.get("value_id") == row["id"] or (op["op"] == "add" and op["row"]["id"] == row["id"])), None)
                provenance = {"by": "ai", "at": values["source"].get("retrieved_at")}
                if last:
                    provenance = {"by": last["author"], "at": last["at"]}
                    if "ai" in row:
                        provenance["ai"] = row["ai"]
                rows.append({key: row.get(key) for key in ("id", "key", "value", "unit", "raw", "grounding")} | {"provenance": provenance})
            doc = {"product": product, "source": {key: values["source"].get(key) for key in ("sha256", "url", "retrieved_at")},
                   "values": rows}
            path = self.root / "published" / product / f"{source_id}.json"
            write(path, doc)
            written.append(str(path))
        self.advance(source_id, "published")
        return {"published": written}


def parse_keep(text: str) -> str:
    """As the real store.parse_keep: Markdownify's levels in its order, html required (values cite its bytes)."""
    chosen = [name.strip() for name in text.split(",") if name.strip()]
    unknown = [name for name in chosen if name not in CAPTURE_LEVELS]
    if unknown or "html" not in chosen:
        raise ValueError(f"capture_keep {text!r}: levels are {', '.join(CAPTURE_LEVELS)}, and html is required")
    return ",".join(name for name in CAPTURE_LEVELS if name in chosen)


def multipart(handler) -> dict:
    """Parse a multipart/form-data body with the stdlib email parser (cgi is gone from 3.13)."""
    length = int(handler.headers.get("Content-Length") or 0)
    body = handler.rfile.read(length)
    head = f"Content-Type: {handler.headers['Content-Type']}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(head + body)
    out: dict[str, list] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        out.setdefault(name, []).append((filename, payload) if filename is not None else payload.decode("utf-8"))
    return out


def make_handler(home: Home):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet: the journey reads stdout for the URL only
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            if isinstance(body, bytes):
                data = body
            elif isinstance(body, str) and not content_type.startswith("application/json"):
                data = body.encode("utf-8")
            else:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def json_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/fields":
                return self.send(200, home.fields())
            if path == "/api/products":
                return self.send(200, home.products())
            if path == "/api/batches":
                return self.send(200, home.batches())
            match = re.fullmatch(r"/api/sources/([^/]+)(?:/(prompt|file/(.+)))?", path)
            if match:
                source_id, tail, name = match.groups()
                record = home.source(source_id)
                if record is None:
                    return self.send(404, {"error": f"no source {source_id}"})
                if tail == "prompt":
                    return self.send(200, home.prompt(source_id), "text/plain; charset=utf-8")
                if name:
                    if name not in {"index.json", "document.md", "snapshot.jpg"} and not re.fullmatch(r"source\.\w+", name):
                        return self.send(404, {"error": f"not a bundle file: {name}"})
                    file = pathlib.Path(record.get("bundle") or "/nonexistent") / name
                    if not file.is_file():
                        return self.send(404, {"error": f"{name} is not in the bundle"})
                    kinds = {".pdf": "application/pdf", ".html": "text/html; charset=utf-8", ".json": "application/json",
                             ".md": "text/markdown; charset=utf-8", ".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
                    return self.send(200, file.read_bytes(), kinds.get(file.suffix, "application/octet-stream"))
                return self.send(200, home.full(source_id))
            return self.static(path)

        def static(self, path):
            relative = "index.html" if path in ("", "/") else urllib.parse.unquote(path.lstrip("/"))
            file = (WEB / relative).resolve()
            if WEB.resolve() not in file.parents or not file.is_file():
                return self.send(404, {"error": f"not found: {path}"})
            kinds = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}
            return self.send(200, file.read_bytes(), kinds.get(file.suffix, "application/octet-stream"))

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            try:
                if path == "/api/products":
                    title = (self.json_body().get("title") or "").strip()
                    if not title:
                        return self.send(400, {"error": "a product needs a title"})
                    return self.send(200, home.create_product(title))
                if path == "/api/batches":
                    form = multipart(self)
                    files = [entry for entry in form.get("files[]", []) if isinstance(entry, tuple)]
                    urls = [line.strip() for line in "".join(form.get("urls", [""])).splitlines() if line.strip()]
                    if not files and not urls:
                        return self.send(400, {"error": "add a file or a URL"})
                    category = form.get("category", [""])[0]
                    if category not in {entry["category"] for entry in home.fields()}:
                        return self.send(400, {"error": f"unknown category {category!r}"})
                    return self.send(200, home.create_batch(files, urls, json.loads(form.get("products", ["[]"])[0]),
                                                            category, parse_keep(form.get("capture_keep", ["html"])[0])))
                match = re.fullmatch(r"/api/sources/([^/]+)/(review|extract|publish)", path)
                if match and home.source(match.group(1)):
                    source_id, action = match.groups()
                    if action == "review":
                        return self.send(200, home.append_op(source_id, self.json_body()))
                    if action == "extract":
                        category = self.json_body().get("category")
                        if category is not None:
                            if category not in {entry["category"] for entry in home.fields()}:
                                return self.send(400, {"error": f"unknown category {category!r}"})
                            with home.lock:
                                record = home.source(source_id)
                                record["category"] = category
                                home.save_source(record)
                        if not home.source(source_id).get("bundle"):
                            return self.send(200, {"queued": None, "category": category, "note": "category saved; extracts after conversion"})
                        home.advance(source_id, "converted")
                        threading.Thread(target=lambda: (time.sleep(home.step), home.extract(source_id)), daemon=True).start()
                        return self.send(200, {"started": True})
                    return self.send(200, home.publish(source_id))
                return self.send(404, {"error": f"not found: {path}"})
            except (ValueError, KeyError) as error:
                return self.send(400, {"error": str(error)})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--home", required=True, type=pathlib.Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--step", type=float, default=1.0, help="seconds per simulated pipeline stage")
    args = parser.parse_args()
    home = Home(args.home, args.step)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(home))
    print(f"http://127.0.0.1:{server.server_address[1]}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
