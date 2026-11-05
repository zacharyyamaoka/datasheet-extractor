// The left pane: the Source Highlighter (source-highlighter/CONTRACT.md §4.2) drawing the captured source and the
// values' groundings. Ported from Markdownify's viewer (src/mdify/viewer/source.js): this file owns the module's
// lifecycle, zoom, and turning a drag on the source into a drawn box; rendering and geometry are the highlighter's.

import { highlight } from "./vendor/source-highlighter.js";
import { h, $ } from "./util.js";

const PDFJS = new URL("./vendor/pdfjs/pdf.min.mjs", import.meta.url).href;
const MIN_DRAWN = 4; // px: a smaller drag is a click, not a box

/** Which renderer a bundle's source needs, and its file name in the bundle, from index.json's media type. */
export function sourceFileOf(mediaType) {
  if (mediaType === "application/pdf") return { kind: "pdf", name: "source.pdf" };
  if (mediaType === "text/html") return { kind: "html", name: "source.html" };
  if (mediaType?.startsWith("image/")) return { kind: "image", name: `source.${mediaType.slice(6).replace("jpeg", "jpg")}` };
  return { kind: "text", name: "source.txt" };
}

/**
 * Draw `source` ({kind, url}) into `host`. `onDraw(location)` hears a box dragged on a page while drawing is on
 * (`setDrawing(true)`): {page, box: [x0, y0, x1, y1]}, 0–1 of the page, origin top-left (source-highlighter §2).
 * Returns {show(layers, focusId), setDrawing(on), ready}.
 */
export function sourcePane(host, { source, onDraw }) {
  host.innerHTML = `<div class="src-bar"><button type="button" class="btn ghost zb" data-zoom="out" title="Zoom out" aria-label="Zoom out">−</button>
      <span class="zl" data-testid="zoom-level"></span><button type="button" class="btn ghost zb" data-zoom="in" title="Zoom in" aria-label="Zoom in">+</button>
      <button type="button" class="btn ghost" data-zoom="fit" title="Fit the page to the pane">Fit</button><span class="sp"></span>
      <span class="src-hint" data-hint></span></div><div class="src-stage" data-testid="source-pane"></div>`;
  const stage = $(".src-stage", host);
  const level = $("[data-testid=zoom-level]", host);
  const hint = $("[data-hint]", host);
  stage.dataset.state = "loading";
  let handle = null;
  let latest = { layers: [], focusId: null };
  let drawing = false;

  const ready = (async () => {
    const spec = source.kind === "html" || source.kind === "text"
      ? { kind: source.kind, [source.kind]: await (await fetch(source.url)).text() }
      : { kind: source.kind, url: source.url };
    handle = highlight(stage, { source: spec, layers: [], pdfjs: PDFJS, zoom: "fit" });
    await handle.ready;
    if (source.kind === "html") {
      // WHY a catcher over the archived page: it is a sandboxed, script-free iframe, so a drag inside it never
      // reaches this document. A transparent layer over it takes the pointer; the marks still draw above it.
      for (const page of stage.querySelectorAll(".sh-page")) {
        const catcher = h("div", "catcher");
        page.appendChild(catcher);
      }
    }
    await handle.update({ layers: latest.layers });
    level.textContent = `${Math.round(handle.zoom * 100)}%`;
    stage.dataset.state = "ready";
  })();
  ready.catch((error) => {
    stage.dataset.state = "failed";
    stage.replaceChildren(h("p", "pane-failed", `The source could not be drawn: ${String(error?.message ?? error)}`));
  });

  // WHY a queue: marks change through `update`, never by drawing the source again, and two quick clicks must land
  // in order — the second click's marks are the ones left on screen.
  let queue = ready;
  let serial = 0;
  function show(layers, focusId) {
    latest = { layers, focusId };
    const mine = ++serial;
    queue = queue.then(async () => {
      if (latest.layers !== layers) return; // a newer ask is queued behind this one
      await handle.update({ layers });
      if (focusId) await handle.focus(focusId, { behavior: "auto" });
      stage.dataset.shown = String(mine); // what a test waits on: these marks are drawn
    }).catch(() => {});
    return queue;
  }

  function zoomTo(zoom) {
    stage.dataset.state = "zooming";
    // WHY focus() with an id no mark has: it is the module's one public door to a new zoom after render.
    queue = queue.then(() => handle?.focus("\u0000zoom", { zoom })).catch(() => {}).then(() => {
      level.textContent = `${Math.round((handle?.zoom || 1) * 100)}%`;
      stage.dataset.state = "ready";
    });
    return queue;
  }
  for (const button of host.querySelectorAll("[data-zoom]")) {
    button.addEventListener("click", () => {
      const step = button.dataset.zoom;
      if (step === "fit") zoomTo("fit");
      else zoomTo(Math.max(0.25, Math.min(4, (handle?.zoom || 1) * (step === "in" ? 1.25 : 0.8))));
    });
  }

  // Fit the page to the pane again once a drag of the boundary settles (the highlighter fits at render time only).
  let timer = 0;
  let width = stage.clientWidth;
  new ResizeObserver(() => {
    if (Math.abs(stage.clientWidth - width) < 2) return;
    width = stage.clientWidth;
    window.clearTimeout(timer);
    timer = window.setTimeout(() => zoomTo("fit"), 200);
  }).observe(stage);

  // ---- drawing a box: the citation for a human row ------------------------------------------------------------
  stage.addEventListener("pointerdown", (event) => {
    if (!drawing || event.button !== 0) return;
    const page = event.target.closest(".sh-page");
    if (!page) return;
    event.preventDefault();
    stage.setPointerCapture(event.pointerId);
    const rect = page.getBoundingClientRect();
    const start = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    const ghost = h("div", "drawn");
    page.appendChild(ghost);
    const clampX = (x) => Math.min(Math.max(x, 0), rect.width);
    const clampY = (y) => Math.min(Math.max(y, 0), rect.height);
    let box = null;
    const onMove = (moved) => {
      const x = clampX(moved.clientX - rect.left);
      const y = clampY(moved.clientY - rect.top);
      box = { x0: Math.min(start.x, x), y0: Math.min(start.y, y), x1: Math.max(start.x, x), y1: Math.max(start.y, y) };
      Object.assign(ghost.style, { left: `${box.x0}px`, top: `${box.y0}px`, width: `${box.x1 - box.x0}px`, height: `${box.y1 - box.y0}px` });
    };
    const onUp = () => {
      stage.removeEventListener("pointermove", onMove);
      stage.removeEventListener("pointerup", onUp);
      stage.removeEventListener("pointercancel", onUp);
      ghost.remove();
      if (!box || box.x1 - box.x0 < MIN_DRAWN || box.y1 - box.y0 < MIN_DRAWN) return;
      const round = (n) => Math.round(n * 100000) / 100000;
      onDraw?.({
        page: Number(page.dataset.page),
        box: [round(box.x0 / rect.width), round(box.y0 / rect.height), round(box.x1 / rect.width), round(box.y1 / rect.height)],
      });
    };
    stage.addEventListener("pointermove", onMove);
    stage.addEventListener("pointerup", onUp);
    stage.addEventListener("pointercancel", onUp);
  });

  function setDrawing(on, message) {
    drawing = on;
    stage.classList.toggle("drawing", on);
    hint.textContent = on ? message || "Drag a box on the source to cite it" : "";
  }

  return { show, setDrawing, ready, stage };
}
