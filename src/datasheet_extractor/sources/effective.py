"""The effective record — values.json with review.json applied — and a product's published datasheet (CONTRACT
§5; the op formats are Markdownify CONTRACT §7.1). Pure functions: no disk, no clock.

- `edit` replaces one field; the row keeps `ai` = the AI's original of every field a person replaced.
- `reject` marks `rejected: true`; `restore` clears it. A rejected row stays in the record.
- `add` appends a human row (`human: true`, its own grounding or `null`, never a borrowed AI one).
- `assign` / `unassign` add / remove product ids on the named value ids (`"*"`: every value row at that point).
- `map` records that a model the datasheet prints is one of your products (`product: null` unmaps): every value read
  for that model (its `part_models`, else a cite row or column that names it) is assigned to the product. Remapping or
  unmapping takes back only what the map gave, never a product an explicit `assign` put on a row. WHY per (value,
  product) and in order: Markdownify §7.1 has explicit ops win over a map's default, and the log's rule is that the
  last op on a value wins, so an assign or unassign after a map wins, and a map after them wins in its turn.
- Ops apply in order, so the last op on a value wins. An op whose value is not in this values.json (one written
  against an earlier extraction) is listed in `unmatched`, never applied to a different value.

A published datasheet is one product's view: its assigned rows, rejected ones and the checker's dropped ones left
out, every value still carrying its grounding and who stands behind it (`provenance`).
"""

from __future__ import annotations

import copy

from .. import option1

EDITABLE = ("key", "value", "unit", "raw", "condition", "grounding")
OPS = ("edit", "reject", "restore", "add", "assign", "unassign", "map")
_AI_FIELDS = ("key", "value", "unit", "raw", "condition", "cite", "grounding", "verdict", "reasons", "part_models")


def value_ids(values: dict | None, review: dict | None = None) -> set[str]:
    """Every id an op may name: the values, the part entries, and the human rows added so far."""
    ids = {row["id"] for row in (values or {}).get("values") or [] if row.get("id")}
    ids |= {entry["id"] for entry in ((values or {}).get("part") or {}).values() if entry and entry.get("id")}
    ids |= {op["row"]["id"] for op in (review or {}).get("ops") or [] if op.get("op") == "add" and op.get("row")}
    return ids


def _ai_row(row: dict, extracted_at: str | None) -> dict:
    out = {"id": row["id"]}
    out.update({name: copy.deepcopy(row[name]) for name in _AI_FIELDS if name in row})
    out.update({"rejected": False, "human": False, "products": [], "provenance": {"by": "ai", "at": extracted_at}})
    return out


def read_for(row: dict, model: str) -> bool:
    """The value was read for `model`: a part-model run produced it, or its cite's row or column names the model."""
    if model in (row.get("part_models") or []):
        return True
    cite = row.get("cite") or {}
    return option1.model_named(model, [text for text in (cite.get("row"), cite.get("column")) if text])


def effective(values: dict | None, review: dict | None, extracted_at: str | None = None) -> dict:
    """{values: [row…], part: {manufacturer, model}, models: {model: product}, unmatched: [op index…]} — every row of
    values.json in order, then the human rows in the order they were added."""
    values = values or {}
    rows = [_ai_row(row, extracted_at) for row in values.get("values") or [] if row.get("id")]
    part = {name: (_ai_row(entry, extracted_at) if entry and entry.get("id") else None)
            for name, entry in (values.get("part") or {}).items()}
    by_id = {row["id"]: row for row in rows}
    by_id.update({row["id"]: row for row in part.values() if row})
    unmatched = []
    mapping: dict[str, str | None] = {}
    by_map: dict[str, set[str]] = {}              # row id → the products on it only because a map put them there
    for index, op in enumerate((review or {}).get("ops") or []):
        kind = op.get("op")
        who = {"by": op.get("author"), "at": op.get("at")}
        if kind == "add":
            row = dict(op.get("row") or {})
            new = {"id": row.get("id"), "key": row.get("key"), "value": row.get("value"), "unit": row.get("unit"),
                   "raw": row.get("raw"), "condition": row.get("condition"), "grounding": row.get("grounding"),
                   "rejected": False, "human": True, "products": [], "provenance": dict(who)}
            if op.get("note"):
                new["note"] = op["note"]
            rows.append(new)
            by_id[new["id"]] = new
            continue
        if kind == "map":
            model, product = op.get("model"), op.get("product")
            old = mapping.get(model)
            mapping[model] = product
            for row in rows:
                if row["human"] or not read_for(row, model):
                    continue
                mapped = by_map.setdefault(row["id"], set())
                if old and old != product and old in mapped and \
                        not any(other != model and target == old and read_for(row, other)
                                for other, target in mapping.items()):
                    row["products"] = [p for p in row["products"] if p != old]
                    mapped.discard(old)
                if product and product not in row["products"]:
                    row["products"] = row["products"] + [product]
                    mapped.add(product)
            continue
        if kind in ("assign", "unassign"):
            wanted = op.get("value_ids") or []
            targets = rows if "*" in wanted else [by_id[i] for i in wanted if i in by_id]
            if not targets and wanted:
                unmatched.append(index)
            for row in targets:
                by_map.get(row["id"], set()).difference_update(op.get("products") or [])
                if kind == "assign":
                    row["products"] = row["products"] + [p for p in op.get("products") or []
                                                         if p not in row["products"]]
                else:
                    row["products"] = [p for p in row["products"] if p not in (op.get("products") or [])]
            continue
        row = by_id.get(op.get("value_id"))
        if row is None:
            unmatched.append(index)
            continue
        if kind == "edit":
            field = op.get("field")
            if not row["human"] and field not in row.setdefault("ai", {}):
                row["ai"][field] = copy.deepcopy(row.get(field))
            row[field] = copy.deepcopy(op.get("to"))
            row["provenance"] = dict(who)
            if op.get("note"):
                row["note"] = op["note"]
        elif kind == "reject":
            row["rejected"] = True
        elif kind == "restore":
            row["rejected"] = False
    return {"values": rows, "part": part, "models": {model: product for model, product in mapping.items() if product},
            "unmatched": unmatched}


def publishable(row: dict) -> bool:
    """A row a datasheet may carry: not rejected, and a human row or one the checker kept."""
    return not row["rejected"] and (row["human"] or row.get("verdict") == "kept")


def products_of(record: dict) -> list[str]:
    """Every product some publishable row is assigned to, in first-assigned order."""
    out = []
    for row in record["values"]:
        if publishable(row):
            out += [product for product in row["products"] if product not in out]
    return out


def published(product_id: str, record: dict, source: dict) -> dict:
    """One product's datasheet (CONTRACT §5) from the effective record and values.json's `source`."""
    values = []
    for row in record["values"]:
        if not publishable(row) or product_id not in row["products"]:
            continue
        provenance = dict(row["provenance"])
        if row.get("ai"):
            provenance["ai"] = copy.deepcopy(row["ai"])
        values.append({"id": row["id"], "key": row["key"], "value": row["value"], "unit": row["unit"],
                       "raw": row["raw"], "grounding": row["grounding"], "provenance": provenance})
    return {"product": product_id,
            "source": {name: (source or {}).get(name) for name in ("sha256", "url", "retrieved_at")},
            "values": values}


def check_op(op: dict, values: dict | None, review: dict | None, known_products: set[str] | None = None) -> str | None:
    """Why `op` cannot be appended, or None. The server stamps `author` and `at` itself."""
    kind = op.get("op")
    if kind not in OPS:
        return f"op is one of {', '.join(OPS)}, not {kind!r}"
    ids = value_ids(values, review)
    if kind == "add":
        row = op.get("row")
        if not isinstance(row, dict) or not row.get("key"):
            return "add needs a row with at least a key"
        if row.get("id") in ids:
            return f"value id {row['id']!r} already exists"
        return None
    if kind == "map":
        if not isinstance(op.get("model"), str) or not op["model"].strip():
            return "map needs the model as the datasheet prints it"
        if "product" not in op or not (op["product"] is None or isinstance(op["product"], str) and op["product"]):
            return "map needs a product id (null unmaps the model)"
        if op["product"] is not None and known_products is not None and op["product"] not in known_products:
            return f"unknown product {op['product']!r}"
        return None
    if kind in ("assign", "unassign"):
        products, wanted = op.get("products"), op.get("value_ids")
        if not isinstance(products, list) or not products or not all(isinstance(p, str) for p in products):
            return f"{kind} needs a non-empty products list"
        if known_products is not None:
            unknown = [p for p in products if p not in known_products]
            if unknown:
                return f"unknown products {unknown}"
        if not isinstance(wanted, list) or not wanted:
            return f"{kind} needs value_ids (a list; \"*\" = all)"
        missing = [i for i in wanted if i != "*" and i not in ids]
        return f"value ids {missing} are not in values.json" if missing else None
    if op.get("value_id") not in ids:
        return f"value id {op.get('value_id')!r} is not in values.json"
    if kind == "edit":
        if op.get("field") not in EDITABLE:
            return f"edit's field is one of {', '.join(EDITABLE)}, not {op.get('field')!r}"
        if "to" not in op:
            return "edit needs `to`"
    return None
