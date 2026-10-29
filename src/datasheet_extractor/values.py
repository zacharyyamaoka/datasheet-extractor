"""What every option shares: reading a proposed value, the cross-value rules, and the values.json document
(CONTRACT §7 `values.json`).

`value_readable_in_raw`, `parse_value` and the repeat/ambiguity rule are robotparts tools/ingestor/check.py's
(copied 2026-10-29): a value is kept only when the number is printed in `raw` as a number of its own, with no
conversion and no arithmetic.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib
import re
import unicodedata

from . import TOOL, VERSION, bind
from .fields import Field

SCHEMA = "extractor/values@0"


def squash(text: str) -> str:
    return " ".join(text.split())


def normal(text: str) -> str:
    """NFKC and whitespace-normalised, for comparing what the model copied with what the source prints. WHY NFKC:
    the model may write 'N·m' for 'N⋅m' or a full-width colon; the digits it copies are what is compared."""
    return squash(unicodedata.normalize("NFKC", text or ""))


def number_forms(value) -> set[str]:
    forms = {repr(value), f"{value:g}", format(float(value), "f").rstrip("0").rstrip(".")}
    if float(value).is_integer():
        forms.add(str(int(value)))
    return {form for form in forms if form}


def value_readable_in_raw(value, raw: str) -> bool:
    text = squash(raw).lower()
    compact = text.replace(" ", "")
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return squash(value).lower() in text
    if isinstance(value, (int, float)):
        variants = {compact, compact.replace(",", "."), compact.replace(",", "")}
        return any(form in variant for form in number_forms(value) for variant in variants)
    if isinstance(value, list):
        return bool(value) and all(value_readable_in_raw(item, raw) for item in value)
    return False


def printed_numbers(raw: str) -> list[tuple[str, list[float]]]:
    return [(match.group(0), bind.token_value(match.group(0))) for match in bind.NUMBER.finditer(raw)]


def parse_value(printed: str, field: Field):
    """The model's value string -> the JSON value, in the source's own unit. Raises ValueError."""
    printed = (printed or "").strip()
    if field.is_text:
        names = [item.strip().lower() for item in printed.split(",") if item.strip()]
        bad = [name for name in names if field.allowed and name not in field.allowed]
        if not names or bad:
            raise ValueError(f"{bad or printed!r} are not allowed names ({', '.join(field.allowed)})")
        return list(dict.fromkeys(names))
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", printed):
        raise ValueError(f"{printed!r} is not a number as printed (digits, one decimal point or comma)")
    options = bind.token_value(printed)
    if len(options) != 1:
        raise ValueError(f"{printed!r} is ambiguous (decimal comma or thousands separator)")
    number = options[0]
    if field.integer:
        if not number.is_integer():
            raise ValueError(f"{printed!r} is not an integer")
        return int(number)
    if re.fullmatch(r"\d+", printed):
        return int(printed)
    return number


def value_problems(field: Field, value, printed: str, raw: str) -> list[str]:
    """Rule: the value is printed in raw as a number of its own (no conversion, no arithmetic)."""
    p = []
    if field.is_text:
        if not isinstance(value, list) or not value:
            return [f"value {value!r} is not a list of names"]
        text = normal(raw).lower()
        missing = [name for name in value if not re.search(r"(?<![a-z0-9])" + re.escape(name).replace(r"\-", r"[-\s]?"),
                                                            text)]
        return [f"{missing!r} is not printed in raw {raw!r}"] if missing else []
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return [f"value {value!r} is not a number"]
    if value <= 0:
        p.append(f"value {value!r} must be > 0")
    tokens = printed_numbers(normal(raw))
    if printed not in [token for token, _ in tokens]:
        p.append(f"value {printed!r} is not printed as a number of its own in raw {raw!r}")
    if not any(float(value) == option for _, options in tokens for option in options):
        p.append(f"no number printed in raw {raw!r} equals {value!r}")
    if not value_readable_in_raw(value, normal(raw)):
        p.append(f"value {value!r} cannot be read in raw {raw!r}")
    return p


@dataclasses.dataclass
class Verdict:
    key: str
    value: object
    unit: str | None
    raw: str
    condition: str | None
    cite: dict | None
    grounding: list
    reasons: list[str]
    proposed: dict                # the model's own row, verbatim

    @property
    def kept(self) -> bool:
        return not self.reasons

    def to_dict(self) -> dict:
        row = {"key": self.key, "value": self.value, "unit": self.unit, "raw": self.raw, "condition": self.condition}
        if self.cite is not None:
            row["cite"] = self.cite
        row.update({"grounding": self.grounding, "verdict": "kept" if self.kept else "dropped",
                    "reasons": self.reasons, "proposed": self.proposed})
        return row


def cross_check(verdicts: list[Verdict]) -> None:
    """One key given twice: the same value keeps the first, different values drop both (ambiguous)."""
    by_key: dict[str, list[Verdict]] = {}
    for verdict in verdicts:
        if verdict.kept:
            by_key.setdefault(verdict.key, []).append(verdict)
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        values = {(repr(v.value), v.unit) for v in group}
        if len(values) == 1:
            for extra in group[1:]:
                extra.reasons.append(f"a repeat of {key!r} (the first is kept)")
        else:
            for verdict in group:
                verdict.reasons.append(f"ambiguous: the source gives {key!r} as {sorted(values)}")


def sha256_of(path: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def source_record(path: pathlib.Path) -> dict:
    """sha256, url and retrieved_at of the source: from a cache sidecar `<file>.json` (robotparts' and
    Markdownify's fetch record) when there is one, else the file itself."""
    path = pathlib.Path(path).resolve()
    record = {"sha256": sha256_of(path), "url": path.as_uri(), "retrieved_at": None}
    for sidecar in (path.with_name(path.name + ".json"), path.parent / "source.json"):
        if sidecar.is_file():
            try:
                meta = json.loads(sidecar.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if meta.get("sha256") not in (None, record["sha256"]):
                continue
            retrievals = meta.get("retrievals") or []
            if retrievals:
                record["url"] = retrievals[-1].get("url") or record["url"]
                record["retrieved_at"] = retrievals[-1].get("retrieved_at")
            break
    return record


def _place(cite: dict | None, proposed: dict | None, checked=None, quote: str = "") -> str:
    """Where a value is, for its id: the cite id (Option 1), else `p<page>` (Option 2 PDF/image) or `html`."""
    cite_id = ((cite or {}).get("id") or "").strip()
    if cite_id:
        return cite_id
    if checked is not None:                             # an Option 2 part: no page of its own, so find the quote
        if checked.media_type == "text/html":
            return "html"
        found = next((n for n, page in enumerate(checked.squashed, 1) if squash(quote) and squash(quote) in page), None)
        return f"p{found}" if found else "p?"
    if proposed is not None and "page" in proposed:     # only the image/PDF schema asks the model for a page
        return f"p{proposed['page']}"
    return "html"


def assign_ids(rows: list[tuple[str, dict, str]]) -> None:
    """Set row["id"] = "<key>@<place>" on each (key, row, place), suffixing #2, #3 on a repeat in output order.
    WHY: a human review (review.json, Markdownify CONTRACT §7.1) points at values by id, so an id must be stable for
    a given file: it depends only on the key, the cite or page, and the order of the output."""
    seen: dict[str, int] = {}
    for key, row, place in rows:
        base = f"{key}@{place}"
        seen[base] = seen.get(base, 0) + 1
        row["id"] = base if seen[base] == 1 else f"{base}#{seen[base]}"


def document(source: dict, pipeline: str, model: str | None, part: dict, verdicts: list[Verdict],
             checked=None) -> dict:
    """`checked`: the Option 2 `Source`, to place a part's quote on a page for its id."""
    rows = [verdict.to_dict() for verdict in verdicts]
    part = {name: (dict(entry) if entry else entry) for name, entry in part.items()}
    ids = [(verdict.key, row, _place(verdict.cite, verdict.proposed)) for verdict, row in zip(verdicts, rows)]
    ids += [(f"part.{name}", entry, _place(entry.get("cite"), None, checked, entry.get("quote") or ""))
            for name, entry in part.items() if entry]
    assign_ids(ids)
    return {"schema": SCHEMA, "source": source,
            "extractor": {"tool": TOOL, "version": VERSION, "model": model, "pipeline": pipeline},
            "part": part, "values": rows}
