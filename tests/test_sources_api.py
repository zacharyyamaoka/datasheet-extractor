"""The Sources app's HTTP API end to end (CONTRACT §1–§5) on a temp `--home`: a tiny HTML page, a small PDF and one
URL go through the queue (real Markdownify conversion, a stand-in capture for the URL, a mocked model call), then
review, prompt, publish, the watcher, re-extraction and a restart. No real model calls."""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

import pytest

from datasheet_extractor import llm
from datasheet_extractor.sources import jobs, server as server_module
from datasheet_extractor.sources.store import Store, read_json, write_json

MARKDOWNIFY = pathlib.Path("/home/bam/markdownify")
needs_markdownify = pytest.mark.skipif(not MARKDOWNIFY.is_dir() or shutil.which("uv") is None,
                                       reason="Markdownify (and uv) are not on this machine")

HTML = """<!doctype html><html><head><title>Acme AX series</title></head><body><h1>Acme AX series actuator</h1>
<table><tr><th>Model</th><th>AX-10</th><th>AX-20</th></tr>
<tr><td>Rated torque (Nm)</td><td>12</td><td>20</td></tr>
<tr><td>Peak torque (Nm)</td><td>30</td><td>55</td></tr></table>
<p>Weight 480 g</p></body></html>
"""


def tiny_pdf(lines: list[str]) -> bytes:
    """A one-page born-digital PDF with one Helvetica line per entry."""
    content = "BT /F1 12 Tf 72 720 Td " + " ".join(f"({line}) Tj 0 -18 Td" for line in lines) + " ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               "/Resources << /Font << /F1 5 0 R >> >> >>",
               f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


PDF = tiny_pdf(["Acme AX-10 actuator", "Rated torque 12 Nm", "Peak torque 30 Nm", "Weight 480 g"])


# -- the model, mocked ------------------------------------------------------------------------------------------------

def _cite(prompt: str, text: str) -> str:
    """The id of the block or cell whose ⟦id⟧ tag comes last before `text` in the tagged prompt."""
    at = prompt.index(text)
    return re.findall(r"⟦([^⟧]+)⟧", prompt[:at])[-1]


class FakeModel:
    """Stands in for llm.extract: reads the tagged markdown and proposes values like a model would."""

    def __init__(self) -> None:
        self.calls: list[str | None] = []
        self.fail_on: set[str] = set()

    def __call__(self, backend, system_prompt, prompt, schema, run_dir, model=None, images=None):
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        part_model = re.search(r"^- model: (.*)$", prompt, re.M).group(1)
        self.calls.append(part_model)
        if any(marker in prompt for marker in self.fail_on):
            return llm.LlmResult(backend, None, None, "the fake model was told to fail", run_dir, "fake 1", 0.01)
        rows = []
        if "⟧ AX-20 |" in prompt:                                    # the comparison table
            column = 2 if part_model == "AX-20" else 1
            for key, label in (("rated_torque", "Rated torque (Nm)"), ("peak_torque", "Peak torque (Nm)")):
                line = next(line for line in prompt.splitlines() if label in line)
                cells = re.findall(r"⟦([^⟧]+)⟧ ([^|]*)", line)
                cell_id, raw = cells[column]
                rows.append({"key": key, "value": raw.strip(), "unit": "N·m", "raw": raw.strip(), "condition": "",
                             "id": cell_id})
        else:
            rows.append({"key": "rated_torque", "value": "12", "unit": "N·m", "raw": "Rated torque 12 Nm",
                         "condition": "", "id": _cite(prompt, "Rated torque 12")})
        weight = _cite(prompt, "Weight 480 g")
        rows.append({"key": "mass", "value": "480", "unit": "g", "raw": "Weight 480 g", "condition": "", "id": weight})
        rows.append({"key": "kv", "value": "999", "unit": "rpm/V", "raw": "999", "condition": "", "id": weight})
        part = {"manufacturer": {"value": "Acme", "raw": "Acme", "id": _cite(prompt, "Acme")},
                "model": {"value": "", "raw": "", "id": ""}}
        return llm.LlmResult(backend, "fake-model", {"part": part, "values": rows}, None, run_dir, "fake 1", 0.01)


# -- the server -------------------------------------------------------------------------------------------------------

class Client:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/")

    def request(self, method: str, path: str, body: bytes | None = None, content_type: str | None = None):
        request = urllib.request.Request(self.url + path, data=body, method=method)
        if content_type:
            request.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.headers.get("Content-Type"), response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Content-Type"), error.read()

    def get(self, path: str):
        status, kind, body = self.request("GET", path)
        return status, (json.loads(body) if kind and kind.startswith("application/json") else body.decode())

    def post(self, path: str, data: dict | None = None):
        status, _, body = self.request("POST", path, json.dumps(data or {}).encode(), "application/json")
        return status, json.loads(body)

    def post_batch(self, files=(), urls=(), products=(), category="actuator", capture_keep="html"):
        boundary = uuid.uuid4().hex
        parts = []
        for name, data in files:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files[]"; filename="{name}"\r\n'
                         f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
        for name, value in (("urls", "\n".join(urls)), ("products", json.dumps(list(products))),
                            ("category", category), ("capture_keep", capture_keep)):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        status, _, raw = self.request("POST", "/api/batches", body, f"multipart/form-data; boundary={boundary}")
        return status, json.loads(raw)

    def wait(self, batch_id: str, stages=("extracted", "failed"), timeout: float = 180) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            _, batches = self.get("/api/batches")
            batch = next(b for b in batches if b["id"] == batch_id)
            if all(source["stage"] in stages for source in batch["sources"]):
                return batch
            time.sleep(0.2)
        raise AssertionError(f"batch {batch_id} did not finish: {batch}")


def fake_capture(html_by_url: dict[str, str]):
    """Stands in for `mdify capture`: converts the page's HTML with the real `mdify <file>` and records the URL."""
    def capture(url: str, keep: str, out: pathlib.Path) -> None:
        page = out.parent / "captured.html"
        page.write_text(html_by_url[url], encoding="utf-8")
        jobs.mdify_convert(page, out)
        record = read_json(out / "source.json")
        record["retrievals"].append({"url": url, "final_url": url, "retrieved_at": "2026-11-05T09:00:00Z",
                                     "http_status": 200, "capture": {"kept": keep.split(",")}})
        write_json(out / "source.json", record)
    return capture


def start(home: pathlib.Path, capture=None):
    app = server_module.App(home, None, {"capture": capture or jobs.mdify_capture, "poll_s": 0.2})
    server = server_module.Server(app, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app.queue.start()
    return app, server, Client(server.url)


def stop(app, server) -> None:
    server.shutdown()
    server.server_close()
    app.queue.stop()


@pytest.fixture
def fake_model(monkeypatch):
    model = FakeModel()
    monkeypatch.setattr(llm, "extract", model)
    return model


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKDOWNIFY_CACHE", str(tmp_path / "mdify-cache"))


# -- the tests --------------------------------------------------------------------------------------------------------

def test_fields_and_products(tmp_path):
    app, server, client = start(tmp_path / "home")
    try:
        status, fields = client.get("/api/fields")
        assert status == 200 and "actuator" in [row["category"] for row in fields]
        assert client.get("/api/products") == (200, [])
        status, product = client.post("/api/products", {"title": "Acme AX-10"})
        assert status == 201 and product == {"id": "acme-ax-10", "title": "Acme AX-10", "provider": "file"}
        assert client.post("/api/products", {"title": "Acme  AX-10"})[0] == 200          # the same slug
        assert client.post("/api/products", {"title": "  "})[0] == 400
        assert client.get("/api/products")[1] == [product]
        stored = read_json(tmp_path / "home" / "products.json")
        assert [row["id"] for row in stored] == ["acme-ax-10"] and stored[0]["created_at"].endswith("Z")
    finally:
        stop(app, server)


def test_bad_batches_are_refused(tmp_path):
    app, server, client = start(tmp_path / "home")
    try:
        assert client.post_batch(urls=["https://example.com"], category="nope")[0] == 400
        assert client.post_batch(urls=["https://example.com"], capture_keep="link,snapshot")[0] == 400
        assert client.post_batch(urls=["https://example.com"], products=["unknown"])[0] == 400
        assert client.post_batch(urls=["ftp://example.com"])[0] == 400
        assert client.post_batch()[0] == 400
        assert client.get("/api/batches") == (200, [])
    finally:
        stop(app, server)


@needs_markdownify
def test_the_queue_review_prompt_and_publish(tmp_path, fake_model, isolated_cache):
    home = tmp_path / "home"
    url = "https://example.com/ax-series"
    page = HTML.replace("<h1>", "<h1 id='captured'>")
    app, server, client = start(home, fake_capture({url: page}))
    try:
        client.post("/api/products", {"title": "Acme AX-10"})
        client.post("/api/products", {"title": "Acme AX-20"})
        status, batch = client.post_batch(files=[("ax.pdf", PDF), ("ax.html", HTML.encode())], urls=[url],
                                          products=["acme-ax-10", "acme-ax-20"], capture_keep="html,snapshot")
        assert status == 201, batch
        assert all(re.fullmatch(r"pending-\d+|[0-9a-f]{12}", s["id"]) for s in batch["sources"])
        assert {s["kind"] for s in batch["sources"]} == {"file", "url"}
        done = client.wait(batch["id"])
        assert [s["stage"] for s in done["sources"]] == ["extracted"] * 3, done
        on_disk = read_json(home / "batches" / batch["id"] / "batch.json")
        assert on_disk["sources"] == [s["id"] for s in done["sources"]]
        assert all(re.fullmatch(r"[0-9a-f]{12}", s["id"]) for s in done["sources"])
        pdf_id, html_id, url_id = (s["id"] for s in done["sources"])

        # §2/§3: source.json, its history, and the old pending id still answers
        status, detail = client.get(f"/api/sources/{html_id}")
        source = detail["source"]
        assert [h["stage"] for h in source["history"]] == ["added", "captured", "converted", "extracted"]
        assert source["sha256"].startswith(html_id) and source["bundle"] == str(home / "sources" / html_id / "bundle")
        assert client.get(f"/api/sources/{source['pending_id']}")[1]["source"]["id"] == html_id
        url_source = client.get(f"/api/sources/{url_id}")[1]
        assert url_source["source"]["capture_keep"] == "html,snapshot" and url_source["values"]["source"]["url"] == url

        # extraction: the HTML table has a column per model, so one call per product; the PDF names neither
        values = detail["values"]
        assert values["extractor"]["part_models"] == [{"model": "AX-10", "products": ["acme-ax-10"]},
                                                      {"model": "AX-20", "products": ["acme-ax-20"]}]
        kept = {(row["key"], row["value"]): row.get("part_models") for row in values["values"]
                if row["verdict"] == "kept"}
        assert kept[("rated_torque", 12)] == ["AX-10"] and kept[("rated_torque", 20)] == ["AX-20"]
        assert kept[("mass", 480)] == ["AX-10", "AX-20"]                  # one row, both runs agree
        assert fake_model.calls.count("the one this datasheet describes") == 1 and fake_model.calls.count("AX-10") == 2
        pdf_values = client.get(f"/api/sources/{pdf_id}")[1]["values"]
        assert pdf_values["extractor"]["part_models"] == []
        assert {row["key"]: row["verdict"] for row in pdf_values["values"]} == \
            {"rated_torque": "kept", "mass": "kept", "kv": "dropped"}

        # §4: bundle files
        status, markdown = client.get(f"/api/sources/{pdf_id}/file/document.md")
        assert status == 200 and "Rated torque 12" in markdown
        assert client.request("GET", f"/api/sources/{pdf_id}/file/source.pdf")[2][:5] == b"%PDF-"
        assert client.get(f"/api/sources/{pdf_id}/file/..%2Fsource.json")[0] == 404
        assert client.get("/api/sources/nope")[0] == 404

        # §4/§7.1: review ops, stamped by the server
        rated = next(r["id"] for r in pdf_values["values"] if r["key"] == "rated_torque")
        mass = next(r["id"] for r in pdf_values["values"] if r["key"] == "mass")
        status, error = client.post(f"/api/sources/{pdf_id}/review", {"op": "reject", "value_id": "nope@x"})
        assert status == 400 and "nope@x" in error["error"]
        assert not (home / "sources" / pdf_id / "review.json").exists()
        status, review = client.post(f"/api/sources/{pdf_id}/review",
                                     {"op": "edit", "value_id": rated, "field": "value", "to": 12.5,
                                      "author": "mallory", "at": "1999-01-01T00:00:00Z"})
        assert status == 200
        op = review["ops"][0]
        assert op["author"] == server_module.author_of(home) != "mallory" and op["at"] != "1999-01-01T00:00:00Z"
        assert op["from"] == 12 and review["schema"] == "extractor/review@0"
        client.post(f"/api/sources/{pdf_id}/review", {"op": "reject", "value_id": mass})
        status, review = client.post(f"/api/sources/{pdf_id}/review",
                                     {"op": "add", "row": {"key": "kv", "value": 100, "unit": "rpm/V"}})
        assert review["ops"][-1]["row"] == {"key": "kv", "value": 100, "unit": "rpm/V", "id": "h1", "grounding": None}
        assert client.post(f"/api/sources/{pdf_id}/review", {"op": "assign", "products": ["ghost"],
                                                             "value_ids": ["*"]})[0] == 400
        assert client.post(f"/api/sources/{pdf_id}/publish")[0] == 400               # nothing assigned yet
        client.post(f"/api/sources/{pdf_id}/review", {"op": "assign", "products": ["acme-ax-10"],
                                                      "value_ids": ["*"]})
        assert len(read_json(home / "sources" / pdf_id / "review.json")["ops"]) == 4
        effective = client.get(f"/api/sources/{pdf_id}")[1]["effective"]
        assert next(r for r in effective["values"] if r["id"] == rated)["ai"] == {"value": 12}

        # §4: the prompt
        status, text = client.get(f"/api/sources/{pdf_id}/prompt")
        assert status == 200
        assert "python -m datasheet_extractor.sources.extraction" in text and str(home) in text
        assert str(home / "sources" / pdf_id / "values.json") in text
        assert str(extraction_fields()) in text and str(home / "sources" / pdf_id / "bundle") in text

        # §5: publish
        status, result = client.post(f"/api/sources/{pdf_id}/publish")
        assert status == 200 and result["source"]["stage"] == "published"
        sheet = read_json(home / "published" / "acme-ax-10" / f"{pdf_id}.json")
        assert sheet["product"] == "acme-ax-10" and sheet["source"]["sha256"] == result["source"]["sha256"]
        assert [v["key"] for v in sheet["values"]] == ["rated_torque", "kv"]      # mass rejected, kv 999 dropped
        assert sheet["values"][0]["value"] == 12.5 and sheet["values"][0]["provenance"]["ai"] == {"value": 12}
        assert sheet["values"][0]["grounding"] and sheet["values"][1]["provenance"]["by"] == op["author"]

        # re-extraction keeps the old values.json aside, intact
        old = (home / "sources" / pdf_id / "values.json").read_bytes()
        status, queued = client.post(f"/api/sources/{pdf_id}/extract", {})
        assert status == 202 and queued == {"queued": pdf_id, "model": "claude"}
        client.wait(batch["id"])
        assert [path.read_bytes() for path in (home / "sources" / pdf_id).glob("values-*.json")] == [old]
        assert client.get(f"/api/sources/{pdf_id}")[1]["source"]["history"][-1]["stage"] == "extracted"
        assert client.post(f"/api/sources/{pdf_id}/extract", {"model": "gpt"})[0] == 400

        # the same bytes again: failed, honestly
        status, again = client.post_batch(files=[("copy.pdf", PDF)])
        failed = client.wait(again["id"])["sources"][0]
        assert failed["stage"] == "failed" and pdf_id in failed["error"]
    finally:
        stop(app, server)


def extraction_fields() -> pathlib.Path:
    from datasheet_extractor.sources import extraction
    return extraction.fields_path("actuator")


@needs_markdownify
def test_the_watcher_picks_up_a_values_json_written_from_outside(tmp_path, fake_model, isolated_cache):
    home = tmp_path / "home"
    fake_model.fail_on.add("Weight 480 g")
    app, server, client = start(home)
    try:
        _, batch = client.post_batch(files=[("ax.html", HTML.encode())])
        source = client.wait(batch["id"])["sources"][0]
        assert source["stage"] == "failed" and "returned no values" in source["error"]
        fake_model.fail_on.clear()
        # an outside agent runs the prompt's command (here: the same function, in-process)
        from datasheet_extractor.sources import extraction
        assert extraction.main(["--home", str(home), source["id"]]) == 0
        assert extraction.main(["--home", str(home), source["id"]]) == 1            # never over an existing one
        done = client.wait(batch["id"], stages=("extracted",), timeout=10)["sources"][0]
        history = client.get(f"/api/sources/{done['id']}")[1]["source"]["history"]
        assert [h["stage"] for h in history][-2:] == ["failed", "extracted"]
    finally:
        stop(app, server)


@needs_markdownify
def test_a_restart_resumes_or_fails_honestly(tmp_path, fake_model, isolated_cache):
    home = tmp_path / "home"
    store = Store(home)
    batch = store.create_batch("left over", "actuator", [], [{"kind": "file", "name": "ax.html",
                                                               "data": HTML.encode()}])
    other = store.create_batch("cut off", "actuator", [], [{"kind": "url", "url": "https://example.com/x",
                                                            "capture_keep": "html"}])
    cut = store.identify(other["sources"][0], "f" * 64)
    store.advance(cut, "captured")
    store.advance(cut, "converted")
    store.advance(cut, "extracted")                       # ...and its values.json was set aside, never replaced
    app, server, client = start(home)
    try:
        resumed = client.wait(batch["id"])["sources"][0]
        assert resumed["stage"] == "extracted"
        history = client.get(f"/api/sources/{resumed['id']}")[1]["source"]["history"]
        assert [h["stage"] for h in history] == ["added", "captured", "converted", "extracted"]
        status, detail = client.get(f"/api/sources/{cut}")
        assert detail["source"]["stage"] == "failed" and "restart" in detail["source"]["error"]
    finally:
        stop(app, server)


def test_serve_prints_the_url_and_answers(tmp_path):
    process = subprocess.Popen(["uv", "run", "extract", "serve", "--home", str(tmp_path / "home")],
                               cwd=pathlib.Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == str((tmp_path / "home").resolve())
        url = process.stdout.readline().strip()
        assert re.fullmatch(r"http://127\.0\.0\.1:\d+/", url)
        assert Client(url).get("/api/batches") == (200, [])
    finally:
        process.terminate()
        process.wait(timeout=10)
