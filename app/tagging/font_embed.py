"""Embed metric-compatible substitutes for unembedded standard fonts (modified output only).

Unembedded Times / Helvetica / Arial / Courier (and Times New Roman, Arial MT, Courier New) are
the most common reason a well-tagged PDF fails PDF/UA (7.21.4.1). Viewers already draw them with
a substitute font, so embedding a font with *identical metrics* (Liberation Serif / Sans / Mono)
keeps every glyph position and every tag; only the glyph shapes may differ slightly from what a
given viewer substituted. That makes it a far less destructive fix than flattening the page.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pikepdf
import pymupdf as fitz
from fontTools import agl
from pikepdf import Array, Dictionary, Name

from app.config import get_settings

log = logging.getLogger(__name__)

_DIRS = (
    "/usr/share/fonts/truetype/liberation2",
    "/usr/share/fonts/truetype/liberation",
    "/usr/local/share/fonts",
    "/Library/Fonts",
    str(Path.home() / "Library/Fonts"),
)
_FAMILY = {"serif": "LiberationSerif", "sans": "LiberationSans", "mono": "LiberationMono"}
_SUBSET = re.compile(r"^[A-Z]{6}\+")


def _font_dirs() -> list[str]:
    extra = get_settings().substitute_font_dir
    return ([extra] if extra else []) + list(_DIRS)


def substitutes_available() -> bool:
    return _find("LiberationSerif-Regular") is not None


def _find(stem: str) -> Path | None:
    for d in _font_dirs():
        p = Path(d) / f"{stem}.ttf"
        if p.is_file():
            return p
    return None


def classify(basefont: str) -> tuple[str, bool, bool] | None:
    """(family, bold, italic) for standard-font names we can substitute, else None."""
    name = _SUBSET.sub("", basefont)
    low = re.sub(r"[\s,\-_]", "", name.lower())
    if low.startswith(("times", "timesnewroman", "tinos", "nimbusroman", "liberationserif")):
        fam = "serif"
    elif low.startswith(("helvetica", "arial", "arimo", "nimbussans", "liberationsans")) \
            and "narrow" not in low and "black" not in low:
        fam = "sans"
    elif low.startswith(("courier", "cousine", "nimbusmono", "liberationmono")):
        fam = "mono"
    else:
        return None
    bold = any(w in low for w in ("bold", "black", "heavy", "demi", "semibold"))
    italic = any(w in low for w in ("italic", "oblique"))
    return fam, bold, italic


def _code_map(font: Dictionary) -> dict[int, str]:
    """Character code -> Unicode for a simple font's encoding."""
    enc = font.get("/Encoding")
    base = "cp1252"
    diffs = None
    if isinstance(enc, Name) and enc == Name.MacRomanEncoding:
        base = "mac_roman"
    elif isinstance(enc, Dictionary):
        if enc.get("/BaseEncoding") == Name.MacRomanEncoding:
            base = "mac_roman"
        diffs = enc.get("/Differences")
    out: dict[int, str] = {}
    for code in range(32, 256):
        try:
            out[code] = bytes([code]).decode(base)
        except UnicodeDecodeError:
            continue
    if isinstance(diffs, Array):
        code = 0
        for item in diffs:
            if isinstance(item, int):
                code = item
            else:
                text = agl.toUnicode(str(item)[1:])
                if text and len(text) == 1 and text != "\ufffd":
                    out[code] = text
                code += 1
    return out


def embed_standard_fonts(src: Path, dst: Path) -> list[str]:
    """Embed substitutes into src, writing dst. Returns 'Original -> Substitute' descriptions."""
    done: list[str] = []
    with pikepdf.open(src) as pdf:
        for obj in pdf.objects:
            if not isinstance(obj, Dictionary) or obj.get("/Type") != Name.Font:
                continue
            if obj.get("/Subtype") not in (Name.Type1, Name.TrueType, Name.MMType1):
                continue
            desc = obj.get("/FontDescriptor")
            if isinstance(desc, Dictionary) and any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3")):
                continue
            original = str(obj.get("/BaseFont", "/"))[1:]
            cls = classify(original)
            if cls is None:
                continue
            fam, bold, italic = cls
            style = ("Bold" if bold else "") + ("Italic" if italic else "") or "Regular"
            stem = f"{_FAMILY[fam]}-{style}"
            path = _find(stem)
            if path is None:
                continue
            font = fitz.Font(fontfile=str(path))
            codes = _code_map(obj)
            widths = []
            for code in range(256):
                ch = codes.get(code)
                widths.append(round(font.glyph_advance(ord(ch)) * 1000) if ch and font.has_glyph(ord(ch)) else 0)
            data = path.read_bytes()
            stream = pdf.make_stream(data)
            stream.Length1 = len(data)
            flags = 32 | (2 if fam == "serif" else 0) | (1 if fam == "mono" else 0) | (64 if italic else 0)
            bbox = font.bbox
            new_desc = Dictionary(
                Type=Name.FontDescriptor,
                FontName=Name("/" + stem),
                Flags=flags,
                FontBBox=Array([round(bbox.x0 * 1000), round(bbox.y0 * 1000), round(bbox.x1 * 1000), round(bbox.y1 * 1000)]),
                ItalicAngle=-12 if italic else 0,
                Ascent=round(font.ascender * 1000),
                Descent=round(font.descender * 1000),
                CapHeight=round(font.ascender * 700),
                StemV=120 if bold else 80,
                FontFile2=stream,
            )
            obj.Subtype = Name.TrueType
            obj.BaseFont = Name("/" + stem)
            obj.FirstChar = 0
            obj.LastChar = 255
            obj.Widths = Array(widths)
            obj.FontDescriptor = pdf.make_indirect(new_desc)
            if "/Encoding" not in obj:
                obj.Encoding = Name.WinAnsiEncoding
            done.append(f"{original} -> {stem}")
        if done:
            pdf.save(dst)
    log.info("embedded %d substitute font(s)", len(done))
    return done
