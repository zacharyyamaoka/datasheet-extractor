// Small shared pieces: the §4 API, DOM helpers, value formatting, remembered layout.

export const STAGES = ["added", "captured", "converted", "extracted", "published"];

/** A source the queue stopped on purpose, with a `note` (CONTRACT §3: link only — nothing stored to read). */
export const stopped = (source) => Boolean(source?.note) && !source?.running;

/** source.json `running` (CONTRACT §3): the step a job is in now, said quietly. */
export const runningText = (step) => (step ? `${{ capture: "capturing", convert: "converting", extract: "extracting" }[step] || step}…` : "");

// ---- the HTTP API (CONTRACT §4) -------------------------------------------------------------------------------------

async function call(method, path, body) {
  const init = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  const type = response.headers.get("Content-Type") || "";
  const payload = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(payload?.error || `${method} ${path}: HTTP ${response.status}`);
  return payload;
}

export const api = {
  fields: () => call("GET", "/api/fields"),
  products: () => call("GET", "/api/products"),
  createProduct: (title) => call("POST", "/api/products", { title }),
  batches: () => call("GET", "/api/batches"),
  createBatch: (form) => call("POST", "/api/batches", form),
  source: (id) => call("GET", `/api/sources/${encodeURIComponent(id)}`),
  file: (id, name) => `/api/sources/${encodeURIComponent(id)}/file/${encodeURIComponent(name)}`,
  review: (id, op) => call("POST", `/api/sources/${encodeURIComponent(id)}/review`, op),
  extract: (id, body = {}) => call("POST", `/api/sources/${encodeURIComponent(id)}/extract`, body),
  prompt: (id) => call("GET", `/api/sources/${encodeURIComponent(id)}/prompt`),
  spec: (id, form = "stated") => call("GET", `/api/sources/${encodeURIComponent(id)}/spec?form=${encodeURIComponent(form)}`),
  publish: (id) => call("POST", `/api/sources/${encodeURIComponent(id)}/publish`, {}),
};

/** A category from GET /api/fields: the contract says "categories available", so take a name or an object's id. */
export const categoryName = (entry) => (typeof entry === "string" ? entry : entry?.id || entry?.name || entry?.category || "");

/** What a category extracts, said plainly: open mode reads every specification; a field list reads its fields. */
export function categoryLabel(entry) {
  const name = categoryName(entry);
  if (entry?.mode === "open") return `${name} — every spec the sheet prints`;
  return entry?.fields ? `${name} — ${entry.fields} fields` : name;
}

let categoriesCache = null;
/** GET /api/fields once per page. */
export async function categories() {
  if (!categoriesCache) categoriesCache = await api.fields();
  return categoriesCache;
}

/** <option>s for a category <select>, `chosen` selected. */
export const categoryOptions = (list, chosen) => list.map((entry) => `<option value="${esc(categoryName(entry))}"${categoryName(entry) === chosen ? " selected" : ""}>${esc(categoryLabel(entry))}</option>`).join("");

/**
 * A field's label, never its bare key: the label the row carries (a hand-written values.json), the label printed in
 * the source (open mode keeps it in the model's proposal), or the category's field label when GET /api/fields gives
 * one (`labels: {key: label}`). Only when none exists is the key shown.
 */
export function fieldLabel(row, original, list) {
  if (row?.label) return row.label;
  if (original?.label) return original.label;
  if (original?.proposed?.label) return original.proposed.label;
  for (const entry of list || []) {
    const label = entry?.labels?.[row?.key];
    if (label) return label;
  }
  return row?.key ?? "";
}

// ---- DOM ------------------------------------------------------------------------------------------------------------

export function h(tag, className, html) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html != null) node.innerHTML = html;
  return node;
}
export const esc = (text) => String(text ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
export const $ = (selector, root) => (root || document).querySelector(selector);
export const $$ = (selector, root) => Array.from((root || document).querySelectorAll(selector));
export const deepEqual = (a, b) => JSON.stringify(a) === JSON.stringify(b);

let toastNode = null;
let toastTimer = 0;
export function toast(message, tone) {
  if (!toastNode) {
    toastNode = h("div", "toast");
    toastNode.setAttribute("role", "status");
    document.body.appendChild(toastNode);
  }
  toastNode.textContent = message;
  toastNode.dataset.tone = tone || "";
  toastNode.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastNode.classList.remove("show"), 2200);
}

/** Copy text; when the clipboard is refused, select it in a box so Ctrl+C still works. `done`: the confirmation, when "<what> copied" is not it. */
export async function copyText(text, what, done = `${what} copied`) {
  try {
    await navigator.clipboard.writeText(text);
    toast(done);
    return true;
  } catch {
    const box = h("textarea", "copy-fallback");
    box.value = text;
    box.readOnly = true;
    box.addEventListener("blur", () => box.remove());
    document.body.appendChild(box);
    box.select();
    toast(`Selected · press Ctrl+C to copy the ${what.toLowerCase()}`);
    return false;
  }
}

// ---- values as printed ----------------------------------------------------------------------------------------------
// WHY never normalise: a value and its unit are shown exactly as stored ("10⁻⁴kg·cm²" stays that) — Zach's truthful
// rendering rule (feedback_truthful_property_rendering). Only a {min, max} range needs joining to be read at all.

export function formatValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object" && "min" in value) return `${value.min} ~ ${value.max}`;
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Read what was typed as the same kind of value it replaces: a number stays a number, a range a range. */
export function parseTyped(text, like) {
  const typed = String(text).trim();
  if (typed === "") return null;
  const range = typed.match(/^([-+]?\d+(?:\.\d+)?)\s*(?:~|–|—|to)\s*([-+]?\d+(?:\.\d+)?)$/);
  if (range && (like == null || typeof like === "object")) return { min: Number(range[1]), max: Number(range[2]) };
  if (/^[-+]?\d+(?:\.\d+)?$/.test(typed) && (like == null || typeof like === "number")) return Number(typed);
  return typed;
}

const pad = (n) => String(n).padStart(2, "0");
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function day(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : `${date.getDate()} ${MONTHS[date.getMonth()]}`;
}
export function clock(iso) {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}
export function moment(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return new Date().toDateString() === date.toDateString() ? clock(iso) : `${day(iso)} ${clock(iso)}`;
}
/** "Zach Yamaoka" → ZY (first and last word); a one-word login ("bam") → BA. */
export function initials(author) {
  const words = String(author || "").split(/\s+/).map((word) => word.replace(/[^\p{L}\p{N}]/gu, "")).filter(Boolean);
  if (words.length > 1) return (words[0][0] + words.at(-1)[0]).toUpperCase();
  return (words[0] || "?").slice(0, 2).toUpperCase();
}

// ---- remembered layout ----------------------------------------------------------------------------------------------
// WHY: every pane boundary drags and is remembered; the log and the mapping step fold — Zach's standing UI rule
// (~/zach_brain/Settings/Memory/feedback_resizable_panels.md). Storage can be blocked; then nothing is remembered.

const STORAGE_PREFIX = "datasheet-sources:";

export function readStored(key, fallback) {
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function writeStored(key, value) {
  try {
    window.localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(value));
  } catch {
    // Storage full or blocked: the layout simply isn't remembered.
  }
}

/**
 * Drag `handle` to size `target` along `axis` ("x" → width, "y" → height), clamped to [min, max()], remembered under
 * `key`. `sign` -1 when the target sits after the handle (dragging toward it shrinks it). Arrow keys move it too.
 */
export function splitter(handle, target, { key, axis = "x", min, max, fallback, sign = 1, onSettle }) {
  const property = axis === "y" ? "height" : "width";
  const clamp = (size) => Math.round(Math.min(Math.max(size, min), Math.max(max(), min)));
  const apply = (size) => {
    target.style.flex = `0 0 ${clamp(size)}px`;
    target.style[property] = `${clamp(size)}px`;
  };
  apply(readStored(key, fallback()));
  const current = () => target.getBoundingClientRect()[property];
  const settle = () => {
    writeStored(key, Math.round(current()));
    onSettle?.();
  };
  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    const start = axis === "y" ? event.clientY : event.clientX;
    const size = current();
    handle.classList.add("drag");
    const onMove = (moved) => apply(size + sign * ((axis === "y" ? moved.clientY : moved.clientX) - start));
    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      handle.classList.remove("drag");
      settle();
    };
    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  });
  handle.addEventListener("keydown", (event) => {
    const back = axis === "y" ? "ArrowUp" : "ArrowLeft";
    const forward = axis === "y" ? "ArrowDown" : "ArrowRight";
    if (event.key !== back && event.key !== forward) return;
    event.preventDefault();
    event.stopPropagation();
    apply(current() + sign * (event.key === forward ? 16 : -16));
    settle();
  });
  // A smaller window can make a remembered size too big; clamp again, without remembering the clamped size.
  window.addEventListener("resize", () => apply(current()));
}

/** A remembered open/closed state; `draw(open)` shows it now and on every change. */
export function foldable(key, fallback, draw) {
  let open = readStored(key, fallback);
  const control = {
    get open() {
      return open;
    },
    toggle() {
      open = !open;
      writeStored(key, open);
      draw(open);
    },
  };
  draw(open);
  return control;
}
