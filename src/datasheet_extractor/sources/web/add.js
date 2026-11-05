// Add Sources: one calm box. Drop any number of files, paste any number of URLs, optionally pick products, a category
// and how much of each web page to keep, then POST /api/batches (CONTRACT §4). The batch goes onto the queue.

import { api, categoryName, esc, h, $, $$, toast } from "./util.js";
import { productSearch, productTitle } from "./products.js";

// Markdownify CONTRACT §2.1, measured on the CubeMars AK80-9 page (2026-10-29). WHY sizes beside each level (Zach,
// 2026-10-29): "provide all the options and then provide the estimate of the storage size, so then the user can pick."
const CAPTURE_LEVELS = [
  { level: "link", size: "~1 KB", gives: "where and when — no copy of the page" },
  { level: "html", size: "192 KB (39 KB gzipped)", gives: "the bytes Markdownify reads and values cite" },
  { level: "snapshot", size: "1.4 MB", gives: "what the page looked like; highlight as page + box" },
  { level: "pdf", size: "5.2 MB", gives: "a printable copy with a text layer" },
  { level: "archive", size: "4.9 MB first page, 2.1–2.7 MB each further page", gives: "the page replayed offline, exactly as delivered" },
];
const DEFAULT_LEVEL = "html";

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
        ${CAPTURE_LEVELS.map(({ level, size, gives }) => `<label class="level"><input type="radio" name="capture_keep" value="${level}"${level === DEFAULT_LEVEL ? " checked" : ""}>
          <b>${level}</b><span class="size" data-testid="size-${level}">${esc(size)}</span><span class="gives">${esc(gives)}</span></label>`).join("")}
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
  for (const radio of $$("input[name=capture_keep]", host)) radio.addEventListener("change", drawSummary);
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

  api.fields().then((list) => {
    category.innerHTML = list.map((entry) => `<option value="${esc(categoryName(entry))}">${esc(categoryName(entry))}</option>`).join("");
  }).catch((error) => toast(`Could not read the categories: ${error.message}`, "error"));

  submit.addEventListener("click", async () => {
    const form = new FormData();
    for (const file of files) form.append("files[]", file, file.name);
    form.append("urls", urlLines(urls.value).join("\n"));
    form.append("products", JSON.stringify(products.map((product) => product.id)));
    form.append("category", category.value);
    form.append("capture_keep", $("input[name=capture_keep]:checked", host).value);
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
}
