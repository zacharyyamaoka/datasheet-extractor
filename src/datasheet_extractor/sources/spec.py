"""Copy as Spec: a source's kept rows as paste-ready robotparts lines, in one of two forms. Pure functions: no disk, no
clock; `server.App.as_spec` and `extract as-spec` feed them.

- **Source file** (`as_stated`, the default): a whole `user/parts/<id>/sources/<name>.py`, robot_models' form since
  2026-11-17 (PEP 0003): a `Source(...)` header that pins the raw file by sha256, then `values = <Type>(key=Stated(
  value, unit, as_written, where=...), ...)`. `as_written` is the label as the source prints it; `where` is a
  source_msgs selector for the words that print the value (locate.py), or for the cited cell when they were not
  located, and the value's note says so.
- **Spec lines** (`as_spec`, `--spec`): `"key": Spec(...)` entries for a catalogue `parts/<id>/part.py`'s
  `specs={...}`. robotparts' `Spec` has no `where` field yet, so the selector rides in the note (`where page=1&…`).

WHY the app's job ends here (Zach, 2026-11-16): his part files under robotparts/user/parts/ are the single source of
truth for a part, so the Sources app never writes one; it hands over text he pastes in, each value carrying where it
was read.
"""

from __future__ import annotations

import importlib
import json
import math
import os
import pathlib
import re
import sys
import textwrap
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
            makers: dict[str, dict] | None = None, wheres: dict | None = None) -> str:
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
    - `wheres`: {row id: locate.Where}; the selector goes in the note (`where page=1&xywh=…`), before the capture
      date. WHY the note: robotparts' Spec has no `where` field (python/robotparts/types.py), and an unknown keyword
      would not construct; once Spec takes `where=`, it moves to its own key.
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
        found = (wheres or {}).get(row.get("id"))
        where_note = ""
        if found is not None and found.selector:
            where_note = f"where {found.selector}" + (f" ({found.note})" if found.note else "")
        note = "; ".join(part for part in (f"{column} column" if column else "", row.get("condition") or "",
                                           row.get("note") or "", where_note, captured) if part)
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


# --- the source-file form (robot_models Stated) ------------------------------------------------------------------------

GENERIC_TYPE = ("robot_models.datasheet", "Datasheet")


def _identifier(key: str) -> bool:
    return key.isidentifier() and not key.startswith("_")


def file_name(source_url: str | None, manufacturer: str | None, media_type: str | None) -> str:
    """A name for the source's pair of files (`<name>.py` beside `<name>.<ext>`), robotparts' pattern: who printed it,
    then what it is (`steadywin_page`, `aifitlab_page`, `meanwell_datasheet`)."""
    host = urlsplit(source_url or "").hostname if (source_url or "").startswith(("http://", "https://")) else None
    who = ""
    if host:
        parts = [part for part in host.lower().split(".") if part not in ("www", "en", "m", "shop", "store")]
        who = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")
    who = re.sub(r"[^a-z0-9]+", "_", (who or manufacturer or "source").lower()).strip("_") or "source"
    what = "page" if (media_type or "").startswith("text/html") else \
        "image" if (media_type or "").startswith("image/") else "datasheet"
    name = f"{who}_{what}"
    return name if name[0].isalpha() else f"source_{name}"


# robot_models, read only to tell a type's typed fields from its `extra` (never needed to emit the text). WHY the
# sibling checkout as a fallback: robotparts' own user/ package finds it the same way (ROBOT_MODELS_SRC overrides).
ROBOT_MODELS_SRC = pathlib.Path(os.environ.get("ROBOT_MODELS_SRC", "/home/bam/robot_models/src"))
# The actuator types a source's fields are matched against when the part names none (most fields stated wins).
ACTUATOR_TYPES = (("robot_models.actuators.servo", "IntegratedServo"), ("robot_models.actuators.servo", "BldcMotor"),
                  ("robot_models.actuators.servo", "Gearbox"))


def _type(part_type: tuple[str, str]):
    module, name = part_type
    try:
        return getattr(importlib.import_module(module), name)
    except ImportError:
        if not ROBOT_MODELS_SRC.is_dir() or str(ROBOT_MODELS_SRC) in sys.path:
            return None
        sys.path.append(str(ROBOT_MODELS_SRC))
        try:
            return getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError):
            return None
    except AttributeError:
        return None


def typed_fields_of(part_type: tuple[str, str] | None) -> set[str] | None:
    """The typed field names of a robot_models type; set() for the generic `Datasheet`; None when robot_models is not
    importable here (then every key that is a Python name is written as a keyword)."""
    if part_type is None or tuple(part_type) == GENERIC_TYPE:
        return set()
    found = _type(part_type)
    return set(found.typed_fields()) if found is not None and hasattr(found, "typed_fields") else None


def guess_type(keys: list[str]) -> tuple[str, str] | None:
    """The actuator type whose typed fields cover most of `keys` (ties: integrated servo, then motor, then gearbox);
    None when robot_models is not importable or no key is covered."""
    best, best_count = None, 0
    for candidate in ACTUATOR_TYPES:
        fields = typed_fields_of(candidate)
        if fields is None:
            return None
        count = len(set(keys) & fields)
        if count > best_count:
            best, best_count = candidate, count
    return best


def as_stated(rows: list[dict], *, source_url: str | None, sha256: str | None, retrieved_at: str | None = None,
              manufacturer: str | None = None, model: str | None = None, media_type: str | None = None,
              name: str | None = None, extension: str | None = None, part_type: tuple[str, str] | None = None,
              type_guessed: bool = False, typed_fields: set[str] | None = None, wheres: dict | None = None,
              extractor: dict | None = None, makers: dict[str, dict] | None = None) -> str:
    """A whole robot_models source file for the kept `rows`, ready to save as `sources/<name>.py` beside the raw file
    `<name><extension>` (the bundle's `source.<ext>`, the bytes `sha256` pins); "" when there are no rows.

    - `part_type`: (module, class) the part resolves its sources with (`products.part_type`); None is robot_models'
      generic `Datasheet`, every value under `extra`. `type_guessed` adds a `# TODO` to check it.
    - `typed_fields`: the type's typed field names; a key outside them goes in `extra={...}`. None: every key that is a
      Python name is written as a keyword (robot_models refuses an unknown one at load, naming the nearest field).
    - `wheres`: {row id: locate.Where}. A value marked by its OCR line or its cell only says so in its note; one with
      no location has no `where` and keeps `as_written` as its anchor.
    - `as_written` is the label the checker read, as the source prints it (`cite.label`); a value whose label is
      inside its own sentence has none.
    - a key given twice keeps its first line; later ones are commented out, as in `as_spec`.
    """
    if not rows:
        return ""
    kind, assumed = source_kind(source_url, manufacturer, makers)
    module, type_name = part_type or GENERIC_TYPE
    if (module, type_name) == GENERIC_TYPE:
        typed_fields = set()
    name = name or file_name(source_url, manufacturer, media_type)
    extension = extension or ""
    names = _column_names(rows)
    web = (source_url or "").startswith(("http://", "https://"))
    host = urlsplit(source_url).hostname if web else None
    what = "web page" if (media_type or "").startswith("text/html") else \
        "image" if (media_type or "").startswith("image/") else "PDF" if media_type == "application/pdf" else "file"
    subject = " ".join(filter(None, (manufacturer, model))) or "this part"
    title = f"{subject} — {what}" + (f" ({host})" if host else "")
    tool = extractor or {}
    by = " ".join(filter(None, (tool.get("tool") or "datasheet-extractor", tool.get("version"))))
    read_with = f" ({tool['model']})" if tool.get("model") else ""
    about = (f"Read by {by}{read_with}: every value checked against the source by its checker and not rejected in "
             f"review (the Sources app's Copy as Spec). Each `where` marks the words that print the value in "
             f"{name}{extension}, the file the sha256 below pins (a source_msgs selector); a value marked only by its "
             f"line or cell says so in its note.")
    lines = ['"""' + f"{subject}, as the {what}" + (f" on {host}" if host else "") + " prints it.", "",
             *textwrap.wrap(about, 116), '"""']
    typed_import = f"from {module} import {type_name}" if (module, type_name) != GENERIC_TYPE else None
    if typed_import:
        lines.append(typed_import)
    lines.append("from robot_models.datasheet import " + ("Datasheet, " if not typed_import else "") + "Source, Stated")
    lines += ["", "source = Source(", f"    {_text(title)},"]
    if assumed:
        lines.append('    # TODO: "manufacturer" is a guess (no maker name is known for this source); "retailer" '
                     "if this is a shop page")
    lines.append(f"    kind={_text(kind)},")
    lines.append(f"    role={_text('product_page' if what == 'web page' else 'datasheet')},")
    if web:
        lines.append(f"    url={_text(source_url)},")
    lines.append(f"    file={_text(name + extension)},")
    if sha256:
        lines.append(f"    sha256={_text(sha256)},")
    if retrieved_at:
        lines.append(f"    read_on={_text(retrieved_at[:10])},")
    lines += [")", ""]
    if type_guessed or part_type is None:
        lines.append(f"# TODO: check the part type: {type_name} is "
                     + ("a guess from which fields the source states" if part_type else
                        "robot_models' generic one (every value under extra); use the part's own"))
    typed, extra, seen = [], [], set()
    for row in rows:
        key = row["key"]
        column = names.get(" ".join(((row.get("cite") or {}).get("column") or "").split()))
        found = (wheres or {}).get(row.get("id"))
        notes = [f"{column} column" if column else "", row.get("condition") or "", row.get("note") or "",
                 found.note if found is not None and found.note else ""]
        value = row.get("value")
        unit = row.get("unit")
        unit_text = "None" if _number(value) is None and not unit else _text(unit or "")
        label = (row.get("cite") or {}).get("label")
        arguments = [_value(value), unit_text] + ([_text(label)] if label else [])
        if found is not None and found.selector:
            arguments.append(f"where={_text(found.selector)}")
        note = "; ".join(part for part in notes if part)
        if note:
            arguments.append(f"note={_text(note)}")
        call = f"Stated({', '.join(arguments)})"
        bare = key[len("spec."):] if key.startswith("spec.") else key
        as_keyword = _identifier(key) and (typed_fields is None or key in typed_fields)
        line = f"{key}={call}," if as_keyword else f"{_text(bare)}: {call},"
        if key in seen:
            line = f"# duplicate key, pick one: {line}"
        seen.add(key)
        (typed if as_keyword else extra).append(line)
    lines.append(f"values = {type_name}(")
    lines += [f"    {line}" for line in typed]
    if extra:
        lines.append("    extra={")
        lines += [f"        {line}" for line in extra]
        lines.append("    },")
    lines.append(")")
    return "\n".join(lines) + "\n"
