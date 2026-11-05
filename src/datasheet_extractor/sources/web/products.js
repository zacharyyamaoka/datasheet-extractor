// Products: the provider's list (GET /api/products) searched as you type, and a new one created inline
// (POST /api/products) when nothing matches. Used by Add Sources, the mapping step and the per-value menu.

import { api, esc, h, $$, toast } from "./util.js";

let cache = null;

/** Every product, fetched once per page (a new one created here is added to it). */
export async function allProducts(refresh) {
  if (!cache || refresh) cache = await api.products();
  return cache;
}

export const productTitle = (id) => (cache || []).find((product) => product.id === id)?.title || id;

export async function createProduct(title) {
  const product = await api.createProduct(title);
  if (cache && !cache.some((known) => known.id === product.id)) cache.unshift(product);
  toast(`Created ${product.title}`);
  return product;
}

const normal = (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, "");

/**
 * A search box over the products. `onPick(product)` hears the choice (an existing product, or one created from the
 * typed title); `exclude()` lists ids not to offer again. Returns the root element; `.focus()` focuses its input.
 */
export function productSearch({ onPick, exclude = () => [], placeholder = "Search products or type a new one", testid }) {
  const root = h("div", "psearch");
  root.innerHTML = `<input class="in" type="search" autocomplete="off" spellcheck="false" placeholder="${esc(placeholder)}"
      aria-label="Search products" ${testid ? `data-testid="${esc(testid)}"` : ""}><ul class="psearch-list" role="listbox" hidden></ul>`;
  const input = root.querySelector("input");
  const list = root.querySelector("ul");
  let active = 0;
  let options = [];

  async function draw() {
    const query = input.value.trim();
    if (!query) {
      list.hidden = true;
      return;
    }
    const products = await allProducts();
    const skip = new Set(exclude());
    const wanted = normal(query);
    const matches = products.filter((product) => !skip.has(product.id) && (normal(product.id).includes(wanted) || normal(product.title).includes(wanted))).slice(0, 8);
    const exact = products.some((product) => normal(product.title) === wanted || normal(product.id) === wanted);
    options = matches.map((product) => ({ product }));
    if (!exact) options.push({ create: query });
    active = Math.min(active, options.length - 1);
    list.innerHTML = options.map((option, index) => option.create
      ? `<li role="option" data-index="${index}" class="create${index === active ? " on" : ""}">Create <b>“${esc(option.create)}”</b> as a new product</li>`
      : `<li role="option" data-index="${index}" class="${index === active ? "on" : ""}"><span>${esc(option.product.title)}</span>${option.product.title !== option.product.id ? ` <code>${esc(option.product.id)}</code>` : ""}<span class="prov">${esc(option.product.provider || "")}</span></li>`).join("");
    list.hidden = !options.length;
    for (const item of $$("li", list)) {
      item.addEventListener("pointerdown", (event) => {
        event.preventDefault(); // keep the input focused; the choice is made on pointerdown so blur cannot eat it
        choose(Number(item.dataset.index));
      });
    }
  }

  async function choose(index) {
    const option = options[index];
    if (!option) return;
    // Cleared before the create round-trip, so what is typed meanwhile is the next search, not wiped by this one.
    input.value = "";
    list.hidden = true;
    onPick(option.create ? await createProduct(option.create) : option.product);
  }

  input.addEventListener("input", () => {
    active = 0;
    draw();
  });
  input.addEventListener("keydown", (event) => {
    event.stopPropagation(); // the review tab's single-key shortcuts must not fire while typing here
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      active = Math.max(0, Math.min(options.length - 1, active + (event.key === "ArrowDown" ? 1 : -1)));
      draw();
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(active);
    } else if (event.key === "Escape") {
      input.value = "";
      list.hidden = true;
      root.dispatchEvent(new CustomEvent("psearch-escape", { bubbles: true }));
    }
  });
  input.addEventListener("blur", () => setTimeout(() => (list.hidden = true), 120));
  root.focus = () => input.focus({ preventScroll: true });
  return root;
}
