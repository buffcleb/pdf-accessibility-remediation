"""PyMuPDF-based document analysis: text blocks, images, tables, fonts, renders for AI."""
from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from pathlib import Path

import pymupdf as fitz

from app.pipeline.structure import BULLET_ONLY, BULLET_START, FOOTNOTE_MARK
from app.pipeline import plots, schemes
from app.pipeline.models import (
    append_block,
    join_statements,
    BBox,
    DocumentAnalysis,
    ImageRegion,
    PageAnalysis,
    TableCell,
    TableRegion,
    TextBlock,
    bbox_area,
    bbox_intersection,
    bbox_union,
)

log = logging.getLogger(__name__)

_BOLD_FLAG = 1 << 4
_STD_ENCODINGS = {"WinAnsiEncoding", "MacRomanEncoding", "StandardEncoding", "PDFDocEncoding"}
_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _rect(b) -> BBox:
    r = fitz.Rect(b)
    return (round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2))


# The long side, in pixels, of any page render that isn't for the AI (those have their own cap, below). A page's size
# is the producer's to set, and a render at a fixed dpi of a page a hundred thousand points across is gigabytes.
MAX_RENDER_PX = 10_000


def capped_dpi(page: fitz.Page, dpi: int, max_px: int = MAX_RENDER_PX) -> int:
    """The dpi to render a whole page at: `dpi`, lowered so the long side stays within max_px pixels."""
    long_side = max(page.rect.width, page.rect.height) * dpi / 72.0
    return max(1, int(dpi * max_px / long_side)) if long_side > max_px else dpi


def render_png(doc: fitz.Document, page_no: int, dpi: int = 150, clip: BBox | None = None, max_px: int = 2000) -> bytes:
    """Render a page (or a clip of it, in visual page space) to PNG for vision models,
    capped at max_px on the long side."""
    page = doc[page_no]
    area = (fitz.Rect(clip) & page.rect) if clip else page.rect
    if clip and not fitz.Rect(clip).intersects(page.rect):
        raise ValueError(f"nothing to render: {tuple(round(v, 1) for v in clip)} is off page {page_no + 1}")
    # A sliver (a hairline, a rule) is padded to at least a pixel each way, so it still makes an image
    least = 72.0 / dpi
    if area.width < least or area.height < least:
        cx, cy = (area.x0 + area.x1) / 2, (area.y0 + area.y1) / 2
        hw, hh = max(area.width, least) / 2, max(area.height, least) / 2
        area = fitz.Rect(cx - hw, cy - hh, cx + hw, cy + hh) & page.rect
    zoom = dpi / 72.0
    long_side = max(area.width, area.height) * zoom
    if long_side > max_px:
        zoom *= max_px / long_side
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=area if clip else None, alpha=False)
    return pix.tobytes("png")


# --------------------------------------------------------------------------- text
# --------------------------------------------------------------------------- text blocks
# A list label standing alone as a run: (a)  a)  1.  (iv)
_LABEL_RUN = re.compile(r"^\s*\(?([a-zA-Z]|[ivxlcIVXLC]{1,5}|\d{1,3})[.)]\s*$")
# A superscript reads as a power after a number, a single-letter variable or a closing bracket.
_DOTS = ".,\u00b7"
_POWER_BASE = re.compile(r"(\d|(^|[^A-Za-z])[A-Za-z]|\))\s*$")
# A superscript that is only a sign, or a digit and a sign ("+", "−", "2+", "3−"): a charge
_CHARGE = re.compile(r"^(\d?)([+\-−–⊕⊖])$")


class _Run:
    """One character with its position, as typeset. Math producers emit glyphs out of order and may
    draw a decimal point into a gap left in a run ("3 46" + "."), so order comes from position."""
    __slots__ = ("x0", "y0", "x1", "y1", "ox", "text", "size", "bold", "base", "script", "color", "seq")

    def __init__(self, span: dict, char: dict, seq: int):
        self.x0, self.y0, self.x1, self.y1 = _rect(char["bbox"])
        self.text = char["c"]
        self.size = float(span["size"])
        self.bold = bool(span["flags"] & _BOLD_FLAG) or "bold" in span["font"].lower()
        self.base = float(char["origin"][1])
        self.ox = float(char["origin"][0])  # pen position: reliable even where glyph boxes are not
        self.script: str | None = None  # "sup" / "sub" when attached to a line as a script
        self.color = int(span.get("color", 0))
        self.seq = seq  # extraction (content stream) order


class _VLine:
    def __init__(self, run: _Run):
        self.runs = [run]
        self.base = run.base
        self.size = run.size
        self.x0, self.x1 = run.x0, run.x1
        # (baseline, size) of the runs that sit on the line itself (not its scripts), without repeats: what a
        # new run is compared with to join a line exactly. A line of a thousand characters has one or two of
        # these; comparing with every run made the grouping cubic on a page's worth of characters
        self.anchors: set[tuple[float, float]] = {(run.base, run.size)} if run.script is None else set()

    def add(self, r: _Run) -> None:
        self.runs.append(r)
        self.x0, self.x1 = min(self.x0, r.x0), max(self.x1, r.x1)
        if r.script is None:
            self.anchors.add((r.base, r.size))

    def on_baseline(self, r: _Run) -> bool:
        """Whether r sits exactly on the baseline of a run already on this line."""
        return any(abs(base - r.base) <= 0.05 * size for base, size in self.anchors)

    @property
    def body(self) -> list[_Run]:
        return [r for r in self.runs if r.script is None] or self.runs


def _visual_lines(runs: list[_Run]) -> list[_VLine]:
    """Group runs into lines by baseline, largest text first, so a smaller run sitting just above or below
    a line's baseline and next to it joins that line as a superscript or subscript instead of becoming a
    line (and a tag) of its own."""
    lines: list[_VLine] = []
    spaces = [r for r in runs if not r.text.strip()]
    for r in runs:
        r.script = None  # decided afresh for this grouping
    for r in sorted((r for r in runs if r.text.strip()), key=lambda r: (-r.size, r.seq)):
        # A clearly smaller run beside a line and off its baseline is a script, even when only slightly
        # off (subscripts often drop just a sixth of a character: "A" + "base")
        host = None
        # Sitting exactly on the baseline of text already in a line, close by: part of that line
        exact = [ln for ln in lines if ln.x0 - 1.5 * ln.size <= r.x0 <= ln.x1 + 1.5 * ln.size and ln.on_baseline(r)]
        if exact:
            min(exact, key=lambda ln: abs(r.base - ln.base)).add(r)
            continue
        for ln in lines:
            near = ln.x0 - 0.6 * ln.size <= r.x0 <= ln.x1 + 1.5 * ln.size
            offset = r.base - ln.base
            shifted = offset >= 0.1 * ln.size or offset <= -0.15 * ln.size
            if r.size <= 0.85 * ln.size and near and shifted and -0.75 * ln.size <= offset <= 0.5 * ln.size:
                if host is None or abs(offset) < abs(r.base - host.base):
                    host = ln
        if host is not None:
            r.script = "sup" if r.base < host.base else "sub"
            host.add(r)
            continue
        same = [ln for ln in lines if abs(r.base - ln.base) <= 0.3 * max(ln.size, r.size)]
        if same:
            min(same, key=lambda ln: abs(r.base - ln.base)).add(r)
            continue
        lines.append(_VLine(r))
    # Spaces only mark word boundaries: they join the nearest line, never start one
    for r in spaces:
        cands = [ln for ln in lines if ln.x0 - ln.size <= r.x0 <= ln.x1 + ln.size]
        if cands:
            ln = min(cands, key=lambda ln: abs(r.base - ln.base))
            if abs(r.base - ln.base) <= 0.8 * ln.size:
                ln.runs.append(r)
    for ln in lines:
        ln.runs.sort(key=lambda r: (r.ox, r.seq))
    return sorted(lines, key=lambda ln: (ln.base, ln.x0))


def _words(runs: list[_Run]) -> list[list[_Run]]:
    """Split a visual line (runs sorted by pen position) into words. A space glyph separates words,
    except where a decimal point was drawn into it ("3 46" + "." = 3.46: a dot between digits);
    without space glyphs, a gap does."""
    words: list[list[_Run]] = []
    cur: list[_Run] = []
    pending: _Run | None = None  # a space glyph seen since the last character
    for r in runs:
        if not r.text.strip():
            pending = r if pending is None else pending
            continue
        if cur and r.script is not None and pending is not None and cur[-1].script == r.script:
            # a space inside a script ("lateral surface" as a subscript) stays in it
            pending.script = r.script
            cur.append(pending)
        if cur and r.script is None:  # a superscript or subscript always belongs to the word before it
            prev = cur[-1]
            # digit, space, dot (dot drawn into the space) / digit, dot, space, digit (dot then the space)
            decimal = pending is not None and (
                (r.text in _DOTS and prev.text.isdigit() and r.ox - pending.ox < 0.5 * pending.size)
                or (r.text.isdigit() and prev.text in _DOTS and len(cur) >= 2 and cur[-2].text.isdigit()
                    and pending.ox - prev.ox < 0.3 * pending.size))
            if (pending is not None and not decimal) or r.x0 - prev.x1 > 0.25 * max(r.size, prev.size, 1.0):
                words.append(cur)
                cur = []
        cur.append(r)
        pending = None
    if cur:
        words.append(cur)
    return words


_PRIMES = {"′": 1, "″": 2, "‴": 3, "'": 1, "’": 1, "ʹ": 1, "ʺ": 2}
_PRIME_WORDS = {1: "prime", 2: "double prime", 3: "triple prime"}


_PRIME_MARKS = re.compile(r"\s*([′″‴])")


def _speak_primes(spoken: str) -> str:
    """Prime marks left in read-aloud text (after a subscript, or not raised) are read as words; plain
    apostrophes are left alone."""
    return _PRIME_MARKS.sub(lambda m: " " + _PRIME_WORDS[_PRIMES[m.group(1)]], spoken)


def _prime_count(text: str) -> int:
    """How many primes a raised mark stands for (F′, f″), or 0 if it isn't only primes."""
    marks = [c for c in text if not c.isspace()]
    return sum(_PRIMES[c] for c in marks) if marks and all(c in _PRIMES for c in marks) else 0


def _word_text(word: list[_Run], spoken: bool) -> str:
    out = ""
    i = 0
    while i < len(word):
        r = word[i]
        if r.script is None:
            out += _run_text(r, spoken)
            i += 1
            continue
        j = i
        while j + 1 < len(word) and word[j + 1].script == r.script:
            j += 1
        text = "".join(_run_text(x, spoken) for x in word[i:j + 1])
        # the word carries on after the script ("πr²h") with a letter or digit: keep them apart when spoken
        more = j + 1 < len(word) and word[j + 1].text[:1].isalnum()
        if spoken and r.script == "sup" and out and (n := _prime_count(text)):
            # A raised prime is a mark on the symbol, not a power: F′ is "F prime"
            out += " " + _PRIME_WORDS.get(n, f"{n} primes") + (" " if more else "")
        elif spoken and r.script == "sup" and out and (charge := _CHARGE.match(text.strip())):
            # A raised sign is a charge or a mark on the symbol, never a power: H⁺ is "H plus", Fe²⁺ "Fe 2 plus"
            sign = "plus" if charge.group(2) in "+⊕" else "minus"
            out += " " + (f"{charge.group(1)} " if charge.group(1) else "") + sign + (" " if more else "")
        elif spoken and r.script == "sup" and out and _POWER_BASE.search(out):
            out += {"2": " squared", "3": " cubed"}.get(text, f" to the power of {text}") + (" " if more else "")
        elif spoken and r.script == "sub" and out:
            out += f" sub {text}" + (" " if more else "")
        else:
            out += text
        i = j + 1
    return out


def _raised_marks(pieces: list[list[_Run]]) -> tuple[list[str], list[str], list[tuple], str | None]:
    """The footnote-like marks raised after words in these lines ("results¹", "noted.*"; not after a digit, where a
    raised number is a power), the text each is written right after ("results", "noted."), where each is drawn, and
    the raised mark the first line starts with (a footnote's own number)."""
    marks: list[str] = []
    after: list[str] = []
    boxes: list[tuple] = []
    lead = None
    for n, piece in enumerate(pieces):
        words = _words(piece)
        for w, word in enumerate(words):
            i = 0
            while i < len(word):
                if word[i].script != "sup":
                    i += 1
                    continue
                j = i
                while j + 1 < len(word) and word[j + 1].script == "sup":
                    j += 1
                text = "".join(r.text for r in word[i:j + 1]).strip()
                if FOOTNOTE_MARK.match(text):
                    before = next((r.text for r in reversed(word[:i]) if r.text.strip()), "")
                    if before and not before.isdigit():
                        marks.append(text)
                        after.append(_word_text(word[:i], False))
                        boxes.append(bbox_union([(r.x0, r.y0, r.x1, r.y1) for r in word[i:j + 1] if r.text.strip()]))
                    elif not before and n == 0 and w == 0 and (j + 1 < len(word) or len(words) > 1):
                        lead = text  # starts the block, with the note's words after it
                i = j + 1
    return marks, after, boxes, lead


def _spoken_line(runs: list[_Run]) -> tuple[str, str]:
    """(spoken, visible) text of a visual line. Equations set side by side with a wide gap between them
    ("10³ = 1000      10⁻³ = 0.001") are read as separate statements."""
    words = [w for w in _words(runs) if any(r.text.strip() for r in w)]
    if not words:
        return "", ""
    sizes = sorted(r.size for w in words for r in w if r.text.strip())
    size = sizes[len(sizes) // 2] or 1.0
    chunks = [[words[0]]]
    for prev, w in zip(words, words[1:]):
        gap = min(r.x0 for r in w if r.text.strip()) - max(r.x1 for r in prev if r.text.strip())
        if gap > 2.0 * size:
            chunks.append([w])
        else:
            chunks[-1].append(w)
    parts = [(_speak_primes(" ".join(_word_text(w, True) for w in c)), " ".join(_word_text(w, False) for w in c))
             for c in chunks]
    return join_statements(parts), " ".join(v for _, v in parts)


def _run_text(r, spoken: bool) -> str:
    return r.spoken if spoken and getattr(r, "spoken", None) else r.text


def _join(runs: list[_Run], spoken: bool) -> str:
    """Text of runs in visual order. spoken=True writes powers and subscripts out for screen readers."""
    return " ".join(_word_text(w, spoken) for w in _words(runs))


def _split_inline_items(line: _VLine) -> list[list[_Run]]:
    """'(a) 346 000 000   (b) 2710   (c) 0.000543' on one line is three list items, not one."""
    words = _words(line.runs)
    if len(words) < 3 or not _LABEL_RUN.match("".join(r.text for r in words[0])):
        return [line.runs]
    # The gap is measured against the line's ordinary text size: tall brackets or symbols in the line
    # would otherwise ask for a much wider gap than the text needs
    sizes = sorted(r.size for r in line.runs if r.text.strip().isalnum() and not r.script) or [line.size]
    size = sizes[len(sizes) // 2]
    cuts = []
    for k in range(1, len(words)):
        w = words[k]
        if any(r.script for r in w) or not _LABEL_RUN.match("".join(r.text for r in w)):
            continue
        if w[0].x0 - words[k - 1][-1].x1 >= 0.9 * size:  # well beyond a word space (about 0.25 em)
            cuts.append(w[0].x0)
    if not cuts:
        return [line.runs]
    bounds = [float("-inf"), *cuts, float("inf")]
    pieces = [[r for r in line.runs if lo <= r.x0 < hi] for lo, hi in zip(bounds, bounds[1:])]
    return [pc for pc in pieces if any(r.text.strip() for r in pc)]


def _style(runs: list[_Run]) -> tuple[float, bool]:
    sizes: Counter = Counter()
    bold = total = 0
    for r in runs:
        n = len(r.text.strip())
        if not n or r.script is not None:
            continue
        sizes[round(r.size * 2) / 2] += n
        bold += n if r.bold else 0
        total += n
    if not total:
        return (max((r.size for r in runs), default=0.0), False)
    return (sizes.most_common(1)[0][0], bold / total > 0.6)


def _visible_backgrounds(page: fitz.Page) -> list[fitz.Rect]:
    """Areas where light text can still be seen: anything filled with a color, and images."""
    rects = []
    for d in page.get_drawings():
        fill = d.get("fill")
        if fill is not None and sum(fill) / len(fill) < 0.9:
            rects.append(fitz.Rect(d["rect"]))
    for info in page.get_image_info():
        rects.append(fitz.Rect(info["bbox"]))
    return rects


# --------------------------------------------------------------------------- two-level math
class _Frac:
    """A stacked fraction read as one piece of a line: numerator over a bar over denominator."""

    def __init__(self, bar, num: list, den: list, host: list):
        bx0, bx1, by = bar
        parts = num + den
        self.x0 = min([bx0] + [r.x0 for r in parts])
        self.x1 = max([bx1] + [r.x1 for r in parts])
        self.y0 = min(r.y0 for r in num)
        self.y1 = max(r.y1 for r in den)
        self.ox = self.x0
        self.size = max(r.size for r in parts)
        self.bold = False
        self.color = 0
        self.script = None
        self.seq = min(r.seq for r in parts)
        nt, dt = _stacked_text(num, False), _stacked_text(den, False)
        ns, ds = _stacked_text(num, True), _stacked_text(den, True)
        simple = all(" " not in t and len(t) <= 6 for t in (nt, dt))
        wrap = lambda t: f"({t})" if " " in t else t  # noqa: E731
        self.text = f"{wrap(nt)}/{wrap(dt)}"
        self.spoken = f"{ns} over {ds}" if simple else f"fraction, {ns}, over {ds}, end fraction"
        # On the line it belongs to: the host's baseline, unless the bar sits well above the host's math
        # axis (about a third of a character above its baseline), which makes it an exponent. A smaller
        # fraction set at the axis ("A = ½bh" in a text-style fraction) is inline, never a subscript.
        hosts = [r for r in host if r.text.strip()]
        hsize = max((r.size for r in hosts), default=0)
        hbase = sorted(r.base for r in hosts)[len(hosts) // 2] if hosts else None
        if hosts and (self.size >= 0.85 * hsize or by > hbase - 0.6 * hsize):
            self.base = hbase
        else:
            self.base = by + 0.3 * self.size


def _stacked_text(runs: list, spoken: bool) -> str:
    return " ".join(_join(vl.runs, spoken) for vl in _visual_lines(list(runs)))


def _fraction_bars(page: fitz.Page) -> list[tuple[float, float, float]]:
    """Short horizontal rules that could be fraction bars: (x0, x1, y). A rule that is part of a box or a grid (a
    table cell's border: another rule carries on from its end, or an upright one meets it) is not one."""
    bars, across, upright = [], [], []
    limit = 0.6 * page.rect.width
    for d in page.get_drawings():
        for it in d.get("items") or []:
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                if abs(p1.y - p2.y) <= 0.3 and abs(p2.x - p1.x) >= 2.5:
                    across.append((min(p1.x, p2.x), max(p1.x, p2.x), (p1.y + p2.y) / 2))
                elif abs(p1.x - p2.x) <= 0.3 and abs(p2.y - p1.y) >= 2.5:
                    upright.append(((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y)))
            elif it[0] == "re":
                r = fitz.Rect(it[1])
                if r.height <= 1.5 and r.width >= 2.5 and d.get("fill") is not None:
                    across.append((r.x0, r.x1, (r.y0 + r.y1) / 2))
                elif r.width <= 1.5 and r.height >= 2.5 and d.get("fill") is not None:
                    upright.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
                elif r.width >= 2.5 and r.height >= 2.5 and d.get("color") is not None:
                    # A stroked box: its four sides
                    across += [(r.x0, r.x1, r.y0), (r.x0, r.x1, r.y1)]
                    upright += [(r.x0, r.y0, r.y1), (r.x1, r.y0, r.y1)]
    near = 1.5
    # Rules by their row (and uprights by their column), in one-point bins, so each rule is compared with the few
    # that could meet it rather than with every rule on the page: a page drawn as thousands of short rules (a hatch,
    # a plotted curve) was quadratic here
    rows: dict[int, list] = {}
    for o in across:
        rows.setdefault(math.floor(o[2]), []).append(o)
    columns: dict[int, list] = {}
    for v in upright:
        columns.setdefault(math.floor(v[0]), []).append(v)

    def rules_near(y: float):
        k = math.floor(y)
        return (o for b in (k - 1, k, k + 1) for o in rows.get(b, ()))

    def uprights_near(x: float):
        k = math.floor(x)
        return (v for b in range(k - 2, k + 3) for v in columns.get(b, ()))

    for bar in across:
        x0, x1, y = bar
        if x1 - x0 > limit:
            continue
        if any(o is not bar and abs(o[2] - y) <= 1.0 and (abs(o[1] - x0) <= near or abs(o[0] - x1) <= near)
               for o in rules_near(y)):
            continue  # one rule carries on where this one ends: a row of cell borders
        if any(v[1] - near <= y <= v[2] + near and (abs(v[0] - x0) <= near or abs(v[0] - x1) <= near)
               for x in (x0, x1) for v in uprights_near(x)):
            continue  # a corner
        bars.append(bar)
    return bars


def _line_of(runs: list, ref, tol: float) -> list:
    return [r for r in runs if abs(r.base - ref) <= tol]


def _with_spaces(part: list, spaces: list) -> list:
    """The part plus the space glyphs between its characters (they mark its word boundaries)."""
    if not part:
        return part
    x0, x1 = min(r.x0 for r in part), max(r.x1 for r in part)
    size = max(r.size for r in part)
    base = sorted(r.base for r in part)[len(part) // 2]
    return part + [r for r in spaces if x0 <= (r.x0 + r.x1) / 2 <= x1 and abs(r.base - base) <= 0.3 * size]


def _underline(bar, num: list, den: list) -> bool:
    """The rule under a line of underlined text (a link that wraps onto a second line): as wide as the text over it
    and just under its baseline, with the next line a full line space below."""
    bx0, bx1, by = bar
    size = max(r.size for r in num)
    base = sorted(r.base for r in num)[len(num) // 2]
    spans = abs(min(r.x0 for r in num) - bx0) <= 0.15 * size and abs(max(r.x1 for r in num) - bx1) <= 0.15 * size
    den_base = sorted(r.base for r in den)[len(den) // 2]
    return spans and by - base <= 0.17 * size and den_base - by >= 1.0 * size


def _prose(part: list) -> bool:
    """Words, not math: a word of four letters or more with no digit or operator anywhere, or three such words
    whatever else is there (a sentence that mentions a number)."""
    text, last = "", None
    for r in sorted(part, key=lambda r: (round(r.base), r.x0)):
        # The spaces between words aren't among the part's pieces: a gap stands for one
        if last is not None and (round(r.base) != round(last.base) or r.x0 - last.x1 > 0.15 * r.size):
            text += " "
        text, last = text + r.text, r
    words, run = 0, 0
    for c in text + " ":
        if c.isalpha():
            run += 1
        else:
            words += run >= 4
            run = 0
    if words >= 3:
        return True
    return words >= 1 and not any(c.isdigit() or c in "+=×÷·∙±−√∑∫∂∆πθ^" for c in text)


def _beside(bar, pool: list, parts: list) -> bool:
    """Something on the bar's own line, right beside it ("slope =" before rise over run)."""
    bx0, bx1, by = bar
    size = max(r.size for r in parts)
    return any(r.text.strip() and r not in parts and r.y0 < by < r.y1
               and (0 <= bx0 - r.x1 <= 1.5 * size or 0 <= r.x0 - bx1 <= 1.5 * size) for r in pool)


def _find_fraction(bar, pool: list):
    """(numerator, denominator, host) for a bar, or None. A fraction has something tight above and below
    the bar, within its width and centered on it, and neither line runs on past the bar's ends (which is
    what an underline or a table rule has)."""
    bx0, bx1, by = bar
    width = bx1 - bx0
    inside = [r for r in pool if r.text.strip() and bx0 - 0.5 <= (r.x0 + r.x1) / 2 <= bx1 + 0.5]
    # Above or below by each character's center (glyph boxes include descent and overlap the bar),
    # and close to it
    num = [r for r in inside if (r.y0 + r.y1) / 2 < by and by - r.y1 <= 0.45 * r.size]
    den = [r for r in inside if (r.y0 + r.y1) / 2 > by and r.y0 - by <= 0.45 * r.size]
    if not num or not den:
        return None
    if _underline(bar, num, den):
        return None
    # Words over or under a rule with nothing beside it: a ruled-off line of text, not rise over run after "slope ="
    if (_prose(num) or _prose(den)) and not _beside(bar, pool, num + den):
        return None
    for part in (num, den):
        x0, x1 = min(r.x0 for r in part), max(r.x1 for r in part)
        size = max(r.size for r in part)
        if x0 < bx0 - 0.3 * size or x1 > bx1 + 0.3 * size:
            return None
        if abs((x0 + x1) / 2 - (bx0 + bx1) / 2) > 0.25 * width + 1.5:
            return None
        base = sorted(r.base for r in part)[len(part) // 2]
        for r in _line_of(pool, base, 0.3 * size):
            if r in part or not r.text.strip():
                continue
            cx = (r.x0 + r.x1) / 2
            if (bx0 - 1.0 * size < cx < bx0) or (bx1 < cx < bx1 + 1.0 * size):
                return None  # the line carries on past the bar: underlined text, not a numerator
    # The rest of the numerator's and denominator's lines: exponents and subscripts, which may hang a
    # little past the bar's ends
    size = max(r.size for r in num + den)
    top = min(r.y0 for r in num) - 0.6 * max(r.size for r in num)
    bottom = max(r.y1 for r in den) + 0.6 * max(r.size for r in den)
    near = [r for r in pool if r.text.strip() and r not in num and r not in den
            and bx0 - 0.4 * size <= (r.x0 + r.x1) / 2 <= bx1 + 0.4 * size]
    num_base = sorted(r.base for r in num)[len(num) // 2]
    den_base = sorted(r.base for r in den)[len(den) // 2]

    def belongs(r, base: float, lo: float, hi: float) -> bool:
        cy = (r.y0 + r.y1) / 2
        if not lo <= cy <= hi:
            return False
        # a smaller script beside the part, or more of the part's own line over the bar
        return r.size < 0.9 * size or (abs(r.base - base) <= 0.3 * size and bx0 - 0.5 <= (r.x0 + r.x1) / 2 <= bx1 + 0.5)

    num += [r for r in near if belongs(r, num_base, top, by - 0.01)]
    den += [r for r in near if r not in num and belongs(r, den_base, by + 0.01, bottom)]
    host = [r for r in pool if r.text.strip() and r not in num and r not in den and r.y0 < by < r.y1
            and (0 <= bx0 - r.x1 <= 1.5 * size or 0 <= r.x0 - bx1 <= 1.5 * size)]
    return num, den, host


def _merge_units(page: fitz.Page, units: list[tuple[list, list]]) -> list[tuple[list, list]]:
    """Join producer blocks that share a visual line (math typeset as separate pieces) or a fraction,
    and replace each fraction's numerator and denominator by one _Frac run."""
    n = len(units)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        a, b = find(a), find(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    owner: dict[int, int] = {}
    pool: list = []
    spaces: list = []
    for ui, (runs, _) in enumerate(units):
        for r in runs:
            owner[id(r)] = ui
            (pool if r.text.strip() else spaces).append(r)

    # Interleaved lines: a run of one block on another block's line, inside or touching it
    lines = []
    for ui, (runs, _) in enumerate(units):
        for ln in _visual_lines([r for r in runs if r.text.strip()]) if runs else []:
            lines.append((ui, ln.base, ln.size, ln.x0, ln.x1))
    lines.sort(key=lambda t: t[1])
    for a in range(len(lines)):
        ua, base_a, size_a, x0a, x1a = lines[a]
        for b in range(a + 1, len(lines)):
            ub, base_b, size_b, x0b, x1b = lines[b]
            tol = 0.3 * max(size_a, size_b)
            if base_b - base_a > tol:
                break
            if ua != ub and x0b <= x1a + 0.5 * size_a and x0a <= x1b + 0.5 * size_b:
                union(ua, ub)

    # Fractions, innermost (narrowest) first so a fraction inside another becomes part of it
    consumed: set[int] = set()
    fracs = []
    for bar in sorted(_fraction_bars(page), key=lambda b: b[1] - b[0]):
        found = _find_fraction(bar, pool)
        if not found:
            continue
        num, den, host = found
        num, den = _with_spaces(num, spaces), _with_spaces(den, spaces)
        frac = _Frac(bar, num, den, host)
        home = owner[id((host or num)[0])]
        for r in num + den + host:
            union(home, owner[id(r)])
        for r in num + den:
            consumed.add(id(r))
        owner[id(frac)] = home
        pool = [r for r in pool if id(r) not in consumed] + [frac]
        fracs.append(frac)

    groups: dict[int, tuple[list, list]] = {}
    for ui, (runs, rotated) in enumerate(units):
        g = groups.setdefault(find(ui), ([], []))
        g[0].extend(r for r in runs if id(r) not in consumed)
        g[1].extend(rotated)
    for frac in fracs:
        if id(frac) not in consumed:
            groups[find(owner[id(frac)])][0].append(frac)
    return [(_drop_overprint(runs), rotated) for runs, rotated in (groups[k] for k in sorted(groups))]


def _drop_overprint(runs: list) -> list:
    """Fake bold prints text twice, a fraction of a point apart, sometimes as two separate blocks: keep
    one copy of each glyph."""
    out, seen = [], {}
    for r in sorted(runs, key=lambda r: r.seq):
        if r.text.strip() and r.x1 - r.x0 >= 0.05 * r.size:  # zero-width glyphs have broken metrics
            key = (r.text, round(r.base))
            near = [q for k in (key, (r.text, key[1] - 1), (r.text, key[1] + 1)) for q in seen.get(k, ())]
            if any(abs(q.x0 - r.x0) < min(0.25 * (r.x1 - r.x0), 0.1 * r.size) and abs(q.base - r.base) < 0.5
                   for q in near):
                continue
            seen.setdefault(key, []).append(r)
        out.append(r)
    return out


def _extract_blocks(page: fitz.Page, page_no: int) -> list[TextBlock]:
    """Text blocks built from visual lines (runs sorted by position, scripts attached to their line),
    split wherever font size / weight changes between lines (separates headings), at every list bullet
    and between list items sharing a line, then re-joined into paragraphs where a producer emitted one
    block per line. Text that can't be seen (white on a blank page) becomes an artifact."""
    data = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
    backgrounds = None
    blocks: list[TextBlock] = []
    invisible: list[BBox] = []
    seq = 0
    units: list[tuple[list, list]] = []  # per producer block: (horizontal runs, rotated lines)
    for blk in data["blocks"]:
        if blk.get("type") != 0:
            continue
        runs: list[_Run] = []
        rotated: list[list[_Run]] = []  # vertical / rotated lines (margin stamps, rotated labels)
        for line in blk["lines"]:
            # Baselines only group horizontal text; other directions keep the producer's line and order
            dx, dy = line.get("dir", (1.0, 0.0))
            sideways = not (dx > 0.9 and abs(dy) < 0.2)
            line_runs: list[_Run] = []
            for span in line["spans"]:
                key = float("-inf")
                for ch in span.get("chars", []):
                    seq += 1
                    r = _Run(span, ch, seq)
                    # Within a span keep the producer's order (ligature halves can report odd positions);
                    # other spans' glyphs still slot in between by position.
                    key = r.ox = max(r.ox, key)
                    # Fake bold prints each glyph twice, slightly offset: keep one
                    if not sideways and r.text.strip() and runs and runs[-1].text == r.text \
                            and r.x1 - r.x0 >= 0.2 * r.size and abs(runs[-1].x0 - r.x0) < 0.25 * max(1.0, r.x1 - r.x0) \
                            and abs(runs[-1].base - r.base) < 0.5:
                        continue
                    if sideways:
                        r.ox = float(r.seq)
                        line_runs.append(r)
                    else:
                        runs.append(r)
            if line_runs:
                rotated.append(line_runs)
        # Fonts with broken metrics report glyphs with no width at one spot: position means nothing
        # there, so keep the content order (as text extraction always did).
        glyphs = [r for r in runs if r.text.strip()]
        if glyphs and sum(1 for r in glyphs if r.x1 - r.x0 < 0.05 * r.size) > 0.3 * len(glyphs):
            for r in runs:
                r.ox = float(r.seq)
        visible: list[_Run] = []
        for r in runs:
            if r.text.strip() and r.color >= 0xF0F0F0:  # near-white: visible only on a dark fill or an image
                if backgrounds is None:
                    backgrounds = _visible_backgrounds(page)
                if not any(b.intersects(fitz.Rect(r.x0, r.y0, r.x1, r.y1)) for b in backgrounds):
                    invisible.append((r.x0, r.y0, r.x1, r.y1))
                    continue
            visible.append(r)
        units.append((visible, rotated))
    # Producers split one visual line (or a fraction and the line it sits in) across blocks: join them,
    # and read each fraction as a unit.
    for runs, rotated in _merge_units(page, units):
        # Each visual line, split where several list items share it
        pieces: list[list[_Run]] = []
        if any(r.text.strip() for r in runs):
            for vl in _visual_lines(runs):
                pieces += _split_inline_items(vl)
        pieces += [ln for ln in rotated if any(r.text.strip() for r in ln)]
        if not pieces:
            continue
        groups: list[list[list[_Run]]] = []
        for piece in pieces:
            text = _join(piece, spoken=False)
            size, bold = _style(piece)
            if groups:
                first = _join(groups[-1][0], spoken=False)
                if len(groups[-1]) == 1 and BULLET_ONLY.match(first):
                    groups[-1].append(piece)  # bullet glyph on its own line: text follows
                    continue
                psize, pbold = _style(groups[-1][-1])
                starts_item = BULLET_START.match(text) or BULLET_ONLY.match(text)
                same_row = abs(piece[0].base - groups[-1][-1][0].base) < 0.3 * max(size, 1.0)
                if not starts_item and not same_row and abs(psize - size) <= 0.6 and pbold == bold:
                    groups[-1].append(piece)
                    continue
            groups.append([piece])
        for g in groups:
            text = _clean(" ".join(_join(pc, spoken=False) for pc in g))
            if not text:
                continue
            flat = [r for pc in g for r in pc]
            spoken = _clean(join_statements([_spoken_line(pc) for pc in g]))
            stream = _clean("".join(r.text for r in sorted(flat, key=lambda r: r.seq)))
            has_scripts = any(r.script for r in flat)
            # Spoken text is needed when scripts are present or the typeset order differs from what's seen
            reorder = re.sub(r"\s+", "", stream) != re.sub(r"\s+", "", text)
            body = [pc for pc in g if not BULLET_ONLY.match(_join(pc, spoken=False))] or g
            size, _ = _style([r for pc in body for r in pc])
            first = sorted(r.base for r in g[0] if r.text.strip() and not r.script) or [None]
            marks, mark_after, mark_boxes, lead = _raised_marks(g) if has_scripts else ([], [], [], None)
            blocks.append(
                TextBlock(
                    id="",
                    page=page_no,
                    bbox=bbox_union([(r.x0, r.y0, r.x1, r.y1) for r in flat if r.text.strip()]),
                    text=text,
                    font_size=size,
                    bold=all(_style(pc)[1] for pc in body),
                    # Spoken form when it differs from the text, or the visual order when the typeset order is scrambled
                    actual_text=spoken if (spoken != text or reorder) else None,
                    has_scripts=has_scripts,
                    baseline=first[len(first) // 2],
                    marks=marks,
                    mark_after=mark_after,
                    mark_boxes=mark_boxes,
                    lead_mark=lead,
                )
            )
    blocks = _merge_paragraph_lines(_attach_bullets(blocks))
    for box in invisible:
        blocks.append(TextBlock(id="", page=page_no, bbox=box, text="", font_size=0.0, role="Artifact",
                                artifact_type="Layout"))
    for i, b in enumerate(blocks):
        b.id = f"p{page_no}b{i}"
    return blocks


def _attach_bullets(blocks: list[TextBlock]) -> list[TextBlock]:
    """A bullet glyph extracted as its own block is joined to the text block beside it on the same line."""
    out = list(blocks)
    for bullet in [b for b in blocks if BULLET_ONLY.match(b.text)]:
        cy, h = (bullet.bbox[1] + bullet.bbox[3]) / 2, max(1.0, bullet.bbox[3] - bullet.bbox[1])
        right = [b for b in out if b is not bullet and not BULLET_ONLY.match(b.text)
                 and b.bbox[0] >= bullet.bbox[2] - 1 and b.bbox[0] - bullet.bbox[2] < 40
                 and b.bbox[1] - h <= cy <= b.bbox[1] + max(h, b.font_size) * 1.2]
        if not right:
            continue
        target = min(right, key=lambda b: b.bbox[0])
        target.text = f"\u2022 {target.text}"
        if target.actual_text:
            target.actual_text = f"\u2022 {target.actual_text}"
        target.bbox = bbox_union([bullet.bbox, target.bbox])
        out.remove(bullet)
    return out


def _merge_paragraph_lines(blocks: list[TextBlock]) -> list[TextBlock]:
    """Join consecutive single-style, left-aligned, tightly spaced blocks (one-line-per-block
    producers and OCR) into paragraphs. List items and style changes always start a new block."""
    out: list[TextBlock] = []
    for b in blocks:
        prev = out[-1] if out else None
        if prev is not None:
            size = max(prev.font_size, b.font_size, 1.0)
            gap = b.bbox[1] - prev.bbox[3]
            same_style = prev.bold == b.bold and abs(prev.font_size - b.font_size) <= max(0.6, 0.12 * size)
            aligned = abs(b.bbox[0] - prev.bbox[0]) <= 6
            if (same_style and aligned and -0.3 * size <= gap <= 0.8 * size
                    and not BULLET_START.match(b.text) and not BULLET_ONLY.match(b.text)):
                append_block(prev, b)
                continue
        out.append(b)
    return out


# --------------------------------------------------------------------------- tables
def _extract_tables(page: fitz.Page, page_no: int) -> list[TableRegion]:
    # find_tables turns PyMuPDF's process-wide "small glyph heights" on while it works, and on a rotated page that
    # throws early it never turns it off: every page after it, in this document and the next, then had shorter text
    # boxes. Put back as it was, so a page's analysis doesn't depend on the pages before it (#124: pages are shared
    # out over processes, each starting fresh)
    small = fitz.TOOLS.set_small_glyph_heights()
    try:
        found = page.find_tables()
    except Exception as exc:  # noqa: BLE001 - table finder is heuristic and occasionally throws
        log.warning("table detection failed on page %s: %s", page_no, exc)
        return []
    finally:
        fitz.TOOLS.set_small_glyph_heights(small)
    tables: list[TableRegion] = []
    for t_idx, tab in enumerate(found.tables):
        if tab.row_count < 2 or tab.col_count < 2:
            continue
        texts = tab.extract()
        raw: list[tuple[BBox, str]] = []
        for r, row in enumerate(tab.rows):
            for c, cb in enumerate(row.cells):
                if cb is not None:
                    txt = texts[r][c] if r < len(texts) and c < len(texts[r]) else ""
                    raw.append((_rect(cb), _clean(txt or "")))
        cells, n_rows, n_cols = rebuild_grid(raw)
        cells, n_rows, n_cols = _compact_grid(cells, n_rows, n_cols)
        if n_rows < 2 or n_cols < 1:
            continue
        irregular = any(c.row_span > 1 or c.col_span > 1 for c in cells) or len(cells) < n_rows * n_cols
        header_external = bool(getattr(tab.header, "external", False))
        if header_external:
            irregular = True
        region = TableRegion(
            id=f"p{page_no}t{t_idx}",
            page=page_no,
            bbox=_rect(tab.bbox),
            n_rows=n_rows,
            n_cols=n_cols,
            cells=cells,
            header_rows=1,
            irregular=irregular or any(not c.text for c in cells if c.row == 0),
        )
        region.header_evidence = _header_row_evidence(region.cells) or _bold_header_row(page, region.cells)
        for cell in region.cells:
            cell.is_header = cell.row < region.header_rows
        tables.append(region)
    return tables


def _looks_scanned(doc, img) -> bool:
    """Whether a page-sized picture looks like a scan rather than a photograph or a slide's background: black and
    white (1 bit, or fax or JBIG2 compression, which only scanners and fax software write), or gray at scanner
    resolution (150 dpi or more). A color picture isn't taken: a slide's background photo looks the same."""
    if not img.xref:
        return False
    def key(name):
        try:
            return doc.xref_get_key(img.xref, name)[1] or ""
        except Exception:  # noqa: BLE001 - an odd image dictionary is simply not a scan
            return ""
    if key("BitsPerComponent") == "1" or any(f in key("Filter") for f in ("CCITTFax", "JBIG2")):
        return True
    width_pt = max(1.0, img.bbox[2] - img.bbox[0])
    return _gray(doc, img.xref) and (img.width or 0) / width_pt * 72 >= 150


def _gray(doc, xref: int) -> bool:
    """Whether an image has one color component: DeviceGray, CalGray, or an ICC profile with /N 1 (how scanners
    and PyMuPDF often write gray)."""
    try:
        kind, value = doc.xref_get_key(xref, "ColorSpace")
        if kind == "xref":
            value = doc.xref_object(int(value.split()[0]), compressed=True)
        if "/DeviceGray" in value or "/CalGray" in value:
            return True
        m = re.search(r"/ICCBased\s+(\d+)\s+0\s+R", value)
        return bool(m) and doc.xref_get_key(int(m.group(1)), "N")[1] == "1"
    except Exception:  # noqa: BLE001 - an odd color space is simply not a gray scan
        return False


def _sparse_text(page) -> bool:
    """A few typed values, not a page of text (a scan that was already OCR'd has its whole text, invisible)."""
    words = page.get_text("words")
    chars = sum(len(w[4]) for w in words)
    area = sum((w[2] - w[0]) * (w[3] - w[1]) for w in words)
    return chars <= 400 and area <= 0.1 * page.rect.width * page.rect.height


def _compact_grid(cells: list[TableCell], n_rows: int, n_cols: int) -> tuple[list[TableCell], int, int]:
    """Drop grid columns/rows that are empty everywhere. Table finders split cells on every
    ruling line, which leaves phantom empty columns that screen readers would announce."""
    used_cols = sorted({c.col for c in cells if c.text})
    used_rows = sorted({c.row for c in cells if c.text})
    if not used_cols or not used_rows:
        return cells, n_rows, n_cols
    col_map = {old: new for new, old in enumerate(used_cols)}
    row_map = {old: new for new, old in enumerate(used_rows)}
    out = []
    for c in cells:
        if c.col in col_map and c.row in row_map:
            c.row, c.col = row_map[c.row], col_map[c.col]
            out.append(c)
        elif c.text == "" and c.bbox is not None:
            continue  # phantom cell; any text in its area falls back to the nearest real cell
    return out, len(used_rows), len(used_cols)


def _bold_header_row(page: fitz.Page, cells: list[TableCell]) -> bool:
    """Row 0 is bold while the body is not."""
    def bold_ratio(cs) -> float | None:
        total = bold = 0
        for c in cs:
            if not c.text or c.bbox is None:
                continue
            for blk in page.get_text("dict", clip=fitz.Rect(c.bbox))["blocks"]:
                for ln in blk.get("lines", []):
                    for sp in ln["spans"]:
                        n = len(sp["text"].strip())
                        total += n
                        if sp["flags"] & _BOLD_FLAG or "bold" in sp["font"].lower():
                            bold += n
        return bold / total if total else None

    head = bold_ratio([c for c in cells if c.row == 0])
    body = bold_ratio([c for c in cells if c.row > 0][:30])
    return head is not None and head > 0.8 and (body is None or body < 0.3)


def _edges(values: list[float], tol: float = 1.5) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if out and v - out[-1] <= tol:
            continue
        out.append(v)
    return out


def rebuild_grid(raw: list[tuple[BBox, str]], min_col: float = 8.0, min_row: float = 6.0
                 ) -> tuple[list[TableCell], int, int]:
    """Rebuild a table grid from cell rectangles.

    Column/row boundaries are the distinct cell edges; gaps narrower than a column or row could be
    (double borders) are dropped. Each cell is placed on the real columns/rows it covers, giving
    its spans. Rows whose positions are mostly not covered by any cell are wrapped text that the
    finder split off, and are folded into the row above."""
    xs = _edges([v for b, _ in raw for v in (b[0], b[2])])
    ys = _edges([v for b, _ in raw for v in (b[1], b[3])])
    cols = [(a, b) for a, b in zip(xs, xs[1:]) if b - a >= min_col]
    rows = [(a, b) for a, b in zip(ys, ys[1:]) if b - a >= min_row]
    if not cols or not rows:
        return [], 0, 0

    def covered(lo: float, hi: float, bands) -> list[int]:
        return [i for i, (a, b) in enumerate(bands) if lo - 1 <= (a + b) / 2 <= hi + 1]

    placed: dict[tuple[int, int], TableCell] = {}
    for bbox, text in raw:
        cs, rs = covered(bbox[0], bbox[2], cols), covered(bbox[1], bbox[3], rows)
        if not cs or not rs:
            continue  # a border-gap sliver
        cell = TableCell(row=rs[0], col=cs[0], bbox=bbox, text=text, row_span=len(rs), col_span=len(cs))
        prev = placed.get((cell.row, cell.col))
        # Overlapping duplicates (a wide cell spanning border gaps): keep the tighter one with text.
        if prev is None or (bool(text), -cell.row_span * cell.col_span) > (bool(prev.text), -prev.row_span * prev.col_span):
            placed[(cell.row, cell.col)] = cell
    cells = list(placed.values())
    n_cols = len(cols)

    def is_continuation(r: int) -> bool:
        """Row r holds only wrapped text: few cells start here, each continues the cell above
        (lower-case start or the cell above ends mid-sentence), and anything else in the row is
        a cell spanning down from the row directly above."""
        starting = [c for c in cells if c.row == r]
        own = {k for c in starting for k in range(c.col, c.col + c.col_span)}
        if not starting or len(own) > n_cols / 2:
            return False
        for c in starting:
            above = next((a for a in cells if a.row == r - 1 and a.col == c.col), None)
            if not c.text or above is None or not (c.text[0].islower() or above.text.rstrip().endswith((",", ";", "-", "and", "or"))):
                return False
        spanning = [c for c in cells if c.row < r < c.row + c.row_span]
        return all(c.row == r - 1 for c in spanning)

    row_map: dict[int, int] = {}
    new_row = -1
    for r in range(len(rows)):
        if r > 0 and is_continuation(r):
            row_map[r] = new_row
        else:
            new_row += 1
            row_map[r] = new_row

    merged: list[TableCell] = []
    for c in sorted(cells, key=lambda c: (c.row, c.col)):
        start, end = row_map[c.row], row_map[c.row + c.row_span - 1]
        host = next((m for m in merged if m.row <= start < m.row + m.row_span
                     and m.col <= c.col < m.col + m.col_span), None)
        if host is not None and row_map.get(c.row - 1) == start:
            host.text = f"{host.text} {c.text}".strip()
            host.bbox = bbox_union([host.bbox, c.bbox]) if host.bbox and c.bbox else host.bbox or c.bbox
            continue
        c.row, c.row_span = start, end - start + 1
        merged.append(c)
    return merged, new_row + 1, n_cols


_NUMERIC = re.compile(r"^[\s$€£%+\-.,()\d]+$")


def _header_row_evidence(cells: list[TableCell]) -> bool:
    """Row 0 is all non-empty labels while the body is mostly numeric (a classic data table)."""
    head = [c.text for c in cells if c.row == 0]
    body = [c.text for c in cells if c.row > 0 and c.text]
    if not head or not body or any(not t for t in head):
        return False
    labels = all(not _NUMERIC.match(t) for t in head)
    numeric_body = sum(1 for t in body if any(ch.isdigit() for ch in t)) / len(body)
    return labels and numeric_body >= 0.5


# --------------------------------------------------------------------------- images
def _extract_images(page: fitz.Page, page_no: int) -> list[ImageRegion]:
    images: list[ImageRegion] = []
    seen: set[tuple] = set()
    for info in page.get_image_info(xrefs=True):
        bbox = _rect(info["bbox"])
        key = (info.get("xref", 0), bbox)
        if key in seen or bbox_area(bbox) <= 0:
            continue
        seen.add(key)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        images.append(
            ImageRegion(
                id=f"p{page_no}i{len(images)}",
                page=page_no,
                bbox=bbox,
                xref=info.get("xref", 0),
                width=info.get("width", 0),
                height=info.get("height", 0),
                # Spacer pixels, rules and tiny ornaments carry no information.
                decorative=w < 6 or h < 6 or (w * h) < 150,
            )
        )
    return images


def _is_page_rule(d: dict, page: fitz.Page) -> bool:
    """Page furniture drawn with strokes only: frames, margin rules, header/footer lines. Left in the
    clustering, one long rule glues every drawing on the page into a single page-sized "figure"."""
    if d.get("fill") is not None or d.get("type") not in ("s", None):
        return False
    r = fitz.Rect(d["rect"])
    items = d.get("items") or []
    if not items or any(it[0] not in ("l", "re") for it in items) or len(items) > 8:
        return False
    return r.height > 0.5 * page.rect.height or r.width > 0.6 * page.rect.width


def _segments(page: fitz.Page, rot: fitz.Matrix) -> list[tuple[float, float, float, float]]:
    """The straight strokes drawn on a page (page rules left out), as the page is seen."""
    out = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - odd drawings: no line art is found, as before
        return out
    for d in drawings:
        if _is_page_rule(d, page):
            continue
        for item in d.get("items") or []:
            if item[0] == "l":
                a, b = item[1] * rot, item[2] * rot
                out.append((a.x, a.y, b.x, b.y))
            elif item[0] == "re":
                # A stroke drawn as a hairline rectangle (drawing programs set an arrow's shaft this way)
                r = fitz.Rect(item[1]) * rot
                if min(r.width, r.height) <= 1.5 < max(r.width, r.height) <= 110:   # longer is a frame or a rule
                    mx, my = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
                    out.append((r.x0, my, r.x1, my) if r.width >= r.height else (mx, r.y0, mx, r.y1))
    return out


def _extract_vector_figures(page: fitz.Page, page_no: int, start: int, tables: list[TableRegion],
                            blocks: list[TextBlock], rot: fitz.Matrix) -> list[ImageRegion]:
    """Charts/diagrams drawn with vector paths (not images). Frames around text and table grids are excluded."""
    try:
        drawings = [d for d in page.get_drawings() if not _is_page_rule(d, page)]
        clusters = page.cluster_drawings(drawings=drawings) if drawings else []
    except Exception:  # noqa: BLE001 - older PyMuPDF or odd drawings
        return []
    if not clusters:
        return []
    page_area = page.rect.width * page.rect.height
    all_drawings = [fitz.Rect(d["rect"]) * rot for d in drawings]
    words = [_rect(fitz.Rect(w[:4]) * rot) for w in page.get_text("words")]
    out: list[ImageRegion] = []
    for rect in clusters:
        bbox = _rect(fitz.Rect(rect) * rot)
        area = bbox_area(bbox)
        if area > 0.9 * page_area or min(bbox[2] - bbox[0], bbox[3] - bbox[1]) < 18:
            continue
        shapes = sum(1 for r in all_drawings if r.intersects(fitz.Rect(bbox)))
        # A curve is often one path of hundreds of short segments: that is a graph, not "a couple of arrows"
        segments = sum(len(d.get("items") or []) for d, r in zip(drawings, all_drawings) if r.intersects(fitz.Rect(bbox)))
        if segments >= 40:
            shapes = max(shapes, 8)
        # Small drawings count only when made of many shapes (a calculator display, a chess board),
        # not a couple of arrows or a rule.
        if area < 0.02 * page_area and (area < 0.004 * page_area or shapes < 8):
            continue
        if shapes < 3:
            continue
        if any(bbox_intersection(bbox, t.bbox) > 0.5 * area for t in tables):
            continue
        # Text in the drawing means a shaded box or frame around text, not a picture. Block boxes alone
        # aren't enough: a paragraph wrapping beside a picture has a block box covering it, but no words.
        block_text = sum(bbox_intersection(bbox, b.bbox) for b in blocks)
        word_text = sum(bbox_intersection(bbox, w) for w in words)
        if word_text > 0.12 * area or (block_text > 0.25 * area and word_text > 0.03 * area):
            continue
        out.append(ImageRegion(id=f"p{page_no}i{start + len(out)}", page=page_no, bbox=bbox, xref=0, kind="vector"))
    return out


# --------------------------------------------------------------------------- fonts
def _font_issues(doc: fitz.Document, page: fitz.Page, page_no: int) -> list[dict]:
    issues = []
    for xref, ext, ftype, basefont, _name, encoding, *_ in page.get_fonts(full=True):
        problems = []
        if ftype == "Type3":
            problems.append("type3")
        elif ext == "n/a":
            problems.append("not_embedded")
        has_tu = doc.xref_get_key(xref, "ToUnicode")[0] != "null"
        if not has_tu and (ftype in ("Type0", "Type3") or (encoding and encoding not in _STD_ENCODINGS)):
            problems.append("no_tounicode")
        if problems:
            issues.append({"page": page_no, "xref": xref, "font": basefont, "type": ftype, "problems": problems})
    return issues


# --------------------------------------------------------------------------- driver
def _to_visual(pa: PageAnalysis, rot: fitz.Matrix) -> None:
    v = lambda b: _rect(fitz.Rect(b) * rot)  # noqa: E731
    for b in pa.blocks:
        b.bbox = v(b.bbox)
    for i in pa.images:
        i.bbox = v(i.bbox)
    for t in pa.tables:
        t.bbox = v(t.bbox)
        for c in t.cells:
            if c.bbox is not None:
                c.bbox = v(c.bbox)


def _cell_at(table: TableRegion, b: TextBlock) -> TableCell | None:
    cx, cy = (b.bbox[0] + b.bbox[2]) / 2, (b.bbox[1] + b.bbox[3]) / 2
    return next((c for c in table.cells if c.bbox and c.bbox[0] <= cx <= c.bbox[2] and c.bbox[1] <= cy <= c.bbox[3]),
                None)


# One page analyzed at a time in a process: table detection switches a PyMuPDF setting for the whole process while
# it works (see _extract_tables), so a page read in another thread meanwhile came out different, and two at once
# could leave it switched. Nothing is lost: PyMuPDF holds the interpreter lock, so threads never ran pages in
# parallel; more than one core is page_pool's processes (#124)
_PAGE_LOCK = threading.Lock()


def analyze_page(doc: fitz.Document, page_no: int) -> tuple[PageAnalysis, list[dict]]:
    with _PAGE_LOCK:
        return _analyze_page(doc, page_no)


def _analyze_page(doc: fitz.Document, page_no: int) -> tuple[PageAnalysis, list[dict]]:
    page = doc[page_no]
    # Everything is analyzed in *visual* page space (what a reader sees, after /Rotate), so layout
    # heuristics like "top of page" and "left column" hold for rotated pages too. PyMuPDF extracts
    # in unrotated space; results are mapped through the rotation matrix below.
    rot = page.rotation_matrix
    m = page.transformation_matrix * rot
    pa = PageAnalysis(
        number=page_no,
        width=page.rect.width,
        height=page.rect.height,
        rotation=page.rotation,
        pdf_to_page=(m.a, m.b, m.c, m.d, m.e, m.f),
    )

    pa.blocks = _extract_blocks(page, page_no)
    pa.tables = _extract_tables(page, page_no)
    pa.images = _extract_images(page, page_no)
    if page.rotation:
        _to_visual(pa, rot)
    vectors = _extract_vector_figures(page, page_no, len(pa.images), pa.tables, pa.blocks, rot)
    if vectors:
        pa.images += vectors
        # Labels inside a chart belong to the figure (its alt text), not the reading flow.
        pa.blocks = [b for b in pa.blocks
                     if not any(bbox_intersection(b.bbox, v.bbox) >= 0.9 * (bbox_area(b.bbox) or 1.0) for v in vectors)]

    # Graphs drawn as hundreds of specks: one figure each, with their rules and labels
    dense = plots.find_dense_regions(pa.images, pa.blocks, pa.width, pa.height)
    if dense:
        plots.add_to_page(pa, dense)

    # Chemical structures and reaction schemes drawn with lines: one figure each, with the labels on them
    segments = _segments(page, rot)
    if segments:
        words = [(_rect(fitz.Rect(w[:4]) * rot), w[4]) for w in page.get_text("words")]
        drawn = schemes.find_regions(segments, words, [(b.bbox, b.text, b.bold) for b in pa.blocks], pa.width, pa.height,
                                     avoid=[t.bbox for t in pa.tables])
        if drawn:
            schemes.add_to_page(pa, drawn, words)

    # Text belonging to a table is tagged through the table cells, not as paragraphs. The cells keep the
    # spoken form of any math the blocks inside them read (fractions, exponents).
    if pa.tables:
        kept = []
        in_cells: dict[int, list[TextBlock]] = {}
        cells: dict[int, TableCell] = {}
        for b in pa.blocks:
            area = bbox_area(b.bbox) or 1.0
            table = next((t for t in pa.tables if bbox_intersection(b.bbox, t.bbox) / area > 0.5), None)
            if table is None:
                kept.append(b)
                continue
            cell = _cell_at(table, b)
            if cell is not None:
                cells[id(cell)] = cell
                in_cells.setdefault(id(cell), []).append(b)
        pa.blocks = kept
        for key, blocks in in_cells.items():
            if any(b.actual_text for b in blocks):
                blocks.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
                cells[key].actual_text = " ".join(b.actual_text or b.text for b in blocks)

    page_area = pa.width * pa.height
    big = [i for i in pa.images if bbox_area(i.bbox) > 0.8 * page_area]
    # A scan, or a scan with a few values typed onto it (a filled-in form): the typed text is real, but the printed
    # form is only in the picture and needs reading
    pa.is_scanned = bool(big) and (not page.get_text("text").strip()
                                   or (any(_looks_scanned(doc, i) for i in big) and _sparse_text(page)))

    issues = _font_issues(doc, page, page_no)
    pa.font_issues = sorted({p for i in issues for p in i["problems"]})
    return pa, issues


def analyze_pages(path: Path | str, numbers: list[int]) -> dict[int, tuple[PageAnalysis, list[dict]]]:
    """The pages numbered, each analyzed as analyze_document does (one page_pool worker's share, #124)."""
    with fitz.open(path) as doc:
        return {n: analyze_page(doc, n) for n in numbers}


def analyze_document(path: Path | str) -> DocumentAnalysis:
    from app.pipeline import page_pool

    doc = fitz.open(path)
    try:
        pages, font_issues = [], []
        workers = page_pool.workers_for(doc.page_count)
        done = page_pool.run("app.pipeline.analyzer:analyze_pages", path, doc.page_count, workers) \
            if workers > 1 else None
        for n in range(doc.page_count):
            pa, issues = done[n] if done is not None else analyze_page(doc, n)
            pages.append(pa)
            font_issues.extend(issues)

        cat = doc.pdf_catalog()
        lang_t, lang_v = doc.xref_get_key(cat, "Lang")
        language = lang_v.strip("()") if lang_t == "string" and lang_v else None
        struct_t, _ = doc.xref_get_key(cat, "StructTreeRoot")
        mark_t, mark_v = doc.xref_get_key(cat, "MarkInfo/Marked")
        title = (doc.metadata or {}).get("title") or None

        sizes: Counter = Counter()
        for p in pages:
            for b in p.blocks:
                sizes[b.font_size] += len(b.text)
        body = sizes.most_common(1)[0][0] if sizes else 10.0

        return DocumentAnalysis(
            page_count=doc.page_count,
            pages=pages,
            title=title.strip() if title else None,
            language=language,
            has_struct_tree=struct_t not in ("null", ""),
            is_marked=mark_v == "true",
            body_font_size=body,
            font_issues=font_issues,
        )
    finally:
        doc.close()
