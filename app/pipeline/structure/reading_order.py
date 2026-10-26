"""Columns and reading order."""
from __future__ import annotations

import bisect

from app.pipeline.models import BBox, PageAnalysis, TextBlock


def row_top(b: TextBlock) -> float:
    """Where a block's first row of text starts: from its baseline, so a stacked fraction or exponent
    reaching above the line doesn't put the block ahead of its neighbors on the same row."""
    if b.baseline is None or not b.font_size:
        return b.bbox[1]
    return max(b.bbox[1], b.baseline - 0.8 * b.font_size)


def _elements(page: PageAnalysis) -> list[tuple[str, BBox]]:
    items: list[tuple[str, BBox]] = [(b.id, (b.bbox[0], row_top(b), b.bbox[2], b.bbox[3]))
                                     for b in page.blocks if b.role != "Artifact"]
    items += [(i.id, i.bbox) for i in page.images]
    items += [(t.id, t.bbox) for t in page.tables]
    return items


def detect_columns(page: PageAnalysis) -> int:
    text = [b.bbox for b in page.blocks if b.role != "Artifact" and len(b.text) > 40]
    if len(text) >= 4:
        found = len(_gutters(text, page.width)) + 1
        if found > 1:
            return found
    # Short lines (a recipe, a price list) or columns under a title narrower than the page
    blocks = [(b.id, b.bbox) for b in page.blocks if b.role != "Artifact" and b.text.strip()]
    if len(blocks) < 8:
        return 1
    _, cols, gutters, _ = _columns_below(blocks, page.width)
    return len(gutters) + 1 if cols else 1


def _gutters(boxes: list[BBox], width: float) -> list[float]:
    """X positions (between 15% and 85% of the width) not crossed by any box."""
    if not boxes:
        return []
    step = 2.0
    lo, hi = width * 0.15, width * 0.85
    covered = []
    x = lo
    while x <= hi:
        covered.append((x, any(b[0] < x < b[2] for b in boxes)))
        x += step
    gutters, run = [], []
    for x, cov in covered:
        if not cov:
            run.append(x)
        elif run:
            if len(run) * step >= 8:
                gutters.append(sum(run) / len(run))
            run = []
    if run and len(run) * step >= 8:
        gutters.append(sum(run) / len(run))
    # A real column gutter has content side by side across it (vertically overlapping), not just
    # something above-left and something below-right of a gap, and on more than one row: a single row of
    # items spread across the page ("(i) 10 s   (ii) 20 s   (iii) 30 s") isn't two columns.
    def rows_across(g: float) -> int:
        lefts = [b for b in boxes if b[2] <= g]
        rights = [b for b in boxes if b[0] >= g]
        if not lefts or not rights:  # a margin, not a gap between things
            return 0
        spans = sorted((max(l[1], r[1]), min(l[3], r[3])) for l in lefts for r in rights
                       if min(l[3], r[3]) - max(l[1], r[1]) > 0)
        rows, end = 0, float("-inf")
        for top, bottom in spans:
            if top >= end:
                rows += 1
                end = bottom
            else:
                end = max(end, bottom)
        return rows
    return [g for g in gutters if rows_across(g) >= 2]


def _columns_below(items: list[tuple[str, BBox]], width: float) -> tuple[list, list, list[float], list]:
    """A band with nothing wide enough to count as full width, and no clear gutter: a title, a byline or a row of
    buttons narrower than the page can still sit across the top of two columns (a recipe's Ingredients beside its
    Directions), and something can sit across the bottom of them (a form's barcode, the next section's heading).
    Returns (what's above, the columns' items, their gutters, what's below): the highest place below which the rest
    is in columns, or failing that the tallest stretch that is. The columns must read down, not across: when most
    rows line up left and right (labels beside their values, a table without rules), reading across is right and
    nothing is split."""
    rows = sorted(items, key=lambda it: (it[1][1], it[1][0]))
    n = len(rows)
    if n < 7:
        return items, [], [], []
    # Worked out once, so each place to split costs a glance, not a search (a page of a textbook has hundreds of
    # blocks, and almost every gap between them is a place to try): the lowest bottom above each row, the highest
    # top and the left and right edges from each row down, and for each x the last row that crosses it.
    above_bottom = [float("-inf")] * (n + 1)
    for i, (_, b) in enumerate(rows):
        above_bottom[i + 1] = max(above_bottom[i], b[3])
    below_top, below_x0, below_x1 = [float("inf")] * (n + 1), [float("inf")] * (n + 1), [float("-inf")] * (n + 1)
    for i in range(n - 1, -1, -1):
        b = rows[i][1]
        below_top[i], below_x0[i], below_x1[i] = min(below_top[i + 1], b[1]), min(below_x0[i + 1], b[0]), max(below_x1[i + 1], b[2])
    step, lo, hi = 2.0, width * 0.15, width * 0.85
    xs = [lo + step * j for j in range(int((hi - lo) / step) + 1)]
    last_cross = [-1] * len(xs)
    for i, (_, b) in enumerate(rows):
        for j in range(max(0, int((b[0] - lo) / step)), min(len(xs), int((b[2] - lo) / step) + 2)):
            if b[0] < xs[j] < b[2]:
                last_cross[j] = i
    for k in range(1, n - 3):
        if above_bottom[k] > below_top[k] + 2:
            continue  # not a clean line between them
        # Is there a gap at least 8 points wide, with something either side, in what's below?
        run = best = 0
        for j, x in enumerate(xs):
            if last_cross[j] < k and below_x0[k] < x < below_x1[k]:
                run += 1
                best = max(best, run)
            else:
                run = 0
        if best * step < 8:
            continue
        above, below = rows[:k], rows[k:]
        gutters = _gutters([b for _, b in below], width)
        if not gutters:
            continue
        g = gutters[0]
        left = [it[1] for it in below if (it[1][0] + it[1][2]) / 2 < g]
        right = [it[1] for it in below if (it[1][0] + it[1][2]) / 2 >= g]
        if len(left) < 3 or len(right) < 3:
            continue
        if _reads_across([it[1] for it in below], g):
            return items, [], [], []
        return above, below, gutters, []

    # Nothing reaches the bottom in columns. Is there a stretch that is in columns, ending where something crosses
    # the gap? For each place to start, the furthest row before one crosses any 8-point-wide strip.
    crossing: list[list[int]] = [[] for _ in xs]
    for i, (_, b) in enumerate(rows):
        for j in range(max(0, int((b[0] - lo) / step)), min(len(xs), int((b[2] - lo) / step) + 2)):
            if b[0] < xs[j] < b[2]:
                crossing[j].append(i)
    # The widest gap first: a wide one that something soon crosses is the real gutter; a sliver that happens to run
    # further down (beside a heading that reaches most of the way across) isn't
    for gap in (24.0, 16.0, 8.0):
        need = int(gap / step)
        for k in range(1, n - 6):
            if above_bottom[k] > below_top[k] + 2:
                continue
            free_until = []
            for c in crossing:
                at = bisect.bisect_left(c, k)
                free_until.append(c[at] if at < len(c) else n)
            end = max((min(free_until[j:j + need]) for j in range(len(xs) - need + 1)), default=k)
            if end >= n or end - k < 6:
                continue
            middle, below = rows[k:end], rows[end:]
            if max(it[1][3] for it in middle) > min(it[1][1] for it in below) + 2:
                continue
            gutters = _gutters([b for _, b in middle], width)
            if not gutters:
                continue
            g = gutters[0]
            left = [it[1] for it in middle if (it[1][0] + it[1][2]) / 2 < g]
            right = [it[1] for it in middle if (it[1][0] + it[1][2]) / 2 >= g]
            if len(left) < 3 or len(right) < 3 or _reads_across([it[1] for it in middle], g):
                continue
            return rows[:k], middle, gutters, below
    return items, [], [], []


def _reads_across(boxes: list[BBox], gutter: float) -> bool:
    """Whether what's either side of a gap lines up row by row (labels beside values, a grid of exercise items,
    a table without rules), so it reads across, not down one side and then the other. Most items on BOTH sides
    need a partner starting at the same height: two columns of prose line up now and then by chance (and their
    headings always do), but never most of the way down both."""
    left = [b for b in boxes if (b[0] + b[2]) / 2 < gutter]
    right = [b for b in boxes if (b[0] + b[2]) / 2 >= gutter]
    if not left or not right:
        return False

    def partnered(side: list[BBox], other: list[BBox]) -> float:
        def same_row(a: BBox, b: BBox) -> bool:
            # a raised or lowered character (a power, a fraction) moves a line's top by a few points
            return abs(a[1] - b[1]) <= max(3.0, 0.5 * min(a[3] - a[1], b[3] - b[1]))
        return sum(1 for a in side if any(same_row(a, b) for b in other)) / len(side)

    return min(partnered(left, right), partnered(right, left)) >= 0.6


_RTL_RANGES = ((0x0590, 0x08FF), (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))  # Hebrew, Arabic, Syriac, Thaana; their forms


def right_to_left(text: str) -> bool:
    """Most of the letters are Arabic or Hebrew (or another right-to-left script): the page is read from the right
    (#368). A handful of letters decides nothing."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 20:
        return False
    rtl = sum(1 for c in letters if any(lo <= ord(c) <= hi for lo, hi in _RTL_RANGES))
    return rtl > len(letters) / 2


def _by_rows(items: list[tuple[str, BBox]], rtl: bool = False) -> list[tuple[str, BBox]]:
    """Top to bottom, and left to right within a row (right to left on a right-to-left page). Items whose tops are
    within a few points of the row's first item share the row: sorting on the exact top would let a point's
    difference put the right-hand item of a row first."""
    rest = sorted(items, key=lambda it: (it[1][1], it[1][0]))
    out: list[tuple[str, BBox]] = []
    while rest:
        first = rest[0]
        h = max(1.0, first[1][3] - first[1][1])
        tol = min(3.0, 0.35 * h)
        row = [it for it in rest if it[1][1] - first[1][1] <= tol]
        out += sorted(row, key=lambda it: -it[1][2]) if rtl else sorted(row, key=lambda it: it[1][0])
        rest = [it for it in rest if it not in row]
    return out


def heuristic_reading_order(page: PageAnalysis) -> list[str]:
    """Top-to-bottom bands separated by full-width elements; columns left-to-right inside a band (right to left on a
    page in Arabic or Hebrew, #368)."""
    items = sorted(_elements(page), key=lambda it: (round(it[1][1], 0), it[1][0]))
    if not items:
        return []
    rtl = right_to_left(" ".join(b.text for b in page.blocks))
    full = page.width * 0.6
    order: list[str] = []
    band: list[tuple[str, BBox]] = []

    def ordered(items: list[tuple[str, BBox]], depth: int = 0) -> list[str]:
        """Columns left to right, each read on its own (it may hold columns too: a logo beside a page that then
        splits in two); otherwise what's above any columns by rows, then those columns."""
        if depth > 3 or len(items) < 2:
            return [i for i, _ in _by_rows(items, rtl)]
        gutters = _gutters([b for _, b in items], page.width)
        if gutters and depth and _reads_across([b for _, b in items], gutters[0]):
            return [i for i, _ in _by_rows(items, rtl)]  # inside a column: labels beside their values, not more columns
        below: list[tuple[str, BBox]] = []
        if gutters:
            head, cols = [], items
        else:
            head, cols, gutters, below = _columns_below(items, page.width)
            if not cols:
                return [i for i, _ in _by_rows(items, rtl)]
        out = [i for i, _ in _by_rows(head, rtl)]
        edges = [float("-inf"), *gutters, float("inf")]
        columns = [[it for it in cols if lo <= (it[1][0] + it[1][2]) / 2 < hi] for lo, hi in zip(edges, edges[1:])]
        # A heading that starts below where every other column has ended, near the end of its own, opens what comes
        # after the columns (an article's first section under its sidebar and abstract): it, and what follows it,
        # is read last
        tail: list[tuple[str, BBox]] = []
        for n, col in enumerate(columns):
            others = [it[1][3] for m, c in enumerate(columns) if m != n for it in c]
            if not others:
                continue
            for it in sorted(col, key=lambda it: it[1][1]):
                el = page.element(it[0])
                if it[1][1] >= max(others) and str(getattr(el, "role", "")).startswith("H"):
                    rest = [x for x in col if x[1][1] >= it[1][1]]
                    # ...when it comes near the end of its column. A title with most of the column under it,
                    # beside a short neighbour (a logo), is the column's own start.
                    if len(rest) <= len(col) - len(rest):
                        tail = rest
                        columns[n] = [x for x in col if x[1][1] < it[1][1]]
                    break
            if tail:
                break
        for col in (reversed(columns) if rtl else columns):
            out += ordered(col, depth + 1)
        return out + [i for i, _ in _by_rows(tail, rtl)] + ordered(below, depth + 1)

    def flush() -> None:
        order.extend(ordered(band))
        band.clear()

    for it in items:
        if it[1][2] - it[1][0] >= full:
            flush()
            order.append(it[0])
        else:
            band.append(it)
    flush()
    return order


def needs_ai_reading_order(page: PageAnalysis) -> bool:
    return page.columns > 1 or (len(page.images) + len(page.tables) > 0 and len(page.blocks) > 6)
