// The review tab (CONTRACT §6): the source on the left, its values on the right, every change one review.json op
// (Markdownify CONTRACT §7.1) POSTed to /api/sources/<id>/review. Ported from the 2026-10-29 review-view round
// (reports/media/review-view-proposals-2026-10-29/src/): V5 Stream is the base — the provenance column, j/k, type to
// replace, x strike/restore, a add, p products, the version log docked at the bottom; V4's mapping step on top;
// V3's per-product columns as the "By product" view. Edit in place, no Fix button, no modal; untouched = accepted.

import {
  STAGES, api, $, $$, categories, runningText, stopped, categoryOptions, copyText, day, deepEqual, esc, fieldLabel, foldable, formatValue, h, initials, moment,
  parseTyped, readStored,
  splitter, toast, writeStored,
} from "./util.js";
import { sourceFileOf, sourcePane } from "./source-pane.js";
import { allProducts, productSearch, productTitle } from "./products.js";

const groundingsOf = (row) => (Array.isArray(row?.grounding) ? row.grounding : row?.grounding ? [row.grounding] : []);
const slug = (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
const normal = (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, "");
/** One model named two ways: a table header ("AK80-9 V3.0 KV100") and the name a product gave it ("AK80-9"). */
const sameModel = (a, b) => normal(a) === normal(b) || normal(a).startsWith(normal(b)) || normal(b).startsWith(normal(a));

/**
 * The models a datasheet covers: the header cells along the axis where the AI read the part's model (one column per
 * model in a comparison table), exactly as printed. Without such a table it is the one model the AI read, if any.
 */
export function coveredModels(index, markdown, part) {
  const read = part?.model?.value != null ? String(part.model.value) : null;
  const blocks = index?.blocks || [];
  const byId = new Map(blocks.map((block) => [block.id, block]));
  const cell = byId.get(part?.model?.cite?.id);
  if (cell?.type === "table_cell" && (cell.kind === "columnHeader" || cell.kind === "rowHeader")) {
    const table = blocks.find((block) => block.type === "table" && (block.children || []).includes(cell.id));
    const characters = Array.from(markdown || ""); // ranges count code points (Markdownify CONTRACT §3)
    const text = (block) => characters.slice(block.range.start, block.range.end).join("").trim();
    const along = (table?.children || []).map((id) => byId.get(id))
      .filter((other) => other && other.kind === cell.kind && (cell.kind === "columnHeader" ? other.row === cell.row : other.col === cell.col));
    const names = along.map(text).filter(Boolean);
    if (names.length) return { models: names, read };
  }
  return { models: read ? [read] : [], read };
}

export function reviewTab(host, { sourceId, batch, onChanged }) {
  let data = null;        // GET /api/sources/<id>: {source, values, review, effective}
  let rows = [];          // the effective values as the table shows them
  let dropped = [];       // values the checker dropped: kept in values.json, listed, not reviewed
  let cur = null;         // the focused row's id
  let editing = null;     // the inline editor's {close}
  let adding = null;      // the add form's {pending grounding}
  let pane = null;
  let index = null;
  let drawable = false;   // a box can be drawn: the source is pages (PDF, image), not an archived web page
  let markdown = "";
  let view = readStored("view", "rows");
  let poll = 0;
  let destroyed = false;
  let pop = null;
  let categoryList = []; // GET /api/fields

  host.innerHTML = `<div class="rv" data-testid="review"><div class="rv-head"></div><div class="rv-ws">
      <div class="rv-left"></div>
      <div class="split" role="separator" aria-orientation="vertical" aria-label="Resize the source and the values" tabindex="0" data-testid="split-main"></div>
      <div class="rv-right"></div></div></div>`;
  const head = $(".rv-head", host);
  const ws = $(".rv-ws", host);
  const left = $(".rv-left", host);
  const right = $(".rv-right", host);
  splitter($("[data-testid=split-main]", host), left, {
    key: "split:source", min: 260, max: () => ws.clientWidth - 380, fallback: () => Math.round(ws.clientWidth * 0.46),
  });

  // ---- the record ----------------------------------------------------------------------------------------------------
  function derive() {
    const original = Object.fromEntries((data.values?.values || []).map((value) => [value.id, value]));
    const ops = data.review?.ops || [];
    const all = data.effective?.values || [];
    dropped = all.filter((value) => !value.human && value.verdict === "dropped").map((value) => ({ ...value, label: fieldLabel(value, original[value.id], categoryList) }));
    rows = all.filter((value) => value.human || value.verdict !== "dropped").map((value) => {
      const ai = value.human ? null : original[value.id] || null;
      const acts = ops.filter((op) => op.op !== "assign" && op.op !== "unassign"
        && (op.value_id === value.id || (op.op === "add" && op.row?.id === value.id)));
      const edited = Boolean(ai) && (!deepEqual(value.value, ai.value) || (value.unit || "") !== (ai.unit || ""));
      const added = value.human ? ops.find((op) => op.op === "add" && op.row?.id === value.id) || null : null;
      // WHY the label from values.json / the add op: `effective` carries the fields an op can change, not the label.
      return {
        ...value, label: added?.row?.label || fieldLabel(value, ai, categoryList), ai, edited, last: acts.at(-1) || null,
        added, products: value.products || [], rejected: Boolean(value.rejected),
      };
    });
  }
  const row = (id) => rows.find((candidate) => candidate.id === id) || null;

  async function load() {
    data = await api.source(sourceId);
    if (destroyed) return;
    derive();
  }

  /** One §7.1 op to the server (it stamps author and at), then the record as the server now computes it. */
  async function send(op) {
    try {
      await api.review(sourceId, op);
      await load();
      draw();
      onChanged?.();
      return true;
    } catch (error) {
      toast(`Not saved: ${error.message}`, "error");
      return false;
    }
  }

  // ---- header: what this source is, the view toggle and the three actions --------------------------------------------
  function drawHead() {
    const source = data.source;
    const kind = index ? sourceFileOf(index.source?.media_type).kind : source.kind;
    const retrieved = data.values?.source?.retrieved_at;
    const sha = source.sha256 || data.values?.source?.sha256;
    const assigned = rows.some((value) => value.products.length && !value.rejected);
    const kept = rows.some((value) => !value.rejected);
    const extracted = Boolean(data.values);
    // WHY no extraction controls on a link-only source: nothing was stored to read, and POST …/extract answers 409.
    const linkOnly = source.capture_keep === "link";
    head.innerHTML = `<span class="kind">${esc(kind)}</span><h1 title="${esc(source.name)}">${esc(source.name)}</h1>
      <span class="meta">${source.url ? `<a href="${esc(source.url)}" target="_blank" rel="noreferrer noopener">${esc(new URL(source.url).host)}</a> · ` : ""}${retrieved ? `retrieved ${esc(day(retrieved))} · ` : ""}${sha ? `sha256 <code>${esc(sha.slice(0, 8))}…</code> · ` : ""}<span class="stage${source.stage === "failed" ? " failed" : ""}" data-testid="source-stage">${esc(source.stage)}</span>${source.running ? ` <span class="running" data-testid="source-running">${esc(runningText(source.running))}</span>` : source.note ? ` <span class="running" data-testid="source-note-text">${esc(source.note)}</span>` : ""}</span>
      <span class="sp"></span>
      ${extracted ? `<div class="seg" role="group" aria-label="View"><button type="button" data-view="rows" aria-pressed="${view === "rows"}">Rows</button><button type="button" data-view="product" aria-pressed="${view === "product"}">By product</button></div>` : ""}
      ${linkOnly ? "" : `<label class="cat" title="What to extract from this source; changing it runs the extraction again"><span class="n">Extract</span>
        <select class="in" data-testid="source-category">${categoryOptions(categoryList, source.category || batch?.category)}</select></label>`}
      <button type="button" class="btn ghost" data-testid="copy-prompt" title="A prompt any AI agent can run to extract this source">Copy prompt</button>
      ${linkOnly ? "" : `<button type="button" class="btn ghost" data-testid="run-extraction" ${source.bundle ? "" : "disabled title=\"not converted yet\""}>Run extraction</button>`}
      <button type="button" class="btn ghost" data-testid="copy-spec" ${kept ? "title=\"The kept rows as Spec(...) lines, ready to paste into a robotparts part file\"" : "disabled title=\"no rows are kept\""}>Copy as Spec</button>
      <button type="button" class="btn pri" data-testid="publish" ${assigned ? "" : "disabled title=\"assign a product first\""}>Publish</button>`;
    for (const button of $$("[data-view]", head)) {
      button.addEventListener("click", () => {
        view = button.dataset.view;
        writeStored("view", view);
        draw();
      });
    }
    // A source's own category (CONTRACT §2, §4): saved to source.json, then the extraction runs again with it.
    $("[data-testid=source-category]", head)?.addEventListener("change", async (event) => {
      const category = event.target.value;
      try {
        const result = await api.extract(sourceId, { category });
        toast(result?.queued ? `Extracting again as ${category}` : `Category set to ${category}; it extracts after conversion`);
        await load();
        drawHead();
        watch();
        onChanged?.();
      } catch (error) {
        toast(`Category not changed: ${error.message}`, "error");
        drawHead();
      }
    });
    $("[data-testid=copy-prompt]", head).addEventListener("click", async () => {
      try {
        await copyText(await api.prompt(sourceId), "Prompt");
      } catch (error) {
        toast(`No prompt: ${error.message}`, "error");
      }
    });
    $("[data-testid=run-extraction]", head)?.addEventListener("click", async () => {
      try {
        await api.extract(sourceId);
        toast("Extraction started");
        await load();
        drawAll();
        watch();
      } catch (error) {
        toast(`Could not start: ${error.message}`, "error");
      }
    });
    // WHY the server builds the text (sources/spec.py): the CLI `extract as-spec` prints the very same block, and the
    // part file Zach pastes it into is the source of truth, so the app stops at paste-ready lines.
    $("[data-testid=copy-spec]", head).addEventListener("click", async () => {
      try {
        const { text, count } = await api.spec(sourceId);
        if (!count) return toast("No rows are kept", "error");
        await copyText(text, "Specs", `Copied ${count} spec${count === 1 ? "" : "s"}`);
      } catch (error) {
        toast(`No specs: ${error.message}`, "error");
      }
    });
    $("[data-testid=publish]", head).addEventListener("click", async () => {
      try {
        const result = await api.publish(sourceId);
        const count = result?.published?.length;
        toast(count != null ? `Published for ${count} product${count === 1 ? "" : "s"}` : "Published");
        await load();
        drawHead();
        onChanged?.();
      } catch (error) {
        toast(`Not published: ${error.message}`, "error");
      }
    });
  }

  /** Poll while the pipeline is working on this source; draw the values once they exist. */
  function watch() {
    clearTimeout(poll);
    // WHY `running` too: a re-extraction keeps the stage at `extracted` while its job runs (CONTRACT §3).
    const busy = (!["extracted", "published", "failed"].includes(data.source.stage) && !stopped(data.source)) || Boolean(data.source.running);
    if (!busy || destroyed) return;
    poll = setTimeout(async () => {
      const before = `${data.source.stage}/${data.source.running}`;
      await load().catch(() => {});
      if (destroyed) return;
      if (`${data.source.stage}/${data.source.running}` !== before) {
        await drawAll();
        onChanged?.();
      }
      watch();
    }, 1200);
  }

  // ---- the source pane ---------------------------------------------------------------------------------------------
  async function mountSource() {
    if (pane || !data.source.bundle || data.source.capture_keep === "link") {
      if (!data.source.bundle || data.source.capture_keep === "link") left.innerHTML = `<div class="src-wait">${data.source.note ? esc(data.source.note) : "The source appears here once it is captured."}</div>`;
      return;
    }
    try {
      index = await (await fetch(api.file(sourceId, "index.json"))).json();
      markdown = await (await fetch(api.file(sourceId, "document.md"))).text();
    } catch {
      index = null;
    }
    if (!index) {
      left.innerHTML = `<div class="src-wait">${data.source.capture_keep === "link" ? "Link only: no copy of the page was kept." : "The source is not converted yet."}</div>`;
      return;
    }
    const file = sourceFileOf(index.source?.media_type);
    left.innerHTML = "";
    const holder = h("div", "src");
    left.appendChild(holder);
    // WHY no drawing on a web page: a drawn box is a page/box Location, which the highlighter cannot place on HTML
    // (source-highlighter CONTRACT §2); a web page is cited by its text instead.
    drawable = file.kind === "pdf" || file.kind === "image";
    pane = sourcePane(holder, { source: { kind: file.kind, url: api.file(sourceId, file.name) }, onDraw: drawn });
    const note = h("div", "src-note");
    note.dataset.testid = "source-note";
    note.hidden = true;
    holder.appendChild(note);
  }

  function layers() {
    const located = rows.filter((value) => groundingsOf(value).length);
    const at = (value, id) => groundingsOf(value).map((location) => ({ ...location, id: id || value.id, label: value.label }));
    const list = [
      { locations: located.filter((value) => !value.last && !value.human).flatMap((value) => at(value, `all:${value.id}`)), style: { name: "highlight", color: "#3d5a80", opacity: 0.1, padding: 1 } },
      { locations: located.filter((value) => (value.edited || value.human) && !value.rejected).flatMap((value) => at(value, `all:${value.id}`)), style: { name: "highlight", color: "#1e7d3a", opacity: 0.28, padding: 1 } },
      { locations: located.filter((value) => value.rejected).flatMap((value) => at(value, `all:${value.id}`)), style: { name: "highlight", color: "#b3261e", opacity: 0.2, padding: 1 } },
    ];
    const focused = row(cur);
    if (focused && groundingsOf(focused).length) {
      list.push({ locations: at(focused), style: { name: "spotlight", opacity: 0.42 } });
      list.push({ locations: at(focused), style: { name: "box", color: "#006699", width: 2.5 } });
    }
    if (adding?.grounding) {
      list.push({ locations: adding.grounding.map((location) => ({ ...location, id: "drawn" })), style: { name: "box", color: "#1e7d3a", width: 2 } });
    }
    return list;
  }

  function showSource(focusId) {
    if (!pane) return;
    const focused = row(cur);
    pane.show(layers(), focusId && focused && groundingsOf(focused).length ? focusId : null);
    const note = $("[data-testid=source-note]", left);
    if (note) {
      note.hidden = !focused;
      if (focused && !groundingsOf(focused).length) {
        note.className = "src-note none";
        const who = focused.added || focused.last;
        note.innerHTML = `<span class="av hu">${esc(initials(who?.author))}</span><span><b>No source cited.</b> ${esc(focused.label)} ${esc(formatValue(focused.value))}${focused.unit ? ` ${esc(focused.unit)}` : ""} was entered by ${esc(who?.author || "a person")}${who ? ` · ${esc(moment(who.at))}` : ""}${who?.note ? ` · “${esc(who.note)}”` : ""}.${drawable ? " Drag a box on the source to cite it." : ""}</span>`;
      } else if (focused) {
        note.className = "src-note cite";
        note.innerHTML = focused.human
          ? `<span class="av hu">${esc(initials(focused.added?.author))}</span><span>${esc(focused.added?.author || "A person")} cited this box for <b>${esc(focused.label)}</b></span>`
          : `<span class="av ai">AI</span><span>AI read <b class="raw">${esc(focused.raw)}</b> here${focused.cite?.id ? ` · <code>${esc(focused.cite.id)}</code>` : ""}${groundingsOf(focused)[0]?.page ? ` · page ${groundingsOf(focused)[0].page}` : ""}${focused.edited ? ` · <b class="now">now ${esc(formatValue(focused.value))}${focused.unit ? ` ${esc(focused.unit)}` : ""}</b>` : ""}${focused.rejected ? ` · <b class="gone">struck</b>` : ""}</span>`;
      }
    }
    // A human row with no citation yet takes a drawn box as its citation (§7.1: a Location the person drew).
    const citing = drawable && (adding || (focused?.human && !groundingsOf(focused).length));
    pane.setDrawing(Boolean(citing), adding ? "Drag a box on the source to cite the new row (optional)" : "Drag a box on the source to cite this row");
  }

  function drawn(location) {
    if (adding) {
      adding.grounding = [location];
      adding.redraw();
      showSource();
      return;
    }
    const focused = row(cur);
    if (focused?.human && !groundingsOf(focused).length) {
      send({ op: "edit", value_id: focused.id, field: "grounding", from: focused.grounding ?? null, to: [location] }).then(() => showSource(focused.id));
    }
  }

  // ---- the right pane --------------------------------------------------------------------------------------------
  function mountRight() {
    if (!data.values) {
      const history = (data.source.history || []).map((step) => `<li><span class="stage">${esc(step.stage)}</span> <span class="n">${esc(moment(step.at))}</span></li>`).join("");
      const reached = STAGES.indexOf(data.source.stage);
      right.innerHTML = `<div class="waiting" data-testid="waiting">${data.source.stage === "failed"
        ? `<p class="answer bad">Failed: ${esc(data.source.error || "no error given")}</p>`
        : stopped(data.source) ? `<p class="answer quiet" data-testid="stopped">${esc(data.source.note)}</p><p class="n">This source keeps only its link, so there is nothing to extract.</p>`
        : `<p class="answer">${esc(data.source.running ? runningText(data.source.running) : data.source.stage)} · the values appear here once extracted <span class="n">(${Math.max(reached, 0) + 1} of ${STAGES.length})</span></p>`}
        <ol class="history">${history}</ol></div>`;
      return;
    }
    right.innerHTML = `<section class="map" data-testid="mapping"><button type="button" class="fold" data-testid="mapping-fold" aria-expanded="true"><span class="chev" aria-hidden="true">▾</span><span data-maphead></span></button><div class="map-body" data-mapbody></div></section>
      <div class="rv-tools"><span class="keys"><kbd>j</kbd><kbd>k</kbd> move <kbd>0-9</kbd> type to replace <kbd>x</kbd> strike/restore <kbd>a</kbd> add <kbd>p</kbd> products</span><span class="sp"></span>
        <button type="button" class="btn sm ghost" data-testid="add-row">+ Add a row</button></div>
      <div class="rv-scroll" data-testid="values"><table class="vt"><thead></thead><tbody></tbody></table><div class="rv-add" data-addhost></div><div data-dropped></div></div>
      <div class="split h" role="separator" aria-orientation="horizontal" aria-label="Resize the version log" tabindex="0" data-testid="split-log"></div>
      <section class="log" data-testid="log"><button type="button" class="fold" data-testid="log-fold" aria-expanded="true"><span class="chev" aria-hidden="true">▾</span><span data-loghead></span></button><ol data-logitems></ol></section>`;
    const logSection = $("[data-testid=log]", right);
    const logSplit = $("[data-testid=split-log]", right);
    splitter(logSplit, logSection, { key: "split:log", axis: "y", sign: -1, min: 64, max: () => right.clientHeight - 160, fallback: () => 170 });
    const logFold = foldable("fold:log", true, (open) => {
      logSection.classList.toggle("folded", !open);
      logSplit.hidden = !open;
      $("[data-testid=log-fold]", right).setAttribute("aria-expanded", String(open));
    });
    $("[data-testid=log-fold]", right).addEventListener("click", () => logFold.toggle());
    const mapSection = $("[data-testid=mapping]", right);
    const mapFold = foldable("fold:mapping", true, (open) => {
      mapSection.classList.toggle("folded", !open);
      $("[data-testid=mapping-fold]", right).setAttribute("aria-expanded", String(open));
    });
    $("[data-testid=mapping-fold]", right).addEventListener("click", () => mapFold.toggle());
    $("[data-testid=add-row]", right).addEventListener("click", startAdd);
  }

  function provenance(value) {
    // WHY two weights: untouched rows read as accepted, quietly; a row a person acted on says who and when, bold.
    const act = value.added || value.last;
    if (act) {
      return `<span class="pv hu${value.rejected ? " rj" : ""}" title="${esc(act.author)} · ${esc(act.at)}"><span class="av hu">${esc(initials(act.author))}</span>${value.rejected ? "✕ " : ""}${esc(moment(act.at))}</span>`;
    }
    const at = data.values?.source?.retrieved_at;
    return `<span class="pv ai" title="values.json · ${esc(data.values?.extractor?.tool || "AI")}">AI${at ? ` · ${esc(day(at))}` : ""}</span>`;
  }

  function valueHtml(value) {
    const unit = value.unit ? ` <span class="u">${esc(value.unit)}</span>` : "";
    const shown = esc(formatValue(value.value));
    if (value.rejected) return `<span class="struck">${shown}</span>${unit}`;
    if (value.human) return `<span class="hv">${shown}</span>${unit}`;
    if (value.edited) {
      const was = `${formatValue(value.ai.value)}${value.ai.unit && value.ai.unit !== value.unit ? ` ${value.ai.unit}` : ""}`;
      return `<span class="hv">${shown}</span>${unit} <span class="was" data-testid="was">was ${esc(was)}</span>`;
    }
    return `<span>${shown}</span>${unit}`;
  }

  function printed(value) {
    if (value.human) return groundingsOf(value).length ? `<span class="n">box drawn on p.${esc(groundingsOf(value)[0].page ?? 1)}</span>` : `<span class="n">no source cited</span>`;
    return `<span class="raw">${esc(value.raw ?? "")}</span>`;
  }

  function productColumns() {
    const ids = new Set(batch?.products || []);
    for (const value of rows) for (const product of value.products) ids.add(product);
    return [...ids];
  }

  function drawTable() {
    const thead = $("thead", right);
    const tbody = $("tbody", right);
    if (view === "product") {
      const columns = productColumns();
      thead.innerHTML = `<tr><th class="cb"></th><th>Field</th><th>Value</th>${columns.map((id) => `<th class="pc" title="${esc(id)}">${esc(productTitle(id))}</th>`).join("")}
        <th class="pc add"><button type="button" class="btn sm ghost" data-testid="add-product-column" data-popper>+ product</button></th></tr>`;
      tbody.innerHTML = rows.map((value) => `<tr class="r${cur === value.id ? " cur" : ""}${value.rejected ? " rej" : ""}${value.human ? " hum" : ""}" data-id="${esc(value.id)}">
        <td class="cb"></td><td class="f">${esc(value.label)}</td><td class="val">${valueHtml(value)}</td>
        ${columns.map((id) => value.products.includes(id)
          ? `<td class="pc on"><button type="button" class="cellbtn" data-unassign="${esc(id)}" title="Applies to ${esc(id)} · click to take it off">${value.rejected ? "—" : "✓"}</button></td>`
          : `<td class="pc"><button type="button" class="cellbtn off" data-assign="${esc(id)}" title="Make this value apply to ${esc(id)}">+</button></td>`).join("")}<td class="pc"></td></tr>`).join("");
      $("[data-testid=add-product-column]", thead).addEventListener("click", (event) => openWholeMenu(event.currentTarget));
      for (const tr of $$("tr.r", tbody)) {
        const id = tr.dataset.id;
        tr.addEventListener("click", () => focusRow(id));
        for (const button of $$("[data-assign]", tr)) button.addEventListener("click", (event) => { event.stopPropagation(); send({ op: "assign", products: [button.dataset.assign], value_ids: [id] }); });
        for (const button of $$("[data-unassign]", tr)) button.addEventListener("click", (event) => { event.stopPropagation(); send({ op: "unassign", products: [button.dataset.unassign], value_ids: [id] }); });
      }
    } else {
      thead.innerHTML = `<tr><th class="cb"></th><th>Provenance</th><th>Field</th><th>Value</th><th>As printed</th><th>Applies to</th><th></th></tr>`;
      tbody.innerHTML = rows.map((value) => {
        const applies = value.products.length ? value.products.map((id) => `<span class="prod" title="${esc(id)}">${esc(productTitle(id))}</span>`).join(" ") : `<span class="n">—</span>`;
        const action = value.rejected
          ? `<button type="button" class="icon-btn" data-restore title="Restore (x)" aria-label="Restore">↺</button>`
          : `<button type="button" class="icon-btn" data-strike title="Strike (x)" aria-label="Strike">✕</button>`;
        return `<tr class="r${cur === value.id ? " cur" : ""}${value.rejected ? " rej" : ""}${value.human ? " hum" : ""}" data-id="${esc(value.id)}" data-testid="row">
          <td class="cb"></td><td class="pvc" data-testid="provenance">${provenance(value)}</td><td class="f">${esc(value.label)}</td>
          <td class="val" data-val data-testid="value">${valueHtml(value)}</td><td class="pr">${printed(value)}</td>
          <td class="ap">${applies} <button type="button" class="icon-btn" data-pmenu data-popper title="Products (p)" aria-label="Products">▾</button></td><td class="x">${action}</td></tr>`;
      }).join("");
      for (const tr of $$("tr.r", tbody)) {
        const id = tr.dataset.id;
        tr.addEventListener("click", () => focusRow(id));
        $("[data-val]", tr).addEventListener("click", (event) => {
          event.stopPropagation();
          focusRow(id);
          startEdit();
        });
        $("[data-strike]", tr)?.addEventListener("click", (event) => {
          event.stopPropagation();
          focusRow(id, true);
          send({ op: "reject", value_id: id });
        });
        $("[data-restore]", tr)?.addEventListener("click", (event) => {
          event.stopPropagation();
          focusRow(id, true);
          send({ op: "restore", value_id: id });
        });
        $("[data-pmenu]", tr).addEventListener("click", (event) => {
          event.stopPropagation();
          focusRow(id, true);
          openProductMenu();
        });
      }
    }
    $("[data-dropped]", right).innerHTML = dropped.length
      ? `<details class="dropped"><summary>${dropped.length} value${dropped.length === 1 ? "" : "s"} the checker dropped (kept in values.json, not reviewed here)</summary><ul>${dropped.map((value) => `<li><b>${esc(value.label)}</b>: raw <span class="raw">${esc(value.raw)}</span>${value.cite?.id ? `, <code>${esc(value.cite.id)}</code>` : ""}${value.reasons?.length ? ` — ${esc(value.reasons.join("; "))}` : ""}</li>`).join("")}</ul></details>`
      : "";
  }

  // ---- the mapping step (V4): this datasheet covers N models → map to your parts ------------------------------------
  function mapping() {
    const { models, read } = coveredModels(index, markdown, data.effective?.part);
    // The queue's extraction runs once per model a batch product names (sources/extraction.py): those models were
    // read, and `extractor.part_models` says which products named each — the first offer when mapping it.
    const runs = data.values?.extractor?.part_models || [];
    const readModels = runs.map((run) => run.model);
    if (!runs.length && read) readModels.push(read);
    const named = (model) => runs.filter((run) => sameModel(run.model, model)).flatMap((run) => run.products || []);
    // WHY the run's name: the server assigns a map's values by `part_models` (the name each per-model run used, e.g.
    // "AK80-9", printed inside the header "AK80-9 V3.0 KV100"), so a map names the model that way when a run read it.
    const mapName = (model) => runs.find((run) => sameModel(run.model, model))?.model ?? model;
    // §7.1 `map`: the server's `effective.models` ({model: product}); else the last map op on a model (null unmaps).
    const mapped = new Map(models.map((model) => [model, null]));
    const served = data.effective?.models;
    const byName = new Map(models.map((model) => [mapName(model), model]));
    if (served) for (const model of models) mapped.set(model, served[mapName(model)] ?? null);
    else for (const op of data.review?.ops || []) if (op.op === "map" && byName.has(op.model)) mapped.set(byName.get(op.model), op.product ?? null);
    return { models, readModels, named, mapped, mapName };
  }

  function drawMapping() {
    const { models, readModels, mapped } = mapping();
    const count = [...mapped.values()].filter(Boolean).length;
    $("[data-maphead]", right).innerHTML = models.length
      ? `<b>This datasheet covers ${models.length} model${models.length === 1 ? "" : "s"}</b> → map to your parts <span class="n" data-testid="mapped-count">(${count} mapped)</span>`
      : `<b>No model is named in this datasheet</b> → map it to your parts`;
    const body = $("[data-mapbody]", right);
    body.innerHTML = models.map((model, at) => {
      const product = mapped.get(model);
      const readHere = readModels.some((other) => sameModel(other, model));
      return `<span class="mp${product ? " on" : ""}${readHere ? " read" : ""}" data-model="${at}"><span class="model">${esc(model)}</span>${readHere ? `<span class="tag" title="the values were read for this model">values read</span>` : ""}
        ${product ? `<span class="to">→ <b>${esc(productTitle(product))}</b><button type="button" class="chip-x" data-unmap aria-label="Unmap ${esc(model)}" title="Unmap">✕</button></span>` : ""}
        <button type="button" class="btn sm ghost" data-map data-popper>${product ? "Change" : "Map →"}</button></span>`;
    }).join("") + (models.length ? "" : `<button type="button" class="btn sm ghost" data-whole data-popper>Map to a part →</button>`);
    for (const pill of $$("[data-model]", body)) {
      const model = models[Number(pill.dataset.model)];
      $("[data-map]", pill).addEventListener("click", (event) => openMapMenu(event.currentTarget, model));
      $("[data-unmap]", pill)?.addEventListener("click", () => send({ op: "map", model: mapping().mapName(model), product: null }));
    }
    $("[data-whole]", body)?.addEventListener("click", (event) => openWholeMenu(event.currentTarget));
  }

  // ---- popovers: product menus --------------------------------------------------------------------------------------
  function closePop() {
    pop?.remove();
    pop = null;
  }
  function openPop(anchor, html) {
    closePop();
    pop = h("div", "pop", html);
    document.body.appendChild(pop);
    const at = anchor.getBoundingClientRect();
    const width = pop.offsetWidth;
    const height = pop.offsetHeight;
    let x = at.left;
    let y = at.bottom + 4;
    if (x + width > window.innerWidth - 8) x = window.innerWidth - width - 8;
    if (y + height > window.innerHeight - 8) y = at.top - height - 4;
    pop.style.left = `${Math.max(8, x)}px`;
    pop.style.top = `${Math.max(8, y)}px`;
    pop.addEventListener("psearch-escape", closePop);
    return pop;
  }
  const outside = (event) => {
    if (pop && !pop.contains(event.target) && !event.target.closest("[data-popper]")) closePop();
  };
  document.addEventListener("pointerdown", outside);

  /** The products to offer first for a model: those that named it at extraction, then those whose name holds it. */
  async function suggestions(model) {
    const products = await allProducts();
    const wanted = normal(model);
    const named = [...new Set(mapping().named(model))];
    const byName = products.filter((product) => !named.includes(product.id) && (normal(product.id).includes(wanted) || normal(product.title).includes(wanted)));
    return [...named.map((id) => products.find((product) => product.id === id) || { id, title: id }), ...byName].slice(0, 5);
  }

  async function openMapMenu(anchor, model) {
    const suggested = await suggestions(model);
    const menu = openPop(anchor, `<h4>Map ${esc(model)} to</h4>${suggested.map((product) => `<button type="button" class="pick" data-pick="${esc(product.id)}">${esc(product.title)} <span class="n">${esc(product.provider || "")}</span></button>`).join("")}<div data-psearch></div>
      <p class="n">${esc(model)}'s values will apply to it.</p>`);
    const pick = (id) => {
      closePop();
      send({ op: "map", model: mapping().mapName(model), product: id });
    };
    for (const button of $$("[data-pick]", menu)) button.addEventListener("click", () => pick(button.dataset.pick));
    const search = productSearch({ onPick: (product) => pick(product.id), testid: "map-search" });
    $("[data-psearch]", menu).appendChild(search);
    search.focus();
  }

  function openWholeMenu(anchor) {
    const menu = openPop(anchor, `<h4>Every value applies to</h4><div data-psearch></div>`);
    const search = productSearch({
      testid: "whole-search",
      onPick: (product) => {
        closePop();
        send({ op: "assign", products: [product.id], value_ids: ["*"] });
      },
    });
    $("[data-psearch]", menu).appendChild(search);
    search.focus();
  }

  /** p: which products the focused value applies to. A tick is one assign op, an untick one unassign op. */
  function openProductMenu() {
    const value = row(cur);
    if (!value) return;
    const anchor = $(`tr.r[data-id="${CSS.escape(cur)}"] [data-pmenu]`, right) || $(`tr.r[data-id="${CSS.escape(cur)}"]`, right);
    if (!anchor) return;
    const candidates = productColumns();
    for (const product of value.products) if (!candidates.includes(product)) candidates.push(product);
    const above = rows[rows.indexOf(value) - 1];
    const same = above ? above.products.filter((product) => !value.products.includes(product)) : [];
    const menu = openPop(anchor, `<h4>${esc(value.label)} applies to</h4>
      ${candidates.map((id) => `<label><input type="checkbox" data-product="${esc(id)}"${value.products.includes(id) ? " checked" : ""}><span class="prod${value.products.includes(id) ? "" : " off"}">${esc(productTitle(id))}</span></label>`).join("") || `<p class="n">No products yet.</p>`}
      ${above ? `<button type="button" class="btn sm ghost" data-same ${same.length ? "" : "disabled"}>Same as the row above${above.products.length ? ` (${esc(above.products.map(productTitle).join(", "))})` : " (none)"}</button>` : ""}
      <div data-psearch></div>`);
    for (const box of $$("[data-product]", menu)) {
      box.addEventListener("change", async () => {
        await send({ op: box.checked ? "assign" : "unassign", products: [box.dataset.product], value_ids: [value.id] });
        openProductMenu();
      });
    }
    $("[data-same]", menu)?.addEventListener("click", () => {
      closePop();
      send({ op: "assign", products: same, value_ids: [value.id] });
    });
    const search = productSearch({
      testid: "value-product-search",
      exclude: () => value.products,
      onPick: async (product) => {
        await send({ op: "assign", products: [product.id], value_ids: [value.id] });
        openProductMenu();
      },
    });
    $("[data-psearch]", menu).appendChild(search);
  }

  // ---- the version log, docked at the bottom, built from review.json ----------------------------------------------
  function opText(op) {
    const field = op.field && op.field !== "value" ? `${op.field}: ` : "";
    if (op.op === "edit") return op.field === "grounding" ? "cited a box drawn on the source" : `${field}${formatValue(op.from)} → ${formatValue(op.to)}`;
    if (op.op === "add") return `${op.row.label || op.row.key} ${formatValue(op.row.value)}${op.row.unit ? ` ${op.row.unit}` : ""}${op.row.grounding ? " · box drawn" : " · no source cited"}`;
    if (op.op === "map") return op.product ? `→ ${productTitle(op.product)}` : "unmapped";
    if (op.op === "assign" || op.op === "unassign") return `${op.op === "assign" ? "→" : "✕"} ${op.products.map(productTitle).join(", ")}`;
    return "";
  }
  function drawLog() {
    const ops = data.review?.ops || [];
    $("[data-loghead]", right).innerHTML = `<b>Version log</b> <span class="n" data-testid="log-count">· ${ops.length} change${ops.length === 1 ? "" : "s"} over the AI's values · newest first</span>`;
    const target = (op) => {
      if (op.op === "add") return { id: op.row.id, name: op.row.label || op.row.key };
      if (op.op === "map") return { id: "", name: `model ${op.model}` };
      if (op.value_ids) return op.value_ids.includes("*") ? { id: "", name: "whole datasheet" } : { id: op.value_ids[0], name: row(op.value_ids[0])?.label || op.value_ids[0] };
      return { id: op.value_id, name: row(op.value_id)?.label || op.value_id };
    };
    const items = ops.slice().reverse().map((op) => {
      const { id, name } = target(op);
      return `<li data-testid="log-item" data-op="${esc(op.op)}" data-jump="${esc(id)}"><code class="t">${esc(moment(op.at))}</code><span class="av hu" title="${esc(op.author)}">${esc(initials(op.author))}</span>
        <b class="op op-${esc(op.op)}">${esc(op.op)}</b><span class="tg" title="${esc(id)}">${esc(name)}</span><span>${esc(opText(op))}</span>${op.note ? ` <i>“${esc(op.note)}”</i>` : ""}</li>`;
    }).join("");
    const values = data.values;
    const kept = (values?.values || []).filter((value) => value.verdict === "kept").length;
    $("[data-logitems]", right).innerHTML = items + `<li class="base"><code class="t">${esc(day(values.source?.retrieved_at))}</code><span class="av ai">AI</span><b class="op">values</b>
      <span class="tg">values.json</span><span>${kept} values · ${esc(values.extractor?.tool || "")}${values.extractor?.model ? ` · ${esc(values.extractor.model)}` : ""}${data.review?.values_sha256 ? ` · sha256 ${esc(data.review.values_sha256.slice(0, 8))}` : ""}</span></li>`;
    for (const item of $$("[data-jump]", right)) {
      item.addEventListener("click", () => {
        if (item.dataset.jump && row(item.dataset.jump)) {
          focusRow(item.dataset.jump);
          scrollRow(item.dataset.jump);
        }
      });
    }
  }

  // ---- focus, edit, add -----------------------------------------------------------------------------------------------
  function focusRow(id, quiet) {
    const changed = cur !== id;
    cur = id;
    for (const tr of $$("tr.r", right)) tr.classList.toggle("cur", tr.dataset.id === id);
    if (changed || !quiet) showSource(id);
  }
  function move(step) {
    if (!rows.length) return;
    const at = rows.findIndex((value) => value.id === cur);
    const next = rows[Math.max(0, Math.min(rows.length - 1, at < 0 ? 0 : at + step))].id;
    focusRow(next);
    scrollRow(next);
  }
  function scrollRow(id) {
    const tr = $(`tr.r[data-id="${CSS.escape(id)}"]`, right);
    const scroller = $(".rv-scroll", right);
    if (!tr || !scroller) return;
    const at = tr.getBoundingClientRect();
    const box = scroller.getBoundingClientRect();
    if (at.top < box.top + 30 || at.bottom > box.bottom - 10) scroller.scrollTop += at.top - box.top - box.height / 3;
  }

  /** Edit in place: the value cell becomes its own inputs. Enter or Save keeps it, Esc or Cancel puts it back. */
  function startEdit(typed) {
    const value = row(cur);
    if (!value || view !== "rows") return;
    const cell = $(`tr.r[data-id="${CSS.escape(cur)}"] [data-val]`, right);
    if (!cell) return;
    const before = cell.innerHTML;
    cell.innerHTML = `<div class="ie" data-testid="editor"><input class="v" aria-label="Value" data-testid="edit-value" value="${esc(typed ?? (value.value == null ? "" : formatValue(value.value)))}">
      <input class="u" aria-label="Unit" data-testid="edit-unit" placeholder="unit" value="${esc(value.unit ?? "")}"><input class="note" aria-label="Note" placeholder="note (optional)" data-testid="edit-note">
      <button type="button" class="btn sm" data-save title="Save (Enter)" aria-label="Save">✓</button><button type="button" class="btn sm ghost" data-cancel title="Cancel (Esc)" aria-label="Cancel">✕</button></div>`;
    const box = $(".ie", cell);
    const [valueInput, unitInput, noteInput] = $$("input", box);
    let done = false;
    const close = async (save) => {
      if (done) return;
      done = true;
      editing = null;
      if (!save) {
        cell.innerHTML = before;
        return;
      }
      const ops = [];
      const note = noteInput.value.trim();
      if (valueInput.value.trim() !== "" || value.value == null) {
        const to = parseTyped(valueInput.value, value.ai ? value.ai.value : value.value);
        if (!deepEqual(to, value.value)) ops.push({ op: "edit", value_id: value.id, field: "value", from: value.value ?? null, to });
      }
      const unit = unitInput.value.trim() || null;
      if (unit !== (value.unit ?? null)) ops.push({ op: "edit", value_id: value.id, field: "unit", from: value.unit ?? null, to: unit });
      if (!ops.length) {
        cell.innerHTML = before;
        return;
      }
      if (note) ops[0].note = note;
      for (const op of ops) await send(op);
      move(1);
    };
    editing = { close };
    box.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter") {
        event.preventDefault();
        close(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        close(false);
      }
    });
    box.addEventListener("click", (event) => event.stopPropagation());
    $("[data-save]", box).addEventListener("click", () => close(true));
    $("[data-cancel]", box).addEventListener("click", () => close(false));
    box.addEventListener("focusout", (event) => {
      if (!box.contains(event.relatedTarget)) setTimeout(() => { if (!done && !box.contains(document.activeElement)) close(true); }, 0);
    });
    valueInput.focus({ preventScroll: true });
    $(".rv-scroll", right).scrollLeft = 0; // the wider editor must not slide the provenance and field columns away
    if (typed != null) valueInput.setSelectionRange(valueInput.value.length, valueInput.value.length);
    else valueInput.select();
  }

  /** a: a human row. Its grounding is a box the person drags on the source, or null ("no source cited") — never an AI one. */
  function startAdd() {
    if (adding || !data.values) return;
    const hostNode = $("[data-addhost]", right);
    adding = { grounding: null, redraw: () => {} };
    hostNode.innerHTML = `<div class="ie add" data-testid="add-form"><input class="t" aria-label="Field" placeholder="Field, e.g. Mass" data-testid="add-field"><input class="v" aria-label="Value" placeholder="Value" data-testid="add-value">
      <input class="u" aria-label="Unit" placeholder="Unit" data-testid="add-unit"><input class="note" aria-label="Note" placeholder="where it comes from (optional)" data-testid="add-note">
      <button type="button" class="btn sm" data-add>Add</button><button type="button" class="btn sm ghost" data-cancel>Cancel</button>
      <span class="cite" data-testid="add-cite"></span></div>`;
    const form = $(".ie", hostNode);
    const get = (name) => $(`[data-testid=add-${name}]`, form).value.trim();
    adding.redraw = () => {
      const cite = $("[data-testid=add-cite]", form);
      cite.innerHTML = adding.grounding
        ? `cites a box on p.${esc(adding.grounding[0].page)} <button type="button" class="link" data-uncite>clear</button>`
        : `<span class="n">no source cited${drawable ? " · drag a box on the source to cite one" : ""}</span>`;
      $("[data-uncite]", cite)?.addEventListener("click", () => {
        adding.grounding = null;
        adding.redraw();
        showSource();
      });
    };
    adding.redraw();
    const finish = () => {
      adding = null;
      hostNode.innerHTML = "";
      showSource();
    };
    const submit = async () => {
      if (!get("field") || !get("value")) {
        toast("A row needs a field and a value");
        return;
      }
      const count = (data.review?.ops || []).filter((op) => op.op === "add").length;
      let id = `h${count + 1}`;
      while (row(id)) id = `h${Number(id.slice(1)) + 1}`;
      const unit = get("unit") || null;
      const added = { id, key: slug(get("field")) + (unit ? `_${slug(unit)}` : ""), label: get("field"), value: parseTyped(get("value"), null), unit, raw: null, grounding: adding.grounding };
      const op = { op: "add", row: added };
      if (get("note")) op.note = get("note");
      finish();
      if (await send(op)) {
        focusRow(id);
        scrollRow(id);
      }
    };
    form.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter") {
        event.preventDefault();
        submit();
      } else if (event.key === "Escape") {
        event.preventDefault();
        finish();
      }
    });
    for (const input of $$("input", form)) input.addEventListener("input", () => input.classList.remove("bad"));
    $("[data-add]", form).addEventListener("click", submit);
    $("[data-cancel]", form).addEventListener("click", finish);
    $("[data-testid=add-field]", form).focus({ preventScroll: true });
    const scroller = $(".rv-scroll", right);
    scroller.scrollTop = scroller.scrollHeight;
    showSource();
  }

  function onKey(event) {
    if (!host.isConnected || !data?.values) return;
    if (event.target.matches?.("input, textarea, select") || editing || adding || event.ctrlKey || event.metaKey || event.altKey) return;
    const key = event.key;
    if (key === "j" || key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (key === "k" || key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (key === "x" && cur) {
      send({ op: row(cur).rejected ? "restore" : "reject", value_id: cur });
    } else if (key === "a") {
      event.preventDefault();
      startAdd();
    } else if (key === "p" && cur) {
      event.preventDefault();
      openProductMenu();
    } else if (key === "Enter" && cur) {
      event.preventDefault();
      startEdit();
    } else if (key === "Escape") {
      closePop();
    } else if (cur && /^[0-9.\-~]$/.test(key)) {
      event.preventDefault();
      startEdit(key);
    }
  }
  document.addEventListener("keydown", onKey);

  function draw() {
    drawHead();
    if (!data.values) return;
    drawTable();
    drawMapping();
    drawLog();
    showSource(cur);
  }

  async function drawAll() {
    await mountSource();
    mountRight();
    draw();
  }

  const ready = (async () => {
    [categoryList] = await Promise.all([categories().catch(() => []), load(), allProducts()]);
    derive();
    await drawAll();
    watch();
  })();
  ready.catch((error) => {
    host.innerHTML = `<p class="pane-failed">Could not open this source: ${esc(error.message)}</p>`;
  });

  return {
    ready,
    destroy() {
      destroyed = true;
      clearTimeout(poll);
      closePop();
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", outside);
    },
  };
}
