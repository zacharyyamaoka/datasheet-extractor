// The queue: every batch, newest first, each source with its stage (CONTRACT §3), redrawn on every poll. WHY (Zach,
// 2026-10-29): an "upload history queue … we can see when they are done".

import { STAGES, api, categories, categoryOptions, esc, $$, moment, toast } from "./util.js";

// A category picked on a row here, until GET /api/batches says which category each source has (its summary carries
// only {id, name, kind, stage, error}); otherwise a source reads as its batch's.
const picked = new Map();

const reviewable = (source) => source.stage === "extracted" || source.stage === "published";

function track(source) {
  if (source.stage === "failed") {
    return `<span class="stage failed" data-stage="failed">failed</span><span class="err" data-testid="error">${esc(source.error || "no error given")}</span>`;
  }
  const reached = STAGES.indexOf(source.stage);
  const steps = STAGES.map((stage, index) => `<span class="step${index < reached ? " done" : ""}${index === reached ? " now" : ""}" title="${stage}"></span>`).join("");
  return `<span class="track" aria-hidden="true">${steps}</span><span class="stage${reached >= 3 ? " ready" : ""}" data-stage="${esc(source.stage)}">${esc(source.stage)}</span>`;
}

export function queueScreen(host, { onOpen, onChanged }) {
  host.innerHTML = `<section class="queue" data-testid="queue"><h1>Queue</h1><div data-batches></div></section>`;
  const list = host.querySelector("[data-batches]");
  let categoryList = [];
  let last = null;
  categories().then((found) => {
    categoryList = found;
    if (last) draw(...last);
  }).catch(() => {});

  function draw(batches, fresh) {
    last = [batches, fresh];
    // WHY skip while a picker is open: the poll redraws every 1.5 s and would close the menu under the pointer.
    if (list.contains(document.activeElement) && document.activeElement.matches("select")) return;
    if (!batches.length) {
      list.innerHTML = `<p class="empty">Nothing added yet. <a href="#/add">Add sources</a> to start.</p>`;
      return;
    }
    list.innerHTML = batches.map((batch) => {
      const done = batch.sources.filter((source) => reviewable(source)).length;
      const failed = batch.sources.filter((source) => source.stage === "failed").length;
      const running = batch.sources.length - done - failed;
      const answer = running ? `${running} of ${batch.sources.length} in progress` : `${done} of ${batch.sources.length} ready to review`;
      const firstReady = batch.sources.find(reviewable);
      return `<article class="batch${batch.id === fresh ? " fresh" : ""}" data-testid="batch" data-batch="${esc(batch.id)}">
        <header><b class="label">${esc(batch.label || batch.id)}</b><span class="n">${esc(moment(batch.created_at))}</span>
          <span class="answer${running ? "" : " ok"}" data-testid="batch-answer">${esc(answer)}${failed ? ` · <span class="bad">${failed} failed</span>` : ""}</span>
          <span class="sp"></span><span class="n">${esc(batch.category || "")}${batch.products?.length ? ` · ${batch.products.length} product${batch.products.length === 1 ? "" : "s"}` : ""}</span>
          ${firstReady ? `<button type="button" class="btn sm" data-open="${esc(firstReady.id)}">Review</button>` : ""}</header>
        <ol class="sources">${batch.sources.map((source) => `<li data-testid="queue-source" data-source="${esc(source.id)}" data-stage="${esc(source.stage)}">
          <span class="kind">${esc(source.kind)}</span><span class="name" title="${esc(source.name)}">${esc(source.name)}</span>${track(source)}
          <span class="sp"></span>${categoryList.length ? `<select class="in sm" data-category="${esc(source.id)}" aria-label="Category of ${esc(source.name)}" title="What to extract; changing it runs the extraction again">${categoryOptions(categoryList, picked.get(source.id) || source.category || batch.category)}</select>` : ""}
          ${reviewable(source) ? `<button type="button" class="btn sm ghost" data-open="${esc(source.id)}">Open</button>` : ""}</li>`).join("")}</ol></article>`;
    }).join("");
    for (const button of $$("[data-open]", list)) {
      button.addEventListener("click", () => onOpen(button.closest("[data-batch]").dataset.batch, button.dataset.open));
    }
    for (const select of $$("[data-category]", list)) {
      select.addEventListener("change", async () => {
        const id = select.dataset.category;
        try {
          const result = await api.extract(id, { category: select.value });
          picked.set(id, select.value);
          toast(result?.queued ? `Extracting again as ${select.value}` : `Category set to ${select.value}; it extracts after conversion`);
          select.blur();
          onChanged?.();
        } catch (error) {
          toast(`Category not changed: ${error.message}`, "error");
          select.blur();
          draw(...last);
        }
      });
    }
  }
  return { draw };
}
