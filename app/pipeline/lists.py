"""List structure from list item labels: which items are siblings, which are nested, and their order.

A run of list items in reading order is split into levels by the kind of label each item has ("3.",
"(a)", "(ii)", "•") and where the label sits:

  * an item whose label is the same kind and style as an open level's is the next item of that level
    (any deeper levels close);
  * an item with a new kind of label is the start of a list nested in the previous item, unless its
    label sits clearly to the left of the open levels, in which case those levels close first;
  * bullets have no sequence, so their indent decides: a bullet further right than the open bullet
    level starts a nested list.

"(i)" is ambiguous: the letter after "(h)" or the roman numeral one. It is a letter when the open
letter level's last label is "h" (and likewise for v, x, l, c), and a roman numeral otherwise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ROMAN = re.compile(r"^(?=[ivxlc]+$)(c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})$", re.I)
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def _style(label: str) -> str:
    label = label.strip()
    if label.startswith("(") and label.endswith(")"):
        return "()"
    if label.endswith(")"):
        return ")"
    if label.endswith("."):
        return "."
    return ""


def _core(label: str) -> str:
    return label.strip().strip("().").strip()


def roman_value(s: str) -> int | None:
    if not s or not _ROMAN.match(s):
        return None
    total, prev = 0, 0
    for ch in reversed(s.lower()):
        v = _ROMAN_VALUES[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


@dataclass(frozen=True)
class LabelKey:
    kind: str  # decimal, lower, upper, lroman, uroman, bullet
    style: str  # "()", ")", "." or "" (bullets: the bullet character)


def label_kind(label: str | None, open_letter_last: dict[LabelKey, str] | None = None) -> tuple[LabelKey, int | None]:
    """The label's kind and its position in its sequence (None for bullets)."""
    if not label:
        return LabelKey("bullet", ""), None
    core, style = _core(label), _style(label)
    if core.isdigit():
        return LabelKey("decimal", style), int(core)
    if len(core) == 1 and core.isalpha():
        lower = core.islower()
        letter = LabelKey("lower" if lower else "upper", style)
        roman = LabelKey("lroman" if lower else "uroman", style)
        if core.lower() in _ROMAN_VALUES:
            open_ = open_letter_last or {}
            prev = open_.get(letter)
            if prev and len(prev) == 1 and ord(prev.lower()) + 1 == ord(core.lower()):
                return letter, ord(core.lower()) - 96  # "(i)" right after "(h)"
            if core.lower() == "i" or roman in open_:
                return roman, roman_value(core)
        return letter, ord(core.lower()) - 96
    rv = roman_value(core)
    if rv is not None:
        return LabelKey("lroman" if core.islower() else "uroman", style), rv
    return LabelKey("bullet", core), None


@dataclass
class Level:
    key: LabelKey
    x: float
    items: list = field(default_factory=list)  # (item, children levels) in order
    last_label: str = ""
    last_ord: int | None = None
    parent: list | None = None  # the list of levels this level belongs to


def nest(items: list, label_of, x_of, size_of, before: dict[LabelKey, str] | None = None) -> list[Level]:
    """Group a run of list items (in reading order) into top-level lists, each a Level whose items are
    (item, [nested Levels]). before: the list just before the run, {its kind of label: its last label}: a list split
    by a paragraph or a figure carries on from it ("(v)" after "(iv)" is roman, "(c)" after "(b)" a letter, #619)."""
    top: list[Level] = []
    stack: list[tuple[Level, list]] = []  # (level, children list of its last item)
    for it in items:
        label = label_of(it)
        x, size = x_of(it), max(size_of(it), 1.0)
        key, _ = label_kind(label, {lv.key: lv.last_label for lv, _ in stack} if stack else (before or {}))
        # Levels whose labels sit clearly to the right of this one are finished
        while stack and x < stack[-1][0].x - 0.6 * size:
            stack.pop()
        match = next((j for j in range(len(stack) - 1, -1, -1) if stack[j][0].key == key
                      and (key.kind != "bullet" or abs(stack[j][0].x - x) <= 0.6 * size)), None)
        n = ordinal(label, key)
        if match is not None:
            del stack[match + 1:]
            old = stack[-1][0]
            if n is not None and old.last_ord is not None and n <= old.last_ord and n <= 1:
                # The sequence starts again ("1." after "7."): a new list beside the old one
                lv = Level(key, x, parent=old.parent)
                old.parent.append(lv)
                stack[-1] = (lv, [])
        else:  # a new kind of label (or a bullet further in): a list nested in the previous item
            home = stack[-1][1] if stack else top
            lv = Level(key, x, parent=home)
            home.append(lv)
            stack.append((lv, []))
        lv = stack[-1][0]
        children: list = []
        lv.items.append((it, children))
        lv.last_label = _core(label or "")
        lv.last_ord = n
        stack[-1] = (lv, children)
    return top


def ordinal(label: str | None, key: LabelKey) -> int | None:
    core = _core(label or "")
    if key.kind == "decimal":
        return int(core) if core.isdigit() else None
    if key.kind in ("lower", "upper"):
        return ord(core.lower()) - 96 if len(core) == 1 and core.isalpha() else None
    if key.kind in ("lroman", "uroman"):
        return roman_value(core)
    return None


def fix_row_order(order: list, label_of, row_of, size_of) -> list:
    """Items of one list laid out side by side ("(g) ...   (h) ...") are read in label order when the
    order given puts them the other way round on the same row (a taller item, e.g. one with a stacked
    fraction, starts higher on the page and was taken to come first)."""
    out = list(order)
    changed = True
    passes = 0
    while changed and passes < len(out):
        changed = False
        passes += 1
        for i in range(len(out) - 1):
            a, b = out[i], out[i + 1]
            la, lb = label_of(a), label_of(b)
            if not la or not lb:
                continue
            ka, _ = label_kind(la)
            kb, _ = label_kind(lb)
            if ka != kb:
                continue
            oa, ob = ordinal(la, ka), ordinal(lb, kb)
            if oa is None or ob is None or oa != ob + 1:
                continue
            if abs(row_of(a) - row_of(b)) <= 0.6 * max(size_of(a), size_of(b), 1.0):
                out[i], out[i + 1] = b, a
                changed = True
    return out


def label_order(levels: list[Level], label_of) -> None:
    """Items of one level whose labels are a shuffled run of consecutive labels ("(a) (d) (g) (b) ..."
    from a grid of items read row by row when it runs down the columns, or the other way round) are put
    in label order; nested lists move with their items."""
    for lv in levels:
        ctx: dict = {}
        ords = []
        for it, _ in lv.items:
            label = label_of(it)
            key, _ = label_kind(label, ctx)
            ctx[key] = _core(label or "")
            ords.append(ordinal(label, key) if key == lv.key else None)
        if (len(ords) >= 3 and None not in ords and len(set(ords)) == len(ords)
                and max(ords) - min(ords) == len(ords) - 1 and ords != sorted(ords)):
            lv.items = [x for _, x in sorted(zip(ords, lv.items), key=lambda p: p[0])]
        for _, children in lv.items:
            label_order(children, label_of)
