"""Fonts that draw nothing.

Acrobat's text recognition writes its invisible text layer in a font of its own ("HiddenHorzOCR") whose program
holds one glyph: an empty "missing glyph". Every character of that layer therefore refers to the missing glyph,
which PDF/UA forbids (7.21.8), hidden text included, though nothing is drawn either way.

Such a font gets a program with one more glyph, as empty as the first, and every character is pointed at it. The
page looks exactly as it did (nothing was drawn, nothing is drawn), widths and the text a reader hears are
untouched, and the pages needn't be flattened.

Only a composite font whose embedded program has the missing glyph alone, and that glyph empty, is touched.
"""
from __future__ import annotations

import io
import logging

import pikepdf
from pikepdf import Array, Dictionary, Name

log = logging.getLogger(__name__)


def _only_an_empty_missing_glyph(data: bytes) -> bool:
    from fontTools.cffLib import CFFFontSet

    try:
        cff = CFFFontSet()
        cff.decompile(io.BytesIO(data), None)
        top = cff[cff.fontNames[0]]
        names = list(top.CharStrings.keys())
        if len(names) != 1:
            return False
        glyph = top.CharStrings[names[0]]
        glyph.decompile()
        return [op for op in glyph.program if isinstance(op, str)] in ([], ["endchar"])
    except Exception:  # noqa: BLE001 - a program that can't be read is left as it is
        return False


def _blank_program(width: int) -> bytes:
    """A TrueType font of two empty glyphs, each `width` thousandths of the text size wide."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.ttLib.tables._g_l_y_f import Glyph

    names = [".notdef", "blank"]
    fb = FontBuilder(unitsPerEm=1000, isTTF=True)
    fb.setupGlyphOrder(names)
    fb.setupCharacterMap({})
    fb.setupGlyf({n: Glyph() for n in names})
    fb.setupHorizontalMetrics({n: (width, 0) for n in names})
    fb.setupHorizontalHeader(ascent=1000, descent=-250)
    fb.setupNameTable({"familyName": "Blank", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    out = io.BytesIO()
    fb.save(out)
    return out.getvalue()


def fill_blank_fonts(pdf: pikepdf.Pdf) -> list[str]:
    """Give every font that can only draw the missing glyph a second, equally empty glyph to draw. Returns the
    names of the fonts changed."""
    done: list[str] = []
    programs: dict[int, pikepdf.Object] = {}
    to_blank = None
    seen: set = set()
    converted: dict = {}  # descriptors (shared between fonts) already given the new program -> its width
    for obj in pdf.objects:
        if not isinstance(obj, Dictionary) or obj.get("/Type") != Name.Font or obj.get("/Subtype") != Name.Type0:
            continue
        kids = obj.get("/DescendantFonts")
        cid = kids[0] if isinstance(kids, Array) and len(kids) else None
        if not isinstance(cid, Dictionary) or cid.get("/Subtype") != Name.CIDFontType0:
            continue
        if cid.is_indirect:
            if cid.objgen in seen:
                continue
            seen.add(cid.objgen)
        desc = cid.get("/FontDescriptor")
        if not isinstance(desc, Dictionary) or "/W" in cid:
            continue  # widths of its own would have to match the new program glyph by glyph
        width = int(cid.get("/DW", 1000))
        shared = desc.objgen if desc.is_indirect else None
        if shared is None or converted.get(shared) != width:
            if shared in converted:
                continue  # one descriptor, two default widths: one program can't serve both
            program = desc.get("/FontFile3")
            if program is None:
                continue
            try:
                data = program.read_bytes()
            except Exception:  # noqa: BLE001
                continue
            if len(data) > 4096 or not _only_an_empty_missing_glyph(data):
                continue
            if width not in programs:
                ttf = _blank_program(width)
                stream = pdf.make_stream(ttf)
                stream.Length1 = len(ttf)
                programs[width] = stream
            del desc["/FontFile3"]
            desc.FontFile2 = programs[width]
            if shared is not None:
                converted[shared] = width
        if to_blank is None:
            to_blank = pdf.make_stream(b"\x00\x01" * 65536)  # every character -> glyph 1, the empty one
        cid.Subtype = Name.CIDFontType2
        cid.CIDToGIDMap = to_blank
        done.append(str(obj.get("/BaseFont", "/"))[1:])
    if done:
        log.info("fonts with only an empty missing glyph given an empty glyph to draw: %s", sorted(set(done)))
    return done
