"""Adds an invisible OCR text layer to scanned pages (render mode 3: visually unchanged)."""
from __future__ import annotations

import csv
import io
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

from app.config import get_settings
from app.pipeline.analyzer import capped_dpi
from app.pipeline.fonts import fix_inserted_cid_fonts, text_layer_font

log = logging.getLogger(__name__)

# Tesseract reads list bullets as a lone letter or symbol.
_BULLET_MISREADS = {"e", "o", "©", "°", "«", "»", "*", "•", "+", "¢", "@"}


def tesseract_available() -> bool:
    try:
        return bool(fitz.get_tessdata())
    except Exception:  # noqa: BLE001 - PyMuPDF raises if tessdata cannot be located
        return False


def write_invisible_line(page: fitz.Page, font: fitz.Font, text: str, origin, size: float,
                         direction=(1.0, 0.0), extent: float | None = None, width: float | None = None,
                         color=(0, 0, 0)) -> None:
    """Write one line of render-mode-3 text at `origin` along `direction` (unrotated page space).
    If `extent` is given, the font size is reduced so the line does not overrun it. If `width` is
    given, the text is stretched or squeezed to exactly that width, so each piece covers the same
    stretch of the page as the text it stands for (the text-layer font's letters are wider or
    narrower than the original's, and pieces would otherwise overlap their neighbors)."""
    natural = font.text_length(text, fontsize=1) or 1.0
    if extent:
        size = min(size, extent / natural)
    size = max(1.0, size)
    cos, sin = direction
    tw = fitz.TextWriter(page.rect)
    o = fitz.Point(origin)
    tw.append(o, text, font=font, fontsize=size)
    m = fitz.Matrix(1, 1)
    if width and width > 0:
        m = fitz.Matrix(min(4.0, max(0.25, width / (natural * size))), 1)
    if abs(cos - 1) >= 1e-3:
        m = m * fitz.Matrix(cos, -sin, sin, cos, 0, 0)
    morph = None if m == fitz.Matrix(1, 1) else (o, m)
    tw.write_text(page, render_mode=3, morph=morph, color=color)


@dataclass
class OcrWord:
    text: str
    conf: float  # Tesseract's confidence, 0-100
    bbox: tuple  # unrotated page space


@dataclass
class OcrLine:
    page: int
    text: str
    bbox: tuple
    origin: tuple
    size: float
    direction: tuple
    extent: float
    words: list = field(default_factory=list)  # OcrWord, in reading order; empty if confidences are unknown
    source: str = "tesseract"  # tesseract | ai-corrected | ai-transcribed

    @property
    def confidence(self) -> float | None:
        return min((w.conf for w in self.words), default=None)

    @property
    def mean_confidence(self) -> float | None:
        return sum(w.conf for w in self.words) / len(self.words) if self.words else None


def tesseract_cli() -> str | None:
    """The Tesseract command line, used for per-word confidence (PyMuPDF's built-in OCR doesn't report it)."""
    return shutil.which("tesseract")


def _tesseract_tsv(page: fitz.Page, language: str, dpi: int = 300) -> list[dict]:
    """Tesseract's word rows (TSV) for the page rendered upright, with boxes mapped back to unrotated page
    space (the space OCR lines are written in)."""
    exe = tesseract_cli()
    if exe is None:
        return []
    dpi = capped_dpi(page, dpi)  # a page of absurd size is read at a lower resolution, not at gigabytes
    # Without annotations: a box drawn round a line of the scan (markup) hid that line from Tesseract
    pix = page.get_pixmap(dpi=dpi, alpha=False, annots=False)
    scale = 72.0 / dpi
    back = ~page.rotation_matrix  # rendered (rotated) space -> unrotated page space
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "page.png"
        pix.save(img)
        try:
            out = subprocess.run([exe, str(img), "stdout", "-l", language, "--dpi", str(dpi), "tsv"],
                                 capture_output=True, text=True, timeout=300, check=True).stdout
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("tesseract confidence pass failed on page %s: %s", page.number, exc)
            return []
    rows = []
    for row in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf") or -1)
        except ValueError:
            continue
        if row.get("level") != "5" or not text or conf < 0:
            continue
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        r = fitz.Rect(x * scale, y * scale, (x + w) * scale, (y + h) * scale) * back
        rows.append({"key": (row["block_num"], row["par_num"], row["line_num"]), "text": text, "conf": conf,
                     "bbox": (r.x0, r.y0, r.x1, r.y1), "visual": (x * scale, y * scale, (x + w) * scale, (y + h) * scale)})
    return rows


def _word_confidences(page: fitz.Page, language: str, dpi: int = 300) -> list[OcrWord]:
    return [OcrWord(r["text"], r["conf"], r["bbox"]) for r in _tesseract_tsv(page, language, dpi)]


# A "word" this many times taller than the page's typical word, read with less than this confidence, is a picture
# read as letters (a bar chart's bars as "alll"), not text: written into the text layer it would be huge, overlap
# the real text around it and turn into a heading.
GRAPHIC_HEIGHT = 3.0
GRAPHIC_CONFIDENCE = 60.0


def _drop_graphics(rows: list[dict], dropped: list | None = None) -> list[dict]:
    """Rows without the pictures read as letters; their boxes go to `dropped` (where pictures are)."""
    heights = sorted(r["visual"][3] - r["visual"][1] for r in rows)
    if len(heights) < 5:
        return rows  # too few words to know what normal text looks like
    typical = heights[len(heights) // 2]
    kept = []
    for r in rows:
        if r["visual"][3] - r["visual"][1] > GRAPHIC_HEIGHT * typical and r["conf"] < GRAPHIC_CONFIDENCE:
            if dropped is not None:
                dropped.append(r["bbox"])
        else:
            kept.append(r)
    if len(kept) < len(rows):
        log.info("OCR: left out %d word(s) that look like pictures read as letters", len(rows) - len(kept))
    return kept


# Tesseract reads a dotted or dashed leader ("Name ........ Date", the line a form's value is written on) as a long
# "word" of a few letters over and over ("aeeseseee..."), with next to no confidence, and the leader's first dots
# as a letter or two; and a blank to fill in as underscores
LEADER_CONFIDENCE = 30.0
_BLANK = re.compile(r"^(_+|[_.\-…·]{2,})$")  # (a lone dot or dash may be the text's own)
# A blank read on the end of a word's letters ("Name__a"), the rest a speck or two
_BLANK_END = re.compile(r"[_.…·]{2,}[^_.…·]{0,2}$")


def _leader(row: dict) -> bool:
    return _letter_leader(row) or row["conf"] < LEADER_CONFIDENCE and bool(_BLANK.match(row["text"]))


def _letter_leader(row: dict) -> bool:
    """A long run of letters only (no digits or signs: math reads with low confidence too) and few kinds of them."""
    text = row["text"]
    kinds = {c.casefold() for c in text}
    return row["conf"] < LEADER_CONFIDENCE and len(text) >= 10 and text.isalpha() and len(kinds) <= len(text) // 4


def _short_of(row: dict, text_boxes: list, rotated: bool) -> dict:
    """A word whose end runs under text the page already has (a form's label, its blank read into it, and the value
    typed on the blank) ends where that text starts, without the blank: otherwise label and value overlap, and are
    read letter by letter in turn."""
    x0, y0, x1, y1 = row["bbox"]
    starts = [t[0] for t in text_boxes if x0 < t[0] < x1 and min(y1, t[3]) - max(y0, t[1]) >= 0.5 * (y1 - y0)]
    text = _BLANK_END.sub("", row["text"])
    if rotated or not starts or not text or text == row["text"]:
        return row  # nothing typed on it, or the word isn't a blank's start: it stays as read
    end = min(starts)
    return {**row, "text": text, "bbox": (x0, y0, end, y1), "visual": (x0, y0, end, y1)}


def _drop_leaders(rows: list[dict]) -> int:
    """Marks the leaders in one line's words (sorted along it) to be left out, with an uncertain letter or two
    beside one (its first dots); returns how many."""
    height = max(r["line"][3] for r in rows) - min(r["line"][1] for r in rows)
    marked = {i for i, r in enumerate(rows) if _leader(r)}
    for i in [i for i in marked if _letter_leader(rows[i])]:
        v = rows[i]["line"]
        for j in (i - 1, i + 1):
            if not 0 <= j < len(rows):
                continue
            w = rows[j]["line"]
            gap = max(w[0] - v[2], v[0] - w[2])
            if len(rows[j]["text"]) <= 2 and rows[j]["conf"] < LEADER_CONFIDENCE and gap <= 2 * height:
                marked.add(j)
    for i in marked:
        rows[i]["skip"] = True
    return len(marked)


def _covered(box, text_boxes: list) -> bool:
    """Whether half or more of a word's box lies on text the page already has."""
    area = max(1e-6, (box[2] - box[0]) * (box[3] - box[1]))
    for t in text_boxes:
        ix = max(0.0, min(box[2], t[2]) - max(box[0], t[0]))
        iy = max(0.0, min(box[3], t[3]) - max(box[1], t[1]))
        if ix * iy >= 0.5 * area:
            return True
    return False


# A turned line is read with confidence: the boxes of a music score's notes and of noise read as letters are often
# taller than wide too
TURNED_CONFIDENCE = 70.0


def _way(rows: list[dict]) -> int:
    """Which way a line of words runs on the upright page: 0 across, -1 up (text turned a quarter left, as in a
    form's margin), 1 down. Tesseract reads turned text too and gives its words in reading order, but as boxes
    taller than wide, stacked in one column. Only a line of real words read with confidence counts."""
    long = [r for r in rows if len(r["text"]) >= 3]
    upright = [r for r in long if r["visual"][3] - r["visual"][1] > 1.5 * (r["visual"][2] - r["visual"][0])]
    if len(upright) < 2 or 3 * len(upright) < 2 * len(long):
        return 0
    if sum(r["conf"] for r in rows) / len(rows) < TURNED_CONFIDENCE:
        return 0
    if max(r["visual"][0] for r in rows) >= min(r["visual"][2] for r in rows):
        return 0  # not one column
    first, last = rows[0]["visual"], rows[-1]["visual"]
    return 1 if first[1] + first[3] < last[1] + last[3] else -1


def _along(box: tuple, way: int) -> tuple:
    """A box in the line's own space: along the line, then across it from the tops of the letters to their foot."""
    x0, y0, x1, y1 = box
    return {0: (x0, y0, x1, y1), -1: (-y1, x0, -y0, x1), 1: (y0, -x1, y1, -x0)}[way]


def _upright(u: float, v: float, way: int) -> fitz.Point:
    """A point of the line's own space back on the upright page."""
    return fitz.Point({0: (u, v), -1: (v, -u), 1: (-v, u)}[way])


def _tsv_lines(page: fitz.Page, language: str, dropped: list | None = None, existing: list | None = None) -> list[OcrLine]:
    """OCR lines straight from Tesseract's own words, so text and confidence come from one reading.
    Lines run left to right on the upright page, or up or down it where the text is turned (see _way); their
    geometry is mapped back to unrotated space.

    existing: boxes of text the page already has (values typed onto a scanned form). OCR words on them are dropped,
    so that text isn't read twice, and a line is split where they were, so each part keeps its own place. Dotted
    leaders read as letters are dropped the same way."""
    groups: dict[tuple, list[dict]] = {}
    for r in _drop_graphics(_tesseract_tsv(page, language), dropped):
        if existing and _covered(r["bbox"], existing):
            r = {**r, "skip": True}
        elif existing:
            r = _short_of(r, existing, page.rotation != 0)
        groups.setdefault(r["key"], []).append(r)
    back = ~page.rotation_matrix
    segments = []
    leaders = 0
    for rows in groups.values():
        way = _way(rows)
        for r in rows:
            r["line"] = _along(r["visual"], way)
        rows.sort(key=lambda r: r["line"][0])
        leaders += _drop_leaders(rows)
        # Table rows come back as one line across the columns: split at wide gaps, so each cell's text sits
        # over its own cell
        height = max(r["line"][3] for r in rows) - min(r["line"][1] for r in rows)
        seg: list[dict] = []
        for r in rows:
            if r.get("skip"):  # the page's own text, or a leader: the line breaks here
                if seg:
                    segments.append((seg, way))
                seg = []
                continue
            if seg and r["line"][0] - seg[-1]["line"][2] > 1.5 * max(height, 1.0):
                segments.append((seg, way))
                seg = []
            seg.append(r)
        if seg:
            segments.append((seg, way))
    if leaders:
        log.info("OCR: left out %d word(s) that look like dotted leaders read as letters", leaders)
    out = []
    for rows, way in segments:
        u0 = min(r["line"][0] for r in rows)
        v0 = min(r["line"][1] for r in rows)
        u1 = max(r["line"][2] for r in rows)
        v1 = max(r["line"][3] for r in rows)
        height = v1 - v0
        size = _line_size(rows)
        # baseline, a little above the bottom of descenders
        origin = _upright(u0, v1 - 0.2 * height, way) * back
        d = (_upright(1, 0, way) - _upright(0, 0, way)) * back - fitz.Point(0, 0) * back  # the reading direction
        box = fitz.Rect(min(r["visual"][0] for r in rows), min(r["visual"][1] for r in rows),
                        max(r["visual"][2] for r in rows), max(r["visual"][3] for r in rows)) * back
        text = " ".join(r["text"] for r in rows)
        line = OcrLine(page.number, text, (box.x0, box.y0, box.x1, box.y1), (origin.x, origin.y), size,
                       (round(d.x), round(d.y)), u1 - u0, words=[OcrWord(r["text"], r["conf"], r["bbox"]) for r in rows])
        if text in _BULLET_MISREADS and line.extent <= height:
            line.text, line.words = "\u2022", [OcrWord("\u2022", 100.0, line.words[0].bbox)]
        out.append(line)
    return out


DESCENDERS = frozenset("gjpqy,;()[]{}|@$")  # what reaches below the baseline
DESCENT = 1.22  # a line's height with descenders over its height without (ascenders or capitals to the baseline)
SIZE_CONFIDENCE = 50.0


def _line_size(rows: list[dict]) -> float:
    """The font size of an OCR line, from the height of its words' boxes as if it reached below the baseline: a line
    with no descenders ("Certified Database") is shorter than one with ("Hyper-V") at the same size, and the lines of
    one heading came apart as two sizes. Words read with little confidence don't count where others are sure (the
    stars of a rating read as letters, taller than the text, made a heading of their line)."""
    sure = [r for r in rows if r["conf"] >= SIZE_CONFIDENCE] or rows
    height = max(r["line"][3] for r in sure) - min(r["line"][1] for r in sure)
    if not any(DESCENDERS.intersection(r["text"]) for r in sure):
        height *= DESCENT
    return max(4.0, height / 1.15)


def _attach_words(line: OcrLine, words: list[OcrWord]) -> None:
    """The confidence-pass words that lie on this line, in order along it."""
    x0, y0, x1, y1 = line.bbox
    mine = []
    for w in words:
        wx0, wy0, wx1, wy1 = w.bbox
        ix = max(0.0, min(x1, wx1) - max(x0, wx0))
        iy = max(0.0, min(y1, wy1) - max(y0, wy0))
        area = max(1e-6, (wx1 - wx0) * (wy1 - wy0))
        if ix * iy >= 0.5 * area:
            mine.append(w)
    cos, sin = line.direction
    mine.sort(key=lambda w: w.bbox[0] * cos + w.bbox[1] * sin)
    line.words = mine


def ocr_lines(src: str, pages: list[int], confidences: bool = True,
              pictures: dict[int, list] | None = None) -> list[OcrLine]:
    """OCR the given pages: one record per text line, with geometry and per-word confidence.

    With the Tesseract command line installed (as in the engine image), its own word reading is used: it
    reports confidence, and in testing it read clean and lightly damaged scans more accurately than
    PyMuPDF's built-in OCR. Otherwise (or with confidences=False) PyMuPDF's OCR is used, without
    confidences.

    Page rotation is left alone so Tesseract sees the page upright; line geometry comes back in
    unrotated page space with a writing direction, which is reproduced exactly.

    pictures: filled with {page: [bbox]} of what was read as letters but looks like a picture (see
    _drop_graphics), for finding figures on scanned pages."""
    settings = get_settings()
    doc = fitz.open(src)
    lines: list[OcrLine] = []
    try:
        for pno in pages:
            page = doc[pno]
            # Text the page already has (values typed onto a scanned form) keeps its own text: OCR doesn't read it again
            existing = [tuple(w[:4]) for w in page.get_text("words")]
            if confidences and settings.ocr_engine != "pymupdf" and tesseract_cli() is not None:
                dropped: list = []
                found = _tsv_lines(page, settings.ocr_language, dropped, existing)
                if pictures is not None and dropped:
                    pictures.setdefault(pno, []).extend(dropped)
                if found:
                    lines += found
                    continue
            tp = page.get_textpage_ocr(language=settings.ocr_language, dpi=capped_dpi(page, 300), full=True)
            data = page.get_text("dict", textpage=tp)
            words = _word_confidences(page, settings.ocr_language) if confidences else []
            for block in data["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(s["text"] for s in line["spans"]).strip()
                    if not text or not line["spans"]:
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    if existing and _covered((x0, y0, x1, y1), existing):
                        continue  # mostly the page's own text (this OCR gives no words to keep apart)
                    cos, sin = line["dir"]
                    extent = (x1 - x0) if abs(cos) >= abs(sin) else (y1 - y0)
                    thickness = (y1 - y0) if abs(cos) >= abs(sin) else (x1 - x0)
                    if text in _BULLET_MISREADS and extent <= thickness:
                        text = "\u2022"
                    rec = OcrLine(pno, text, (x0, y0, x1, y1), tuple(line["spans"][0]["origin"]),
                                  line["spans"][0]["size"], (cos, sin), extent)
                    if words:
                        _attach_words(rec, words)
                    lines.append(rec)
    finally:
        doc.close()
    return lines


def write_ocr_layer(src: str, dst: str, lines: list[OcrLine]) -> int:
    """Write the OCR lines as an invisible text layer onto src's pages, saving dst. Returns lines written."""
    font, _ = text_layer_font()
    doc = fitz.open(src)
    inserted = 0
    try:
        wrapped: set[int] = set()
        for ln in lines:
            if not ln.text.strip():
                continue  # every word on it was a picture read as letters
            page = doc[ln.page]
            if ln.page not in wrapped:
                # Scanner output often leaves a bare `cm` in the content stream; isolate it so it
                # cannot scale the text we append.
                page.wrap_contents()
                wrapped.add(ln.page)
            try:
                write_invisible_line(page, font, ln.text, ln.origin, ln.size, ln.direction, ln.extent)
                inserted += 1
            except Exception as exc:  # noqa: BLE001 - unsupported glyphs etc.
                log.debug("skipping OCR line on page %s: %s", ln.page, exc)
        doc.save(dst, garbage=0, deflate=True)
    finally:
        doc.close()
    fix_inserted_cid_fonts(dst, font.name)
    return inserted


def ocr_pages(src: str, dst: str, pages: list[int]) -> int:
    """OCR the given pages of src and write dst with the text layer. Returns the number of lines inserted."""
    return write_ocr_layer(src, dst, ocr_lines(src, pages, confidences=False))


def block_confidence(pages, lines: list[OcrLine]) -> None:
    """Each text block of an OCR'd page gets the mean confidence of the OCR words whose middle lies in it (None when
    the OCR gave no confidences): a scanned formula reads with low confidence, prose with high."""
    words: dict[int, list[OcrWord]] = {}
    for ln in lines:
        words.setdefault(ln.page, []).extend(ln.words)
    for page in pages:
        mine = words.get(page.number)
        if not mine:
            continue
        for b in page.blocks:
            x0, y0, x1, y1 = b.bbox
            inside = [w.conf for w in mine
                      if x0 - 1 <= (w.bbox[0] + w.bbox[2]) / 2 <= x1 + 1 and y0 - 1 <= (w.bbox[1] + w.bbox[3]) / 2 <= y1 + 1]
            if inside:
                b.ocr_conf = sum(inside) / len(inside)
