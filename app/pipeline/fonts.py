"""Locate an embeddable Unicode TrueType font for invisible text layers (OCR / flattened pages)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pymupdf as fitz

from app.config import get_settings

_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


@lru_cache
def text_layer_font() -> tuple[fitz.Font, str | None]:
    """Returns (font, fontfile path). Falls back to built-in Helvetica (not embedded)."""
    configured = get_settings().destructive_font_path
    for path in ([configured] if configured else []) + list(_CANDIDATES):
        if path and Path(path).is_file():
            return fitz.Font(fontfile=path), path
    return fitz.Font("helv"), None


def fix_inserted_cid_fonts(path: str, font_name: str) -> int:
    """PyMuPDF writes Type 2 CIDFonts without /CIDToGIDMap (PDF/UA 7.21.3.2). Its CIDs are glyph ids,
    so /Identity is exact. Only fonts we inserted (matched by name) are touched."""
    import pikepdf

    fixed = 0
    targets = {font_name, font_name.replace(" ", "")}
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        for obj in pdf.objects:
            # PyMuPDF names the Type0 parent after the font's full name; fix its descendants.
            if not isinstance(obj, pikepdf.Dictionary) or obj.get("/Subtype") != pikepdf.Name.Type0:
                continue
            if str(obj.get("/BaseFont", ""))[1:] not in targets:
                continue
            for desc in obj.get("/DescendantFonts") or []:
                if desc.get("/Subtype") == pikepdf.Name.CIDFontType2 and "/CIDToGIDMap" not in desc:
                    desc.CIDToGIDMap = pikepdf.Name.Identity
                    fixed += 1
        if fixed:
            pdf.save(path)
    return fixed
