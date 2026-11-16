"""Copy as Spec: a source's kept rows as paste-ready `"key": Spec(...)` entries for a robotparts part file's
`specs={...}` dict. Pure functions: no disk, no clock; `server.App.as_spec` and `extract as-spec` feed them.

WHY the app's job ends here (Zach, 2026-11-16): his part files under robotparts/user/parts/ are the single source of
truth for a part, so the Sources app never writes one; it hands over lines he pastes in, each carrying where the value
was read (source_url, raw, quote, page, a note with the capture date and the saved copy's hash).
"""

from __future__ import annotations

import json
import math
import pathlib
import re
import tomllib
from urllib.parse import urlsplit

from .effective import publishable


def kept_rows(record: dict, product: str | None = None) -> list[dict]:
    """The rows Publish would carry: not rejected, a human row or one the checker kept. `product` narrows them to the
    rows assigned to that product."""
    return [row for row in record["values"] if publishable(row) and (product is None or product in row["products"])]


def _text(value: str) -> str:
    """A Python string literal. WHY json: it escapes quotes, backslashes and control characters, keeps '·' and 'Ω'
    as they are, and every escape it writes (\\n, \\t, \\", \\\\, \\u00XX) means the same in Python."""
    return json.dumps(value, ensure_ascii=False)


def _number(value):
    """The value as a number when it is one or parses as one (a hand-typed '0.150'), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
        if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?|[+-]?\d+[eE][+-]?\d+", text):
            return float(text)
    return None


def _value(value) -> str:
    number = _number(value)
    if number is not None:
        return repr(number)
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return _text(value[0])
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "[" + ", ".join(_text(item) for item in value) + "]"
    return repr(value)


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


# robotparts' registry of each maker's own web domains, read at every call (never copied: a domain Zach adds there
# counts at the next Copy as Spec). WHY: a part file's `source_kind="manufacturer"` must cite one of its maker's
# domains (robotparts SCHEMA2.md §2), so that file, not a guess from the printed maker name, is the judge.
MAKERS_TOML = pathlib.Path("/home/bam/robotparts/makers.toml")
# Shops that sell other makers' parts: a page there is a `retailer` page, unless the maker printed on it is the shop
# itself (makers.toml lists StepperOnline's own domain: its own gearbox there is its own page).
SHOPS = ("aifitlab.com", "alibaba.com", "aliexpress.com", "omc-stepperonline.com")


def load_makers(path: pathlib.Path | None = None) -> dict[str, dict]:
    """{maker slug: {name, domains}} from makers.toml, or {} when the file is missing or unreadable."""
    try:
        with open(path or MAKERS_TOML, "rb") as handle:
            table = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {slug: {"name": entry.get("name") or slug, "domains": [str(d).lower() for d in entry.get("domains") or []]}
            for slug, entry in table.items() if isinstance(entry, dict)}


def _on(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _is_maker(printed: str | None, slug: str, maker: dict) -> bool:
    """The maker name the datasheet printed is this registry maker (by slug or name), whole words aside."""
    name = _alnum(printed or "")
    return len(name) >= 3 and any(len(own) >= 3 and (own in name or name in own)
                                  for own in (_alnum(slug), _alnum(maker["name"])))


def source_kind(source_url: str | None, manufacturer: str | None,
                makers: dict[str, dict] | None = None) -> tuple[str, bool]:
    """(`manufacturer` | `retailer`, assumed). In order:
    1. a web host on a shop (SHOPS) is a `retailer`, unless the maker the datasheet printed is the maker that owns that
       domain in makers.toml (StepperOnline's own gearbox on omc-stepperonline.com);
    2. a host on any maker's domain in makers.toml is `manufacturer`;
    3. else the app knows the maker only as the name the datasheet printed (values.json `part.manufacturer`): a host
       that carries that name (or a word of it) is the maker's page, any other host a retailer;
    4. with no maker name, or a saved file with no host, it says `manufacturer` and `assumed`.
    `makers` is the registry (`load_makers`); None reads makers.toml now, and a missing file is no maker at all."""
    host = urlsplit(source_url or "").hostname if (source_url or "").startswith(("http://", "https://")) else None
    host = host.lower() if host else None
    if not host:
        return "manufacturer", True
    registry = load_makers() if makers is None else makers
    owner = next(((slug, maker) for slug, maker in registry.items() if any(_on(host, d) for d in maker["domains"])), None)
    if any(_on(host, shop) for shop in SHOPS):
        return ("manufacturer" if owner and _is_maker(manufacturer, *owner) else "retailer"), False
    if owner:
        return "manufacturer", False
    if not _alnum(manufacturer or ""):
        return "manufacturer", True
    name, words = _alnum(manufacturer), [_alnum(word) for word in re.split(r"\W+", manufacturer)]
    mine = name in _alnum(host) or any(len(word) >= 4 and word in _alnum(host) for word in words)
    return ("manufacturer" if mine else "retailer"), False


def _quote(row: dict, texts: dict[str, str] | None) -> str | None:
    """The row's quote: the cited cell's printed text (else `raw`), led by its row label when the cell lacks it."""
    cite = row.get("cite") or {}
    cell = " ".join(((texts or {}).get(cite.get("id")) or row.get("raw") or "").split())
    if not cell:
        return None
    label = " ".join((cite.get("row") or "").split())
    return f"{label} {cell}" if label and label.lower() not in cell.lower() else cell


def _page(row: dict) -> int | None:
    grounding = row.get("grounding") or []
    grounding = grounding if isinstance(grounding, list) else [grounding]
    page = next((g.get("page") for g in grounding if isinstance(g, dict) and isinstance(g.get("page"), int)), None)
    return page


def _column_names(rows: list[dict]) -> dict[str, str]:
    """{column header as cited: the name a note gives it}, only when the rows come from more than one column (a
    variant table), else {}. WHY the shared leading headers are cut: Markdownify cites a column as its whole header
    path ('GIM4305-10 / Y / GDZ34'); the note needs only what tells the columns apart ('GDZ34')."""
    columns = {" ".join(((row.get("cite") or {}).get("column") or "").split()) for row in rows} - {""}
    if len(columns) < 2:
        return {}
    paths = {column: column.split(" / ") for column in columns}
    shared = 0
    while all(len(path) > shared + 1 for path in paths.values()) and len({path[shared] for path in paths.values()}) == 1:
        shared += 1
    return {column: " / ".join(path[shared:]) for column, path in paths.items()}


def as_spec(rows: list[dict], *, source_url: str | None, sha256: str | None, retrieved_at: str | None = None,
            manufacturer: str | None = None, paged: bool = False, texts: dict[str, str] | None = None,
            makers: dict[str, dict] | None = None) -> str:
    """One `    "key": Spec(value, "unit", source_url=…, source_kind=…, raw=…, quote=…, page=…, note=…),` line per
    row, ready to paste inside `specs={...}`; "" when there are no rows.

    - `texts`: the printed text of each cited cell or block, by cite id (the bundle's), for the quote.
    - `paged`: the source is pages (a PDF or image), so `page` means something; a web page has none.
    - the note carries, in order: the column the value came from (only when the rows came from more than one column of
      a table, i.e. a variant table), the row's condition, a reviewer's note, then `captured <day> sha256 <12 hex>`.
    - a key given twice keeps its first line; later ones are commented out, since a dict literal would drop them
      silently.
    - `source_kind` is `manufacturer` or `retailer` (`source_kind`, judged by robotparts' makers.toml, or `makers`);
      when it had to assume `manufacturer`, the block starts with a `# TODO` comment saying so.
    """
    if not rows:
        return ""
    kind, assumed = source_kind(source_url, manufacturer, makers)
    names = _column_names(rows)
    captured = " ".join(part for part in (f"captured {retrieved_at[:10]}" if retrieved_at else "",
                                          f"sha256 {sha256[:12]}" if sha256 else "") if part)
    lines = []
    if assumed:
        lines.append(f'    # TODO: source_kind "manufacturer" is a guess (no maker name is known for this source); '
                     f'use "retailer" if this is a shop page')
    seen: set[str] = set()
    for row in rows:
        column = names.get(" ".join(((row.get("cite") or {}).get("column") or "").split()))
        note = "; ".join(part for part in (f"{column} column" if column else "",
                                           row.get("condition") or "", row.get("note") or "", captured) if part)
        quote = _quote(row, texts)
        page = _page(row) if paged else None
        arguments = [_value(row.get("value")), _text(row.get("unit") or ""), f"source_url={_text(source_url or '')}",
                     f"source_kind={_text(kind)}"]
        if row.get("raw"):
            arguments.append(f"raw={_text(row['raw'])}")
        if quote:
            arguments.append(f"quote={_text(quote)}")
        if page is not None:
            arguments.append(f"page={page}")
        if note:
            arguments.append(f"note={_text(note)}")
        line = f"{_text(row['key'])}: Spec({', '.join(arguments)}),"
        if row["key"] in seen:
            lines.append(f"    # duplicate key, pick one: {line}")
        else:
            seen.add(row["key"])
            lines.append(f"    {line}")
    return "\n".join(lines) + "\n"
