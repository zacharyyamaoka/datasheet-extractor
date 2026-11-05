"""The Sources app (CONTRACT §6) in headless Chromium, end to end, every check a measurement:

- every review op made in the UI round-trips to <home>/sources/<id>/review.json on disk (§7.1 shape, in order);
- a clicked row's spotlight mark sits on its grounding within 1 px (rectangle computed here from the box and the
  rendered page, the highlighter's geometry rule: box × page size, padded by `padding` × zoom);
- every pane boundary drags (getBoundingClientRect before and after the drag, and after a reload) and the log and the
  mapping step fold and stay folded;
- no console errors; no control without an event listener of its own (CDP DOMDebugger.getEventListeners);
- the vendored modules still hash to web/vendor/VENDOR.json.

    uv run --directory /home/bam/markdownify python /home/bam/datasheet-extractor/tests/sources_journey.py
        [--server stub|real] [--media]

`--server stub` (default) runs tests/sources_journey_stub.py; `--server real` runs `extract serve` in a temp --home.
The measured results go to reports/media/sources-app/journey-results-<server>.json (excluded from git); `--media`
also records the hero video and three screenshots there.
Every process it starts is stopped by its own pid.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

REPO = pathlib.Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "datasheet_extractor" / "sources" / "web"
MEDIA = REPO / "reports" / "media" / "sources-app"
BUNDLE = pathlib.Path.home() / ".cache" / "markdownify" / "bundles" / "ac3e7b8d27dcd72277b6bdbb208586a2343a635edf1ff578fffd131a31713f6f"
PDF = BUNDLE / "source.pdf"
CUBEMARS_URL = "https://www.cubemars.com/product/ak80-9-v3-0-robotic-actuator.html"
SPOT_PADDING = 2  # source-highlighter STYLE_DEFAULTS.spotlight.padding (the review tab passes none of its own)
TOLERANCE = 1
VIEWPORT = {"width": 1500, "height": 940}
# Markdownify CONTRACT §2.1, the measured sizes the Add screen must show beside each level.
LEVEL_SIZES = {"link": "~1 KB", "html": "192 KB (39 KB gzipped)", "snapshot": "1.4 MB", "pdf": "5.2 MB",
               "archive": "4.9 MB first page, 2.1–2.7 MB each further page"}
RESULTS: dict[str, object] = {}
FAILURES: list[str] = []


def check(ok: bool, what: str, detail: object = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {what}{f'  ({detail})' if detail != '' else ''}", flush=True)
    if not ok:
        FAILURES.append(f"{what}: {detail}")


# ---- the server, stopped by its own pid -------------------------------------------------------------------------

class Server:
    def __init__(self, kind: str, home: pathlib.Path, step: float):
        if kind == "stub":
            argv = [sys.executable, str(REPO / "tests" / "sources_journey_stub.py"), "--home", str(home), "--port", "0",
                    "--step", str(step)]
        else:
            argv = ["uv", "run", "--directory", str(REPO), "extract", "serve", "--port", "0", "--home", str(home)]
        self.process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
        lines = []
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                break
            lines.append(line)
            if "http://127.0.0.1:" in line:
                self.url = line[line.index("http://"):].split()[0].rstrip("/") + "/"
                return
        self.stop()
        raise RuntimeError(f"the server did not print its URL: {''.join(lines)}")

    def stop(self) -> None:
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)  # its own session: `uv run` and the server under it
            self.process.wait(timeout=10)


# ---- measurements -------------------------------------------------------------------------------------------------

def rect(page, selector: str) -> dict | None:
    return page.evaluate("""(selector) => { const node = document.querySelector(selector); if (!node) return null;
        const r = node.getBoundingClientRect(); return {left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height}; }""", selector)


def near(actual: dict, expected: dict, keys=("left", "top", "right", "bottom")) -> float:
    return max(abs(actual[key] - expected[key]) for key in keys)


def expected_spot(page, location: dict, page_width_pt: float) -> dict:
    """The highlighter's geometry rule for a page/box Location, from the rendered page's own rectangle."""
    page_rect = rect(page, f'[data-testid="source-pane"] .sh-page[data-page="{location["page"]}"]')
    zoom = page_rect["width"] / page_width_pt
    x0, y0, x1, y1 = location["box"]
    pad = SPOT_PADDING * zoom
    return {"left": max(page_rect["left"], page_rect["left"] + x0 * page_rect["width"] - pad),
            "top": max(page_rect["top"], page_rect["top"] + y0 * page_rect["height"] - pad),
            "right": min(page_rect["right"], page_rect["left"] + x1 * page_rect["width"] + pad),
            "bottom": min(page_rect["bottom"], page_rect["top"] + y1 * page_rect["height"] + pad)}


def shown(page) -> str:
    return page.evaluate("() => document.querySelector('[data-testid=source-pane]').dataset.shown || '0'")


def click_and_wait_shown(page, selector: str) -> None:
    """Click, then wait until the source pane has drawn the marks that click asked for (source-pane.js sets
    data-shown after each update; read before the click so a fast redraw is not missed)."""
    before = shown(page)
    page.click(selector)
    page.wait_for_function("(before) => { const s = document.querySelector('[data-testid=source-pane]'); return s.dataset.state === 'ready' && (s.dataset.shown || '0') !== before; }", arg=before, timeout=10000)


def union(page, selector: str) -> dict | None:
    """The bounding rectangle of every element `selector` matches (an html Location draws one mark per line box)."""
    return page.evaluate("""(selector) => { const rects = [...document.querySelectorAll(selector)].map((n) => n.getBoundingClientRect());
        if (!rects.length) return null;
        return {left: Math.min(...rects.map((r) => r.left)), top: Math.min(...rects.map((r) => r.top)),
                right: Math.max(...rects.map((r) => r.right)), bottom: Math.max(...rects.map((r) => r.bottom))}; }""", selector)


def expected_location(page, location: dict) -> dict | None:
    """Where a spotlight for `location` must sit, measured here, not by the highlighter: a page/box from the rendered
    page's rectangle; an html Location from the archived page's own DOM (its xpath element's text, laid out by the
    browser, scaled like the page)."""
    if "box" in location:
        return expected_spot(page, {"page": location.get("page", 1), "box": location["box"]}, page_width_pt(page, location.get("page", 1)))
    html = location.get("html") or {}
    return page.evaluate("""({xpath, pad}) => {
        const holder = document.querySelector('[data-testid="source-pane"] .sh-page[data-page="1"]');
        const frame = holder.querySelector('iframe');
        const doc = frame.contentDocument;
        const node = doc.evaluate(xpath, doc, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;
        if (!node) return null;
        const range = doc.createRange();
        range.selectNodeContents(node);
        const rects = [...range.getClientRects()].filter((r) => r.width > 0 && r.height > 0);
        if (!rects.length) return null;
        const page = holder.getBoundingClientRect();
        const zoom = page.width / frame.offsetWidth;
        const x0 = Math.min(...rects.map((r) => r.left)), y0 = Math.min(...rects.map((r) => r.top));
        const x1 = Math.max(...rects.map((r) => r.right)), y1 = Math.max(...rects.map((r) => r.bottom));
        return {left: page.left + (x0 - pad) * zoom, top: page.top + (y0 - pad) * zoom, right: page.left + (x1 + pad) * zoom, bottom: page.top + (y1 + pad) * zoom};
    }""", {"xpath": html.get("xpath"), "pad": SPOT_PADDING})


def page_width_pt(page, number: int) -> float:
    """A PDF page's width in points, from the bundle's index.json (the source of truth for the page size)."""
    index = json.loads((BUNDLE / "index.json").read_text(encoding="utf-8"))
    return next(entry["width"] for entry in index["pages"] if entry["page"] == number)


def wait_ready(page) -> None:
    page.wait_for_selector('[data-testid="source-pane"][data-state="ready"]', timeout=30000)
    page.wait_for_selector("tr.r", timeout=10000)


class Disk:
    """review.json and published/ as the server wrote them."""

    def __init__(self, home: pathlib.Path):
        self.home = home

    def ops(self, source_id: str) -> list[dict]:
        path = self.home / "sources" / source_id / "review.json"
        return json.loads(path.read_text(encoding="utf-8"))["ops"] if path.exists() else []

    def wait_ops(self, source_id: str, count: int, timeout: float = 10) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ops = self.ops(source_id)
            if len(ops) >= count:
                return ops
            time.sleep(0.05)
        return self.ops(source_id)


def row_selector(value_id: str) -> str:
    return f'tr.r[data-id="{value_id}"]'


# ---- no control without a handler ------------------------------------------------------------------------------

CONTROLS = "button, input, select, textarea, [role=button], [role=separator], [tabindex]:not([tabindex='-1'])"
HANDLER_EVENTS = {"click", "change", "input", "pointerdown", "keydown", "submit", "drop"}


def controls_without_handlers(page, cdp, where: str) -> list[str]:
    count = page.evaluate("""(selector) => {
        const list = [...document.querySelectorAll(selector)].filter((node) => !node.disabled && node.type !== 'hidden'
          && (node.offsetParent !== null || node.type === 'file') && !node.closest('[hidden]'));
        list.forEach((node, index) => node.dataset.ctlProbe = String(index));
        return list.length; }""", CONTROLS)
    missing = []
    for index in range(count):
        found = cdp.send("Runtime.evaluate", {"expression": f'document.querySelector(\'[data-ctl-probe="{index}"]\')'})
        object_id = found["result"]["objectId"]
        listeners = cdp.send("DOMDebugger.getEventListeners", {"objectId": object_id, "depth": 0})["listeners"]
        if not {listener["type"] for listener in listeners} & HANDLER_EVENTS:
            missing.append(page.evaluate(f"""() => {{ const n = document.querySelector('[data-ctl-probe="{index}"]');
                return n.outerHTML.slice(0, 120); }}"""))
    page.evaluate("() => document.querySelectorAll('[data-ctl-probe]').forEach((n) => delete n.dataset.ctlProbe)")
    RESULTS.setdefault("controls_checked", {})[where] = count
    return missing


# ---- the journey ----------------------------------------------------------------------------------------------------

def vendor_hashes() -> None:
    record = json.loads((WEB / "vendor" / "VENDOR.json").read_text(encoding="utf-8"))["files"]
    for name, entry in record.items():
        digest = hashlib.sha256((WEB / "vendor" / name).read_bytes()).hexdigest()
        check(digest == entry["sha256"], f"vendor/{name} hashes to VENDOR.json", digest[:12])


def journey(browser, server: Server, disk: Disk, kind: str) -> None:
    context = browser.new_context(viewport=VIEWPORT)
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=server.url.rstrip("/"))
    page = context.new_page()
    cdp = context.new_cdp_session(page)
    errors: list[str] = []
    page.on("console", lambda message: errors.append(f"{message.type}: {message.text}") if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
    # WHY two plans: the stub has canned values for the LRS-350 PDF only; against the real server the only category
    # is actuator, which reads 1 value off a power supply, so the review steps run on an actuator page (CubeMars
    # AK80-9, uploaded as a saved .html file — no live fetch) and the drawn box on the PDF. A link is stub-only:
    # capturing one for real is a live fetch plus another model call.
    uploads = [UPLOAD] + ([CUBEMARS_UPLOAD] if kind == "real" else [])
    links = [CUBEMARS_URL] if kind == "stub" else []
    titles = ["Mean Well LRS-350-24 (journey)"] + (["CubeMars AK80-9 (journey)"] if kind == "real" else [])
    review_name = "LRS-350-SPEC.pdf" if kind == "stub" else CUBEMARS_UPLOAD["name"]

    # 1. Add Sources ----------------------------------------------------------------------------------------------
    page.goto(server.url + "#/add")
    page.wait_for_selector("[data-testid=category] option", state="attached")
    sizes = {level: page.text_content(f"[data-testid=size-{level}]") for level in LEVEL_SIZES}
    check(sizes == LEVEL_SIZES, "the Add screen shows §2.1's measured size beside each capture level", sizes)
    # capture levels are a set: html always on and locked; snapshot, pdf, archive optional; "link only" explicit
    locked = page.evaluate("() => { const b = document.querySelector('[data-testid=keep-html]'); return [b.checked, b.disabled]; }")
    check(locked == [True, True], "html is always kept and locked", locked)
    check(page.text_content("[data-testid=keep-total]") == "≈ 192 KB", "the running total starts at html's measured size", page.text_content("[data-testid=keep-total]"))
    page.check("[data-testid=keep-snapshot]")
    total = (page.text_content("[data-testid=keep-total]"), page.text_content("[data-testid=keep-list]"))
    check(total == ("≈ 1.6 MB", "html + snapshot"), "ticking snapshot adds its size to the total", total)
    page.check("[data-testid=keep-link-only]")
    link_only = page.evaluate("""() => [document.querySelector('[data-testid=keep-html]').checked, document.querySelector('[data-testid=keep-snapshot]').disabled,
        document.querySelector('[data-testid=keep-total]').textContent]""")
    check(link_only == [False, True, "≈ 1 KB"], "'link only' unticks html and keeps no copy", link_only)
    page.uncheck("[data-testid=keep-link-only]")
    page.check("[data-testid=keep-snapshot]")
    options = page.evaluate("() => [...document.querySelectorAll('[data-testid=category] option')].map((o) => o.textContent)")
    check("psu — every spec the sheet prints" in options and any(option.startswith("actuator — ") for option in options),
          "categories are labelled by what they extract (psu: open mode)", options)
    missing = controls_without_handlers(page, cdp, "add")
    check(not missing, "Add: every control has its own handler", missing)
    page.set_input_files("[data-testid=file-input]", uploads)
    page.fill("[data-testid=urls]", "".join(link + "\n" for link in links))
    for number, title in enumerate(titles, 1):
        page.fill("[data-testid=add-product-search]", title)
        page.wait_for_selector(".psearch-list li.create")
        page.click(".psearch-list li.create")
        page.wait_for_function("(n) => document.querySelectorAll('[data-testid=add-products] .prod').length === n", arg=number)
    catalog = json.loads(page.evaluate("async () => JSON.stringify(await (await fetch('/api/products')).json())"))
    created = {product["title"]: product["id"] for product in catalog if product["title"] in titles}
    check(len(created) == len(titles), "products created inline exist via POST /api/products", created)
    page.select_option("[data-testid=category]", "actuator")
    count = len(uploads) + len(links)
    check(page.text_content("[data-testid=submit]") == f"Add {count} sources to the queue", "submit counts the files and links", page.text_content("[data-testid=submit]"))
    # WHY read it in the page: Playwright does not expose a multipart body that carries file blobs.
    page.evaluate("""() => { const send = window.fetch; window.__sentKeep = [];
        window.fetch = (url, init) => { if (init?.body instanceof FormData) window.__sentKeep.push(init.body.get('capture_keep')); return send(url, init); }; }""")
    page.click("[data-testid=submit]")
    sent_keep = page.evaluate("() => window.__sentKeep")
    check(sent_keep == ["html,snapshot"], "capture_keep is sent as the comma list", sent_keep)

    # 2. Queue: stages advance live -------------------------------------------------------------------------------
    page.wait_for_selector("[data-testid=queue] [data-testid=batch]")
    stage_of = """(name) => { const li = [...document.querySelectorAll('[data-testid=queue-source]')].find((n) => n.querySelector('.name').textContent === name);
        return li ? [li.dataset.stage, li.dataset.source] : [null, null]; }"""
    seen = []
    deadline = time.monotonic() + (60 if kind == "stub" else 1200)
    while time.monotonic() < deadline:
        stage, _ = page.evaluate(stage_of, "LRS-350-SPEC.pdf")
        if stage and (not seen or seen[-1] != stage):
            seen.append(stage)
        done = [page.evaluate(stage_of, upload["name"])[0] in ("extracted", "failed") for upload in uploads]
        if all(done):
            break
        time.sleep(0.1)
    RESULTS["queue_stages_seen"] = seen
    check(seen[-1:] == ["extracted"] and len(seen) >= 3, "the queue shows the PDF's stages advancing to extracted by polling", seen)
    check(not controls_without_handlers(page, cdp, "queue"), "Queue: every control has its own handler")
    pdf_id = page.evaluate(stage_of, "LRS-350-SPEC.pdf")[1]
    check(pdf_id == PDF_SHA[:12], "the source id is the first 12 hex of its sha256 once captured (§2)", pdf_id)
    # the queue row's category picker: POST /api/sources/<id>/extract {category}, saved to source.json (CONTRACT §2, §4).
    # Stub: on the failed link (saved for after conversion). Real: the PDF as psu (open mode), a real re-extraction.
    if kind == "stub":
        page.wait_for_selector("[data-testid=queue-source][data-stage=failed] [data-category]")
    picked_name = "LRS-350-SPEC.pdf" if kind == "real" else page.evaluate("""() => [...document.querySelectorAll('[data-testid=queue-source]')]
        .find((n) => n.dataset.stage === 'failed').querySelector('.name').textContent""")
    picked_id = page.evaluate(stage_of, picked_name)[1]
    with page.expect_response(lambda response: response.url.endswith(f"/api/sources/{picked_id}/extract")) as answered:
        page.select_option(f'[data-category="{picked_id}"]', "psu")
    on_disk = json.loads((disk.home / "sources" / picked_id / "source.json").read_text(encoding="utf-8")).get("category")
    check(answered.value.ok and on_disk == "psu", "the queue's category picker sets the source's category", (answered.value.status, on_disk))
    review_stage, source_id = page.evaluate(stage_of, review_name)
    check(review_stage == "extracted", f"{review_name} is extracted", review_stage)
    product_id = created.get(titles[-1])
    expected_ops: dict[str, list[dict]] = {}

    def expect(op: dict, what: str, on: str | None = None) -> None:
        on = on or source_id
        expected = expected_ops.setdefault(on, [])
        expected.append(op)
        ops = disk.wait_ops(on, len(expected))
        last = ops[len(expected) - 1] if len(ops) >= len(expected) else {}
        ok = all(last.get(key) == want for key, want in op.items()) and bool(last.get("author")) and bool(last.get("at"))
        check(ok, f"op round-trips to review.json: {what}", {key: last.get(key) for key in op} if not ok else last["op"])
        page.wait_for_function("(n) => (document.querySelectorAll('[data-testid=log-item]').length === n)", arg=len(expected), timeout=5000)

    def spot_error(value: dict) -> float:
        """Click a row; the spotlight marks' union against its first grounding's rectangle."""
        click_and_wait_shown(page, f'{row_selector(value["id"])} td.f')
        mark = union(page, f'[data-testid="source-pane"] .sh-mark.sh-spotlight[data-id="{value["id"]}"]')
        expected = expected_location(page, value["grounding"][0])
        return near(mark, expected) if mark and expected else 99

    # 3. The review tab -------------------------------------------------------------------------------------------
    page.click(f'[data-testid=queue-source][data-source="{source_id}"] [data-open]')
    wait_ready(page)
    state = json.loads(page.evaluate(f"async () => JSON.stringify(await (await fetch('/api/sources/{source_id}')).json())"))
    kept = [value for value in state["effective"]["values"] if value.get("verdict") == "kept"]
    RESULTS["review_source"] = {"name": review_name, "id": source_id, "kept": len(kept)}
    check(page.locator("tr.r").count() == len(kept), "one row per kept value of `effective`", f"{page.locator('tr.r').count()} rows, {len(kept)} kept")
    rendered_raw = page.evaluate("() => [...document.querySelectorAll('tr.r td.pr .raw')].map((n) => n.textContent)")
    check(rendered_raw == [value["raw"] for value in kept], "raw values render exactly as printed", rendered_raw[:4])
    faint = page.evaluate("() => [...document.querySelectorAll('tr.r [data-testid=provenance] .pv.ai')].map((n) => n.textContent)")
    check(len(faint) == len(kept) and all(text.startswith("AI · ") for text in faint), "untouched rows read a faint 'AI · date'", faint[:2])
    models = page.evaluate("() => [...document.querySelectorAll('[data-testid=mapping] .mp .model')].map((n) => n.textContent)")
    if kind == "stub":
        check(len(models) == 8 and "LRS-350-24" in models, "the mapping step lists the 8 models of the comparison table", models)
    else:
        check(bool(models), "the mapping step lists the models this datasheet covers", models)
    check(len(kept) >= 3, "the extraction kept at least 3 values to review", len(kept))
    shown_labels = page.evaluate("() => [...document.querySelectorAll('tr.r td.f')].map((n) => n.textContent)")
    bare = [label for label, value in zip(shown_labels, kept) if label == value["key"]]
    RESULTS["rows_showing_their_key"] = len(bare)
    check(not bare, "every row shows its field's label, never the bare key", bare[:4])

    def pick(key: str, fallback: int) -> dict:
        """The LRS-350 fixture's row by key; a real extraction's by position."""
        return next((value for value in kept if value["key"] == key), kept[fallback])

    def replaced(value) -> tuple[str, object]:
        """What to type over a value, and what it reads as: a rounded number for a fraction, else one more."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "15", "15"
        number = round(value) if value != round(value) else value + 1
        return str(number), number

    power, tolerance, current = pick("rated_power_w", 0), pick("voltage_tolerance_pct", 1), pick("rated_current_a", 2)
    errors_px = [spot_error(value) for value in [kept[0], kept[1], power]]
    RESULTS["spotlight_errors_px"] = [round(error, 3) for error in errors_px]
    check(max(errors_px) <= TOLERANCE, "the spotlight mark matches the grounding within 1 px", f"max {max(errors_px):.3f} px")
    visible = page.evaluate("""(id) => { const m = document.querySelector(`.sh-mark.sh-box[data-id="${id}"]`).getBoundingClientRect();
        const s = document.querySelector('[data-testid=source-pane]').getBoundingClientRect(); return m.top >= s.top && m.bottom <= s.bottom && m.left >= s.left && m.right <= s.right; }""", power["id"])
    check(visible, "the spotlit grounding is scrolled into view")

    # edit in place: click the value, type, Enter
    page.click(f'{row_selector(power["id"])} [data-testid=value]')
    typed, power_to = replaced(power["value"])
    page.fill("[data-testid=edit-value]", typed)
    page.press("[data-testid=edit-value]", "Enter")
    expect({"op": "edit", "value_id": power["id"], "field": "value", "from": power["value"], "to": power_to}, "edit (mouse + Enter)")
    was = page.text_content(f'{row_selector(power["id"])} [data-testid=was]')
    bold = page.evaluate(f"""() => {{ const n = document.querySelector('{row_selector(power["id"])} .pv.hu'); return n ? [n.textContent, getComputedStyle(n).fontWeight] : null; }}""")
    check(was == f"was {power['value']}" and bold and int(bold[1]) >= 600, "an edited row says 'was <AI value>' and bold initials · time", (was, bold))

    # j/k, then x strikes; ↺ and ✕ by mouse
    page.click(f'{row_selector(tolerance["id"])} td.f')
    page.keyboard.press("k")
    page.keyboard.press("j")
    check(page.evaluate(f"() => document.querySelector('{row_selector(tolerance['id'])}').classList.contains('cur')"), "j/k move the focused row")
    page.keyboard.press("x")
    expect({"op": "reject", "value_id": tolerance["id"]}, "reject (x)")
    struck = page.evaluate(f"""() => getComputedStyle(document.querySelector('{row_selector(tolerance["id"])} .struck')).textDecorationLine""")
    check(struck == "line-through", "a struck value stays visible, struck through", struck)
    page.click(f'{row_selector(tolerance["id"])} [data-restore]')
    expect({"op": "restore", "value_id": tolerance["id"]}, "restore (mouse ↺)")
    page.click(f'{row_selector(tolerance["id"])} [data-strike]')
    expect({"op": "reject", "value_id": tolerance["id"]}, "reject (mouse ✕)")
    # type to replace (keyboard)
    page.click(f'{row_selector(current["id"])} td.f')
    typed, current_to = replaced(current["value"])
    page.keyboard.type(typed)
    page.keyboard.press("Enter")
    expect({"op": "edit", "value_id": current["id"], "field": "value", "from": current["value"], "to": current_to}, "edit (type to replace)")

    # products: p opens the menu; tick assigns, untick unassigns
    page.click(f'{row_selector(current["id"])} td.f')
    page.keyboard.press("p")
    page.fill("[data-testid=value-product-search]", product_id)
    page.wait_for_selector(".psearch-list li:not(.create)")
    page.click(".psearch-list li:not(.create)")
    expect({"op": "assign", "products": [product_id], "value_ids": [current["id"]]}, "assign one value (p + search)")
    page.uncheck(f'.pop [data-product="{product_id}"]')
    expect({"op": "unassign", "products": [product_id], "value_ids": [current["id"]]}, "unassign one value (untick)")
    page.mouse.click(5, 300)

    # the mapping step: a model → the created product (found by search in the Map menu)
    model = "LRS-350-24" if "LRS-350-24" in models else models[0]
    page.locator("[data-testid=mapping] .mp").filter(has=page.get_by_text(model, exact=True)).locator("[data-map]").click()
    page.fill("[data-testid=map-search]", product_id)
    page.wait_for_selector(".pop .psearch-list li:not(.create)")
    page.click(".pop .psearch-list li:not(.create)")
    # every value, or — when the extraction read some per model — the shared ones and that model's own (§7.1)
    norm = lambda text: "".join(ch for ch in text.lower() if ch.isalnum())  # noqa: E731
    same = lambda a, b: norm(a).startswith(norm(b)) or norm(b).startswith(norm(a))  # noqa: E731 - "AK80-9 V3.0 KV100" is "AK80-9"
    per_model = any(value.get("part_models") for value in kept)
    mapped_ids = [value["id"] for value in kept if not value.get("part_models") or any(same(model, other) for other in value["part_models"])] if per_model else []
    mapped_ids = mapped_ids or ["*"]
    RESULTS["mapped"] = {"model": model, "per_model": per_model, "value_ids": len(mapped_ids)}
    expect({"op": "assign", "products": [product_id], "value_ids": mapped_ids, "note": f"model {model}"}, "map a model (assign)")
    check("(1 mapped)" in page.text_content("[data-testid=mapped-count]"), "the mapping step counts the mapped model", page.text_content("[data-testid=mapped-count]"))

    # a human row from the keyboard (a), no box: grounding null, "no source cited"
    page.keyboard.press("a")
    page.fill("[data-testid=add-field]", "Warranty")
    page.fill("[data-testid=add-value]", "3")
    page.fill("[data-testid=add-unit]", "years")
    page.press("[data-testid=add-value]", "Enter")
    expected_ops[source_id].append({"op": "add"})
    ops = disk.wait_ops(source_id, len(expected_ops[source_id]))
    check(ops[-1]["op"] == "add" and ops[-1]["row"]["grounding"] is None and ops[-1]["row"]["label"] == "Warranty",
          "op round-trips to review.json: add with no box → grounding null", ops[-1].get("row"))
    page.wait_for_function("(n) => (document.querySelectorAll('[data-testid=log-item]').length === n)", arg=len(expected_ops[source_id]))
    warranty = ops[-1]["row"]["id"]
    nosource = page.text_content(f'{row_selector(warranty)} td.pr')
    check(nosource == "no source cited", "a human row with no box reads 'no source cited'", nosource)

    # By product view: one column per product; a click assigns, another unassigns
    page.click("[data-view=product]")
    page.wait_for_selector("th.pc")
    headers = page.evaluate("() => [...document.querySelectorAll('th.pc:not(.add)')].map((n) => n.title)")
    check(product_id in headers, "By product shows a column per product", headers)
    check(not controls_without_handlers(page, cdp, "review-by-product"), "Review (By product): every control has its own handler")
    # WHY the human row: it was added after the model was mapped, so it does not apply to the product yet
    page.click(f'{row_selector(warranty)} [data-assign="{product_id}"]')
    expect({"op": "assign", "products": [product_id], "value_ids": [warranty]}, "assign from the By product matrix")
    page.click(f'{row_selector(warranty)} [data-unassign="{product_id}"]')
    expect({"op": "unassign", "products": [product_id], "value_ids": [warranty]}, "unassign from the By product matrix")
    page.click("[data-view=rows]")
    page.wait_for_selector("[data-testid=provenance]")

    # the log is review.json, newest first
    log = page.evaluate("() => [...document.querySelectorAll('[data-testid=log-item]')].map((n) => n.dataset.op)")
    disk_ops = [op["op"] for op in disk.ops(source_id)]
    check(log == list(reversed(disk_ops)), "the version log is review.json, newest first", log)
    RESULTS["ops_on_disk"] = disk_ops
    missing = controls_without_handlers(page, cdp, "review-rows")
    check(not missing, "Review (Rows): every control has its own handler", missing)
    page.click(f'{row_selector(current["id"])} [data-pmenu]')
    check(not controls_without_handlers(page, cdp, "review-product-menu"), "Review (product menu open): every control has its own handler")
    page.mouse.click(5, 300)

    # Copy prompt → clipboard is GET /api/sources/<id>/prompt
    page.click("[data-testid=copy-prompt]")
    page.wait_for_timeout(300)
    clip = page.evaluate("() => navigator.clipboard.readText()")
    prompt = page.evaluate(f"async () => await (await fetch('/api/sources/{source_id}/prompt')).text()")
    check(clip == prompt and len(prompt) > 40, "Copy prompt puts GET …/prompt on the clipboard", f"{len(clip)} chars")

    # Publish → published/<product>/<source>.json for every assigned product, stage published
    page.click("[data-testid=publish]")
    published = disk.home / "published" / product_id / f"{source_id}.json"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not published.exists():
        time.sleep(0.05)
    doc = json.loads(published.read_text(encoding="utf-8")) if published.exists() else {}
    check(bool(doc) and doc["product"] == product_id and tolerance["id"] not in {value["id"] for value in doc["values"]}
          and any(value["id"] == power["id"] and value["value"] == power_to for value in doc["values"]),
          "Publish writes published/<product>/<source>.json without the struck value, with the edit", published)
    page.wait_for_function("() => document.querySelector('[data-testid=source-stage]').textContent === 'published'")

    # Run extraction (stub only: the real one runs a model) → back to extracted, review kept
    if kind == "stub":
        page.click("[data-testid=run-extraction]")
        page.wait_for_function("() => document.querySelector('[data-testid=source-stage]').textContent === 'extracted'", timeout=15000)
        check(len(disk.ops(source_id)) == len(disk_ops), "Run extraction re-runs and keeps review.json", len(disk.ops(source_id)))

    # 4. A human row cited by a box drawn on the PDF (its own tab in the batch when it is another source) -------------
    if pdf_id != source_id:
        # the PDF is re-extracting as psu (open mode) since the queue's picker; wait for its values
        # WHY count history: a re-extraction leaves the stage at `extracted` while it runs (values.json is archived
        # first); it is done when a second `extracted` (or a `failed`) is appended.
        def finished() -> bool:
            record = json.loads((disk.home / "sources" / pdf_id / "source.json").read_text(encoding="utf-8"))
            stages = [step["stage"] for step in record.get("history", [])]
            return stages.count("extracted") >= 2 or stages[-1:] == ["failed"]
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline and not finished():
            time.sleep(1)
        record = json.loads((disk.home / "sources" / pdf_id / "source.json").read_text(encoding="utf-8"))
        RESULTS["pdf_after_psu"] = {key: record.get(key) for key in ("stage", "category", "error")} | {"history": [step["stage"] for step in record.get("history", [])]}
        check(record["stage"] == "extracted", "the PDF re-extracts as psu (open mode)", RESULTS["pdf_after_psu"])
        page.click(f'[data-testid=tabs] [data-tab="{pdf_id}"]')
        page.wait_for_function("(id) => location.hash.endsWith(id)", arg=pdf_id)
        wait_ready(page)
        check(page.input_value("[data-testid=source-category]") == "psu", "the tab's category picker shows the source's own category", page.input_value("[data-testid=source-category]"))
        open_rows = (json.loads(page.evaluate(f"async () => JSON.stringify(await (await fetch('/api/sources/{pdf_id}')).json())"))["effective"] or {}).get("values") or []
        open_rows = [value for value in open_rows if value.get("verdict") == "kept"]
        labels = page.evaluate("() => [...document.querySelectorAll('tr.r td.f')].map((n) => n.textContent)")
        RESULTS["open_mode"] = {"kept": len(open_rows), "labels": labels[:6]}
        check(bool(open_rows) and all(not label.startswith("spec.") for label in labels),
              "open mode (psu) rows show the label printed in the source", labels[:4])
    ops_before = len(disk.ops(pdf_id))
    page.click("[data-testid=add-row]")
    page.fill("[data-testid=add-field]", "Mass")
    page.fill("[data-testid=add-value]", "1.2")
    page.fill("[data-testid=add-unit]", "kg")
    page.fill("[data-testid=add-note]", "weighed on the bench")
    page.locator('[data-testid="source-pane"] .sh-page[data-page="2"]').scroll_into_view_if_needed()
    page_rect = rect(page, '[data-testid="source-pane"] .sh-page[data-page="2"]')
    pane_rect = rect(page, '[data-testid="source-pane"]')
    top = max(page_rect["top"], pane_rect["top"]) + 40
    start = (page_rect["left"] + page_rect["width"] * 0.30, top)
    end = (page_rect["left"] + page_rect["width"] * 0.45, top + 30)
    page.mouse.move(*start)
    page.mouse.down()
    page.mouse.move(end[0], end[1], steps=6)
    page.mouse.up()
    page.wait_for_function("() => document.querySelector('[data-testid=add-cite]').textContent.includes('p.2')")
    page.click(".ie.add [data-add]")
    drawn_box = [round((start[0] - page_rect["left"]) / page_rect["width"], 5), round((start[1] - page_rect["top"]) / page_rect["height"], 5),
                 round((end[0] - page_rect["left"]) / page_rect["width"], 5), round((end[1] - page_rect["top"]) / page_rect["height"], 5)]
    expected_ops[pdf_id] = [None] * ops_before + [{"op": "add"}]
    ops = disk.wait_ops(pdf_id, len(expected_ops[pdf_id]))
    added = ops[-1] if ops and ops[-1]["op"] == "add" else {}
    grounding = (added.get("row") or {}).get("grounding") or [{}]
    box_error = max(abs(a - b) * page_rect["width"] for a, b in zip(grounding[0].get("box", [9, 9, 9, 9]), drawn_box))
    check(added.get("row", {}).get("label") == "Mass" and added["row"]["value"] == 1.2 and added["row"]["unit"] == "kg" and grounding[0].get("page") == 2 and box_error <= TOLERANCE,
          "op round-trips to review.json: add a human row whose grounding is the drawn box", f"box error {box_error:.3f} px")
    RESULTS["drawn_box_error_px"] = round(box_error, 3)
    page.wait_for_function("(n) => (document.querySelectorAll('[data-testid=log-item]').length === n)", arg=len(expected_ops[pdf_id]))
    human_id = added.get("row", {}).get("id")
    error = spot_error({"id": human_id, "grounding": grounding})
    check(error <= TOLERANCE, "clicking the human row spotlights its drawn box within 1 px", f"{error:.3f} px")

    # the tab header's category picker (stub: the canned extraction runs again; real: a model call, so not here)
    if kind == "stub":
        with page.expect_response(lambda response: response.url.endswith(f"/api/sources/{pdf_id}/extract")) as answered:
            page.select_option("[data-testid=source-category]", "psu")
        page.wait_for_function("() => document.querySelector('[data-testid=source-stage]').textContent === 'extracted'", timeout=15000)
        on_disk = json.loads((disk.home / "sources" / pdf_id / "source.json").read_text(encoding="utf-8")).get("category")
        check(answered.value.ok and on_disk == "psu", "the tab's category picker sets the category and extracts again", (answered.value.status, on_disk))

    # 5. Drag test: every boundary, before / after / after a reload -----------------------------------------------
    def drag(handle: str, dx: float, dy: float) -> None:
        box = rect(page, handle)
        x, y = box["left"] + box["width"] / 2, box["top"] + box["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + dx, y + dy, steps=8)
        page.mouse.up()

    left_before = rect(page, ".rv-left")["width"]
    drag("[data-testid=split-main]", -140, 0)
    left_after = rect(page, ".rv-left")["width"]
    log_before = rect(page, "[data-testid=log]")["height"]
    drag("[data-testid=split-log]", 0, -70)
    log_after = rect(page, "[data-testid=log]")["height"]
    page.reload()
    wait_ready(page)
    left_reload = rect(page, ".rv-left")["width"]
    log_reload = rect(page, "[data-testid=log]")["height"]
    RESULTS["drag"] = {"source_pane_width": [round(left_before, 1), round(left_after, 1), round(left_reload, 1)],
                       "log_height": [round(log_before, 1), round(log_after, 1), round(log_reload, 1)]}
    check(abs((left_before - left_after) - 140) <= TOLERANCE and abs(left_reload - left_after) <= TOLERANCE,
          "source|values boundary drags 140 px and survives a reload", RESULTS["drag"]["source_pane_width"])
    check(abs((log_after - log_before) - 70) <= TOLERANCE and abs(log_reload - log_after) <= TOLERANCE,
          "values|log boundary drags 70 px and survives a reload", RESULTS["drag"]["log_height"])
    page.click("[data-testid=log-fold]")
    page.click("[data-testid=mapping-fold]")
    page.reload()
    wait_ready(page)
    log_items_hidden = page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=log] ol')).display === 'none'")
    map_hidden = page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=mapping] .map-body')).display === 'none'")
    folded_height = rect(page, "[data-testid=log]")["height"]
    check(log_items_hidden and map_hidden and folded_height < 40, "the log and the mapping step fold and stay folded after a reload", f"log {folded_height:.0f} px")
    page.click("[data-testid=log-fold]")
    page.click("[data-testid=mapping-fold]")
    check(abs(rect(page, "[data-testid=log]")["height"] - log_after) <= TOLERANCE, "unfolding the log restores its dragged height", rect(page, "[data-testid=log]")["height"])

    # WHY set these aside: the source highlighter shows an archived web page in a sandboxed, script-free srcdoc frame
    # whose CSP blocks every fetch (source-highlighter ARCHIVED_PAGE_CSP), so the page's own scripts and assets are
    # refused on purpose and Chromium logs each refusal. Those are the sandbox working; anything else fails.
    sandboxed = [error for error in errors if "violates the following Content Security Policy directive" in error
                 or "Blocked script execution in 'about:srcdoc'" in error or "from frame with URL about:srcdoc" in error]
    app_errors = [error for error in errors if error not in sandboxed]
    RESULTS["console_errors"] = app_errors
    RESULTS["archived_page_refusals"] = len(sandboxed)
    check(not app_errors, "no console errors over the whole journey", app_errors[:3] or f"{len(sandboxed)} archived-page refusals by the sandbox, set aside")
    context.close()


PDF_SHA = hashlib.sha256(PDF.read_bytes()).hexdigest() if PDF.exists() else ""
# The maker's file name, as a person would drop it (the bundle keeps it as source.pdf).
UPLOAD = {"name": "LRS-350-SPEC.pdf", "mimeType": "application/pdf", "buffer": PDF.read_bytes()} if PDF.exists() else None
CUBEMARS_HTML = BUNDLE.parent / "04e9b6e63a10d733533f0677b8daa6c8b4f490573a233e4eaae6259e1d5b11f2" / "source.html"
CUBEMARS_UPLOAD = {"name": "ak80-9-v3-0-robotic-actuator.html", "mimeType": "text/html", "buffer": CUBEMARS_HTML.read_bytes()} if CUBEMARS_HTML.exists() else None


# ---- the hero video and screenshots -------------------------------------------------------------------------------

def hero(browser, server: Server) -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    video_dir = pathlib.Path(tempfile.mkdtemp(prefix="sources-hero-"))
    size = {"width": 1280, "height": 800}
    context = browser.new_context(viewport=size, record_video_dir=str(video_dir), record_video_size=size)
    page = context.new_page()
    started = time.monotonic()
    page.goto(server.url + "#/add")
    page.wait_for_selector("[data-testid=category] option", state="attached")
    page.wait_for_timeout(600)
    page.set_input_files("[data-testid=file-input]", UPLOAD)
    page.wait_for_timeout(400)
    page.click("[data-testid=urls]")
    page.keyboard.type(CUBEMARS_URL, delay=4)
    page.wait_for_timeout(500)
    page.screenshot(path=str(MEDIA / "1-add-sources.png"))
    page.click("[data-testid=submit]")
    page.wait_for_selector("[data-testid=queue] [data-testid=batch]")
    page.wait_for_function("""() => [...document.querySelectorAll('[data-testid=queue-source]')].some((n) => n.dataset.stage === 'extracted')""", timeout=30000)
    page.wait_for_timeout(900)
    page.screenshot(path=str(MEDIA / "2-queue.png"))
    page.click("[data-testid=queue-source][data-stage=extracted] [data-open]")
    wait_ready(page)
    page.wait_for_timeout(700)
    power = page.evaluate("() => [...document.querySelectorAll('tr.r')].find((n) => n.querySelector('td.f').textContent === 'Rated power').dataset.id")
    page.click(f"{row_selector(power)} td.f")
    page.wait_for_timeout(1300)
    page.click(f"{row_selector(power)} [data-testid=value]")
    page.wait_for_timeout(300)
    page.keyboard.type("350", delay=120)
    page.wait_for_timeout(250)
    page.keyboard.press("Enter")
    page.wait_for_timeout(1100)
    tolerance = page.evaluate("() => [...document.querySelectorAll('tr.r')].find((n) => n.querySelector('td.f').textContent === 'Voltage tolerance').dataset.id")
    page.click(f"{row_selector(tolerance)} td.f")
    page.wait_for_timeout(700)
    page.keyboard.press("x")
    page.wait_for_function("() => document.querySelectorAll('[data-testid=log-item]').length === 2")
    page.wait_for_timeout(400)
    page.hover("[data-testid=log] li[data-op=reject]")
    page.wait_for_timeout(1500)
    page.screenshot(path=str(MEDIA / "3-review.png"))
    RESULTS["hero_seconds"] = round(time.monotonic() - started, 1)
    video = page.video.path()
    context.close()
    webm = MEDIA / "hero.webm"
    shutil.move(video, webm)
    shutil.rmtree(video_dir, ignore_errors=True)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(webm), "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(MEDIA / "hero.mp4")], check=False)
    print(f"media: {MEDIA}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--server", choices=["stub", "real"], default="stub")
    parser.add_argument("--media", action="store_true")
    parser.add_argument("--step", type=float, default=0.8, help="stub: seconds per simulated stage")
    args = parser.parse_args()
    vendor_hashes()
    home = pathlib.Path(tempfile.mkdtemp(prefix="sources-journey-"))
    server = Server(args.server, home, args.step)
    print(f"server: {args.server} {server.url} (pid {server.process.pid}, home {home})", flush=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                journey(browser, server, Disk(home), args.server)
                if args.media:  # a fresh home, so the hero's log starts empty
                    hero_home = pathlib.Path(tempfile.mkdtemp(prefix="sources-hero-home-"))
                    hero_server = Server(args.server, hero_home, args.step)
                    try:
                        hero(browser, hero_server)
                    finally:
                        hero_server.stop()
                        shutil.rmtree(hero_home, ignore_errors=True)
            finally:
                browser.close()
    finally:
        server.stop()
        shutil.rmtree(home, ignore_errors=True)
    RESULTS["server"] = args.server
    RESULTS["failures"] = FAILURES
    MEDIA.mkdir(parents=True, exist_ok=True)
    (MEDIA / f"journey-results-{args.server}.json").write_text(json.dumps(RESULTS, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in RESULTS.items() if key != "console_errors"}, indent=1, ensure_ascii=False))
    print(f"{'FAILED' if FAILURES else 'PASSED'}: {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
