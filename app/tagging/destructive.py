"""Destructive fixes, used only for the `_compliant_modified.pdf` output.

Pages whose problems cannot be fixed by tagging alone (unembedded or Type3 fonts, fonts
without Unicode mappings, content veraPDF still flags) are flattened:

  * the page is rasterized at RASTER_DPI and drawn as a background image (tagged as Artifact),
  * every text span is re-drawn as invisible text (render mode 3) in an embedded Unicode font
    at its original position, so it is selectable, searchable and tagged as real content,
  * each original image XObject is re-drawn at its original position on top, so figures keep
    their own tag and alt text,
  * straight rules (fraction bars, table lines) are re-drawn fully transparent, so the text layer
    reads the same way as the original page (fractions, table cells).

The page looks nearly identical but is no longer vector-accurate, which is why this output is
reported separately from the visually exact one.
"""
from __future__ import annotations

import logging

import pymupdf as fitz

from app.config import get_settings
from app.pipeline.analyzer import capped_dpi
from app.pipeline.fonts import fix_inserted_cid_fonts, text_layer_font
from app.pipeline.ocr import write_invisible_line

log = logging.getLogger(__name__)

# veraPDF PDF/UA-1 clauses that flattening addresses (fonts; content that could not be tagged).
DESTRUCTIVE_CLAUSES = ("7.21", "7.1")


def _usable(ch: str, font: fitz.Font) -> bool:
    return ch != "\ufffd" and (ch.isspace() or font.has_glyph(ord(ch)) > 0)


def _unseen(span: dict, look: fitz.Pixmap, page: fitz.Page) -> bool:
    """Near-white text over a plain white area: nobody can see it on the original page (often leftover
    or hidden text), so it doesn't become part of the readable text layer."""
    c = span.get("color", 0)
    if min((c >> 16) & 255, (c >> 8) & 255, c & 255) < 235:
        return False
    sx, sy = look.width / page.rect.width, look.height / page.rect.height
    x0, y0, x1, y1 = span["bbox"]
    for y in range(max(0, int(y0 * sy)), min(look.height, int(y1 * sy) + 1)):
        for x in range(max(0, int(x0 * sx)), min(look.width, int(x1 * sx) + 1)):
            if min(look.pixel(x, y)) < 235:
                return False
    return True


def _text_spans(page: fitz.Page, font: fitz.Font) -> list[tuple]:
    """Text to redraw invisibly. Characters the text-layer font cannot draw are dropped (they would
    become .notdef glyphs, PDF/UA 7.21.8). If most of the page's text has no usable Unicode (fonts
    without ToUnicode), OCR the page instead when Tesseract is available."""
    spans, total, bad = [], 0, 0
    look = page.get_pixmap(dpi=capped_dpi(page, 36), alpha=False, annots=False)
    for block in page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if _unseen(span, look, page):
                    continue
                raw = "".join(c["c"] for c in span["chars"])
                total += len(raw.strip())
                text = "".join(ch for ch in raw if _usable(ch, font))
                bad += len(raw.strip()) - len(text.strip())
                if text.strip():
                    # Horizontal text is placed glyph by glyph at the original pen positions: the text-layer
                    # font's letters are wider or narrower than the original's, and whole runs would drift
                    # into their neighbors (a word written into the middle of a number)
                    glyphs = None
                    if abs(line["dir"][0] - 1) < 1e-3:
                        glyphs = [(c["c"], c["origin"]) for c in span["chars"] if _usable(c["c"], font)]
                    spans.append((span["origin"], text, span["size"], line["dir"], glyphs,
                                  fitz.sRGB_to_pdf(span["color"])))
    if total and bad / total > 0.3:
        from app.pipeline.ocr import tesseract_available

        if tesseract_available():
            settings = get_settings()
            tp = page.get_textpage_ocr(language=settings.ocr_language, dpi=capped_dpi(page, 300), full=True)
            ocr = []
            for block in page.get_text("dict", textpage=tp)["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(ch for ch in "".join(s["text"] for s in line["spans"]) if _usable(ch, font))
                    if text.strip() and line["spans"]:
                        ocr.append((line["spans"][0]["origin"], text, line["spans"][0]["size"], line["dir"], None, (0, 0, 0)))
            log.info("page %s: %d%% of text unmappable; using OCR text layer", page.number, round(100 * bad / total))
            return ocr
    return spans


def _rules(page: fitz.Page) -> list[tuple]:
    """Straight lines and thin bars (fraction bars, table rules, underlines), in unrotated page space."""
    out = []
    for d in page.get_drawings():
        for it in d.get("items") or []:
            if it[0] == "l":
                out.append(("l", fitz.Point(it[1]), fitz.Point(it[2]), d.get("width") or 0.5))
            elif it[0] == "re":
                r = fitz.Rect(it[1])
                if min(r.width, r.height) <= 2.0 and d.get("fill") is not None:
                    out.append(("re", r))
    return out


def _redraw_rules(page: fitz.Page, rules: list[tuple]) -> None:
    """Draw the rules again fully transparent: the raster shows them, but reading the page's text
    layer still needs them (a bar between two lines of math makes them a fraction; rules outline
    table cells)."""
    if not rules:
        return
    shape = page.new_shape()
    for r in rules:
        if r[0] == "l":
            shape.draw_line(r[1], r[2])
            shape.finish(color=(0, 0, 0), width=r[3], stroke_opacity=0)
        else:
            shape.draw_rect(r[1])
            shape.finish(color=None, fill=(0, 0, 0), fill_opacity=0)
    shape.commit()


def _write_glyphs(page: fitz.Page, font: fitz.Font, glyphs: list, size: float, color) -> None:
    """Invisible text, each character at its own original pen position (keeps the color: white text
    on a white page stays recognizable as such)."""
    tw = fitz.TextWriter(page.rect)
    for ch, origin in glyphs:
        tw.append(fitz.Point(origin), ch, font=font, fontsize=max(1.0, size))
    tw.write_text(page, render_mode=3, color=color)


def flatten_pages(src: str, dst: str, pages: list[int],
                  vector_figures: dict[int, list[tuple[float, float, float, float]]] | None = None
                  ) -> dict[int, list[tuple[float, float, float, float]]]:
    """Flatten pages in place. Vector figures are re-drawn as their own image so they keep a Figure tag.
    Returns, per page, the bbox of the background raster (to mark as Artifact)."""
    vector_figures = vector_figures or {}
    settings = get_settings()
    font, _ = text_layer_font()
    doc = fitz.open(src)
    backgrounds: dict[int, list] = {}
    try:
        for pno in pages:
            page = doc[pno]
            rotation = page.rotation
            derot = page.derotation_matrix  # analysis bboxes are in visual space; flattening works unrotated
            # Text (and OCR, which must see the page upright) is collected before un-rotating;
            # its coordinates are unrotated either way.
            spans = _text_spans(page, font)
            if rotation:
                page.set_rotation(0)
            rules = _rules(page)
            rect = page.rect
            # A page of absurd size is rasterized at a lower dpi, not at gigabytes; ordinary pages at RASTER_DPI
            zoom = capped_dpi(page, settings.raster_dpi) / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, annots=False)
            images = [(info["xref"], info["bbox"]) for info in page.get_image_info(xrefs=True) if info.get("xref")]
            vector_clips = [
                (fitz.Rect(b), page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(b), alpha=False, annots=False))
                for b in [fitz.Rect(vb) * derot for vb in vector_figures.get(pno, [])]
            ]

            xref = doc.get_new_xref()
            doc.update_object(xref, "<<>>")
            doc.update_stream(xref, b" ")
            page.set_contents(xref)

            page.insert_image(rect, pixmap=pix, keep_proportion=False)
            for img_xref, bbox in images:
                try:
                    page.insert_image(fitz.Rect(bbox), xref=img_xref, keep_proportion=False)
                except Exception as exc:  # noqa: BLE001 - masked/odd images: raster already shows them
                    log.debug("could not re-draw image %s: %s", img_xref, exc)
            for clip_rect, clip_pix in vector_clips:
                page.insert_image(clip_rect, pixmap=clip_pix, keep_proportion=False)
            _redraw_rules(page, rules)
            for origin, text, size, direction, glyphs, color in spans:
                try:
                    if glyphs:
                        _write_glyphs(page, font, glyphs, size, color)
                    else:
                        # Keep the color: it's invisible either way, but white-on-white text stays recognizable
                        write_invisible_line(page, font, text, origin, size, direction, color=color)
                except Exception as exc:  # noqa: BLE001
                    log.debug("skipping span on page %s: %s", pno, exc)
            backgrounds[pno] = [tuple(rect)]
            if rotation:
                page.set_rotation(rotation)
        doc.save(dst, garbage=3, deflate=True)
    finally:
        doc.close()
    fix_inserted_cid_fonts(dst, font.name)
    return backgrounds
