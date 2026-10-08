"""Glyph lists in font descriptors that don't match the embedded font (PDF/UA-1 7.21.4.2).

A font descriptor may carry an optional list of the glyphs its embedded program contains: /CIDSet for
CID fonts, /CharSet for Type 1 fonts. If it's there it must be complete, and subsetting tools often
get it wrong. The list is optional (PDF 1.7, PDF/UA-1), nothing uses it to draw, so removing an
incomplete one fixes the failure without changing how anything renders or reads.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name

from app.validation.verapdf import RuleFailure

log = logging.getLogger(__name__)

CLAUSE = "7.21.4.2"


def _plain(name: str) -> str:
    """Font name without its subset tag ("ABCDEF+Calibri" -> "Calibri")."""
    name = name.lstrip("/")
    return name.split("+", 1)[1] if len(name) > 7 and name[6] == "+" else name


def _descriptor_fonts(pdf: pikepdf.Pdf):
    """(font name, font descriptor, kind) for every font descriptor, kind being "cid" or "type1"."""
    seen: set[tuple[int, int]] = set()
    for obj in pdf.objects:
        if not isinstance(obj, Dictionary) or obj.get("/Type") != Name.Font:
            continue
        sub = obj.get("/Subtype")
        if sub in (Name.CIDFontType0, Name.CIDFontType2):
            kind = "cid"
        elif sub in (Name.Type1, Name.MMType1):
            kind = "type1"
        else:
            continue
        fd = obj.get("/FontDescriptor")
        if not isinstance(fd, Dictionary) or (fd.is_indirect and fd.objgen in seen):
            continue
        if fd.is_indirect:
            seen.add(fd.objgen)
        yield str(obj.get("/BaseFont", "/?")), fd, kind


def remove_incomplete_glyph_lists(src: Path, dst: Path, failures: list[RuleFailure]) -> list[str]:
    """Remove the /CIDSet (test 2) or /CharSet (test 1) that veraPDF reported as incomplete under
    7.21.4.2, from the fonts it named (or from every font of that kind if it named none). Writes dst
    only when something changed. Returns one description per font fixed."""
    wanted: dict[str, set[str] | None] = {}  # "cid" / "type1" -> plain font names, None = all
    for f in failures:
        if f.clause != CLAUSE:
            continue
        kind = {"1": "type1", "2": "cid"}.get(str(f.test))
        if kind is None:
            continue
        names = {_plain(n) for n in f.fonts if n}
        if kind in wanted and wanted[kind] is None:
            continue
        wanted[kind] = None if not names else (wanted.get(kind) or set()) | names
    if not wanted:
        return []
    fixed: list[str] = []
    with pikepdf.open(src) as pdf:
        for name, fd, kind in _descriptor_fonts(pdf):
            if kind not in wanted:
                continue
            names = wanted[kind]
            if names is not None and _plain(name) not in names:
                continue
            key = "/CIDSet" if kind == "cid" else "/CharSet"
            if key in fd:
                del fd[key]
                fixed.append(f"{_plain(name)}: incomplete {key[1:]} removed")
        if fixed:
            pdf.save(dst)
    log.info("removed incomplete glyph lists from %d font(s)", len(fixed))
    return fixed
