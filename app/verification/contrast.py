"""Text contrast (#367, WCAG 1.4.3): text below 4.5:1 against what it's drawn on (3:1 for large text: 18 pt, or
14 pt bold) is listed for review, one finding per page with the colors and the ratio. A report, not a fix: the
pages aren't changed.

The text's color is the one it's painted with (PyMuPDF's text trace); invisible text (an OCR layer, or text drawn
fully transparent) is skipped. The background is read from the rendered page: the commonest color in the text's box
once the glyphs' own color is set aside. Text over a photograph or a gradient has no common color there, so it's left
out rather than guessed at.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf as fitz

from app.verification.model import Finding

SCALE = 1.5          # rendering scale (108 dpi): enough pixels under 9 pt text to find its background
MIN_SHARE = 0.35     # the background's color covers at least this much of the box's pixels left after the glyphs
MAX_EXAMPLES = 3
NORMAL, LARGE = 4.5, 3.0


def _lin(c: np.ndarray) -> np.ndarray:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def luminance(rgb) -> float:
    r, g, b = _lin(np.asarray(rgb, dtype=float))
    return float(0.2126 * r + 0.7152 * g + 0.0722 * b)


def ratio(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _rgb(color, n: int) -> tuple[float, float, float] | None:
    c = tuple(float(v) for v in (color or ()))
    if n == 1 and len(c) >= 1:
        return (c[0],) * 3
    if n == 3 and len(c) >= 3:
        return c[:3]
    if n == 4 and len(c) >= 4:  # CMYK, roughly
        k = c[3]
        return tuple((1 - v) * (1 - k) for v in c[:3])
    return None


def _hex(rgb) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, v)) * 255):02X}" for v in rgb)


def _large(span: dict) -> bool:
    size = float(span.get("size") or 0)
    font = str(span.get("font") or "").lower()
    bold = (any(w in font for w in ("bold", "black", "heavy", "semibold", "demi"))
            or bool(int(span.get("flags") or 0) & 16))
    return size >= 18 or (bold and size >= 14)


def _background(pix: np.ndarray, box, text_rgb) -> tuple[float, float, float] | None:
    """The commonest color in the box, glyph pixels (near the text's own color) set aside; None when nothing is
    common enough to be a background, or when the text's color doesn't show at all (it's hidden under a shape)."""
    x0, y0, x1, y1 = (int(round(v * SCALE)) for v in box)
    h, w = pix.shape[:2]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    px = pix[y0:y1, x0:x1].reshape(-1, 3).astype(float) / 255.0
    near = np.abs(px - np.asarray(text_rgb)).max(axis=1) < 0.12
    if near.sum() < 0.01 * len(px):
        return None  # none of the text's color shows: it's covered by something drawn after it, not faint
    rest = px[~near]
    if len(rest) < 0.15 * len(px):
        return None  # all glyph: nothing to compare with
    bins = np.round(rest * 15).astype(int)
    keys = bins[:, 0] * 256 + bins[:, 1] * 16 + bins[:, 2]
    values, counts = np.unique(keys, return_counts=True)
    top = int(np.argmax(counts))
    if counts[top] < MIN_SHARE * len(rest):
        return None  # a photograph or a gradient
    return tuple(float(v) for v in rest[keys == values[top]].mean(axis=0))


def check(path: Path, max_pages: int) -> list[Finding]:
    out: list[Finding] = []
    with fitz.open(path) as doc:
        for n, page in enumerate(doc):
            if n >= max_pages:
                break
            # Painted text only: not an invisible OCR layer (type 3), nor text drawn fully transparent (hidden text,
            # which would otherwise read as "the same color as its background")
            spans = [s for s in page.get_texttrace() if s.get("type") == 0 and s.get("chars")
                     and (s.get("opacity") is None or float(s["opacity"]) >= 0.1)]
            if not spans:
                continue
            pm = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE), colorspace=fitz.csRGB, alpha=False)
            pix = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, 3)
            rot = page.rotation_matrix
            low = []
            for s in spans:
                rgb = _rgb(s.get("color"), int(s.get("colorspace") or 0))
                if rgb is None:
                    continue
                text = "".join(chr(c[0]) for c in s["chars"] if c[0] > 31).strip()
                if len(text) < 2:
                    continue  # a bullet or a stray mark
                box = fitz.Rect(s["bbox"]) * rot
                bg = _background(pix, box, rgb)
                if bg is None:
                    continue
                opacity = float(s.get("opacity") if s.get("opacity") is not None else 1.0)
                shown = tuple(opacity * t + (1 - opacity) * b for t, b in zip(rgb, bg))
                need = LARGE if _large(s) else NORMAL
                r = ratio(shown, bg)
                if r < need - 0.05:  # rounding: 4.48 reads as 4.5
                    low.append((r, need, text, shown, bg))
            if not low:
                continue
            low.sort(key=lambda x: x[0])
            examples = "; ".join(f"“{t[:40]}” {_hex(fg)} on {_hex(bg)}, {r:.1f}:1 (needs {need:g}:1)"
                                 for r, need, t, fg, bg in low[:MAX_EXAMPLES])
            more = f" and {len(low) - MAX_EXAMPLES} more" if len(low) > MAX_EXAMPLES else ""
            r, need, t, fg, bg = low[0]
            out.append(Finding(
                layer="visual", severity="review", issue="text with low contrast", page=n + 1, text=t[:120],
                detail=f"{len(low)} run(s) of text below the minimum contrast: {examples}{more}",
                action="Check it can be read; the colors are in the source document, so fix them there and convert "
                       "again if it's too faint",
                extra={"ratio": round(r, 2), "needs": need, "color": _hex(fg), "background": _hex(bg),
                       "count": len(low)}))
    return out
