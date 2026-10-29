"""The consumer's field list (CONTRACT §7 `fields.json`) and the unit spellings the checker reads.

WHY plain-string labels compiled back into patterns: the contract hands the extractor words ("Rated torque",
"额定扭矩"), not regexes, so any consumer can write a fields.json. A label matches case-insensitively with any run
of spaces or hyphens where it has one ("no load speed" reads "No-Load Speed"), an optional dot where it has one, and
— for a label that starts with an ASCII letter — only at the start of a word, which is robotparts' vocab.py rule
(review F1 there: 'ratio' matched inside 'Operation'). A label of three letters or fewer ('kv', 'od', 'can') must
also end a word.

`UNIT_FORMS` and `DIAMETER_MARKS` are copied from robotparts' tools/ingestor/vocab.py: they are spellings of
units and signs, not knowledge about robot parts, and the copied binder (bind.py) reads them.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re

_SEP = r"\s*[·.*•⋅∙\-]?\s*"
_SQ = r"\s*(?:²|\^?2)"
# Surface forms of the unit labels. Lookarounds keep "Nm" from matching inside "Nm/A" or "mNm",
# and "g" from matching inside "kg" or "g·cm²". (robotparts vocab.py UNIT_FORMS, verbatim.)
UNIT_FORMS: dict[str, str] = {
    "N·m": rf"(?<![A-Za-zμµ])N{_SEP}[mM](?![A-Za-z/])",
    "mN·m": rf"(?<![A-Za-z])mN{_SEP}m(?![A-Za-z/])",
    "rpm": r"(?i)(?<![a-z])(?:rpm|r/min|rev/min|r\.p\.m\.?)(?![a-z])|转/分",
    "rad/s": r"(?i)(?<![a-z])rad\s*/\s*s(?![a-z])",
    "deg/s": r"(?i)(?:(?<![a-z])deg|°)\s*/\s*s(?![a-z])",
    ":1": r"\d\s*[:：]\s*\d",
    "kg": r"(?i)(?<![a-z])kg(?![a-z])|千克|公斤",
    "g": rf"(?<![A-Za-z])[gG](?![A-Za-z]|{_SEP}[cm]m)|(?i:(?<![a-z])grams?(?![a-z]))|克",
    "mm": r"(?i)(?<![a-z])mm(?![a-z²]|\^?2)|毫米",
    "m": r"(?<![A-Za-z])m(?![A-Za-z/²]|\^?2)",
    "V": r"(?<![A-Za-z])V(?:DC|dc|AC|ac)?(?![A-Za-z0-9])|\(\s*v\s*\)|(?<=\d)\s?v(?![A-Za-z0-9])|伏",
    "W": r"(?<![A-Za-z])W(?![A-Za-z0-9])|瓦",
    "arcmin": r"(?i)arc\s*-?\s*min|′|(?<=\d)\s*'|角分|弧分",
    "deg": r"(?i)(?<![a-z])deg(?:rees?)?(?![a-z/])|°(?!\s*/|\s*C)",
    "rad": r"(?i)(?<![a-z])rad(?![a-z/])",
    "mrad": r"(?i)(?<![a-z])mrad(?![a-z/])",
    "kg·m²": rf"(?i)(?<![a-z])kg{_SEP}m{_SQ}",
    "kg·cm²": rf"(?i)(?<![a-z])kg{_SEP}cm{_SQ}",
    "kg·mm²": rf"(?i)(?<![a-z])kg{_SEP}mm{_SQ}",
    "g·cm²": rf"(?i)(?<![a-z])g{_SEP}cm{_SQ}",
    "g·mm²": rf"(?i)(?<![a-z])g{_SEP}mm{_SQ}",
    "rpm/V": r"(?i)rpm\s*/\s*v(?![a-z])|(?<![a-z])kv(?![a-z])",
    "Ω": r"(?<![mkMμµ])Ω|(?i:(?<![a-z])ohms?(?![a-z]))|欧",
    "mΩ": r"mΩ|(?i:(?<![a-z])m\s*ohms?(?![a-z])|milliohms?)|毫欧",
}
DIAMETER_MARKS = "ΦφФф⌀Øø"


def label_pattern(label: str) -> str:
    """One plain label as a tolerant, case-insensitive pattern (see the module docstring)."""
    out = []
    for char in label.strip():
        if char.isspace() or char == "-":
            if not out or out[-1] != r"[\s\-]*":
                out.append(r"[\s\-]*")
        elif char == ".":
            out.append(r"\.?")
        else:
            out.append(re.escape(char))
    pattern = "".join(out)
    if re.match(r"[A-Za-z]", label):
        pattern = rf"(?<![A-Za-z]){pattern}"
        if len(re.sub(r"[^A-Za-z]", "", label)) <= 3:
            pattern += r"(?![A-Za-z])"
    return pattern


@dataclasses.dataclass(frozen=True)
class Field:
    key: str
    labels: tuple[str, ...]          # compiled-from-words patterns (bind.py reads `labels` and `marks`)
    words: tuple[str, ...]           # the labels as fields.json gives them
    units: tuple[str, ...]
    kind: str                        # number | range | text
    per_model: bool
    integer: bool = False
    guidance: str = ""
    definition: str = ""
    marks: tuple[str, ...] = ()
    allowed: tuple[str, ...] = ()    # for a text field: the names a value may use

    @property
    def is_text(self) -> bool:
        return self.kind == "text"


def load(path: pathlib.Path) -> tuple[Field, ...]:
    rows = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    fields = []
    for row in rows:
        words = tuple(row["labels"])
        fields.append(Field(key=row["key"], labels=tuple(label_pattern(word) for word in words), words=words,
                            units=tuple(row.get("units") or [""]), kind=row.get("kind", "number"),
                            per_model=bool(row.get("per_model", False)), integer=bool(row.get("integer", False)),
                            guidance=row.get("guidance", ""), definition=row.get("definition", ""),
                            marks=tuple(row.get("marks") or ()), allowed=tuple(row.get("allowed") or ())))
    return tuple(fields)


def unit_found(unit: str, text: str) -> bool:
    if unit == "":
        return True
    pattern = UNIT_FORMS.get(unit)
    return bool(pattern and re.search(pattern, text))


def label_found(field: Field, text: str) -> bool:
    """The text names the field somewhere: a label, or a mark in a cell."""
    return any(re.search(label, text, re.IGNORECASE) for label in field.labels + field.marks)


def by_key(fields: tuple[Field, ...]) -> dict[str, Field]:
    return {field.key: field for field in fields}
