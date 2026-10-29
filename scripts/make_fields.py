#!/usr/bin/env python3
"""Write fields/actuator.json (CONTRACT §7 `fields.json`) from robotparts' Ingestor vocabulary.

    uv run python scripts/make_fields.py [--robotparts /home/bam/robotparts] [--out fields/actuator.json]

WHY generated, not hand-written: robotparts' `tools/ingestor/vocab.py` is the vocabulary its 217 checked values
were extracted with, so comparing the two pipelines is fair only if both ask for exactly the same keys, labels and
units. robotparts is read, never written: the module is imported with bytecode writes off, so no `__pycache__`
lands in its tree.

What each field carries:
- `labels`: vocab.py's row-label regexes expanded into the plain strings they match ("max(?:imum|\\.)? torque" →
  "max torque", "maximum torque", "max. torque"), English and Chinese as vocab has them. The extractor compiles
  them back into tolerant patterns (fields.py), so the consumer hands over words, not regexes.
- `units`: the registry's unit labels for the key, in its order (the first is the SI-nearest the registry lists).
- `kind`: `number` (the registry's number and integer keys) or `text` (`buses`); `integer` marks pole pairs.
- `per_model`: true for every number, which a comparison table prints once per model; `buses` is false.
- `guidance` and `definition`: what the model must check before giving the key (vocab.py's guidance, the
  registry's definition), so Option 1 and Option 2 see the same instructions robotparts' prompt gave.
- `marks`: signs inside a value cell that name the key ('Φ98*61.7mm' is a diameter), from vocab.py.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

try:
    import re._parser as sre_parse  # Python 3.11+
    from re._constants import (ANY, ASSERT, ASSERT_NOT, AT, BRANCH, CATEGORY, IN, LITERAL, MAX_REPEAT, MIN_REPEAT,
                               NOT_LITERAL, SUBPATTERN)
except ImportError:  # pragma: no cover
    raise SystemExit("needs Python 3.11+ (re._parser)")

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAX_VARIANTS = 16


def _expand(parsed) -> list[str]:
    """Every string a (simple) parsed regex matches, with `\\s*`-like gaps read as one space."""
    variants = [""]
    for op, arg in parsed:
        if op == LITERAL:
            options = [chr(arg)]
        elif op in (ASSERT, ASSERT_NOT, AT):
            continue  # lookarounds and anchors match no characters
        elif op == SUBPATTERN:
            options = _expand(arg[3])
        elif op == BRANCH:
            options = [text for branch in arg[1] for text in _expand(branch)]
        elif op in (MAX_REPEAT, MIN_REPEAT):
            low, high, inner = arg
            inner_options = _expand(inner)
            gap = all(not text.strip() for text in inner_options)
            if gap:
                options = [" "] if high > 1 else ([""] if low == 0 else [" "])
                if high == 1 and low == 0:
                    options = ["", " "]
            elif high == 1:
                options = ([""] if low == 0 else []) + inner_options
            else:
                options = inner_options
        elif op == IN:
            members = [item for item in arg]
            if any(kind == CATEGORY for kind, _ in members):
                options = [" "]  # \s inside a class: a gap
            else:
                literals = [chr(value) for kind, value in members if kind == LITERAL]
                options = literals[:1] or [""]
                if "-" in literals and " " not in options:
                    options = [" "]  # '[\s-]' gaps print as a space
        elif op == CATEGORY:
            options = [" "]
        elif op in (ANY, NOT_LITERAL):
            options = [""]
        else:
            options = [""]
        variants = [left + right for left in variants for right in options][: MAX_VARIANTS * 4]
    return variants


def label_strings(pattern: str) -> list[str]:
    """The plain labels a vocab.py regex stands for, deduplicated, case kept as written (lower case)."""
    pattern = pattern.replace("(?i)", "")
    out: list[str] = []
    for text in _expand(sre_parse.parse(pattern)):
        text = " ".join(text.split())
        text = re.sub(r"\s+([.])", r"\1", text)
        text = text.replace(" - ", "-")  # 'pole\s*-?\s*pairs?' reads 'pole-pair', not 'pole - pair'
        if text and text.lower() not in (seen.lower() for seen in out):
            out.append(text)
    return out[:MAX_VARIANTS]


def load_vocab(robotparts: pathlib.Path):
    sys.dont_write_bytecode = True  # WHY: robotparts is read-only to this project
    sys.path.insert(0, str(robotparts / "tools"))
    from ingestor import vocab  # noqa: E402
    return vocab


def build(robotparts: pathlib.Path) -> list[dict]:
    vocab = load_vocab(robotparts)
    registry = vocab.registry()
    fields = []
    for entry in vocab.vocabulary("actuator"):
        reg = registry["keys"][entry.key]
        labels: list[str] = []
        for pattern in entry.labels:
            # vocab's _word() prefixes ASCII labels with a start-of-word lookbehind; it matches no characters
            for text in label_strings(pattern):
                if text.lower() not in (seen.lower() for seen in labels):
                    labels.append(text)
        number = reg["value"] in ("number", "integer")
        field = {
            "key": entry.key,
            "labels": labels,
            "units": list(reg["units"]),
            "kind": "number" if number else "text",
            "per_model": number,
        }
        if reg["value"] == "integer":
            field["integer"] = True
        field["guidance"] = entry.guidance
        field["definition"] = reg["definition"]
        if entry.marks:
            field["marks"] = list(entry.marks)
        if not number:
            field["allowed"] = registry["buses"] + registry["protocols"]
        fields.append(field)
    return fields


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--robotparts", type=pathlib.Path, default=pathlib.Path("/home/bam/robotparts"))
    parser.add_argument("--out", type=pathlib.Path, default=ROOT / "fields" / "actuator.json")
    arguments = parser.parse_args()
    fields = build(arguments.robotparts)
    arguments.out.write_text(json.dumps(fields, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{arguments.out}: {len(fields)} fields, {sum(len(f['labels']) for f in fields)} labels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
