"""Chemical structures and reaction schemes drawn with lines.

A drawn molecule is bond strokes plus a few characters of real text for its atoms and groups ("O", "OH", "OAc").
Taken apart, the strokes are too small or too mixed with text to count as a picture, so only some ring systems
became figures (and only the rings), other molecules were left out altogether, and every atom label was a
paragraph of its own: a screen reader read out a stream of loose letters with no molecule around them. Here a
drawing made of bonds becomes one figure, with the labels on it and the short text beside it; structures joined
by a reaction arrow or a plus sign in one row are one figure together, the scheme, since what the page shows is
the step from one to the other. It is then described and tagged like any other figure, every stroke and label
inside it, and the page isn't changed.

What tells bonds from other line art (a chart, a flow diagram, a table's grid): bonds are short strokes of one
length, drawn at slants. A sentence or a caption beside a scheme stays text.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from app.pipeline.models import BBox, ImageRegion, bbox_area, bbox_intersection, bbox_union

Segment = tuple[float, float, float, float]

_CELL = 6.0          # points: the grid strokes are gathered on
_REACH = 2           # cells: strokes this near are one drawing (a bond stops short of the atom label it meets)
_BOND = (7.0, 45.0)  # a bond's length; shorter strokes are hash marks and arrow heads, longer ones rules
_SAME = 0.3          # bonds of one drawing differ in length by less than this share
_MIN_BONDS = 4
_JOINT = 2.5         # bond ends this near are one atom
_LABEL_PAD = 3.0
_ROW_GAP = 110.0     # a drawing wider or taller than this isn't an arrow or a mark between structures
_ARROW_GAP = 40.0    # an arrow or a plus sign this near a structure, in its row, belongs to its scheme
_MARK_GAP = 10.0     # the same for a mark right above or below it
_NAME_BELOW = 14.0   # a compound's name sits this far under its structure
_JOINERS = ("+", "→", "⟶", "⇒", "⟹", "⇌", "↔", "or")
_CAPTION = re.compile(r"^\s*(?:fig(?:ure)?\.?|scheme|table|chart|eq(?:uation)?\.?)\s*\d", re.IGNORECASE)
# What is written on a structure: element symbols and the usual groups, with counts and charges
_ATOM = re.compile(
    r"^[\(\[]?(?:[A-Z][a-z]?\d*|R\d?'*|X|Y|Z|Nu|E|Ar|Ph|Me|Et|Bu|Pr|Bn|Ac|Ts|Ms|Tf|Boc|Cbz|TMS|TBS|TBDPS|PMB|THP|"
    r"[nist]-?Bu|[ni]-?Pr|O[A-Z][a-z]*\d*|N[A-Z][a-z]*\d*|S[A-Z][a-z]*\d*|C[OH]\d*[A-Z]?[a-z]*\d*|δ)+"
    r"[\)\]]?\d*[+\-−–⊕⊖]*$")


def _length(s: Segment) -> float:
    return math.hypot(s[2] - s[0], s[3] - s[1])


def _slant(s: Segment) -> float:
    """Degrees off the nearest axis: 0 for a horizontal or upright stroke, 45 at most."""
    a = abs(math.degrees(math.atan2(s[3] - s[1], s[2] - s[0]))) % 90
    return min(a, 90 - a)


def _box(s: Segment) -> BBox:
    return (min(s[0], s[2]), min(s[1], s[3]), max(s[0], s[2]), max(s[1], s[3]))


def _cells(s: Segment) -> set[tuple[int, int]]:
    n = max(1, int(_length(s) // (_CELL / 2)))
    return {(int((s[0] + (s[2] - s[0]) * k / n) // _CELL), int((s[1] + (s[3] - s[1]) * k / n) // _CELL)) for k in range(n + 1)}


# An atom other than carbon, or a group holding one, as written on a structure: what line art that isn't
# chemistry doesn't have on it. The symbols that are also a word or a numeral on their own ("I", "B", "P", "K")
# are left out: a frame around a heading that ends in "I" is not an iodide
_HETERO = re.compile(
    r"^(?:HO|OH|O|N|NH|HN|S|SH|HS|F|Cl|Br|Si|Se|Li|Na|Mg|Zn|Cu|Pd|Sn|"
    r"[OSN](?:Me|Et|Ac|Bn|Bz|Ts|Ms|Tf|Ph|Boc|H|R)\d?|(?:Me|Et|Ac|Bn|Ts|Ph|R)[OSN]|H\d?N|NH\d|N\d|NO\d?|O\dN|"
    r"CO\d?[A-Z]?[a-z]{0,2}|[A-Z]?[a-z]{0,2}O\dC|CN|NC|CHO|OHC|COOH|HOOC|MgBr|OLi|PPh\d|Ph\dP)[+\-−–⊕⊖]*$")


def is_hetero_label(text: str) -> bool:
    return bool(_HETERO.match((text or "").strip()))


def is_atom_label(text: str) -> bool:
    """A few characters naming an atom or a group, as written on a structure."""
    t = (text or "").strip()
    return 0 < len(t) <= 8 and bool(_ATOM.match(t))


def _is_label(text: str) -> bool:
    """Short text that belongs to a drawing (a reagent on an arrow, a compound's name), not a sentence or caption."""
    t = (text or "").strip()
    if not t or _CAPTION.match(t):
        return False
    return len(t.split()) <= 4 and len(t) <= 40


def _drawings(segments: list[Segment]) -> list[list[Segment]]:
    """Strokes gathered into drawings: those that touch, or nearly do."""
    by_cell: dict[tuple[int, int], list[int]] = {}
    for n, s in enumerate(segments):
        for c in _cells(s):
            by_cell.setdefault(c, []).append(n)
    seen_cells: set[tuple[int, int]] = set()
    seen: set[int] = set()
    out: list[list[Segment]] = []
    for start in by_cell:
        if start in seen_cells:
            continue
        seen_cells.add(start)
        todo, members = [start], []
        while todo:
            cx, cy = todo.pop()
            for n in by_cell[(cx, cy)]:
                if n not in seen:
                    seen.add(n)
                    members.append(segments[n])
            for dx in range(-_REACH, _REACH + 1):
                for dy in range(-_REACH, _REACH + 1):
                    c = (cx + dx, cy + dy)
                    if c in by_cell and c not in seen_cells:
                        seen_cells.add(c)
                        todo.append(c)
        out.append(members)
    return out


def _bonds(drawing: list[Segment]) -> list[Segment]:
    """The strokes of a drawing that are bonds: of the length most of its longer strokes share."""
    long = [s for s in drawing if _BOND[0] <= _length(s) <= _BOND[1]]
    if not long:
        return []
    common = Counter(round(_length(s)) for s in long).most_common(1)[0][0]
    return [s for s in long if abs(_length(s) - common) <= _SAME * common]


def _is_structure(drawing: list[Segment], hetero: int) -> bool:
    """Bonds alone are not enough: a resistor in a circuit, a chevron on a slide and a hatched bar are short slanted
    strokes of one length too. A structure also has a ring of five to seven bonds, or an atom that isn't carbon
    written on it ("O", "NH2", "OAc")."""
    bonds = _bonds(drawing)
    slanted = [s for s in bonds if _slant(s) > 12]
    if len(bonds) < _MIN_BONDS or len(slanted) < 2:
        return False
    # A structure turns corners: bonds in at least two directions (a row of slashes has one)
    directions = {round((math.degrees(math.atan2(s[3] - s[1], s[2] - s[0])) % 180) / 30) % 6 for s in bonds}
    if len(directions) < 2:
        return False
    return hetero > 0 or _has_ring(bonds)


def _has_ring(bonds: list[Segment]) -> bool:
    """Whether some five, six or seven of the bonds close a ring, end to end."""
    points: list[tuple[float, float]] = []

    def node(x: float, y: float) -> int:
        for n, (px, py) in enumerate(points):
            if abs(px - x) <= _JOINT and abs(py - y) <= _JOINT:
                return n
        points.append((x, y))
        return len(points) - 1

    edges: dict[int, set[int]] = {}
    for s in bonds:
        a, b = node(s[0], s[1]), node(s[2], s[3])
        if a != b:
            edges.setdefault(a, set()).add(b)
            edges.setdefault(b, set()).add(a)

    def walk(start: int, at: int, came: int, depth: int, seen: frozenset[int]) -> bool:
        for nxt in edges.get(at, ()):
            if nxt == came:
                continue
            if nxt == start:
                if 5 <= depth <= 7:
                    return True
                continue
            if nxt not in seen and depth < 7 and walk(start, nxt, at, depth + 1, seen | {nxt}):
                return True
        return False

    return any(walk(n, n, -1, 1, frozenset({n})) for n in list(edges)[:400])


def find_regions(segments: list[Segment], words: list[tuple[BBox, str]], texts: list[tuple[BBox, str, bool]],
                 width: float, height: float, avoid: list[BBox] | None = None) -> list[BBox]:
    """Boxes of the structures and schemes on a page. `segments` are the straight strokes drawn on it, `words` its
    words with their boxes (atom labels are single words), `texts` its lines or blocks of text as (box, text, bold)
    (what may be a label of the drawing), `avoid` regions that are something else already (tables)."""
    if len(segments) < _MIN_BONDS:
        return []
    atoms = [(b, t) for b, t in words if is_atom_label(t)]
    structures: list[BBox] = []
    arrows: list[bool] = []  # whether the structure's own drawing has a reaction arrow in it
    loose: list[BBox] = []   # drawings that aren't structures: arrows, brackets, the marks between two forms
    for drawing in _drawings(segments):
        box = bbox_union([_box(s) for s in drawing])
        if any(bbox_intersection(_fat(box), a) > 0.5 * (bbox_area(_fat(box)) or 1.0) for a in avoid or []):
            continue
        grown = _pad(box, _LABEL_PAD)
        on_it = [b for b, _ in atoms if bbox_intersection(b, grown) > 0]
        hetero = sum(1 for b, t in atoms if is_hetero_label(t) and bbox_intersection(b, grown) > 0)
        if _is_structure(drawing, hetero):
            structures.append(bbox_union([box] + on_it))
            arrows.append(_has_arrow(drawing))
        elif len(drawing) >= 2 and box[2] - box[0] <= _ROW_GAP and box[3] - box[1] <= _ROW_GAP:
            # One stroke alone is a rule under a word or the edge of something, not an arrow (shaft and head)
            loose.append(box)
    if not structures:
        return []
    loose += [b for b, t in words if t.strip() in _JOINERS]
    schemes = _join(structures, loose, arrows)
    out = []
    for box in schemes:
        # The atoms and charges on it, the reagents on an arrow, a compound's name under it
        for _ in range(3):
            near = (box[0] - _LABEL_PAD, box[1] - _LABEL_PAD, box[2] + _LABEL_PAD, box[3] + _NAME_BELOW)
            more = [b for b, t in atoms if bbox_intersection(b, _pad(box, _LABEL_PAD)) > 0]
            # A line above the drawing is a heading or the sentence before it, never a label of it
            more += [b for b, t, _ in texts if _is_label(t) and bbox_intersection(b, near) > 0
                     and (b[1] + b[3]) / 2 >= box[1] and b[2] - b[0] <= max(120.0, 2 * (box[2] - box[0]))
                     # mostly beside this drawing: a line that only starts here ("OH" of this structure and
                     # "CH3" of the next, set as one line) would pull the next structure in with it
                     and min(b[2], box[2] + _LABEL_PAD) - max(b[0], box[0] - _LABEL_PAD) >= 0.5 * (b[2] - b[0])]
            new = bbox_union([box] + more)
            new = (max(0.0, new[0]), max(0.0, new[1]), min(width, new[2]), min(height, new[3]))
            if new == box:
                break
            box = new
        out.append(box)
    # Boxes that run into each other are one figure. Until nothing changes: a box may only meet another once
    # that one has grown by a third (a figure inside a figure otherwise).
    merged = sorted(out, key=lambda b: (b[1], b[0]))
    while True:
        pair = next(((i, j) for i in range(len(merged)) for j in range(i + 1, len(merged))
                     if bbox_intersection(merged[i], merged[j]) > 0.1 * min(bbox_area(merged[i]), bbox_area(merged[j]))), None)
        if pair is None:
            break
        i, j = pair
        merged[i] = bbox_union([merged[i], merged[j]])
        del merged[j]
    return [b for b in merged if bbox_area(b) < 0.9 * width * height]


def _pad(b: BBox, by: float) -> BBox:
    return (b[0] - by, b[1] - by, b[2] + by, b[3] + by)


def _fat(b: BBox) -> BBox:
    """An arrow's shaft has no height to intersect with: give it half a point each way."""
    return _pad(b, 0.5)


def _beside(a: BBox, b: BBox, gap: float) -> bool:
    """b is in a's row, within `gap` to its left or right."""
    shared = min(a[3], b[3]) - max(a[1], b[1])
    if shared < 0.3 * min(a[3] - a[1], b[3] - b[1]) and shared < 0.6 * (b[3] - b[1]):
        return False
    return max(a[0], b[0]) - min(a[2], b[2]) <= gap


def _stacked(a: BBox, b: BBox, gap: float) -> bool:
    """b is right above or below a, within `gap`, and mostly within its width (the marks between two forms)."""
    shared = min(a[2], b[2]) - max(a[0], b[0])
    return shared >= 0.5 * (b[2] - b[0]) and max(a[1], b[1]) - min(a[3], b[3]) <= gap


def _has_arrow(drawing: list[Segment]) -> bool:
    """Whether a reaction arrow was drawn so close to the structure that it was gathered with it: a stroke along
    the line of the page, clearly longer than the bonds, with a head (strokes at one end folded back along it) and
    nothing leading away from its ends. A long bond out to a group has other bonds leading away from its atom, and
    a rule under a word has no head."""
    bonds = _bonds(drawing)
    if not bonds:
        return False
    longest = max(_length(s) for s in bonds)
    for shaft in drawing:
        if shaft in bonds or _slant(shaft) >= 5 or abs(shaft[3] - shaft[1]) >= 2 or _length(shaft) < max(20.0, 1.5 * longest):
            continue
        x0, x1 = min(shaft[0], shaft[2]), max(shaft[0], shaft[2])
        head = away = False
        for other in drawing:
            if other is shaft or _length(other) < 2:
                continue
            for near, far in (((other[0], other[1]), (other[2], other[3])), ((other[2], other[3]), (other[0], other[1]))):
                gap = min(math.hypot(near[0] - ex, near[1] - shaft[1]) for ex in (x0, x1))
                if gap > _JOINT + 1:
                    continue
                if x0 + 1 < far[0] < x1 - 1 and abs(far[1] - shaft[1]) > 0.5:
                    head = True    # folded back along the shaft
                elif gap <= 1.0 and (not x0 - 1 <= far[0] <= x1 + 1 or abs(far[1] - shaft[1]) > _JOINT + 1):
                    away = True    # joined to it and leading off somewhere else: a bond at an atom (an arrow
                    #                set close to a structure only comes near its last bond)
        if head and not away:
            return True
    return False


def _join(structures: list[BBox], loose: list[BBox], arrows: list[bool] | None = None) -> list[BBox]:
    """A structure takes in the arrows, plus signs and marks beside it; structures whose boxes then meet are one
    scheme, and so is a structure drawn with its arrow and the one that arrow points to. Two structures with
    nothing drawn between them stay two figures (a row of compounds, each named)."""
    boxes = [_fat(b) for b in structures]
    grew = list(arrows) if arrows else [False] * len(boxes)
    free = [_fat(b) for b in loose]
    changed = True
    while changed:
        changed = False
        for n, box in enumerate(boxes):
            take = [c for c in free if _beside(box, c, _ARROW_GAP) or _stacked(box, c, _MARK_GAP)]
            if take:
                boxes[n], grew[n] = bbox_union([box] + take), True
                free = [c for c in free if c not in take]
                changed = True
        pair = next(((i, j) for i in range(len(boxes)) for j in range(i + 1, len(boxes))
                     if bbox_intersection(boxes[i], boxes[j]) > 0
                     or ((grew[i] or grew[j]) and (_beside(boxes[i], boxes[j], _ARROW_GAP)
                                                   or _stacked(boxes[i], boxes[j], _MARK_GAP)
                                                   or _stacked(boxes[j], boxes[i], _MARK_GAP)))), None)
        if pair:
            i, j = pair
            boxes[i], grew[i] = bbox_union([boxes[i], boxes[j]]), True
            del boxes[j], grew[j]
            changed = True
    return boxes


def add_to_page(page, boxes: list[BBox], words: list[tuple[BBox, str]] | None = None) -> list[ImageRegion]:
    """Each region becomes one figure holding the strokes and labels inside it. A sentence that happens to lie
    inside a scheme's box stays text. A line of text made only of labels on the drawings ("OH" of one structure
    and "CH3" of the next, set on one line) goes into them too: `words` says where each of its words is."""
    def home(bbox: BBox) -> int | None:
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        return next((n for n, b in enumerate(boxes) if b[0] - 1 <= cx <= b[2] + 1 and b[1] - 1 <= cy <= b[3] + 1), None)

    taken: list[list[tuple[BBox, str]]] = [[] for _ in boxes]
    gone = []
    for block in page.blocks:
        if not block.text.strip():
            continue
        inside = next((n for n, b in enumerate(boxes)
                       if bbox_intersection(block.bbox, b) >= 0.8 * (bbox_area(block.bbox) or 1.0)), None)
        labelish = _is_label(block.text) or all(is_atom_label(w) or w in _JOINERS for w in block.text.split())
        if inside is not None and labelish:
            taken[inside].append((block.bbox, block.text.strip()))
            gone.append(block)
            continue
        # Spread over several drawings: every word of it must sit on one
        mine = [(b, t) for b, t in words or [] if bbox_intersection(b, block.bbox) >= 0.6 * (bbox_area(b) or 1.0)]
        if mine and len(mine) >= len(block.text.split()) and all(home(b) is not None for b, _ in mine) \
                and all(is_atom_label(t) or t in _JOINERS or _is_label(t) for _, t in mine) and len(block.text) <= 80:
            for b, t in mine:
                taken[home(b)].append((b, t))
            gone.append(block)
    made = []
    for n, box in enumerate(boxes):
        labels = " ".join(t for _, t in sorted(taken[n], key=lambda bt: (round(bt[0][1] / 6), bt[0][0])))
        made.append(ImageRegion(id=f"p{page.number}s{n}", page=page.number, bbox=box, xref=0, kind="vector",
                                holds_pieces=True, labels=labels[:400] or None, drawing="scheme"))
    page.blocks = [b for b in page.blocks if not any(b is g for g in gone)]

    def within(bbox: BBox) -> bool:
        probe = _fat(bbox)
        return any(bbox_intersection(probe, _pad(b, 1.0)) >= 0.8 * bbox_area(probe) for b in boxes)

    # The pieces found before (a ring system on its own, a stray stroke) are part of the whole now
    page.images = [i for i in page.images if not within(i.bbox)] + made
    return made
