// Add Sources: one calm box. Drop any number of files, paste any number of URLs, optionally pick products, a category
// and how much of each web page to keep, then POST /api/batches (CONTRACT §4). The batch goes onto the queue.

import { api, categories, categoryOptions, esc, h, $, $$, toast } from "./util.js";
import { productSearch, productTitle } from "./products.js";

// Markdownify CONTRACT §2.1, measured on the CubeMars AK80-9 page (2026-10-29). WHY sizes beside each level (Zach,
// 2026-10-29): "provide all the options and then provide the estimate of the storage size, so then the user can pick."
// WHY a set, not a ladder: each level keeps a different thing, so they add up; html is always kept because values
// cite its bytes, and "link only" (no copy at all) is its own explicit choice. `bytes` is the measured size, for the
// running total; the archive's is its first page.
const CAPTURE_LEVELS = [
  { level: "link", size: "~1 KB", bytes: 1e3, gives: "link only, no copy — where and when" },
  { level: "html", size: "192 KB (39 KB gzipped)", bytes: 192e3, gives: "values cite these bytes" },
  { level: "snapshot", size: "1.4 MB", bytes: 1.4e6, gives: "what the page looked like; highlight as page + box" },
  { level: "pdf", size: "5.2 MB", bytes: 5.2e6, gives: "a printable copy with a text layer" },
  { level: "archive", size: "4.9 MB first page, 2.1–2.7 MB each further page", bytes: 4.9e6, gives: "the page replayed offline, exactly as delivered" },
];
const ORDER = CAPTURE_LEVELS.map(({ level }) => level); // Markdownify's order, which capture_keep is sent in
const megabytes = (bytes) => (bytes < 1e6 ? `${Math.round(bytes / 1e3)} KB` : `${(bytes / 1e6).toFixed(1)} MB`);

const kb = (bytes) => (bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`);
const urlLines = (text) => text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);

export function addScreen(host, { onAdded }) {
  const files = [];
  const products = [];
  host.innerHTML = `<section class="add" data-testid="add-screen">
    <h1>Add sources</h1>
    <p class="lede">Datasheets in, one tab each to review. Files and links go into one batch on the queue.</p>
    <div class="add-box">
      <div class="drop" data-testid="drop" tabindex="0" role="button" aria-label="Drop files here or choose files">
        <span class="drop-icon" aria-hidden="true">⇪</span><span>Drop files here, or <u>choose files</u></span>
        <span class="n">PDF, images, saved web pages — any number</span>
        <input type="file" multiple hidden data-testid="file-input">
      </div>
      <ul class="files" data-testid="files"></ul>
      <label class="field"><span>Links, one per line</span>
        <textarea class="in" rows="3" spellcheck="false" placeholder="https://…" data-testid="urls"></textarea></label>
      <div class="field"><span>Products <span class="n">optional — what these datasheets describe</span></span>
        <div class="chips" data-testid="add-products"></div><div data-psearch></div></div>
      <label class="field inline"><span>Category</span><select class="in" data-testid="category"></select>
        <span class="n">which fields to extract</span></label>
      <fieldset class="field levels" data-testid="levels"><legend>Keep from each web page <span class="n" data-levelnote></span></legend>
        ${CAPTURE_LEVELS.filter(({ level }) => level !== "link").map(({ level, size, gives }) => `<label class="level${level === "html" ? " locked" : ""}"><input type="checkbox" name="capture_keep" value="${level}" data-testid="keep-${level}"${level === "html" ? " checked disabled" : ""}>
          <b>${level}</b><span class="size" data-testid="size-${level}">${esc(size)}</span><span class="gives">${esc(gives)}</span></label>`).join("")}
        <label class="level linkonly"><input type="checkbox" data-testid="keep-link-only"><b>link only</b><span class="size" data-testid="size-link">${esc(CAPTURE_LEVELS[0].size)}</span><span class="gives">${esc(CAPTURE_LEVELS[0].gives)}</span></label>
        <p class="total">Kept per page: <b data-testid="keep-total"></b> <span class="n" data-testid="keep-list"></span></p>
        <p class="n">Sizes measured on one product page (CubeMars AK80-9, 29 Oct 2026). Files are kept whole.</p></fieldset>
      <div class="add-go"><button type="button" class="btn pri" data-testid="submit" disabled>Add to queue</button><span class="n" data-summary></span></div>
    </div></section>`;

  const drop = $("[data-testid=drop]", host);
  const input = $("[data-testid=file-input]", host);
  const urls = $("[data-testid=urls]", host);
  const submit = $("[data-testid=submit]", host);
  const category = $("[data-testid=category]", host);

  function drawFiles() {
    const list = $("[data-testid=files]", host);
    list.innerHTML = files.map((file, index) => `<li><span class="kind">${esc((file.name.split(".").pop() || "file").slice(0, 5))}</span>
      <span class="name">${esc(file.name)}</span><span class="n">${kb(file.size)}</span>
      <button type="button" class="icon-btn" data-remove="${index}" aria-label="Remove ${esc(file.name)}" title="Remove">✕</button></li>`).join("");
    for (const button of $$("[data-remove]", list)) {
      button.addEventListener("click", () => {
        files.splice(Number(button.dataset.remove), 1);
        drawFiles();
      });
    }
    drawSummary();
  }

  function drawProducts() {
    const chips = $("[data-testid=add-products]", host);
    chips.innerHTML = products.map((product) => `<span class="prod">${esc(productTitle(product.id))}
      <button type="button" class="chip-x" data-unpick="${esc(product.id)}" aria-label="Remove ${esc(product.title)}">✕</button></span>`).join("");
    for (const button of $$("[data-unpick]", chips)) {
      button.addEventListener("click", () => {
        products.splice(products.findIndex((product) => product.id === button.dataset.unpick), 1);
        drawProducts();
      });
    }
  }

  function drawSummary() {
    const links = urlLines(urls.value).length;
    const count = files.length + links;
    submit.disabled = count === 0;
    submit.textContent = count ? `Add ${count} source${count === 1 ? "" : "s"} to the queue` : "Add to queue";
    $("[data-summary]", host).textContent = count ? `${files.length} file${files.length === 1 ? "" : "s"} · ${links} link${links === 1 ? "" : "s"}` : "";
    host.querySelector(".levels").classList.toggle("idle", links === 0);
    $("[data-levelnote]", host).textContent = links ? `— applies to the ${links} link${links === 1 ? "" : "s"}` : "— applies to links";
  }

  /** The levels to keep, as the comma list POST /api/batches takes (Markdownify CONTRACT §2.1 order). */
  function kept() {
    if ($("[data-testid=keep-link-only]", host).checked) return ["link"];
    return ORDER.filter((level) => $(`[data-testid=keep-${level}]`, host)?.checked);
  }

  function drawLevels() {
    const linkOnly = $("[data-testid=keep-link-only]", host).checked;
    const html = $("[data-testid=keep-html]", host);
    html.checked = !linkOnly;
    for (const level of ["snapshot", "pdf", "archive"]) {
      const box = $(`[data-testid=keep-${level}]`, host);
      box.disabled = linkOnly;
      if (linkOnly) box.checked = false;
    }
    host.querySelector(".level.locked").classList.toggle("off", linkOnly);
    const levels = kept();
    const total = levels.reduce((sum, level) => sum + CAPTURE_LEVELS.find((entry) => entry.level === level).bytes, 0);
    $("[data-testid=keep-total]", host).textContent = `≈ ${megabytes(total)}`;
    $("[data-testid=keep-list]", host).textContent = `${levels.join(" + ")}${levels.includes("archive") ? " · first page; each further page adds 2.1–2.7 MB" : ""}`;
  }

  function takeFiles(list) {
    for (const file of list) files.push(file);
    drawFiles();
  }

  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      input.click();
    }
  });
  drop.addEventListener("dragover", (event) => {
    event.preventDefault();
    drop.classList.add("over");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("over");
    takeFiles(event.dataTransfer.files);
  });
  input.addEventListener("change", () => {
    takeFiles(input.files);
    input.value = "";
  });
  urls.addEventListener("input", drawSummary);
  for (const box of $$("input[name=capture_keep], [data-testid=keep-link-only]", host)) box.addEventListener("change", drawLevels);
  category.addEventListener("change", drawSummary);

  const search = productSearch({
    testid: "add-product-search",
    exclude: () => products.map((product) => product.id),
    onPick: (product) => {
      products.push(product);
      drawProducts();
    },
  });
  $("[data-psearch]", host).appendChild(search);

  categories().then((list) => {
    category.innerHTML = categoryOptions(list, null);
  }).catch((error) => toast(`Could not read the categories: ${error.message}`, "error"));

  submit.addEventListener("click", async () => {
    const form = new FormData();
    for (const file of files) form.append("files[]", file, file.name);
    form.append("urls", urlLines(urls.value).join("\n"));
    form.append("products", JSON.stringify(products.map((product) => product.id)));
    form.append("category", category.value);
    form.append("capture_keep", kept().join(","));
    submit.disabled = true;
    try {
      const batch = await api.createBatch(form);
      files.length = 0;
      products.length = 0;
      urls.value = "";
      drawFiles();
      drawProducts();
      onAdded(batch);
    } catch (error) {
      toast(`Could not add the batch: ${error.message}`, "error");
      drawSummary();
    }
  });

  drawFiles();
  drawLevels();
}
