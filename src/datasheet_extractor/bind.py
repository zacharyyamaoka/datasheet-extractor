"""Where a quote ties a number to its row label: the binding rule of the deterministic checker.

Copied from robotparts tools/ingestor/bind.py (2026-10-29) with three edits only: `Entry` is fields.py's `Field`,
a text field (`kind: text`) stands where robotparts said `string-list`, and the import paths. Option 2 runs it on
the model's verbatim quote exactly as robotparts does; Option 1 runs it on a cited prose block's own text. WHY
copied, not imported: robotparts is read-only to this project, and its rules are the yardstick Option 2 is held to.

WHY (review F1, 2026-10-22): the first checker asked only whether a quote carried *some* row label of the key
and whether the number appeared *somewhere* in it. A quote spanning a table header and a data row then let any
printed number pass as any key whose label sat anywhere in it (rated_torque = 442, which is the rated power),
let another model's row pass for this model (430 W, the V2.0 row on the V3.0 page), and let the two numbers of
'Ф98*61.7mm' swap. The values were right because the LLM was, not because the checker would have caught it.

So a quote is read as row labels and value cells, and a value is kept only where the text ties them together:

- A **value cell** is a number, or numbers joined into one cell: 'Ф98*61.7mm' (sizes), '18V~36V' (a range),
  '24/48' (alternatives at stated conditions), '320 rpm / 33.5 rad/s' (one value in two units), '9:1' (a ratio).
  Digits inside a unit ('g.cm2') are not numbers. Each number carries the unit printed after it, or else the
  one printed after a later number of its cell ('24/48 V').
- A cell's **row label** is the label right before it. Between them may sit only unit words, a parenthesised
  qualifier ('(Nm)', '(48 V)'), a condition ('@48V', 'at 48 V') and at most `MAX_GAP_WORDS` other words: no
  other number, no other key's label, no comma or 'and', no word that names another quantity ('Density'). Prose that puts the number first ('a 10:1
  gear reduction') binds to the label right after it, but only when nothing binds from before. A diameter sign
  labels its own cell: 'Ф98*61.7mm' is diameter × length.
- The value must sit at its key's place in the cell: a diameter is the first size and a length the second; a
  range's low end is voltage_min and its high end voltage_max; a ratio 'N:1' or '1:N' gives N. A value picked
  from alternatives ('24/48') must say its condition in `note`.
- The unit printed at the number must be the value's unit; with none at the number, the label must print it.
- On the page, the quote must not cut the cell short ('(rpm) 23' out of '(rpm) 235'), and the cell must not sit
  in a row of bare values ('Rated torque (Nm) 18 9 10', one column per model; a comparison block's rows): a
  label followed by several numbers picks out none of them. A tolerance after it ('248 ±10%') is no value.

- The value comes from this part's own content (review V2-1). On an HTML page, the label and the number must sit
  outside the site's navigation (nav, header, footer, aside, menu roles) and outside a link's text: CubeMars's
  menu lists the whole catalogue ('- AKE90-8 KV35'), and a link names another page, not a value of this one. Where
  a page has no structure (PDF), and on HTML too, the label may not follow a model designation other than the
  part's own: 'AKE90-8 KV35' is another model's name, 'AK10-9 V3.0 KV60' on the AK10-9 V3.0's page is its own.
  A part whose model is unknown has no own designation, so a value read off any designation is dropped.

Not done on purpose: aligning a table's header row with a data row by column index. The page text drops empty
cells (CubeMars's comparison block has an empty Operation column), so an index read from text can silently shift
a value into its neighbour's column, the very error this rule exists to stop. Such values are dropped instead.
"""

from __future__ import annotations

import dataclasses
import functools
import re

from .text import BREAK, LINK, NAVIGATION
from .fields import DIAMETER_MARKS, UNIT_FORMS, Field as Entry, unit_found

NUMBER = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)*")
# ':1' is a ratio, read as a cell; every other unit form is printed text that may carry digits ('g.cm2').
_UNITS = {label: re.compile(pattern) for label, pattern in UNIT_FORMS.items() if label != ":1"}
# A unit printed right after a number. WHY not the 'KV' form of rpm/V: 'KV60' is a label before its number, and
# the KV of 'Pole Pairs 21 KV (rpm/V) 60' is the next row's label, not the unit of 21.
_ATTACHED = dict(_UNITS, **{"rpm/V": re.compile(r"(?i)rpm\s*/\s*v(?![a-z])")})
_JOIN = re.compile(r"(\s*)(to|[*×xX~～∼–—\-/:∶：])(\s*)(?:[" + DIAMETER_MARKS + r"]\s*)?")
_KINDS = {"sizes": "*×xX", "range": "~～∼–—-", "alternatives": "/", "ratio": ":∶："}
# '@48V', 'at 48 V': a stated condition between a label and its number, not a number of its own.
_CONDITION = re.compile(r"(?:@|(?<![A-Za-z])at)\s*$", re.IGNORECASE)
_BREAK = re.compile(r"[,;，；、|&+]|(?<![A-Za-z])(?:and|or|while|plus|versus|vs)(?![A-Za-z])|和|及|与", re.IGNORECASE)
# Words that name a quantity of their own: one of them between a label and a number means the number is that
# quantity's ('Maximum Torque Density (Nm/kg) 56.4' is no peak torque).
_OTHER = re.compile(r"(?<![A-Za-z])(?:current|inductance|constant|density|resolution|temperature|efficiency|frequency|"
                    r"power|torque|speed|voltage|weight|mass|diameter|length|height|ratio|inertia|resistance|force|"
                    r"pressure|noise|kt|ke)(?![A-Za-z])|电流|电感|常数|密度|温度|效率|功率|电阻|转速|扭矩|转矩|电压|重量",
                    re.IGNORECASE)
_WORDS = re.compile(r"[A-Za-z]+|[一-鿿]")
_MARK = re.compile(r"[" + DIAMETER_MARKS + r"]\s*$")
# A row of values: another number right after the cell, or right before it (its unit aside). WHY no '±' here:
# '248 ±10%' is a value and its tolerance.
_AFTER = re.compile(r"\s*[≤≥<>~≈]?\s*[" + DIAMETER_MARKS + r"]?\s*\d")
_BEFORE = re.compile(r"\d[^\s\d]{0,6}\s*$")
# A model designation, ending right where a label (or a number-first cell) starts: 'AKE90-8 ', 'AK80-9 V3.0 ',
# 'DM-J8009-2EC '. It starts with a letter and carries a digit, so '48V', '(Nm)' and 'Rated' are none.
_DESIGNATION = re.compile(r"(?<![\w.\-/])([A-Za-z][A-Za-z\-]*\d[\w.\-]*(?:\s+V\d+(?:\.\d+)*)?)\s*$")
MAX_GAP_WORDS = 3
MAX_GAP_CHARS = 60
SIZE_KEYS = frozenset({"outer_diameter", "axial_length"})


@dataclasses.dataclass(frozen=True)
class Number:
    start: int
    end: int
    text: str
    options: tuple[float, ...]


@dataclasses.dataclass(frozen=True)
class Cell:
    numbers: tuple[Number, ...]
    joins: tuple[str, ...]
    kind: str               # single | sizes | range | alternatives | dual | ratio | mixed
    marked: bool            # a diameter sign right before the first number
    lead: int               # where the cell starts, its diameter sign included
    unit_end: int           # where the cell ends, its unit included
    units: tuple            # per number: the registry unit labels printed for it (frozenset, maybe empty)

    @property
    def start(self) -> int:
        return self.numbers[0].start

    @property
    def end(self) -> int:
        return self.numbers[-1].end


@dataclasses.dataclass(frozen=True)
class Label:
    start: int
    end: int
    keys: frozenset


@dataclasses.dataclass(frozen=True)
class Binding:
    keys: frozenset         # the keys the cell's label names; empty when nothing ties to it
    side: str               # before (label, then number) | after (number, then label) | mark (Φ) | ""
    label: Label | None
    region: tuple[int, int]  # label and gap: where a unit may be printed for the number
    why: str = ""            # when nothing ties to it, why not


def token_value(token: str) -> list[float]:
    """What a printed number can mean: '2.5' -> 2.5; '2,5' -> 2.5 (decimal comma); '1,000' -> 1000 or 1.0."""
    if "," in token and "." in token:
        return [float(token.replace(",", ""))]
    if "," in token:
        options = [float(token.replace(",", "."))] if token.count(",") == 1 else []
        if re.fullmatch(r"\d{1,3}(,\d{3})+", token):
            options.append(float(token.replace(",", "")))
        return options
    try:
        return [float(token)]
    except ValueError:
        return []


def squash(text: str) -> str:
    return " ".join(text.split())


def _unit_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for pattern in _UNITS.values() for m in pattern.finditer(text) if m.end() > m.start()]


def numbers_in(text: str) -> list[Number]:
    spans = _unit_spans(text)
    out = []
    for m in NUMBER.finditer(text):
        if any(a <= m.start() and m.end() <= b for a, b in spans):
            continue  # WHY: the 2 of 'g.cm2' belongs to the unit
        out.append(Number(m.start(), m.end(), m.group(0), tuple(token_value(m.group(0)))))
    return out


def unit_at(text: str, pos: int) -> tuple[int, frozenset]:
    """The registry units printed at `pos` (after at most one space), longest form first: (end, labels)."""
    best, labels = pos, set()
    for start in ((pos, pos + 1) if text[pos:pos + 1] == " " else (pos,)):
        for label, pattern in _ATTACHED.items():
            m = pattern.match(text, start)
            if m and m.end() > m.start():
                if m.end() > best:
                    best, labels = m.end(), {label}
                elif m.end() == best:
                    labels.add(label)
    return best, frozenset(labels)


def _kind(joins: list[str], units: list[frozenset]) -> str:
    if not joins:
        return "single"
    kinds = {"range" if join == "to" else next(k for k, chars in _KINDS.items() if join in chars) for join in joins}
    kind = kinds.pop() if len(kinds) == 1 else "mixed"
    if kind == "alternatives" and all(units) and len(set(units)) == len(units):
        return "dual"  # '320 rpm / 33.5 rad/s': one value in several units, not values at several conditions
    return kind


@functools.lru_cache(maxsize=256)
def cells_in(text: str) -> tuple[Cell, ...]:
    """Every value cell of a (whitespace-normalised) text, in order."""
    numbers = numbers_in(text)
    cells, index = [], 0
    while index < len(numbers):
        group, joins, own = [numbers[index]], [], []
        while index + 1 < len(numbers):
            current, following = numbers[index], numbers[index + 1]
            unit_end, here = unit_at(text, current.end)
            m = _JOIN.fullmatch(text, unit_end, following.start)
            if not m:
                break
            before, join, after = m.group(1), m.group(2), m.group(3)
            # WHY: '10.7 -20' is a number and a minus sign, and 'to' joins words only with spaces round it.
            if (join == "-" and before and not after) or (join == "to" and not (before and after)):
                break
            group.append(following)
            joins.append(join)
            own.append(here)
            index += 1
        unit_end, last = unit_at(text, group[-1].end)
        own.append(last)
        units, carry = [], frozenset()
        for printed in reversed(own):  # a unit after a later number covers the bare ones before it ('24/48 V')
            carry = printed or carry
            units.append(carry)
        units.reverse()
        window = max(0, group[0].start - 3)
        mark = _MARK.search(text, window, group[0].start)
        lead = mark.start() if mark else group[0].start
        cells.append(Cell(tuple(group), tuple(joins), _kind(joins, units), bool(mark), lead, unit_end, tuple(units)))
        index += 1
    return tuple(cells)


def _parens(text: str) -> list[tuple[int, int]]:
    spans, stack = [], []
    for position, char in enumerate(text):
        if char in "(（":
            stack.append(position)
        elif char in ")）" and stack:
            spans.append((stack.pop(), position + 1))
    return spans


def _strip_parens(text: str) -> str:
    for start, end in sorted(_parens(text), key=lambda span: span[0] - span[1]):
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def labels_in(text: str, entries: tuple[Entry, ...]) -> list[Label]:
    """The row labels of a text: a label inside parentheses is a qualifier, not a row label; where labels of
    different keys overlap the longest wins ('STD Operating Voltage' over 'Operating Voltage'), and one span
    names every key that shares it ('voltage range': voltage_min and voltage_max)."""
    parens = _parens(text)
    found = []
    for item in entries:
        for pattern in item.labels:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                if m.end() > m.start() and not any(a <= m.start() and m.end() <= b for a, b in parens):
                    found.append((m.start(), m.end(), item.key))
    found.sort(key=lambda f: (f[0] - f[1], f[0]))
    kept: list[list] = []
    for start, end, key in found:
        same = next((k for k in kept if k[0] == start and k[1] == end), None)
        if same is not None:
            same[2].add(key)
        elif not any(start < k[1] and k[0] < end for k in kept):
            kept.append([start, end, {key}])
    return sorted((Label(s, e, frozenset(k)) for s, e, k in kept), key=lambda label: label.start)


def _gap_problem(gap: str) -> str | None:
    """Why a label and a number with `gap` between them are not one row (None: they are)."""
    bare = _strip_parens(gap)
    if len(squash(bare)) > MAX_GAP_CHARS:
        return f"{len(squash(bare))} characters sit between them"
    numbers = [n for n in numbers_in(bare) if not (_CONDITION.search(bare, max(0, n.start - 4), n.start)
                                                   and unit_at(bare, n.end)[1])]
    if numbers:
        return f"another number ({numbers[0].text}) sits between them"
    stop = _BREAK.search(bare)
    if stop:
        return f"{stop.group(0)!r} separates them"
    other = _OTHER.search(bare)
    if other:
        return f"{other.group(0)!r} names another quantity"
    words = [w for w in _WORDS.findall(_strip_units(bare))]
    if len(words) > MAX_GAP_WORDS:
        return f"{len(words)} words sit between them"
    return None


def _strip_units(text: str) -> str:
    for start, end in sorted(_unit_spans(text), key=lambda span: span[0] - span[1]):
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def _number_before(text: str, position: int) -> bool:
    return bool(_BEFORE.search(text, max(0, position - 12), position))


def _word_of(cell: Cell, text: str) -> str | None:
    """The word a cell's first number is glued into ('V3.0', 'AK80'), or None when it stands on its own."""
    if cell.lead == 0 or not re.match(r"[A-Za-z]", text[cell.lead - 1]):
        return None
    start = cell.lead
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    return text[start:cell.numbers[0].end]


def bind(cell: Cell, labels: list[Label], text: str) -> Binding:
    """The keys a cell's row label names, and how the text ties them."""
    why = "no row label comes before or after it"
    prior = [label for label in labels if label.end <= cell.lead]
    word = _word_of(cell, text)
    # WHY (review V2-1): 'AK10-9 V3.0 KV60' read number first gave kv = 3.0. A number glued to the letters before it
    # belongs to that word (a version, a model name); only its own label may be glued to it ('KV60').
    if word is not None and not any(label.end == cell.lead for label in prior):
        return Binding(frozenset(), "", None, (cell.lead, cell.unit_end),
                       f"it is part of the word {word!r} (a version or a name), not a value of its own")
    if prior:
        label = max(prior, key=lambda item: item.end)
        problem = _gap_problem(text[label.end:cell.lead])
        if problem is None:
            keys = SIZE_KEYS if cell.marked and label.keys <= SIZE_KEYS else label.keys
            return Binding(keys, "before", label, (label.start, cell.unit_end))
        why = f"the label before it, {text[label.start:label.end]!r}, is not its own: {problem}"
    if cell.marked:
        if _number_before(text, cell.lead):
            return Binding(frozenset(), "", None, (cell.lead, cell.unit_end),
                           "it sits in a row of values, not after a label")
        return Binding(SIZE_KEYS, "mark", None, (cell.lead, cell.unit_end))
    later = [label for label in labels if label.start >= cell.unit_end]
    if later:
        label = min(later, key=lambda item: item.start)
        problem = _gap_problem(text[cell.unit_end:label.start])
        if problem is None:
            if _number_before(text, cell.lead):
                return Binding(frozenset(), "", None, (cell.lead, cell.unit_end),
                               "it sits in a row of values, not before its label")
            return Binding(label.keys, "after", label, (cell.lead, label.end))
        if not prior:
            why = f"the label after it, {text[label.start:label.end]!r}, is not its own: {problem}"
    return Binding(frozenset(), "", None, (cell.lead, cell.unit_end), why)


def _show(cell: Cell, text: str) -> str:
    return text[cell.lead:cell.unit_end]


def _place_problem(key: str, number: Number, cell: Cell, note: str, text: str) -> str | None:
    """Whether the number sits at the key's place in its cell."""
    shown, index = _show(cell, text), cell.numbers.index(number)
    if cell.kind == "single":
        return None
    if cell.kind == "sizes":
        if key not in SIZE_KEYS:
            return f"{shown!r} states sizes, not {key}"
        if not cell.marked or len(cell.numbers) != 2:
            return f"{shown!r} does not mark which size is the diameter (a diameter sign before the first)"
        want = 0 if key == "outer_diameter" else 1
        if index != want:
            return f"in {shown!r} the {key} is the {'first' if want == 0 else 'second'} number, not {number.text}"
        return None
    if cell.kind == "range":
        if key not in ("voltage_min", "voltage_max") or len(cell.numbers) != 2:
            return f"{shown!r} is a range, not one {key}"
        want = 0 if key == "voltage_min" else 1
        if index != want:
            return f"{key} is the {'low' if want == 0 else 'high'} end of {shown!r}, not {number.text}"
        return None
    if cell.kind == "ratio":
        if key != "gear_ratio" or len(cell.numbers) != 2:
            return f"{shown!r} is a ratio, not {key}"
        first, second = (n.options[0] if n.options else None for n in cell.numbers)
        want = 0 if second == 1 else 1 if first == 1 else None
        if want is None:
            return f"the ratio {shown!r} has no 1 to read N from without arithmetic"
        if index != want:
            return f"the ratio {shown!r} gives {cell.numbers[want].text}, not {number.text}"
        return None
    if cell.kind == "alternatives":
        if not (note or "").strip():
            return f"{shown!r} gives {key} at several conditions; the value must say which in its note"
        return None
    if cell.kind == "dual":
        return None  # each number is the value in its own unit, checked by _unit_problem
    return f"{shown!r} mixes sizes, ranges or alternatives, so its numbers cannot be read one by one"


def _unit_problem(key: str, unit, cell: Cell, number: Number, binding: Binding, text: str) -> str | None:
    own = cell.units[cell.numbers.index(number)]
    printed = sorted(own)
    if key == "gear_ratio" or unit == "":
        return f"the number prints {printed[0]!r}, and {key} has no such unit" if printed else None
    if printed:
        return None if unit in own else f"the number prints {printed[0]!r}, not the unit {unit!r}"
    start, end = binding.region
    if unit_found(unit, text[start:end]):
        return None
    return f"the unit {unit!r} is printed neither at the number nor in its label"


def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z]", "", name.lower())


def _designation_problem(text: str, offset: int, cell: Cell, binding: Binding, model: str | None,
                         zones: bytes | None = None) -> str | None:
    """Whether the label (or, number first, the cell) follows a model designation that is not the part's own,
    in the same cell or line. `offset` is where the quote sits in `text` (0 for the quote itself, its start on the
    page); `zones` the page's flags, whose BREAK marks a cell's or a line's end."""
    first = offset + (binding.label.start if binding.side == "before" and binding.label else cell.lead)
    m = _DESIGNATION.search(text, max(0, first - 48), first)
    if not m or not re.search(r"\d", _strip_units(m.group(1))):
        return None  # WHY: a unit's own digits ('g.cm2') name no model
    if zones is not None and any(flag & BREAK for flag in zones[m.end(1):first]):
        return None  # WHY: 'Connector XT30PW-M' ends its row; 'Rated voltage (V) 48' is the next row's
    designation = m.group(1)
    named = text[m.start(1):offset + cell.unit_end]
    # WHY both: the atlas names 'AK10-9 V3.0' (the KV is the label after it), a folder part 'AKE40-6 KV90' (the
    # KV is part of its name)
    if model and _norm(model) in (_norm(designation), _norm(named)):
        return None
    return (f"it follows the model designation {designation!r} ({squash(named)!r}), "
            f"another model's name, not this part's ({model!r})" if model else
            f"it follows the model designation {designation!r} ({squash(named)!r}), and this part's own model is "
            "unknown, so it may be another model's name")


def _zone_problem(zones: bytes, offset: int, cell: Cell, binding: Binding) -> str | None:
    """Whether the label or the number sits in the page's navigation or in a link's text (text.html_zones)."""
    spans = [(cell.lead, cell.unit_end)] + ([(binding.label.start, binding.label.end)] if binding.label else [])
    flags = 0
    for start, end in spans:
        for flag in zones[offset + start:offset + end]:
            flags |= flag
    if flags & NAVIGATION:
        return "on the page it sits in the site's navigation (nav, header, footer, a menu), not in this part's content"
    if flags & LINK:
        return "on the page it is a link's text, which names another page, not a value of this one"
    return None


def _page_problem(quote: str, cell: Cell, binding: Binding, page: str, zones: bytes | None = None,
                  model: str | None = None) -> str | None:
    """On the page itself: the quote does not cut the cell short, the cell is not one of a row of values, and it
    is this part's own content (not navigation, a link, or another model's name). One occurrence that passes is
    enough."""
    problem = None
    start = page.find(quote)
    while start >= 0:
        first, end = start + cell.start, start + cell.end
        found = next((c for c in cells_in(page) if c.start <= first < c.end), None)
        if found is None or (found.start, found.end) != (first, end):
            problem = f"the quote cuts the page's {_show(found, page)!r} short" if found else "the cell is not on the page"
        elif binding.side == "before" and _AFTER.match(page, found.unit_end):
            problem = (f"on the page more values follow it ({page[found.lead:found.unit_end + 12].strip()!r}…): "
                       "a row of values, one per column, so the label picks out none of them")
        elif binding.side in ("after", "mark") and _number_before(page, found.lead):
            problem = "on the page it sits in a row of values after another number"
        else:
            zones = zones if zones is not None and len(zones) == len(page) else None
            problem = ((_zone_problem(zones, start, cell, binding) if zones is not None else None)
                       or _designation_problem(page, start, cell, binding, model, zones))
            if problem is None:
                return None
        start = page.find(quote, start + 1)
    return problem


@dataclasses.dataclass(frozen=True)
class Reading:
    problems: list           # why the quote does not tie the value to its key (empty: it does)
    cell: dict | None = None  # where it was read: {text, kind, index, of}


def read(key: str, value, unit, note: str | None, raw: str, quote: str, page: str | None,
         entries: tuple[Entry, ...], zones: bytes | None = None, model: str | None = None) -> Reading:
    """Whether the quote ties `value` to a row label of `key` at its place, and from which cell.
    `page` is the whitespace-normalised text of the cited page, or None when it is unknown; `zones` its
    text.html_zones flags (None: no structure); `model` the part's own model name (None: unknown)."""
    if any(item.key == key and item.is_text for item in entries) or isinstance(value, bool) \
            or not isinstance(value, (int, float)):
        return Reading([])
    text, raw_text = squash(quote or ""), squash(raw or "")
    if not text or not raw_text:
        return Reading([])
    cells, labels, reasons = cells_in(text), labels_in(text, entries), []
    position = text.find(raw_text)
    while position >= 0:
        for cell in cells:
            for number in cell.numbers:
                if not (position <= number.start and number.end <= position + len(raw_text)):
                    continue
                if float(value) not in number.options:
                    continue
                binding = bind(cell, labels, text)
                if key not in binding.keys:
                    named = ", ".join(sorted(binding.keys))
                    reasons.append(f"{number.text} is not tied to a row label of {key}: " +
                                   (f"its label names {named}" if binding.keys else binding.why))
                    continue
                problem = (_place_problem(key, number, cell, note or "", text)
                           or _unit_problem(key, unit, cell, number, binding, text)
                           or (_page_problem(text, cell, binding, page, zones, model) if page
                               else _designation_problem(text, 0, cell, binding, model)))
                if problem is None:
                    return Reading([], {"text": _show(cell, text), "kind": cell.kind,
                                        "index": cell.numbers.index(number), "of": len(cell.numbers)})
                reasons.append(problem)
        position = text.find(raw_text, position + 1)
    return Reading(list(dict.fromkeys(reasons))[:1] or [f"no number of raw {raw!r} in the quote is {value!r}"])


def problems(key: str, value, unit, note: str | None, raw: str, quote: str, page: str | None,
             entries: tuple[Entry, ...], zones: bytes | None = None, model: str | None = None) -> list[str]:
    return read(key, value, unit, note, raw, quote, page, entries, zones, model).problems


def condition_problems(cells: dict[str, dict]) -> dict[str, str]:
    """Values read from alternatives at stated conditions ('24/48', '109/228'): within one part, those with the
    same number of alternatives must all take the same one. Which is wrong cannot be told, so every one of a
    group that disagrees gets the reason. `cells` maps a spec key to its reading's cell."""
    groups: dict[int, dict[str, dict]] = {}
    for spec_key, cell in cells.items():
        if cell and cell["kind"] == "alternatives":
            groups.setdefault(cell["of"], {})[spec_key] = cell
    out = {}
    for group in groups.values():
        if len({cell["index"] for cell in group.values()}) > 1:
            taken = "; ".join(f"{key} takes no. {cell['index'] + 1} of {cell['text']!r}" for key, cell in sorted(group.items()))
            for spec_key in group:
                out[spec_key] = f"inconsistent conditions: {taken} (values read at stated conditions must take the same one)"
    return out
