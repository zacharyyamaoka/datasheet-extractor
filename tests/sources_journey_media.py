"""The Sources app's evidence — hero video and three screenshots — recorded from the REAL `extract serve` in a temp
--home (never the stub), into reports/media/sources-app/ (excluded from git):

    uv run --directory /home/bam/markdownify python /home/bam/datasheet-extractor/tests/sources_journey_media.py

One batch: the Mean Well LRS-350 PDF as `psu` (open mode) and the CubeMars AK80-9 URL as `actuator`, kept as
html + snapshot; a product created inline; LRS-350-24 mapped to it; one value edited and one struck; the version log.
The hero is the real session cut to 10–20 s: adding the sources at real speed, the queue's minutes of capture,
conversion and model calls sped up (and labelled so), then the review at real speed. Every process it starts is
stopped by its own pid.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from sources_journey import CUBEMARS_URL, MEDIA, UPLOAD, Server, row_selector, wait_ready  # noqa: E402

SIZE = {"width": 1280, "height": 800}
PRODUCT = "Mean Well LRS-350-24"
QUEUE_SECONDS = 4.0          # the queue segment's length in the hero, however long the real queue took
FONT = pathlib.Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
RESULTS: dict[str, object] = {}


def row_by_label(page, *labels: str) -> str | None:
    """The id of the first row whose field label is one of `labels` (as printed, any case)."""
    return page.evaluate("""(labels) => { const want = labels.map((l) => l.toLowerCase());
        const tr = [...document.querySelectorAll('tr.r')].find((n) => want.includes(n.querySelector('td.f').textContent.trim().toLowerCase()));
        return tr ? tr.dataset.id : null; }""", list(labels))


def record(browser, server: Server, home: pathlib.Path) -> tuple[pathlib.Path, dict]:
    video_dir = pathlib.Path(tempfile.mkdtemp(prefix="sources-media-video-"))
    context = browser.new_context(viewport=SIZE, record_video_dir=str(video_dir), record_video_size=SIZE)
    page = context.new_page()
    start = time.monotonic()
    marks: dict[str, float] = {}
    mark = lambda name: marks.__setitem__(name, time.monotonic() - start)  # noqa: E731

    # Add Sources: the PDF + the URL, a product created inline, psu for the batch, html + snapshot kept
    page.goto(server.url + "#/add")
    page.wait_for_selector("[data-testid=category] option", state="attached")
    page.wait_for_timeout(500)
    page.set_input_files("[data-testid=file-input]", UPLOAD)
    page.wait_for_timeout(300)
    page.click("[data-testid=urls]")
    page.keyboard.type(CUBEMARS_URL, delay=3)
    page.click("[data-testid=add-product-search]")
    page.keyboard.type(PRODUCT, delay=12)
    page.wait_for_selector(".psearch-list li.create")
    page.click(".psearch-list li.create")
    page.wait_for_selector("[data-testid=add-products] .prod")
    page.select_option("[data-testid=category]", "psu")
    page.check("[data-testid=keep-snapshot]")
    page.wait_for_timeout(500)
    page.screenshot(path=str(MEDIA / "1-add-sources.png"))
    page.click("[data-testid=submit]")
    page.wait_for_selector("[data-testid=queue] [data-testid=batch]")
    mark("queued")

    # the URL is the actuator: its row's category picker, while it is still being captured
    page.wait_for_function("() => [...document.querySelectorAll('[data-testid=queue-source]')].some((n) => n.querySelector('.kind').textContent === 'url' && n.querySelector('[data-category]'))")
    url_id = page.evaluate("() => [...document.querySelectorAll('[data-testid=queue-source]')].find((n) => n.querySelector('.kind').textContent === 'url').dataset.source")
    with page.expect_response(lambda response: "/extract" in response.url) as answered:
        page.select_option(f'[data-category="{url_id}"]', "actuator")
    RESULTS["url_category_set"] = answered.value.status

    deadline = time.monotonic() + 1500
    while time.monotonic() < deadline:
        stages = page.evaluate("() => [...document.querySelectorAll('[data-testid=queue-source]')].map((n) => [n.dataset.stage, Boolean(n.querySelector('[data-testid=running]'))])")
        if stages and all(stage in ("extracted", "failed") and not running for stage, running in stages):
            break
        time.sleep(1)
    RESULTS["queue"] = page.evaluate("""() => [...document.querySelectorAll('[data-testid=queue-source]')].map((n) => ({name: n.querySelector('.name').textContent,
        stage: n.dataset.stage, error: n.querySelector('[data-testid=error]')?.textContent || null}))""")
    page.wait_for_timeout(1600)
    page.screenshot(path=str(MEDIA / "2-queue.png"))
    mark("queue_done")
    RESULTS["queue_seconds"] = round(marks["queue_done"] - marks["queued"], 1)

    # the LRS-350 tab: map LRS-350-24 to the product (off camera: the hero cuts from the queue to the click)
    pdf_id = page.evaluate("() => [...document.querySelectorAll('[data-testid=queue-source]')].find((n) => n.querySelector('.name').textContent === 'LRS-350-SPEC.pdf').dataset.source")
    page.click(f'[data-testid=queue-source][data-source="{pdf_id}"] [data-open]')
    wait_ready(page)
    product_id = next(p["id"] for p in json.loads(page.evaluate("async () => JSON.stringify(await (await fetch('/api/products')).json())")) if p["title"] == PRODUCT)
    page.locator("[data-testid=mapping] .mp").filter(has=page.get_by_text("LRS-350-24", exact=True)).locator("[data-map]").click()
    page.fill("[data-testid=map-search]", product_id)
    page.wait_for_selector(".pop .psearch-list li:not(.create)")
    page.click(".pop .psearch-list li:not(.create)")
    page.wait_for_selector("[data-testid=log-item][data-op=map]")
    page.mouse.move(900, 780)
    page.wait_for_timeout(600)
    mark("review")

    # click a value → spotlight; edit it; strike another; the log shows both
    power = row_by_label(page, "RATED POWER", "Rated power")
    struck = row_by_label(page, "VOLTAGE TOLERANCE", "Voltage tolerance", "RIPPLE & NOISE (max.)") or page.evaluate("() => document.querySelectorAll('tr.r')[1].dataset.id")
    page.click(f"{row_selector(power)} td.f")
    page.wait_for_timeout(1400)
    page.click(f"{row_selector(power)} [data-testid=value]")
    page.wait_for_timeout(300)
    page.keyboard.press("Control+a")
    page.keyboard.type("350", delay=120)
    page.wait_for_timeout(250)
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-testid=log-item][data-op=edit]")
    page.wait_for_timeout(900)
    page.click(f"{row_selector(struck)} td.f")
    page.wait_for_timeout(700)
    page.keyboard.press("x")
    page.wait_for_selector("[data-testid=log-item][data-op=reject]")
    page.wait_for_timeout(400)
    page.hover("[data-testid=log] li[data-op=reject]")
    page.wait_for_timeout(1500)
    page.screenshot(path=str(MEDIA / "3-review.png"))
    mark("end")

    ops = json.loads((home / "sources" / pdf_id / "review.json").read_text(encoding="utf-8"))["ops"]
    RESULTS["ops"] = [{key: op.get(key) for key in ("op", "value_id", "model", "product", "from", "to", "author")} for op in ops]
    RESULTS["provenance_shown"] = page.evaluate(f"() => document.querySelector('{row_selector(power)} .pv.hu')?.textContent")
    RESULTS["pdf_category"] = json.loads((home / "sources" / pdf_id / "source.json").read_text(encoding="utf-8")).get("category")
    url_dir = url_source(home)
    RESULTS["url_source"] = {key: json.loads((url_dir / "source.json").read_text(encoding="utf-8")).get(key) for key in ("capture_keep", "category", "stage")} if url_dir else None
    video = pathlib.Path(page.video.path())
    context.close()
    return video, marks


def url_source(home: pathlib.Path) -> pathlib.Path | None:
    for path in (home / "sources").glob("*/source.json"):
        if json.loads(path.read_text(encoding="utf-8")).get("kind") == "url":
            return path.parent
    return None


def cut(video: pathlib.Path, marks: dict) -> None:
    """Real-time add, the queue sped up to QUEUE_SECONDS (labelled with its real length), real-time review."""
    queue_start, queue_end = marks["queued"] + 1.0, marks["queue_done"]
    speed = max(1.0, (queue_end - queue_start) / QUEUE_SECONDS)
    real = f"{int(queue_end - queue_start) // 60}:{int(queue_end - queue_start) % 60:02d}"
    label = (f",drawtext=fontfile={FONT}:text='queue · {real.replace(":", "\\:")} real time at {speed:.0f}x':x=w-tw-24:y=h-th-20:fontsize=20:"
             "fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=8") if FONT.exists() else ""
    graph = (f"[0:v]trim=0:{queue_start},setpts=PTS-STARTPTS[a];"
             f"[0:v]trim={queue_start}:{queue_end},setpts=(PTS-STARTPTS)/{speed}{label}[b];"
             f"[0:v]trim={marks['review']}:{marks['end']},setpts=PTS-STARTPTS[c];[a][b][c]concat=n=3:v=1[out]")
    for name, codec in (("hero.mp4", ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]), ("hero.webm", ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "34"])):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-filter_complex", graph, "-map", "[out]", "-an", *codec, str(MEDIA / name)], check=True)
    duration = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(MEDIA / "hero.mp4")],
                              capture_output=True, text=True, check=True).stdout.strip()
    RESULTS["hero_seconds"] = round(float(duration), 1)
    RESULTS["queue_speedup"] = round(speed, 1)


def main() -> int:
    MEDIA.mkdir(parents=True, exist_ok=True)
    home = pathlib.Path(tempfile.mkdtemp(prefix="sources-media-home-"))
    server = Server("real", home, 0)
    print(f"server: real {server.url} (pid {server.process.pid}, home {home})", flush=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                video, marks = record(browser, server, home)
            finally:
                browser.close()
        cut(video, marks)
        shutil.rmtree(video.parent, ignore_errors=True)
    finally:
        server.stop()
        shutil.rmtree(home, ignore_errors=True)
    (MEDIA / "media-results-real.json").write_text(json.dumps(RESULTS, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(RESULTS, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
