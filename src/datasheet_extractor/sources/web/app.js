// The Sources app (CONTRACT §6): three screens in one page — Add Sources, the Queue, and a batch with one tab per
// source, each tab the review view. Routes live in the hash: #/add, #/queue, #/batch/<batch id>/<source id>.

import { api, esc, h, $, $$, toast } from "./util.js";
import { addScreen } from "./add.js";
import { queueScreen } from "./queue.js";
import { reviewTab } from "./review.js";

const POLL_MS = 1500;
const screen = $("#screen");
const tabs = $("[data-testid=tabs]");
let batches = [];
let queue = null;
let review = null;
let freshBatch = null;
let lastRoute = "";
let order = new Map(); // batch id → its source ids at the last poll, to follow a source renamed once captured (§2)

const terminal = (stage) => stage === "extracted" || stage === "published" || stage === "failed";

function route() {
  const [, name, batchId, sourceId] = (location.hash || "").match(/^#\/(\w+)(?:\/([^/]+))?(?:\/([^/]+))?/) || [];
  return { name: name || (batches.length ? "queue" : "add"), batchId, sourceId };
}

function go(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}

function drawNav() {
  const running = batches.flatMap((batch) => batch.sources).filter((source) => !terminal(source.stage)).length;
  $("[data-testid=queue-badge]").textContent = running ? String(running) : "";
  $("[data-testid=queue-badge]").hidden = !running;
  const { name } = route();
  for (const link of $$("[data-nav]")) link.classList.toggle("on", link.dataset.nav === name);
}

function drawTabs() {
  const { name, batchId, sourceId } = route();
  const batch = batches.find((candidate) => candidate.id === batchId);
  tabs.hidden = name !== "batch" || !batch;
  if (tabs.hidden) return;
  tabs.innerHTML = `<span class="batch-label" title="${esc(batch.id)}">${esc(batch.label || batch.id)}</span>` + batch.sources.map((source) => `<button type="button" role="tab" class="tab${source.id === sourceId ? " on" : ""}" data-tab="${esc(source.id)}" aria-selected="${source.id === sourceId}" title="${esc(source.name)} · ${esc(source.stage)}">
      <span class="dot ${esc(source.stage)}" aria-hidden="true"></span><span class="tab-name">${esc(source.name)}</span></button>`).join("");
  for (const tab of $$("[data-tab]", tabs)) tab.addEventListener("click", () => go(`#/batch/${batch.id}/${tab.dataset.tab}`));
}

async function refresh() {
  try {
    batches = await api.batches();
  } catch (error) {
    toast(`Could not read the queue: ${error.message}`, "error");
    return;
  }
  // A source's id changes from pending-<n> to its sha256 prefix once captured; follow an open tab across that.
  const { name, batchId, sourceId } = route();
  if (name === "batch" && sourceId) {
    const batch = batches.find((candidate) => candidate.id === batchId);
    const before = order.get(batchId) || [];
    if (batch && !batch.sources.some((source) => source.id === sourceId) && before.includes(sourceId)) {
      const moved = batch.sources[before.indexOf(sourceId)];
      if (moved) history.replaceState(null, "", `#/batch/${batchId}/${moved.id}`);
    }
  }
  order = new Map(batches.map((batch) => [batch.id, batch.sources.map((source) => source.id)]));
  drawNav();
  if (route().name === "queue" && queue) queue.draw(batches, freshBatch);
  if (route().name === "batch") {
    drawTabs();
    const now = route();
    if (`${now.batchId}/${now.sourceId}` !== lastRoute.split(":")[1]) render();
  }
}

function render() {
  const { name, batchId, sourceId } = route();
  const key = `${name}:${batchId}/${sourceId}`;
  review?.destroy();
  review = null;
  queue = null;
  lastRoute = key;
  drawNav();
  drawTabs();
  if (name === "add") {
    addScreen(screen, {
      onAdded: async (batch) => {
        freshBatch = batch.id;
        toast(`Added ${batch.sources.length} source${batch.sources.length === 1 ? "" : "s"} to the queue`);
        await refresh();
        go("#/queue");
      },
    });
  } else if (name === "batch") {
    const batch = batches.find((candidate) => candidate.id === batchId);
    if (!batch) {
      screen.innerHTML = `<p class="empty">No batch ${esc(batchId)}. <a href="#/queue">Back to the queue</a></p>`;
      return;
    }
    const chosen = sourceId || (batch.sources.find((source) => source.stage === "extracted") || batch.sources[0])?.id;
    if (!sourceId && chosen) {
      history.replaceState(null, "", `#/batch/${batch.id}/${chosen}`);
      render();
      return;
    }
    screen.innerHTML = "";
    const holder = h("div", "tab-body");
    screen.appendChild(holder);
    review = reviewTab(holder, { sourceId: chosen, batch, onChanged: refresh });
  } else {
    queue = queueScreen(screen, { onOpen: (batch, source) => go(`#/batch/${batch}/${source}`), onChanged: refresh });
    queue.draw(batches, freshBatch);
  }
}

window.addEventListener("hashchange", render);
for (const link of $$("[data-nav]")) link.addEventListener("click", () => setTimeout(drawNav));

(async () => {
  await refresh();
  render();
  setInterval(refresh, POLL_MS);
})();
