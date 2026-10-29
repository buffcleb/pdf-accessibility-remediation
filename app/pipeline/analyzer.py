"""PyMuPDF-based document analysis: text blocks, images, tables, fonts, renders for AI."""
from __future__ import annotations

import logging
import math
import re
import threading
from collections import Counter
from pathlib import Path

import numpy as np
import pymupdf as fitz

from app.pipeline.lists import roman_value
from app.pipeline.markers import BULLET_ONLY, BULLET_START, FOOTNOTE_MARK, LABEL_RUN
from app.pipeline.symbol_chars import unicode_for
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
# Lines of one block differ in text size by at most this many points; and the gap from one line of a paragraph to the
# next, as a share of the text size
_STYLE_SIZE_TOL = 0.6
_LINE_GAP = (-0.3, 0.8)

# A superscript reads as a power after a number, a single-letter variable or a closing bracket.
_DOTS = ".,\u00b7"
_POWER_BASE = re.compile(r"(\d|(^|[^A-Za-z])[A-Za-z]|\))\s*$")
# A superscript that is only a sign, or a digit and a sign ("+", "−", "2+", "3−"): a charge
_CHARGE = re.compile(r"^(\d?)([+\-−–⊕⊖])$")


class _Run:
    """One character with its position, as typeset. Math producers emit glyphs out of order and may
    draw a decimal point into a gap left in a run ("3 46" + "."), so order comes from position."""
    __slots__ = ("x0", "y0", "x1", "y1", "ox", "text", "size", "bold", "base", "script", "color", "seq", "ocr")

    def __init__(self, span: dict, char: dict, seq: int):
        self.x0, self.y0, self.x1, self.y1 = _rect(char["bbox"])
        # A Symbol or Wingdings character the document's own map gives as a private use code point, as Unicode (#605)
        self.text = unicode_for(span["font"], char["c"])
        self.size = float(span["size"])
        self.bold = bool(span["flags"] & _BOLD_FLAG) or "bold" in span["font"].lower()
        self.base = float(char["origin"][1])
        self.ox = float(char["origin"][0])  # pen position: reliable even where glyph boxes are not
        self.script: str | None = None  # "sup" / "sub" when attached to a line as a script
        self.color = int(span.get("color", 0))
        self.seq = seq  # extraction (content stream) order
        self.ocr = False  # from the engine's OCR layer: written a line at a time, at a size that fits the line's box


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
            # An OCR layer's smaller text is a smaller line (a form's label), never a script: OCR writes no scripts
            if (not r.ocr and r.size <= 0.85 * ln.size and near and shifted
                    and -0.75 * ln.size <= offset <= 0.5 * ln.size):
                if host is None or abs(offset) < abs(r.base - host.base):
                    host = ln
        if host is not None:
            r.script = "sup" if r.base < host.base else "sub"
            host.add(r)
            continue
        # An OCR line's baseline is worked out from its box, so lines of different print on one row come out
        # nearly level (a form's number in small print, its title across the page): much smaller, it's its own
        same = [ln for ln in lines if abs(r.base - ln.base) <= 0.3 * max(ln.size, r.size)
                and not (r.ocr and r.size < 0.7 * ln.size)]
        if same:
            min(same, key=lambda ln: abs(r.base - ln.base)).add(r)
            continue
        lines.append(_VLine(r))
    lines = _join_shifted(lines)
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


def _join_shifted(lines: list[_VLine]) -> list[_VLine]:
    """Scripts typeset at full size and moved off the line by a text rise ("N" then "2(g)" lowered a third of a
    character, where another line of the same document uses a smaller font): too far from the baseline to be the
    same line, too large to be taken for a script, so they became a line of their own and split the formula. A
    line of the same size whose baseline is a third to a half of a character off another's, starting inside it or
    right where it ends, joins it as its scripts. Lines of text are always a whole line apart."""
    out: list[_VLine] = []
    for ln in sorted(lines, key=lambda ln: (ln.x0, -len(ln.runs))):
        if all(r.ocr for r in ln.runs):  # an OCR layer's line is a whole line, never moved off another
            out.append(ln)
            continue
        host = None
        for h in out:
            # The same size: a text rise moves text without scaling it. (A footnote mark a point smaller, raised
            # beside its note, is the footnotes' business, not a script of the note's first line.)
            if abs(h.size - ln.size) > 0.05 * max(h.size, ln.size) or not 0.3 * h.size < abs(ln.base - h.base) <= 0.5 * h.size:
                continue
            inside = any(r.x0 >= ln.x0 for r in h.runs)  # the host's text goes on after the shifted part starts
            touching = 0 <= ln.x0 - h.x1 <= 0.6 * h.size
            if not (inside or touching):
                continue
            # A short raised or lowered start ("2" then the line): the line is ln, the start its script
            swap = not inside and len(h.runs) < len(ln.runs)
            script, line = (h, ln) if swap else (ln, h)
            if _glued(script.runs, line.runs, h.size):
                host = h
                break
        if host is None:
            out.append(ln)
            continue
        if swap:
            out.remove(host)
            host, ln = ln, host
            out.append(host)
        for r in ln.runs:
            r.script = r.script or ("sub" if r.base > host.base else "sup")
            host.add(r)
    return out


def _glued(shifted: list[_Run], line: list[_Run], size: float) -> bool:
    """Whether each piece of the shifted text touches a character of the line, as a script does: starting where
    one ends ("N" + "2(g)"), or ending where one starts (a raised "2" before a line). Labels set above or below an
    arrow in a reaction or block diagram sit apart from the text beside them: they stay lines of their own."""
    gap = 0.35 * size
    ends = [r.x1 for r in line]
    starts = [r.x0 for r in line]
    pieces: list[list[_Run]] = []
    for r in sorted(shifted, key=lambda r: r.x0):
        if pieces and r.x0 - pieces[-1][-1].x1 <= gap:
            pieces[-1].append(r)
        else:
            pieces.append([r])
    return all(any(abs(p[0].x0 - e) <= gap for e in ends) or any(abs(st - p[-1].x1) <= gap for st in starts)
               for p in pieces)


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


def _centred_over(above: list[_Run], below: list[_Run], width: float, size: float) -> bool:
    """The line above is centred on the page (as far in from the margin the line below starts at as it stops short of
    the other margin, at least 3 em each side), and the line below starts at that margin."""
    a = [r for r in above if r.text.strip()]
    b = [r for r in below if r.text.strip()]
    if not a or not b:
        return False
    a0, a1, margin = min(r.x0 for r in a), max(r.x1 for r in a), min(r.x0 for r in b)
    left, right = a0 - margin, (width - margin) - a1
    return min(left, right) >= 3 * size and abs(left - right) <= max(size, 0.1 * left)


def _leads_with_mark(piece: list[_Run]) -> bool:
    """The line starts with a raised footnote mark, with words after it."""
    return any(r.script == "sup" for r in piece) and _raised_marks([piece])[3] is not None


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


def _apart_from_typed(runs: list[_Run]) -> list[list[_Run]]:
    """A line of a scanned form split where it passes between the form's print (the OCR layer) and a value typed
    onto it: the label and the value are each a block of their own, and each reads as drawn (the value is drawn
    before the OCR layer, so one tag holding both would show the value first)."""
    out: list[list[_Run]] = []
    kind = None
    for r in runs:
        if r.text.strip() and r.ocr != kind:
            kind = r.ocr
            out.append([])
        if not out:
            out.append([])
        out[-1].append(r)
    return [part for part in out if any(r.text.strip() for r in part)]


def _label_kind(word: list[_Run]) -> str:
    """A list label's kind: a number, or a letter or roman numeral."""
    return "number" if any(r.text.isdigit() for r in word) else "letter"


_PART_LABEL = re.compile(r"^\s*\(([b-z])\)")


def _wraps_into_part(group: list[list[_Run]], text: str) -> bool:
    """A part's label at the start of a line, where the block so far has the part before it in its running text
    ("... (a) the sum of ...; (b) the product" then "(c) the product 5.6 × π") and the line above runs on to the
    block's right edge: the text wraps there, it doesn't start a list item. (Parts set one under another end short.)"""
    m = _PART_LABEL.match(text)
    if not m:
        return False
    right = max(r.x1 for line in group for r in line)
    size = max(_style(group[-1])[0], 1.0)
    above = " ".join(_join(line, spoken=False) for line in group)
    return max(r.x1 for r in group[-1]) >= right - 2 * size and above.find(f"({chr(ord(m.group(1)) - 1)})") > 0


def _split_inline_items(line: _VLine) -> list[list[_Run]]:
    """'(a) 346 000 000   (b) 2710   (c) 0.000543' on one line is three list items, not one."""
    words = _words(line.runs)
    if len(words) < 3 or not LABEL_RUN.match("".join(r.text for r in words[0])):
        return [line.runs]
    # The gap is measured against the line's ordinary text size: tall brackets or symbols in the line
    # would otherwise ask for a much wider gap than the text needs
    sizes = sorted(r.size for r in line.runs if r.text.strip().isalnum() and not r.script) or [line.size]
    size = sizes[len(sizes) // 2]
    cuts = []
    for k in range(1, len(words)):
        w = words[k]
        if any(r.script for r in w) or not LABEL_RUN.match("".join(r.text for r in w)):
            continue
        if k == 1 and _label_kind(words[0]) != _label_kind(w):
            continue  # "7.   (a) Can an object ...": a problem's number and its first part, one item
        if w[0].x0 - words[k - 1][-1].x1 >= 0.9 * size:  # well beyond a word space (about 0.25 em)
            cuts.append(w[0].x0)
    if not cuts:
        return [line.runs]
    bounds = [float("-inf"), *cuts, float("inf")]
    pieces = [[r for r in line.runs if lo <= r.x0 < hi] for lo, hi in zip(bounds, bounds[1:])]
    return [pc for pc in pieces if any(r.text.strip() for r in pc)]


_CHAPTER_NO = re.compile(r"^(?:\d{1,2}|[IVX]{1,5})$")


def _apart_from_chapter_number(runs: list[_Run]) -> list[list[_Run]]:
    """A chapter's number set huge beside its title's last line ("made in a small batch vessel at room heat   4", the 4 in
    80 points): a piece of its own, as it is when it stands on a line apart, and not a word of the title (#665)."""
    words = _words(runs)
    if len(words) < 2:
        return [runs]
    for word in (words[-1], words[0]):
        text = "".join(r.text for r in word).strip()
        rest = [r.size for w in words if w is not word for r in w if r.text.strip()]
        if rest and _CHAPTER_NO.match(text) and min(r.size for r in word) >= 2.5 * max(rest):
            inside = set(map(id, word))
            return [[r for r in runs if id(r) not in inside], word]
    return [runs]


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
    """Areas where light text can still be seen: anything filled with a color, and images (in visual page space)."""
    rects = []
    for d in page.get_drawings():
        fill = d.get("fill")
        if fill is not None and sum(fill) / len(fill) < 0.9:
            rects.append(fitz.Rect(d["rect"]))
    for info in page.get_image_info():
        rects.append(fitz.Rect(info["bbox"]))
    return [r * page.rotation_matrix for r in rects] if page.rotation else rects


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
        self.ocr = False
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
    """Short horizontal rules that could be fraction bars: (x0, x1, y), in visual page space. A rule that is part of a
    box or a grid (a table cell's border: another rule carries on from its end, or an upright one meets it) is not
    one."""
    bars, across, upright = [], [], []
    limit = 0.6 * page.rect.width
    rot = page.rotation_matrix if page.rotation else fitz.Identity
    for d in page.get_drawings():
        for it in d.get("items") or []:
            if it[0] == "l":
                p1, p2 = it[1] * rot, it[2] * rot
                if abs(p1.y - p2.y) <= 0.3 and abs(p2.x - p1.x) >= 2.5:
                    across.append((min(p1.x, p2.x), max(p1.x, p2.x), (p1.y + p2.y) / 2))
                elif abs(p1.x - p2.x) <= 0.3 and abs(p2.y - p1.y) >= 2.5:
                    upright.append(((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y)))
            elif it[0] == "re":
                r = fitz.Rect(it[1]) * rot
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

    def runs_on(r) -> bool:
        # A piece of a line of text that carries on well past the bar: the paragraph above or below the fraction (or
        # the page's footer under it), set a little smaller, not a script of its numerator or denominator ("e in gram"
        # of "solute in grams" read into W over M.W)
        return any(o is not r and o.text.strip() and abs(o.base - r.base) <= 0.3 * r.size
                   and (o.x1 < bx0 - size or o.x0 > bx1 + size) for o in pool)

    def belongs(r, base: float, lo: float, hi: float) -> bool:
        cy = (r.y0 + r.y1) / 2
        if not lo <= cy <= hi:
            return False
        # a smaller script beside the part, or more of the part's own line over the bar
        return (r.size < 0.9 * size and not runs_on(r)) or (
            abs(r.base - base) <= 0.3 * size and bx0 - 0.5 <= (r.x0 + r.x1) / 2 <= bx1 + 0.5)

    num += [r for r in near if belongs(r, num_base, top, by - 0.01)]
    den += [r for r in near if r not in num and belongs(r, den_base, by + 0.01, bottom)]
    host = [r for r in pool if r.text.strip() and r not in num and r not in den and r.y0 < by < r.y1
            and (0 <= bx0 - r.x1 <= 1.5 * size or 0 <= r.x0 - bx1 <= 1.5 * size)]
    return num, den, host


_RADICALS = frozenset("√∛∜")


# An ion's charge written alone: "2-", "3+", "2–"
_ION_CHARGE = re.compile(r"\s*\d\s*[+\-\u2013\u2212]\s*")


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
    lines, charges = [], []
    for ui, (runs, _) in enumerate(units):
        for ln in _visual_lines([r for r in runs if r.text.strip()]) if runs else []:
            lines.append((ui, ln.base, ln.size, ln.x0, ln.x1))
            if _ION_CHARGE.fullmatch("".join(r.text for r in ln.runs)):
                charges.append(lines[-1])
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
            elif ua != ub:
                # A tall radical sign set as a block of its own parts the line: the sign in the gap, reaching above
                # and below the line, joins all three ("T = 2π" √ "ℓ/g for the time"). Only a radical: a tall
                # integral or sum in a displayed equation stands between pieces that are lines of their own
                (lu, lb, lx1), (ru, rx0) = ((ua, base_a, x1a), (ub, x0b)) if x0b > x1a else ((ub, base_b, x1b), (ua, x0a))
                for r in pool:
                    if (r.text in _RADICALS and owner[id(r)] not in (lu, ru) and lx1 - 1 <= r.x0 and r.x1 <= rx0 + 1 and r.x1 - r.x0 < 3 * size_a
                            and r.y1 - r.y0 >= 2.5 * size_a and r.y0 < lb - 0.7 * size_a and r.y1 > lb + 0.5 * size_a
                            and sum(1 for o in units[owner[id(r)]][0] if o.text.strip()) == 1):
                        union(lu, owner[id(r)])
                        union(lu, ru)
                        # Read in the line, not as a line of its own (its origin is at its top) nor as text of
                        # another size
                        r.base, r.size = lb, size_a
                        break

    # An ion's charge in smaller print, a line of its own that the producer started a block with ("Pb(OH)4" | "2- +
    # ClO- -> ..."): it fills the gap the test above measures, its baseline raised too far to count, but it touches the
    # end of a line in larger print, inside that line's height. Read apart, one equation was three paragraphs. Only a
    # charge: a root's index or a power in TeX math is placed by the fraction and radical rules
    for us, base_s, size_s, x0s, x1s in charges:
        for ul, base_l, size_l, x0l, x1l in lines:
            if (us != ul and size_s <= 0.8 * size_l and -0.75 * size_l <= base_s - base_l <= 0.4 * size_l
                    and x0s <= x1l + 0.5 * size_s and x0l <= x1s + 0.5 * size_s):
                union(us, ul)

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


def _text_to_visual(data: dict, rot: fitz.Matrix) -> None:
    """PyMuPDF's text of a rotated page, its boxes, pen positions and writing directions turned into visual page space.
    Text that reads across the screen is written up or down the unrotated page (a landscape page, a screenshot turned
    to fit): judged unturned, each of its lines stood apart, never joined into paragraphs or read by baseline."""
    turn = fitz.Matrix(rot.a, rot.b, rot.c, rot.d, 0, 0)
    for blk in data["blocks"]:
        if blk.get("type") != 0:
            continue
        blk["bbox"] = tuple(fitz.Rect(blk["bbox"]) * rot)
        for line in blk["lines"]:
            line["bbox"] = tuple(fitz.Rect(line["bbox"]) * rot)
            if "dir" in line:
                line["dir"] = tuple(fitz.Point(line["dir"]) * turn)
            for span in line["spans"]:
                span["bbox"] = tuple(fitz.Rect(span["bbox"]) * rot)
                if "origin" in span:
                    span["origin"] = tuple(fitz.Point(span["origin"]) * rot)
                for ch in span.get("chars", []):
                    ch["bbox"] = tuple(fitz.Rect(ch["bbox"]) * rot)
                    ch["origin"] = tuple(fitz.Point(ch["origin"]) * rot)


# A contents page's own title, and what an entry ends with: its page number, in figures or roman numerals
_CONTENTS_TITLE = re.compile(r"^\s*(?:table\s+of\s+)?contents\s*$|^\s*list\s+of\s+(?:figures|tables|illustrations)\s*$",
                             re.I)
_PAGE_REF = re.compile(r"(?:^|\s)(\d{1,4}|[ivxlc]{1,7}|[IVXLC]{1,7})\s*$")
# What starts an entry of its own: a section's number, or the word that labels a chapter or part
_ENTRY_START = re.compile(r"^\s*(?:\d{1,3}(?:\.\d{1,3})*\.?\s"
                          r"|(?:Chapter|CHAPTER|Part|PART|Appendix|APPENDIX|Section|SECTION)\s+\w+)")


def _page_ref(ws: list) -> int | None:
    """The page number a contents line ends with, set apart from its words (after a tab or dot leaders, at least two
    spaces' width), or None. A word that happens to be made of i, v, x, l and c ("civil") is a numeral only when it
    reads as one."""
    if len(ws) < 2:
        return None
    last, before = ws[-1], ws[-2]
    t = last[4].strip()
    value = int(t) if t.isdigit() and len(t) <= 4 else roman_value(t)
    if value is None:
        return None
    apart = last[0] - before[2] >= 2 * max(2.0, 0.25 * (last[3] - last[1])) or before[4].rstrip().endswith("..")
    return value if apart else None


def _contents_page(page: fitz.Page) -> bool:
    """Whether a page is (part of) a table of contents: it says so ("Table of Contents", "List of Figures"), or most of
    its lines are entries, words ending in a page number at one right edge and mostly led by a section's number (the
    contents' later pages, which have no title). Read as ordinary text its entries were joined into paragraphs of
    several entries each, and its chapter entries were taken for headings (#665)."""
    # Lines by height, not by the producer's lines: an entry's page number is often set apart after a tab
    rows: list[list] = []
    for w in sorted(page.get_text("words"), key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        if rows and abs((w[1] + w[3]) / 2 - (rows[-1][0][1] + rows[-1][0][3]) / 2) <= 3:
            rows[-1].append(w)
        else:
            rows.append([w])
    texts = [(" ".join(w[4] for w in ws), ws) for ws in (sorted(r, key=lambda w: w[0]) for r in rows)]
    worded = [(t, ws) for t, ws in texts if sum(ch.isalpha() for ch in t) >= 3]
    numbered = [(t, ws, ref) for t, ws in worded if (ref := _page_ref(ws)) is not None]
    # Its title at the top, over entries (a slide deck's "Table of Contents" link on every slide is no contents)
    if len(numbered) >= 3 and any(_CONTENTS_TITLE.match(t) and ws[0][3] < page.rect.height / 3 for t, ws in texts):
        return True
    if len(worded) < 8 or len(numbered) < 0.6 * len(worded):
        return False
    rights = Counter(round(ws[-1][2]) for _, ws, _ in numbered)
    edge, _ = rights.most_common(1)[0]
    at_edge = sum(1 for _, ws, _ in numbered if abs(ws[-1][2] - edge) <= 3)
    led = sum(1 for t, _, _ in numbered if _ENTRY_START.match(t))
    # The page numbers go up the page down (or stay: two sections on one page)
    refs = [ref for _, _, ref in numbered]
    rising = sum(1 for a, b in zip(refs, refs[1:]) if b >= a)
    return (at_edge >= 0.6 * len(numbered) and led >= 0.3 * len(numbered)
            and rising >= 0.9 * (len(refs) - 1))


def _contents_entries(blocks: list[TextBlock]) -> list[TextBlock]:
    """A contents page's lines (one block each) as its entries: a line ending in a page number closes an entry, a
    line led by a section's number starts one, and a line that does neither is the entry above it wrapped. Entries
    are TOCI; the page's title, in larger print, and its running header and page number stay as they are."""
    sizes = sorted(b.font_size for b in blocks if _PAGE_REF.search(b.text))
    size = sizes[len(sizes) // 2] if sizes else 0.0
    out: list[TextBlock] = []
    entry: TextBlock | None = None
    closed = True
    for b in blocks:
        worded = sum(ch.isalpha() for ch in b.text) >= 3
        if not size or not worded or b.font_size > 1.15 * size or _CONTENTS_TITLE.match(b.text):
            out.append(b)
            entry = None
            continue
        ends = bool(_PAGE_REF.search(b.text))
        starts = bool(_ENTRY_START.match(b.text))
        if entry is not None and not starts and (not closed or not ends):
            append_block(entry, b)
            closed = closed or ends
            continue
        b.role = "TOCI"
        out.append(b)
        entry, closed = b, ends
    return out


def _extract_blocks(page: fitz.Page, page_no: int, ocr: bool = False) -> list[TextBlock]:
    """Text blocks built from visual lines (runs sorted by position, scripts attached to their line),
    split wherever font size / weight changes between lines (separates headings), at every list bullet
    and between list items sharing a line, then re-joined into paragraphs where a producer emitted one
    block per line. Text that can't be seen (white on a blank page) becomes an artifact.

    ocr: the page has the engine's OCR layer, whose invisible text has no scripts (see _visual_lines). Invisible
    text elsewhere (a flattened page's, glyph by glyph at the original sizes) may have."""
    data = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP)
    if page.rotation:
        _text_to_visual(data, page.rotation_matrix)
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
                prev: _Run | None = None
                for ch in span.get("chars", []):
                    seq += 1
                    r = _Run(span, ch, seq)
                    if (prev is not None and r.text != ch["c"] and r.x1 - r.x0 < 0.05 * r.size
                            and abs(r.x0 - prev.x1) < 0.5 and abs(r.base - prev.base) < 0.5):
                        # One glyph the font's map gives as two: the letter typed, then the symbol it stands for in
                        # Word ("à" then U+F0E0, a Wingdings arrow), at no width. The symbol, in the letter's place
                        prev.text = r.text
                        continue
                    prev = r
                    r.ocr = ocr and span.get("alpha", 255) == 0  # (render mode 3 is reported as alpha 0)
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
    contents = _contents_page(page)
    # Producers split one visual line (or a fraction and the line it sits in) across blocks: join them,
    # and read each fraction as a unit.
    for runs, rotated in _merge_units(page, units):
        # Each visual line, split where several list items share it
        pieces: list[list[_Run]] = []
        if any(r.text.strip() for r in runs):
            for vl in _visual_lines(runs):
                for item in _split_inline_items(vl):
                    for part in _apart_from_chapter_number(item):
                        pieces += _apart_from_typed(part) if ocr else [part]
        pieces += [ln for ln in rotated if any(r.text.strip() for r in ln)]
        if not pieces:
            continue
        groups: list[list[list[_Run]]] = []
        for piece in pieces:
            if groups:
                first = _join(groups[-1][0], spoken=False)
                if len(groups[-1]) == 1 and BULLET_ONLY.match(first):
                    groups[-1].append(piece)  # bullet glyph on its own line: text follows
                    continue
                rule = "contents line" if contents else _starts_new_block(groups[-1], piece, page.rect.width)
                if rule is None:
                    groups[-1].append(piece)
                    continue
                log.debug("page %d: new block (%s)", page_no + 1, rule)
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
                    fraction=any(isinstance(r, _Frac) for r in flat),
                    baseline=first[len(first) // 2],
                    marks=marks,
                    mark_after=mark_after,
                    mark_boxes=mark_boxes,
                    lead_mark=lead,
                    typed_on_scan=ocr and any(r.text.strip() and not r.ocr for r in flat),
                )
            )
    blocks = _contents_entries(blocks) if contents else _merge_paragraph_lines(_attach_bullets(blocks))
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


def _starts_new_block(group: list[list[_Run]], piece: list[_Run], page_width: float) -> str | None:
    """Why a visual line starts a new block instead of continuing group (the block's lines so far): the name of the
    first rule that holds, or None when it continues the block."""
    text = _join(piece, spoken=False)
    size, bold = _style(piece)
    psize, pbold = _style(group[-1])
    rules = (
        ("list marker", lambda: (BULLET_START.match(text) and not _wraps_into_part(group, text))
         or BULLET_ONLY.match(text)),
        # Notes set one after another: a line that starts with its own raised number ("³Da dun") under a note that
        # did too is the next note (#598)
        ("next note", lambda: _leads_with_mark(piece) and _leads_with_mark(group[0])),
        # A bold centred line (a title) over a bold line at the margin ("Part 1: ..."): two headings, not one (#604)
        ("centred title over margin line",
         lambda: bold and pbold and len(group) == 1 and _centred_over(group[0], piece, page_width, size)),
        ("same row", lambda: abs(piece[0].base - group[-1][0].base) < 0.3 * max(size, 1.0)),
        ("style change", lambda: abs(psize - size) > _STYLE_SIZE_TOL or pbold != bold),
    )
    return next((name for name, holds in rules if holds()), None)


def _continues_paragraph(prev: TextBlock, b: TextBlock) -> bool:
    """Whether block b, the next one down, is the next line of prev's paragraph: the same style, the same left
    edge, a line's gap below it, and neither a list item nor the next of a run of notes."""
    size = max(prev.font_size, b.font_size, 1.0)
    gap = b.bbox[1] - prev.bbox[3]
    same_style = prev.bold == b.bold and abs(prev.font_size - b.font_size) <= max(_STYLE_SIZE_TOL, 0.12 * size)
    aligned = abs(b.bbox[0] - prev.bbox[0]) <= 6 or _indented_on(prev, b, size)
    return (same_style and aligned and _LINE_GAP[0] * size <= gap <= _LINE_GAP[1] * size
            and not BULLET_START.match(b.text) and not BULLET_ONLY.match(b.text)
            and not (b.lead_mark and prev.lead_mark))  # two notes, each starting with its raised number


_ENDS_SENTENCE = (".", ":", "?", "!", ";")


def _indented_on(prev: TextBlock, b: TextBlock, size: float) -> bool:
    """Whether b goes on with prev's paragraph though its left edge is elsewhere: prev is one line that runs on to
    where the paragraph's lines end, and b is the rest, set further in (a caption's or a heading's hanging indent,
    "Figure 1.1 Reaction energy profile for an" over "uncatalysed reaction", the lower case saying it goes on) or
    further out (a paragraph's first line indented, the sentence running on). Read apart, a caption's first line
    was the caption and the rest a paragraph, and a heading's last words a heading of their own (#665)."""
    one_line = prev.bbox[3] - prev.bbox[1] <= 1.6 * size
    runs_on = prev.bbox[2] >= b.bbox[2] - 1.5 * size and not prev.text.rstrip().endswith(_ENDS_SENTENCE)
    dx = b.bbox[0] - prev.bbox[0]
    if not (one_line and runs_on) or abs(dx) > max(4 * size, 0.2 * (prev.bbox[2] - prev.bbox[0])):
        return False
    first = b.text.lstrip()[:1]
    return dx < 0 or first.islower() or prev.text.rstrip().endswith(("-", "‐", "–"))


def _merge_paragraph_lines(blocks: list[TextBlock]) -> list[TextBlock]:
    """Join consecutive single-style, left-aligned, tightly spaced blocks (one-line-per-block
    producers and OCR) into paragraphs. List items and style changes always start a new block."""
    out: list[TextBlock] = []
    for b in blocks:
        if out and _continues_paragraph(out[-1], b):
            append_block(out[-1], b)
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
    tables += _ruled_tables(page, page_no, len(found.tables), [t.bbox for t in tables])
    return tables


# A table's caption, which sits between two tables and not inside one
_CAPTION_START = re.compile(r"^\s*(?:table|figure|fig\.|scheme|chart)\s+[A-Z]?\d", re.I)


def _rule_levels(page: fitz.Page) -> list[tuple[float, float, float, list[float]]]:
    """The horizontal rules drawn across the page, by height: each a row of segments drawn edge to edge (Word draws
    a cell's border for each cell, so a rule is broken where its columns meet): (height, left, right, the breaks)."""
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - odd drawings: no ruled table is found, as before
        return []
    found: list[tuple[float, float, float]] = []
    for d in drawings:
        colour = d.get("fill") if d.get("type") == "f" else d.get("color")
        if colour is not None and min(colour) > 0.9:
            continue  # a white line is no rule
        for it in d.get("items") or []:
            if it[0] == "re" and it[1].height <= 2 and it[1].width >= 5:
                found.append(((it[1].y0 + it[1].y1) / 2, it[1].x0, it[1].x1))
            elif it[0] == "l" and abs(it[1].y - it[2].y) < 1 and abs(it[1].x - it[2].x) >= 5:
                found.append((it[1].y, min(it[1].x, it[2].x), max(it[1].x, it[2].x)))
    levels: list[tuple[float, list[tuple[float, float]]]] = []
    for y, x0, x1 in sorted(found):
        if levels and y - levels[-1][0] <= 1.5:
            levels[-1][1].append((x0, x1))
        else:
            levels.append((y, [(x0, x1)]))
    out = []
    for y, segs in levels:
        segs.sort()
        joined: list[list[float]] = []
        for x0, x1 in segs:
            if joined and x0 - joined[-1][1] <= 2.5:
                joined[-1][1] = max(joined[-1][1], x1)
                if x0 > joined[-1][2][-1] + 2:  # where one cell's border ends and the next one's starts
                    joined[-1][2].append(x0)
            elif not joined or x1 > joined[-1][1]:
                joined.append([x0, x1, [x0]])
        # One unbroken rule per height: two side by side are two things (a form's lines to write on)
        if len(joined) == 1 and joined[0][1] - joined[0][0] >= 60:
            x0, x1, starts = joined[0]
            out.append((y, x0, x1, starts[1:]))
    return out


def _ruled_tables(page: fitz.Page, page_no: int, start: int, avoid: list[BBox]) -> list[TableRegion]:
    """Tables drawn with horizontal rules only: one over the header, one under it and one under the last row, with
    no lines between the columns ("three-line" tables, as Word's and the journals' styles draw them). The finder
    needs lines on every side and finds none of them, and their header row read as a run of headings (#665).

    The rules must be stacked one under another, the same width, with nothing but the table between them: a caption
    or a line of prose between two rules ends a table. The columns are where the rules break (Word draws each cell's
    border), else the gaps the words leave."""
    levels = _rule_levels(page)
    if len(levels) < 3:
        return []
    words = [w for w in page.get_text("words") if w[4].strip()]
    lines: dict[tuple, list] = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)

    def between(y0: float, y1: float, x0: float, x1: float) -> list[list]:
        return [ws for ws in lines.values() if y0 < (ws[0][1] + ws[0][3]) / 2 < y1
                and x0 - 2 <= min(w[0] for w in ws) and max(w[2] for w in ws) <= x1 + 2]

    def breaks_table(band: list[list], x0: float, x1: float, edges: list[float]) -> bool:
        for ws in band:
            text = " ".join(w[4] for w in ws)
            crosses = sum(1 for e in edges if min(w[0] for w in ws) < e - 2 and max(w[2] for w in ws) > e + 2)
            if _CAPTION_START.match(text) or (len(ws) >= 8 and crosses and max(w[2] for w in ws) - min(w[0] for w in ws)
                                              >= 0.6 * (x1 - x0)):
                return True
        return False

    stacks: list[list] = []
    for y, left, right, breaks in levels:
        span = (left, right)
        last = stacks[-1][-1] if stacks else None
        if (last is not None and abs(last[1][0] - span[0]) <= 3 and abs(last[1][1] - span[1]) <= 3
                and bool(last[2]) == bool(breaks)  # a table's rules are all broken by its columns, or none are
                and not breaks_table(between(last[0], y, *span), *span, stacks[-1][0][2] or breaks)):
            stacks[-1].append((y, span, breaks))
        else:
            stacks.append([(y, span, breaks)])
    tables: list[TableRegion] = []
    for stack in stacks:
        if len(stack) < 3:
            continue
        top, x0, x1, bottom = stack[0][0], stack[0][1][0], stack[0][1][1], stack[-1][0]
        bbox = (x0, top, x1, bottom)
        if any(bbox_intersection(bbox, a) > 0.2 * (bbox_area(bbox) or 1.0) for a in avoid):
            continue
        region = _ruled_table(f"p{page_no}t{start + len(tables)}", page_no, bbox, [y for y, _, _ in stack],
                              stack[0][2], between(top, bottom, x0, x1))
        if region is not None:
            tables.append(region)
    return tables


def _ruled_table(tid: str, page_no: int, bbox: BBox, rules: list[float], breaks: list[float],
                 lines: list[list]) -> TableRegion | None:
    """One ruled table's grid: the rows between the first two rules are its header, one row; below them each line
    of text is a row, unless it only goes on with the cells above it (a cell's text wrapped onto a second line)."""
    x0, _, x1, _ = bbox
    edges = [x0] + breaks + [x1]
    if len(edges) == 2:
        # No breaks in the rules: the columns are the gaps that run down the whole table between the words
        covered = sorted((w[0], w[2]) for ws in lines for w in ws)
        gaps, reach = [], x0
        for a, b in covered:
            if a - reach >= 8:
                gaps.append((reach + a) / 2)
            reach = max(reach, b)
        edges = [x0] + gaps + [x1]
    n_cols = len(edges) - 1
    if n_cols < 2:
        return None

    def column(w) -> int:
        cx = (w[0] + w[2]) / 2
        return next((i for i in range(n_cols) if cx < edges[i + 1]), n_cols - 1)

    rows: list[dict[int, list]] = []
    row_bands: list[int] = []
    rows_y: list[float] = []
    for ws in sorted(lines, key=lambda ws: ((ws[0][1] + ws[0][3]) / 2, ws[0][0])):
        cy = (ws[0][1] + ws[0][3]) / 2
        band = next(i for i in range(len(rules) - 1) if cy < rules[i + 1])
        cells: dict[int, list] = {}
        for w in ws:
            cells.setdefault(column(w), []).append(w)
        same_line = rows and row_bands[-1] == band and abs(rows_y[-1] - cy) < 3
        wrapped = (rows and row_bands[-1] == band
                   and (band == 0 or (0 not in cells and 0 in rows[-1] and len(cells) <= max(1, n_cols // 2))))
        if same_line or wrapped:
            for c, cw in cells.items():
                rows[-1].setdefault(c, []).extend(cw)
            rows_y[-1] = cy
            continue
        rows.append(cells)
        row_bands.append(band)
        rows_y.append(cy)
    if len(rows) < 2 or row_bands[0] != 0 or len(rows[0]) < 2:
        return None
    # A header says what its columns hold, in words ("1 2 3 4 5 6" over the same is a diagram's labels)
    if not any(sum(ch.isalpha() for w in ws for ch in w[4]) >= 3 for ws in rows[0].values()):
        return None
    # Many rules with one line between each two: a column of worked fractions (a calculation set out with its
    # bars), not a table's rows, which a three-line table holds several of between two rules
    if len(rules) >= 5 and len(set(row_bands)) == len(rows):
        return None
    # Each cell is its column's width and its row's height, the rows meeting halfway between their words, so every
    # piece of text in the table falls in a cell
    tops = [min(w[1] for ws in row.values() for w in ws) for row in rows]
    bottoms = [max(w[3] for ws in row.values() for w in ws) for row in rows]
    cuts = [rules[0]] + [(bottoms[r] + tops[r + 1]) / 2 for r in range(len(rows) - 1)] + [rules[-1]]
    cells_out: list[TableCell] = []
    for r, row in enumerate(rows):
        for c in range(n_cols):
            ws = sorted(row.get(c, []), key=lambda w: (round((w[1] + w[3]) / 2), w[0]))
            cells_out.append(TableCell(row=r, col=c, bbox=(edges[c], cuts[r], edges[c + 1], cuts[r + 1]),
                                       text=_clean(" ".join(w[4] for w in ws)), is_header=r == 0))
    region = TableRegion(id=tid, page=page_no, bbox=bbox, n_rows=len(rows), n_cols=n_cols, cells=cells_out,
                         header_rows=1, irregular=any(not c.text for c in cells_out if c.row == 0))
    region.header_evidence = True  # a rule under it sets the header row apart
    return region


def covers_page(bbox, width: float, height: float) -> bool:
    """Whether an image is a scanned page's picture of the page: what's visible of it covers most of the page. A scan
    can sit inside margins, be drawn in several layers, or be larger than the page and cut off at its edge (a web
    page "printed" to PDF), so its own width and height don't say."""
    w = min(bbox[2], width) - max(bbox[0], 0.0)
    h = min(bbox[3], height) - max(bbox[1], 0.0)
    return w > 0 and h > 0 and w * h >= 0.6 * width * height


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
        # Nothing but rules, with words among them: a table's borders (Word draws them as hairline filled boxes), its
        # text too sparse for the test above (an answer key, choices beside a picture), not a drawing
        inside = [r for r in all_drawings if r.intersects(fitz.Rect(bbox))]
        if word_text > 0 and inside and all(min(r.width, r.height) <= 1.5 for r in inside):
            continue
        out.append(ImageRegion(id=f"p{page_no}i{start + len(out)}", page=page_no, bbox=bbox, xref=0, kind="vector"))
    return out


_LABEL_PAD = 16.0  # points: how far outside a drawing its labels sit (an axis's name, its values)


def _axis_label(b: TextBlock, body: float) -> bool:
    """A graph's label: a name or a value (plots._is_label), or a row of values ("t (s) 2 4 6 8 10 12 14 16"). Never
    a list item, a sentence or an equation ending one, a heading (bold or larger than the page's text) or an artifact."""
    words = b.text.split()
    if (not words or b.role != "P" or b.bold or b.font_size > 1.15 * body or BULLET_START.match(b.text)
            or b.text.rstrip()[-1] in ".:;"):
        return False
    return plots._is_label(b) or (len(words) <= 12 and all(len(w) <= 4 for w in words))


def _page_body_size(pa: PageAnalysis) -> float:
    """The size most of the page's paragraph text is set in (by characters), or 0."""
    sizes = sorted(b.font_size for b in pa.blocks for _ in range(min(len(b.text), 400)) if b.role == "P" and b.font_size)
    return sizes[len(sizes) // 2] if sizes else 0.0


def _take_in_labels(pa: PageAnalysis, v: ImageRegion) -> None:
    """The labels just outside a drawn graph (its axes' names and values) are part of it, read through its alt text,
    not as paragraphs beside it: the figure's box grows to take them in."""
    body = _page_body_size(pa)
    for _ in range(3):
        grown = (v.bbox[0] - _LABEL_PAD, v.bbox[1] - _LABEL_PAD, v.bbox[2] + _LABEL_PAD, v.bbox[3] + _LABEL_PAD)
        near = [b for b in pa.blocks if _axis_label(b, body)
                and 0.08 * pa.height < b.bbox[1] and b.bbox[3] < 0.92 * pa.height  # (not a running header or footer)
                and bbox_intersection(b.bbox, grown) >= 0.5 * (bbox_area(b.bbox) or 1.0)]
        if not near:
            return
        v.bbox = bbox_union([v.bbox] + [b.bbox for b in near])
        pa.blocks = [b for b in pa.blocks if b not in near]


def _labels_on_pictures(pa: PageAnalysis, pictures: list[ImageRegion] | None = None, joined: bool = False) -> None:
    """Short labels set as text on top of a picture (a diagram's "voltmeter", "wire", "salt bridge"; a row of marks set
    in type) are part of it, read through its alt text, as a drawn chart's are: read as paragraphs after it they said
    nothing. Only a picture smaller than a third of the page, with two or more labels and nothing else on it: a
    page-sized background, a banner with its title, or text read off a scan by OCR stays text.

    joined: pictures made of pieces drawn over one another (a graph placed in strips), which one label is enough for,
    and a label set sideways in larger print too (an axis's name, never a title): left out, it stood between the graph
    and its caption."""
    body = _page_body_size(pa)
    least = 1 if joined else 2
    for img in pa.images if pictures is None else pictures:
        if img.kind != "image" or img.decorative or bbox_area(img.bbox) > pa.width * pa.height / 3:
            continue
        on = [b for b in pa.blocks if b.role != "Artifact"
              and bbox_intersection(b.bbox, img.bbox) >= 0.9 * (bbox_area(b.bbox) or 1.0)]
        if len(on) < least or any(sum(1 for w in b.text.split() if any(c.isalnum() for c in w)) > 6
                                  or BULLET_START.match(b.text) or b.ocr_conf is not None or b.typed_on_scan
                                  or (body and b.font_size > 1.15 * body and not (joined and _sideways(b)))
                                  for b in on):
            continue
        on.sort(key=lambda b: (b.bbox[1], b.bbox[0]))
        img.labels = " ".join(b.actual_text or b.text for b in on)
        pa.blocks = [b for b in pa.blocks if b not in on]


def _sideways(b: TextBlock) -> bool:
    """A label set on its side: a few characters in a box much taller than wide."""
    w, h = b.bbox[2] - b.bbox[0], b.bbox[3] - b.bbox[1]
    return len(b.text.strip()) <= 12 and h > 2 * w


def line_shaped(bbox: BBox) -> bool:
    """A picture shaped like a line or a word of print: a few lines tall at most, and wider than it is tall."""
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    return 0 < h <= 40 and w >= 2.5 * h


def _join_stacked_pictures(pa: PageAnalysis) -> None:
    """Pictures drawn on top of one another are one picture: a logo built of layers (its ring, its emblem, the ribbon
    under it), a word placed on a diagram as a picture of its own, two photos of one apparatus overlapping. Each was a
    Figure, so the logo was described six times over. They become one figure holding them all, when either covers
    half of the smaller; so do the pictures inside a chart drawn with lines (its curve placed as a picture between
    drawn axes). A page-sized picture is the page's background, not part of what's drawn on it."""
    pics = [i for i in pa.images if i.xref and i.kind == "image" and not i.decorative
            and not covers_page(i.bbox, pa.width, pa.height)]
    group = list(range(len(pics)))

    def root(n: int) -> int:
        while group[n] != n:
            group[n] = group[group[n]]
            n = group[n]
        return n

    for a in range(len(pics)):
        for b in range(a + 1, len(pics)):
            small = min(bbox_area(pics[a].bbox), bbox_area(pics[b].bbox)) or 1.0
            if bbox_intersection(pics[a].bbox, pics[b].bbox) >= 0.5 * small:
                group[root(b)] = root(a)
    members: dict[int, list[ImageRegion]] = {}
    for n, img in enumerate(pics):
        members.setdefault(root(n), []).append(img)
    gone: list[ImageRegion] = []
    made: list[ImageRegion] = []
    for pieces in members.values():
        if len(pieces) < 2:
            continue
        gone += pieces
        labels = " ".join(p.labels for p in pieces if p.labels)
        made.append(ImageRegion(id=f"p{pa.number}j{len(made)}", page=pa.number, bbox=bbox_union([p.bbox for p in pieces]),
                                xref=0, holds_pieces=True, labels=labels or None, drawing="layers"))
    pa.images = [i for i in pa.images if not any(i is g for g in gone)] + made
    # Their labels, but not a badge's number (a circle and its shadow): the number says what the badge means
    _labels_on_pictures(pa, [m for m in made if max(m.bbox[2] - m.bbox[0], m.bbox[3] - m.bbox[1]) > 0.12 * pa.width],
                        joined=True)
    # A picture inside one of these, or inside a chart drawn with lines, is a piece of it too (a strip of a graph
    # placed between two others it doesn't overlap; a smaller group of layers in a diagram's box), largest first
    holders = [v for v in pa.images if (v in made or (v.kind == "vector" and not v.holds_pieces)) and not v.decorative]
    for v in sorted(holders, key=lambda v: -bbox_area(v.bbox)):
        if not any(v is i for i in pa.images):
            continue  # (taken into a larger one)
        # (slivers and specks too, as a graph drawn dot by dot takes them: left on their own they stood between the
        # figure and its caption)
        inside = [i for i in pa.images if i.kind == "image" and i is not v
                  and bbox_intersection(i.bbox, v.bbox) >= 0.8 * (bbox_area(i.bbox) or 1.0)]
        if v in made or any(not i.decorative for i in inside):
            v.holds_pieces = True
            v.labels = " ".join(x for x in [v.labels] + [i.labels for i in inside] if x) or None
            pa.images = [i for i in pa.images if not any(i is g for g in inside)]


def _text_as_pictures(page: fitz.Page, pa: PageAnalysis) -> bool:
    """A page with no text whose print was placed as pictures, a line or a word each (an online converter's cover
    page): not a scan, but only OCR can read it. Two or more line-shaped pictures say so; a photo or a drawing alone
    doesn't. Upright pages only (its pictures and the OCR lines are compared where they lie)."""
    if page.rotation or page.get_text("text").strip():
        return False
    return sum(1 for i in pa.images if i.xref and not i.decorative and line_shaped(i.bbox)) >= 2


def _background_picture(page: fitz.Page, pa: PageAnalysis) -> None:
    """A picture covering the page with the page's text printed on it (a certificate's border, a letterhead) is the
    page's background: an artifact. As a Figure it came first on the page, described as whatever stood out in it.
    Only when it is blank where the page's print is: a scanned score with its title typed on it is the page's content."""
    text = [b for b in pa.blocks if b.role != "Artifact" and b.text.strip()]
    if len(text) < 3:
        return
    for img in pa.images:
        if not img.xref or img.decorative or not covers_page(img.bbox, pa.width, pa.height):
            continue
        on = sum(1 for b in text if bbox_intersection(b.bbox, img.bbox) >= 0.9 * (bbox_area(b.bbox) or 1.0))
        if on >= 0.8 * len(text) and _blank_inside(page, pa, img):
            img.decorative = True
            img.alt = None


def _blank_inside(page: fitz.Page, pa: PageAnalysis, img: ImageRegion) -> bool:
    """Whether a page-sized picture holds next to no ink away from its edges (a border, a tint), the page's text and
    the other pictures on it left out."""
    x0, y0, x1, y1 = img.bbox
    dx, dy = 0.12 * (x1 - x0), 0.12 * (y1 - y0)
    inner = fitz.Rect(x0 + dx, y0 + dy, x1 - dx, y1 - dy) & page.rect
    if inner.is_empty or page.rotation:
        return False
    pix = page.get_pixmap(dpi=24, clip=inner, colorspace=fitz.csGRAY, alpha=False, annots=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width].copy()
    scale = pix.width / inner.width
    for box in [b.bbox for b in pa.blocks] + [i.bbox for i in pa.images if i is not img]:
        c0, r0 = int((box[0] - inner.x0) * scale) - 1, int((box[1] - inner.y0) * scale) - 1
        c1, r1 = int((box[2] - inner.x0) * scale) + 2, int((box[3] - inner.y0) * scale) + 2
        arr[max(r0, 0):max(r1, 0), max(c0, 0):max(c1, 0)] = 255
    paper = float(np.median(arr))
    return float((arr < paper - 40).mean()) < 0.02


# A displayed equation's number at the right of its line: "Equation 1.1", "Eq. 3", "(2.4)"
_EQUATION_LABEL = re.compile(r"^\s*(?:\(?\s*(?:equation|eqn?\.?)\s*\d+(?:[.\-]\d+)*[a-z]?\s*\)?"
                             r"|\(\d+(?:\.\d+)*[a-z]?\))\s*$", re.I)


def _equation_lines(pa: PageAnalysis) -> None:
    """A displayed equation set as pieces on one line, numbered at the right ("Equation 1.1"): a fraction's top and
    bottom, an exponent and a few symbols as text, and the parts the producer drew as small pictures. Read one by one
    they say nothing (one became a heading for its size, each picture got its own description), so the line's pieces
    become one Formula picture holding them, read as a whole; the number stays a paragraph beside it (#665)."""
    for label in [b for b in pa.blocks if _EQUATION_LABEL.match(b.text)]:
        lx0, ly0, _, ly1 = label.bbox
        band = (ly0, ly1)
        taken_blocks: list[TextBlock] = []
        taken_images: list[ImageRegion] = []
        for _ in range(4):  # a fraction's top and bottom reach above and below the number's line
            grew = False
            for b in pa.blocks:
                if b is label or b in taken_blocks or b.role == "Artifact" or b.bbox[2] > lx0 + 1:
                    continue
                if b.bbox[1] < band[1] and b.bbox[3] > band[0]:
                    taken_blocks.append(b)
                    grew = True
            for img in pa.images:
                if img in taken_images or img.decorative or img.bbox[2] > lx0 + 1:
                    continue
                if img.bbox[1] < band[1] and img.bbox[3] > band[0]:
                    taken_images.append(img)
                    grew = True
            pieces = [b.bbox for b in taken_blocks] + [i.bbox for i in taken_images]
            if pieces:
                band = (min(band[0], min(p[1] for p in pieces)), max(band[1], max(p[3] for p in pieces)))
            if not grew:
                break
        # Only pieces: a sentence on the line (prose wrapping beside a number in brackets) is not an equation
        if (len(taken_blocks) + len(taken_images) < 2 or not taken_images
                or any(len(b.text.split()) > 6 or b.text.rstrip().endswith((".", ":")) for b in taken_blocks)
                or band[1] - band[0] > 6 * max(label.font_size, 6.0)
                or any(i.holds_pieces or i.kind == "music" for i in taken_images)):
            continue
        box = bbox_union([b.bbox for b in taken_blocks] + [i.bbox for i in taken_images])
        taken_images.sort(key=lambda i: i.bbox[0])
        holder = ImageRegion(id=taken_images[0].id, page=pa.number, bbox=box, xref=0, kind="formula",
                             holds_pieces=True, drawing="equation",
                             labels=" ".join(b.text for b in sorted(taken_blocks, key=lambda b: b.bbox[0])) or None)
        pa.images = [i for i in pa.images if i not in taken_images] + [holder]
        pa.blocks = [b for b in pa.blocks if b not in taken_blocks]


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
    """Images and tables into visual page space (text blocks are built in it: _text_to_visual)."""
    v = lambda b: _rect(fitz.Rect(b) * rot)  # noqa: E731
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


def analyze_page(doc: fitz.Document, page_no: int, ocr: bool = False) -> tuple[PageAnalysis, list[dict]]:
    with _PAGE_LOCK:
        return _analyze_page(doc, page_no, ocr)


def _analyze_page(doc: fitz.Document, page_no: int, ocr: bool = False) -> tuple[PageAnalysis, list[dict]]:
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

    pa.blocks = _extract_blocks(page, page_no, ocr)
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
    # (before the pictures are joined: a line's picture inside a drawn table's grid is taken into it)
    pa.text_pictures = _text_as_pictures(page, pa)
    # (after the schemes, which find their own labels)
    for v in vectors:
        if v in pa.images:
            _take_in_labels(pa, v)
    _labels_on_pictures(pa)
    _equation_lines(pa)
    # (after a displayed equation's line takes its pictures: one of its pieces drawn over another is part of it)
    _join_stacked_pictures(pa)

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
    # form is only in the picture and needs reading. A page with no text at all is read when a picture covers most of
    # it, inside margins too: a screenshot of a web page placed on a page fills 78% of it
    if page.get_text("text").strip():
        pa.is_scanned = bool(big) and any(_looks_scanned(doc, i) for i in big) and _sparse_text(page)
        if not pa.is_scanned:
            _background_picture(page, pa)
    else:
        # (a picture: drawings found above, made of lines and curves, are no scan). A page whose lines of print are
        # pictures of their own is read too
        pa.is_scanned = pa.text_pictures or any(i.xref and covers_page(i.bbox, pa.width, pa.height) for i in pa.images)

    issues = _font_issues(doc, page, page_no)
    pa.font_issues = sorted({p for i in issues for p in i["problems"]})
    return pa, issues


def analyze_pages(path: Path | str, numbers: list[int], ocr_pages: frozenset = frozenset()
                  ) -> dict[int, tuple[PageAnalysis, list[dict]]]:
    """The pages numbered, each analyzed as analyze_document does (one page_pool worker's share, #124)."""
    with fitz.open(path) as doc:
        return {n: analyze_page(doc, n, n in ocr_pages) for n in numbers}


def analyze_document(path: Path | str, ocr_pages=()) -> DocumentAnalysis:
    """ocr_pages: the pages given the engine's OCR layer."""
    from app.pipeline import page_pool

    doc = fitz.open(path)
    try:
        pages, font_issues = [], []
        workers = page_pool.workers_for(doc.page_count)
        ocr_pages = frozenset(ocr_pages)
        done = page_pool.run("app.pipeline.analyzer:analyze_pages", path, doc.page_count, workers,
                             arg=ocr_pages or None) if workers > 1 else None
        for n in range(doc.page_count):
            pa, issues = done[n] if done is not None else analyze_page(doc, n, n in ocr_pages)
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
