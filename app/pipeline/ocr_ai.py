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


def check_correction(line: OcrLine, corrected: str) -> tuple[bool, str, list[tuple[str, str]], str]:
    """(ok, reason, [(old, new) changes], text to use). The AI's line is lined up with Tesseract's words:
    words Tesseract was sure of must stay (only their spacing may change: "Toprovide" -> "To provide");
    uncertain words may be corrected, split, merged, or dropped if they're specks with no letters."""
    s = get_settings()
    old = [w.text for w in line.words]
    sure = [w.conf >= s.ocr_ai_min_conf for w in line.words]
    new = corrected.split()
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    out: list[str] = []
    changes = []
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
            return False, f"the AI changed words Tesseract was sure of ({before!r} -> {after!r})", [], ""
        if op == "insert":
            return False, f"the AI added {after!r}, which isn't in Tesseract's reading", [], ""
        if op == "delete":
            if any(_has_text(w) for w in old[i0:i1]):
                return False, f"the AI dropped {before!r}", [], ""
            changes.append((before, ""))  # specks of dirt read as punctuation
            continue
        if not _plausible(before.replace(" ", ""), after.replace(" ", "")):
            return False, f"{after!r} doesn't look like {before!r}", [], ""
        if j1 - j0 > i1 - i0 and len(after.replace(" ", "")) > 1.3 * len(before.replace(" ", "")):
            return False, f"the AI added words ({before!r} -> {after!r})", [], ""
        fixed = new[j0:j1]
        if i1 - i0 == 1 and j1 - j0 == 1:
            fixed = [_keep_punctuation(old[i0], new[j0])]
        out += fixed
        changes.append((before, " ".join(fixed)))
    text = " ".join(out)
    if _scripts(text) - (_scripts(" ".join(old)) | {"LATIN"}):
        return False, "the AI used letters from another writing system", [], ""
    return True, "", [c for c in changes if c[0] != c[1]], text


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


async def correct_lines(ai, pdf_path: str, lines: list[OcrLine], stats) -> None:
    """Correct or transcribe uncertain lines in place. stats: EnrichmentStats (ocr list, findings)."""
    s = get_settings()
    todo = [(ln, m) for ln in lines if (m := mode(ln)) is not None]
    if len(todo) > s.ocr_ai_max_lines:
        # Most uncertain first
        todo.sort(key=lambda t: t[0].mean_confidence or 0.0)
        stats.findings.append(f"{len(todo) - s.ocr_ai_max_lines} uncertain OCR line(s) beyond the limit of "
                              f"{s.ocr_ai_max_lines} kept Tesseract's text")
        todo = todo[:s.ocr_ai_max_lines]
    doc = fitz.open(pdf_path)
    try:
        async def one(line: OcrLine, how: str) -> None:
            x0, y0, x1, y1 = line.bbox
            pad = 0.6 * line.size  # a little of the lines around, for context
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
                ok, why, changes, text = check_correction(line, text)
                if ok and not changes:
                    return  # the AI agreed with Tesseract
            else:
                ok, why = check_transcription(line, text)
                changes = []
            if not ok:
                stats.ocr_rejected += 1
                stats.findings.append(f"{where}: AI OCR {how} not used ({why}); Tesseract's text kept")
                return
            before = line.text
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

