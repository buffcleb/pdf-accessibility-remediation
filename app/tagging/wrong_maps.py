"""Composite fonts whose own ToUnicode map is wrong, corrected so the text can be read (PDF/UA 7.21.7). Rendering is
unchanged: only the maps are rewritten.

A printer driver can write a map that gives each glyph's number in the original font instead of the character it
draws (Foxit PhantomPDF Printer 10 does, for every font of a 72-page audit report: "/,%(57" for LIBERTY). The first
258 glyphs of the common TrueType fonts (Arial, Times New Roman, Tahoma...) are in the Macintosh standard order, where
glyph 3 is the space and 36 the letter A, so such a map reads as the text shifted by 29. Glyphs past that order, and
any the driver couldn't place, are mapped to U+0000. The embedded subsets carry no cmap and no glyph names, so the map
is all there is to go by. Three repairs, in order:

  * a glyph that draws nothing (no outline) mapped to U+0000 is a space;
  * a map whose text reads as words when each entry is taken as a glyph number in the standard order, and doesn't as
    it stands, is read that way;
  * the glyphs still without text are read by OCR: each recognized word laid over the run of glyphs it covers, where
    both have the same number of letters, gives each glyph a vote, and a glyph takes the character most of its votes
    agree on. A word whose other glyphs already have text counts only when they agree with what OCR read.

When OCR runs, it also checks a map read by glyph numbers: one that OCR mostly disagrees with is put back.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name

from app.tagging.font_unicode import _entries, glyph_to_unicode, rewrite_map

log = logging.getLogger(__name__)

_NO_TEXT = set("\x00﻿￾")
_UNKNOWN = "�"  # a glyph without text, in a reading
_VOWELS = set("aeiouyàáâäãåæèéêëìíîïòóôöõøùúûüýÿœ")
_NUMBER = re.compile(r"^[\d.,$()%\-–—/:#*+=]+$")
_TRIM = ".,;:!?()[]\"'’‘”“"
OCR_PAGES = 8  # pages read by OCR per document, those with the most glyphs still to read
CHECK_PAGES = 3  # pages read by OCR only to check a map read by glyph numbers


def unresolved(text: str | None) -> bool:
    """A map entry that gives no text (U+0000, a byte order mark, nothing): what PDF/UA 7.21.7 fails."""
    return text is None or all(c in _NO_TEXT for c in text)


@dataclass
class _Font:
    obj: Dictionary
    name: str
    text: str  # the map as written
    tt: object  # fontTools TTFont
    order: list[str]
    gid_cid: dict[int, int] | None  # None: CIDs are glyph numbers (Identity)
    entries: dict[int, str] = field(default_factory=dict)  # CID -> text, as repaired so far
    blanks: int = 0
    shifted: dict[int, str] | None = None  # the entries before reading them as glyph numbers
    read: int = 0

    def cid(self, gid: int) -> int:
        return gid if self.gid_cid is None else self.gid_cid.get(gid, gid)

    def gid(self, cid: int) -> int:
        if self.gid_cid is None:
            return cid
        return next((g for g, c in self.gid_cid.items() if c == cid), cid)

    def blank(self, cid: int) -> bool:
        gid = self.gid(cid)
        if not 0 < gid < len(self.order):
            return False
        try:
            return self.tt["glyf"][self.order[gid]].numberOfContours == 0
        except (KeyError, AttributeError):
            return False


def _candidate(obj: Dictionary) -> _Font | None:
    """A composite TrueType font with Identity-H codes, its program embedded and a map of its own."""
    from fontTools.ttLib import TTFont

    if obj.get("/Subtype") != Name.Type0 or obj.get("/Encoding") != Name("/Identity-H") or "/ToUnicode" not in obj:
        return None
    try:
        cid = obj.DescendantFonts[0]
        program = cid.FontDescriptor.FontFile2.read_bytes()
        text = obj.ToUnicode.read_bytes().decode("latin-1")
        tt = TTFont(BytesIO(program), lazy=True)
        order = tt.getGlyphOrder()
    except Exception:  # noqa: BLE001 - a font program or map that can't be read is left as it is
        return None
    gid_cid = None
    to_gid = cid.get("/CIDToGIDMap")
    if isinstance(to_gid, pikepdf.Stream):
        data = to_gid.read_bytes()
        gid_cid = {int.from_bytes(data[i:i + 2], "big"): i // 2 for i in range(0, len(data) - 1, 2)}
    entries = {}
    for src, dst in _entries(text):
        if len(src) <= 4:
            entries[int(src, 16)] = dst
    return _Font(obj, str(obj.get("/BaseFont", "/?"))[1:], text, tt, order, gid_cid, dict(entries))


def _suspect(f: _Font) -> bool:
    """A map worth reading the document for: one with entries that give no text or a control code, or, in a map of
    some size, no lowercase letter or more punctuation and symbols than a text font's map has (a shifted alphabet is
    capitals, digits and punctuation)."""
    if any(unresolved(t) or any(unicodedata.category(c) == "Cc" for c in t) for t in f.entries.values()):
        return True
    if len(f.entries) < 8:
        return False
    if not any(c.islower() for t in f.entries.values() for c in t):
        return True
    odd = sum(1 for t in f.entries.values() if len(t) == 1 and unicodedata.category(t)[0] in "PS")
    return odd >= 0.3 * len(f.entries)


def _as_glyph_number(text: str) -> str | None:
    """A map entry taken as a glyph number in the Macintosh standard order."""
    from fontTools.ttLib.standardGlyphOrder import standardGlyphOrder

    if len(text) != 1 or not 0 < ord(text) < len(standardGlyphOrder):
        return None
    return glyph_to_unicode(standardGlyphOrder[ord(text)])


def _wordlike(text: str) -> tuple[float, int]:
    """(share of words that look like words, how many were judged): letters only, with a vowel. Numbers and words with
    a glyph that has no text are left out."""
    words = [w.strip(_TRIM) for w in text.split()]
    words = [w for w in words if w and not _NUMBER.match(w) and _UNKNOWN not in w]
    good = sum(1 for w in words if all(c.isalpha() or c in "-'’" for c in w) and any(c.lower() in _VOWELS for c in w))
    return (good / len(words) if words else 0.0), len(words)


def _reading(runs: list[list[int]], entries: dict[int, str]) -> str:
    out = []
    for run in runs:
        out.append("".join(" " if (t := entries.get(cid)) is not None and t.isspace()
                           else (_UNKNOWN if unresolved(t) else t) for cid in run))
    return " ".join(out)


def _traces(doc, fonts: dict[str, _Font]
            ) -> tuple[dict[str, list[list[int]]], dict[int, Counter], dict[str, list[int]]]:
    """Per font: its runs of CIDs as drawn; per page: how often it draws each glyph that still has no text, by
    (font, CID); and per font [glyphs whose text MuPDF gives as the map does, glyphs with text], which says whether
    the trace's glyph numbers are the font's CIDs (for a whole TrueType program MuPDF can number them otherwise)."""
    runs: dict[str, list[list[int]]] = defaultdict(list)
    todo: dict[int, Counter] = defaultdict(Counter)
    same: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for page in doc:
        try:
            trace = page.get_texttrace()
        except Exception:  # noqa: BLE001 - a page MuPDF can't trace adds nothing
            continue
        for item in trace:
            f = fonts.get(item["font"])
            if f is None:
                continue
            cids = []
            for ucs, gid, *_ in item["chars"]:
                cid = f.cid(gid)
                cids.append(cid)
                if 0 < ucs < 0xFFFD:
                    same[item["font"]][0] += f.entries.get(cid) == chr(ucs)
                    same[item["font"]][1] += 1
            runs[item["font"]].append(cids)
            todo[page.number].update((item["font"], c) for c in cids if unresolved(f.entries.get(c)))
    return runs, todo, same


def _pages_to_read(todo: dict[int, Counter]) -> list[int]:
    """Up to OCR_PAGES pages that between them show every glyph still without text, if they can: each next page the
    one showing the most of those not yet shown (by how often), then the busiest of the rest."""
    left = {k for c in todo.values() for k in c}
    pages: list[int] = []
    while left and len(pages) < OCR_PAGES:
        best = max((p for p in todo if p not in pages), key=lambda p: (len(left & set(todo[p])), sum(todo[p].values())))
        if not left & set(todo[best]):
            break
        pages.append(best)
        left -= set(todo[best])
    rest = sorted((p for p in todo if p not in pages), key=lambda p: -sum(todo[p].values()))
    return pages + rest[:OCR_PAGES - len(pages)]


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def _read_pages(doc, pages: list[int]) -> list[tuple[list, list]]:
    """OCR each page: (its words, its glyph trace)."""
    from app.config import get_settings
    from app.pipeline.analyzer import capped_dpi

    settings = get_settings()
    out = []
    for pno in pages:
        page = doc[pno]
        try:
            tp = page.get_textpage_ocr(language=settings.ocr_language, dpi=capped_dpi(page, 300), full=True)
            out.append((page.get_text("words", textpage=tp), page.get_texttrace()))
        except Exception as exc:  # noqa: BLE001 - OCR failing on a page leaves its glyphs unread
            log.warning("page %s: OCR for font maps failed: %s", pno + 1, exc)
    return out


def _align(read: list[tuple[list, list]], fonts: dict[str, _Font], maps: dict[str, dict[int, str]]
           ) -> tuple[dict[str, dict[int, Counter]], dict[str, list[int]]]:
    """Lay each OCR word over the glyphs it covers, the fonts read through `maps`. Returns the votes for glyphs
    without text (font -> CID -> characters) and per font [glyphs with text OCR agreed with, glyphs compared]."""
    votes: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    agree: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for words, trace in read:
        glyphs = []  # (centre x, centre y, font key or None, CID, text)
        for item in trace:
            key = item["font"] if item["font"] in fonts else None
            for ucs, gid, _origin, bbox in item["chars"]:
                if key:
                    cid = fonts[key].cid(gid)
                    text = maps[key].get(cid)
                    text = None if unresolved(text) else text
                else:
                    cid, text = gid, (chr(ucs) if 0 < ucs < 0xFFFD else None)
                if text is not None and text.isspace():
                    continue
                glyphs.append(((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, key, cid, text))
        for x0, y0, x1, y1, word, *_ in words:
            word = word.strip()
            if not word:
                continue
            under = sorted((g for g in glyphs if x0 - 1 <= g[0] <= x1 + 1 and y0 - 1 <= g[1] <= y1 + 1),
                           key=lambda g: g[0])
            if len(under) != len(word):
                continue
            known = [(g, ch) for g, ch in zip(under, word) if g[4] is not None]
            for g, ch in known:
                if g[2]:
                    agree[g[2]][0] += _same(g[4], ch)
                    agree[g[2]][1] += 1
            if not all(_same(g[4], ch) for g, ch in known):
                continue
            weight = 2 if len(known) >= 2 else 1  # a word whose other letters agree counts double
            for g, ch in zip(under, word):
                if g[4] is None and g[2]:
                    votes[g[2]][g[3]][ch] += weight
    return votes, agree


def _share(counts: list[int]) -> float:
    return counts[0] / counts[1] if counts[1] else 0.0


def repair_wrong_maps(pdf: pikepdf.Pdf, src: Path) -> list[str]:
    """Correct wrong ToUnicode maps of composite TrueType fonts in `pdf` (opened from `src`), in place. Returns a line
    per font repaired, for the report."""
    import pymupdf as fitz

    from app.pipeline.ocr import tesseract_available

    found = [f for obj in pdf.objects if isinstance(obj, Dictionary) and (f := _candidate(obj)) is not None]
    found = [f for f in found if _suspect(f)]
    if not found:
        return []
    for f in found:
        for cid, text in list(f.entries.items()):
            if unresolved(text) and f.blank(cid):
                f.entries[cid] = " "
                f.blanks += 1
    # MuPDF names a font in a trace by its BaseFont without the subset tag: a name two fonts share is left alone
    by_name: dict[str, list[_Font]] = defaultdict(list)
    for f in found:
        by_name[f.name.split("+", 1)[-1]].append(f)
    fonts = {k: v[0] for k, v in by_name.items() if len(v) == 1}
    as_written = {k: dict(f.entries) for k, f in fonts.items()}
    as_numbers = {k: {cid: t if unresolved(t) or t == " " else (_as_glyph_number(t) or "")
                      for cid, t in f.entries.items()} for k, f in fonts.items()}
    doc = fitz.open(src)
    try:
        runs, _, same = _traces(doc, fonts)
        for key in list(fonts):
            ok, seen = same[key]
            if not seen or ok < 0.95 * seen:
                log.info("%s: the page trace doesn't number its glyphs as the map does (%d of %d): left alone",
                         fonts[key].name, ok, seen)
                del fonts[key]
        judged = set()
        for key, f in fonts.items():
            now, _ = _wordlike(_reading(runs.get(key, []), as_written[key]))
            then, m = _wordlike(_reading(runs.get(key, []), as_numbers[key]))
            if m >= 10:
                judged.add(key)
                if then >= 0.6 and then >= now + 0.3:
                    f.shifted, f.entries = as_written[key], dict(as_numbers[key])
                    log.info("%s: map read as glyph numbers (words %.0f%% -> %.0f%%)", f.name, 100 * now, 100 * then)
        _, todo, _ = _traces(doc, fonts)
        if (todo or any(f.shifted is not None for f in fonts.values())) and tesseract_available():
            pages = _pages_to_read(todo)
            if not pages:  # only checking: the pages that use the fonts read by glyph numbers most
                use = Counter()
                for page in doc:
                    use[page.number] = sum(1 for item in page.get_texttrace()
                                           if (f := fonts.get(item["font"])) is not None and f.shifted is not None)
                pages = [p for p, n in use.most_common(CHECK_PAGES) if n]
            read = _read_pages(doc, pages)
            _, by_written = _align(read, fonts, as_written)
            _, by_numbers = _align(read, fonts, as_numbers)
            for key, f in fonts.items():
                seen = by_numbers[key][1]
                if seen < 10:
                    continue
                if f.shifted is not None and _share(by_numbers[key]) < 0.6:
                    log.info("%s: OCR agrees with the glyph-number reading on %d of %d glyphs: kept the map", f.name,
                             *by_numbers[key])
                    f.entries, f.shifted = f.shifted, None
                elif (f.shifted is None and key not in judged and _share(by_numbers[key]) >= 0.6
                      and _share(by_numbers[key]) >= _share(by_written[key]) + 0.3):
                    log.info("%s: map read as glyph numbers (OCR agrees on %d of %d glyphs)", f.name,
                             *by_numbers[key])
                    f.shifted, f.entries = f.entries, dict(as_numbers[key])
            votes, _ = _align(read, fonts, {k: f.entries for k, f in fonts.items()})
            for key, by_cid in votes.items():
                f = fonts[key]
                for cid, counted in by_cid.items():
                    (ch, top), total = counted.most_common(1)[0], sum(counted.values())
                    # Most of the votes, and two of them, or one no other vote disagrees with
                    if top >= 2 * total / 3 and (top >= 2 or top == total) and unresolved(f.entries.get(cid)):
                        f.entries[cid] = ch
                        f.read += 1
                    log.debug("%s: CID %d votes %s", f.name, cid, dict(counted))
    finally:
        doc.close()
    fixed = []
    for f in found:
        if not (f.blanks or f.shifted is not None or f.read):
            continue
        rewrite_map(pdf, f.obj, f.text, [(f"{cid:04X}", t) for cid, t in sorted(f.entries.items())])
        left = sum(1 for t in f.entries.values() if unresolved(t))
        parts = ([f"read as glyph numbers ({len(f.entries)} entries)"] if f.shifted is not None else []) \
            + ([f"{f.blanks} empty glyph(s) as spaces"] if f.blanks else []) \
            + ([f"{f.read} character(s) read by OCR"] if f.read else []) \
            + ([f"{left} still without text"] if left else [])
        fixed.append(f"{f.name} (wrong map: {', '.join(parts)})")
    return fixed
