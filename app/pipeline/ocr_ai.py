"""AI-assisted OCR correction for scanned pages.

OCR text is the document's content (what screen readers read and search finds), so the AI corrects
Tesseract's text rather than replacing it:

  * correct: lines with some uncertain words. The AI sees the line image and Tesseract's words with the
    uncertain ones marked, and returns the line. Only marked words may change, each change must look
    like the word it replaces, and the number of words must stay the same, so the text stays aligned
    with the image.
  * transcribe: lines Tesseract mostly failed on (handwriting, very poor print). The AI reads the line
    afresh; its text replaces Tesseract's for that line and is always flagged for review.

Anything that fails a check keeps Tesseract's text.
"""
from __future__ import annotations

import asyncio
import difflib
import unicodedata

import pymupdf as fitz

from app.config import get_settings
from app.pipeline.analyzer import render_png
from app.pipeline.ocr import OcrLine

MARK_OPEN, MARK_CLOSE = "[[", "]]"
PICTURE = "[picture]"  # the AI's answer for an unsure "word" that is a picture read as letters (rating stars, an icon)
DROP_CONFIDENCE = 50.0  # under this the AI may leave a word out without saying [picture]: Tesseract hardly read it


def _has_text(word: str) -> bool:
    return any(c.isalnum() for c in word)


def mode(line: OcrLine) -> str | None:
    """"correct", "transcribe" or None (fine as it is, junk such as table borders and leader dots, or no
    confidence to judge by)."""
    s = get_settings()
    if not line.words or not _has_text(line.text):
        return None
    # The confidence pass must agree with the text layer's reading, or corrections can't be lined up
    joined = " ".join(w.text for w in line.words)
    if difflib.SequenceMatcher(None, joined, line.text).ratio() < 0.8:
        return None
    mean = line.mean_confidence or 0.0
    if mean < s.ocr_ai_transcribe_below and len(line.text) >= 3:
        return "transcribe"
    if any(w.conf < s.ocr_ai_min_conf and _has_text(w.text) for w in line.words):
        return "correct"
    return None


def marked_text(line: OcrLine) -> str:
    """Tesseract's words with the uncertain ones in [[ ]]."""
    s = get_settings()
    return " ".join(f"{MARK_OPEN}{w.text}{MARK_CLOSE}" if w.conf < s.ocr_ai_min_conf else w.text
                    for w in line.words)


def _shape(word: str) -> str:
    return unicodedata.normalize("NFKC", word).casefold()


def _plausible(old: str, new: str) -> bool:
    """A correction must look like what it replaces: similar length and mostly the same letters."""
    if not new:
        return False
    if not 0.5 <= len(new) / max(1, len(old)) <= 2.0:
        return False
    return difflib.SequenceMatcher(None, _shape(old), _shape(new)).ratio() >= 0.4


def _scripts(text: str) -> set[str]:
    out = set()
    for c in text:
        if c.isalpha():
            name = unicodedata.name(c, "")
            out.add(name.split()[0] if name else "?")
    return out


_EDGE_PUNCT = ".,;:!?\"'’”“)("


def _keep_punctuation(old: str, new: str) -> str:
    """Models tend to drop printed punctuation ("University." -> "University"): put it back."""
    lead = old[:len(old) - len(old.lstrip(_EDGE_PUNCT))]
    trail = old[len(old.rstrip(_EDGE_PUNCT)):]
    if trail and not new.endswith(trail) and not any(c in _EDGE_PUNCT for c in new[-1:]):
        new += trail
    if lead and not new.startswith(lead) and not any(c in _EDGE_PUNCT for c in new[:1]):
        new = lead + new
    return new


def check_correction(line: OcrLine, corrected: str
                     ) -> tuple[bool, str, list[tuple[str, str]], str, set[int]]:
    """(ok, reason, [(old, new) changes], text to use, the words left out by number). The AI's line is lined up with
    Tesseract's words: words Tesseract was sure of must stay (only their spacing may change: "Toprovide" -> "To
    provide"); uncertain words may be corrected, split, merged, or dropped if they're specks with no letters, if the AI
    says they are pictures ([picture]: the stars of a rating read as letters), or if Tesseract hardly read them (a model
    that leaves the stars out instead of answering [picture])."""
    s = get_settings()
    old = [w.text for w in line.words]
    sure = [w.conf >= s.ocr_ai_min_conf for w in line.words]
    new = corrected.split()
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    out: list[str] = []
    changes = []
    dropped: set[int] = set()
    for op, i0, i1, j0, j1 in sm.get_opcodes():
        if op == "equal":
            out += old[i0:i1]
            continue
        before, after = " ".join(old[i0:i1]), " ".join(new[j0:j1])
        if before.replace(" ", "") == after.replace(" ", ""):
            out += new[j0:j1]  # only the spacing changed
            changes.append((before, after))
            continue
        if any(sure[i0:i1]):
            return False, f"the AI changed words Tesseract was sure of ({before!r} -> {after!r})", [], "", set()
        if op == "insert":
            return False, f"the AI added {after!r}, which isn't in Tesseract's reading", [], "", set()
        if op == "delete":
            if any(_has_text(w.text) and w.conf >= DROP_CONFIDENCE for w in line.words[i0:i1]):
                return False, f"the AI dropped {before!r}", [], "", set()
            changes.append((before, ""))  # specks of dirt read as punctuation, or marks Tesseract could hardly read
            dropped.update(range(i0, i1))
            continue
        if new[j0:j1] and all(w == PICTURE for w in new[j0:j1]):
            changes.append((before, ""))  # a picture read as letters: not text
            dropped.update(range(i0, i1))
            continue
        if not _plausible(before.replace(" ", ""), after.replace(" ", "")):
            return False, f"{after!r} doesn't look like {before!r}", [], "", set()
        if j1 - j0 > i1 - i0 and len(after.replace(" ", "")) > 1.3 * len(before.replace(" ", "")):
            return False, f"the AI added words ({before!r} -> {after!r})", [], "", set()
        fixed = new[j0:j1]
        if i1 - i0 == 1 and j1 - j0 == 1:
            fixed = [_keep_punctuation(old[i0], new[j0])]
        out += fixed
        changes.append((before, " ".join(fixed)))
    text = " ".join(out)
    if _scripts(text) - (_scripts(" ".join(old)) | {"LATIN"}):
        return False, "the AI used letters from another writing system", [], "", set()
    return True, "", [c for c in changes if c[0] != c[1]], text, dropped


def _leave_out(line: OcrLine, dropped: set[int]) -> None:
    """The line's place without the words left out: its text starts and ends where the words that stay do, not over
    a picture at either end."""
    kept = [w for i, w in enumerate(line.words) if i not in dropped]
    line.words = kept
    if not kept:
        return
    cos, sin = line.direction
    along = lambda x, y: x * cos + y * sin  # noqa: E731
    ends = [along(x, y) for w in kept for x in (w.bbox[0], w.bbox[2]) for y in (w.bbox[1], w.bbox[3])]
    start, end = min(ends), max(ends)
    shift = start - along(*line.origin)
    if shift > 0:
        line.origin = (line.origin[0] + shift * cos, line.origin[1] + shift * sin)
    line.extent = max(1.0, end - max(start, along(*line.origin)))
    line.bbox = (min(w.bbox[0] for w in kept), min(w.bbox[1] for w in kept),
                 max(w.bbox[2] for w in kept), max(w.bbox[3] for w in kept))


def check_transcription(line: OcrLine, text: str) -> tuple[bool, str]:
    """A fresh reading of a line Tesseract failed on: it must have real text, fit the line (not much longer or
    shorter than the space it covers) and contain no markup."""
    text = text.strip()
    if not text or not _has_text(text.replace("[illegible]", "")):
        return False, "the AI couldn't read it either"
    if MARK_OPEN in text or "\n" in text:
        return False, "the AI returned more than one line or kept the markers"
    # A line holds roughly one character per half its height in width; allow wide variation for handwriting
    expected = max(1.0, line.extent / max(1.0, 0.5 * line.size))
    if not 0.3 * expected <= len(text) <= 2.5 * expected + 10:
        return False, f"{len(text)} characters doesn't fit a line of about {expected:.0f}"
    return True, ""


def without_neighbours(text: str, near: list[str]) -> str:
    """A fresh reading without what the AI read of the lines around it. The line's image has a little of them for
    context (see correct_lines), and the AI sometimes reads one as part of the line: a form's two-line label read
    as one, the second line then said twice and squeezed into the first line's place. Where the reading starts or
    ends with a nearby line's text (as Tesseract read it), that part is cut off."""
    words = text.split()
    for other in near:
        theirs = other.split()
        n = len(theirs)
        if not n or n >= len(words):
            continue
        shape = _shape(" ".join(theirs))
        if difflib.SequenceMatcher(None, _shape(" ".join(words[-n:])), shape).ratio() >= 0.8:
            words = words[:-n]
        elif difflib.SequenceMatcher(None, _shape(" ".join(words[:n])), shape).ratio() >= 0.8:
            words = words[n:]
    return " ".join(words)


def _context_pad(line: OcrLine) -> float:
    return 0.6 * line.size  # how much of the lines around a line's image shows, for context


def _near(line: OcrLine, lines: list[OcrLine]) -> list[OcrLine]:
    """The other lines whose box reaches into this line's image."""
    pad = _context_pad(line)
    x0, y0, x1, y1 = line.bbox
    return [o for o in lines if o is not line and o.page == line.page and o.bbox[0] <= x1 + pad
            and o.bbox[2] >= x0 - pad and o.bbox[1] <= y1 + pad and o.bbox[3] >= y0 - pad]


async def correct_lines(ai, pdf_path: str, lines: list[OcrLine], stats) -> None:
    """Correct or transcribe uncertain lines in place. stats: EnrichmentStats (ocr list, findings)."""
    s = get_settings()
    todo = [(ln, m) for ln in lines if (m := mode(ln)) is not None]
    if len(todo) > s.ocr_ai_max_lines:
        # Most uncertain first
        todo.sort(key=lambda t: t[0].mean_confidence or 0.0)
        stats.limit_hit("OCR_AI_MAX_LINES", s.ocr_ai_max_lines, len(todo), "uncertain OCR lines kept Tesseract's text")
        stats.findings.append(f"{len(todo) - s.ocr_ai_max_lines} uncertain OCR line(s) beyond the limit of "
                              f"{s.ocr_ai_max_lines} kept Tesseract's text")
        todo = todo[:s.ocr_ai_max_lines]
    # Tesseract's reading of the lines around each, taken now: lines change as the AI's readings land
    around = {id(ln): [o.text for o in _near(ln, lines)] for ln, _ in todo}
    doc = fitz.open(pdf_path)
    try:
        async def one(line: OcrLine, how: str) -> None:
            x0, y0, x1, y1 = line.bbox
            pad = _context_pad(line)  # a little of the lines around, for context
            # OCR boxes are in unrotated page space; the render clips the page as shown, so a rotated page's
            # box is turned with it (and the line comes out upright, as the AI should see it)
            clip = fitz.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) * doc[line.page].rotation_matrix
            where = f"page {line.page + 1}"
            try:
                png = render_png(doc, line.page, dpi=200, clip=tuple(clip), max_px=1600)
                res = await ai.correct_ocr_line(png, line.page, marked_text(line) if how == "correct" else line.text,
                                                how)
            except Exception as exc:  # one line never fails the job: Tesseract's reading stands
                stats.findings.append(f"{where}: AI OCR {how} failed ({exc}); Tesseract's text kept")
                return
            text = " ".join(res["text"].split())
            if how == "correct":
                ok, why, changes, text, dropped = check_correction(line, text)
                if ok and not changes:
                    return  # the AI agreed with Tesseract
            else:
                text = without_neighbours(" ".join(w for w in text.split() if w != PICTURE), around[id(line)])
                ok, why = check_transcription(line, text)
                changes = []
            if not ok:
                stats.ocr_rejected += 1
                stats.findings.append(f"{where}: AI OCR {how} not used ({why}); Tesseract's text kept")
                return
            before = line.text
            if how == "correct" and dropped:
                _leave_out(line, dropped)
            line.text = text
            line.source = "ai-corrected" if how == "correct" else "ai-transcribed"
            stats.ocr.append({"page": line.page, "bbox": list(line.bbox), "mode": how, "before": before, "after": text,
                              "changes": changes, "confidence": round(line.mean_confidence or 0.0, 1)})

        await asyncio.gather(*(one(ln, m) for ln, m in todo))
    finally:
        doc.close()
    n_words = sum(len(o["changes"]) for o in stats.ocr if o["mode"] == "correct")
    n_lines = sum(1 for o in stats.ocr if o["mode"] == "transcribe")
    if n_words or n_lines:
        stats.findings.append(f"OCR: {n_words} word(s) corrected and {n_lines} line(s) transcribed by AI; check them")

