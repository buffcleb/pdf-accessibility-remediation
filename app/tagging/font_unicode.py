"""Add missing /ToUnicode maps to simple fonts (PDF/UA 7.21.7). Rendering is unchanged.

Fonts without a Unicode map cannot be read aloud or searched. For Type1 fonts (TeX's Computer
Modern and AMS fonts above all) the glyph names are known, either from the font dictionary's
/Differences or from the font program's built-in encoding, so the map can be derived from them:
standard Adobe glyph names directly, TeX-specific names (summationdisplay, parenleftBig,
bracelefttp, ...) through the table and size-suffix rules below. Codes whose glyph name cannot be
resolved are left unmapped rather than guessed.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pikepdf
from fontTools import agl
from pikepdf import Array, Dictionary, Name

log = logging.getLogger(__name__)

_STD_ENC = {Name.WinAnsiEncoding, Name.MacRomanEncoding, Name.StandardEncoding}
_DUP = re.compile(rb"dup\s+(\d+)\s*/([^\s/]+)\s+put")
_SIZE_SUFFIX = re.compile(r"(?:big|Big|bigg|Bigg|text|display|small|Small)$")

# TeX extension-font pieces and names outside the Adobe Glyph List.
_TEX = {
    "bracelefttp": "⎧", "braceleftmid": "⎨", "braceleftbt": "⎩", "bracerighttp": "⎫",
    "bracerightmid": "⎬", "bracerightbt": "⎭", "braceex": "⎪",
    "bracketlefttp": "⎡", "bracketleftex": "⎢", "bracketleftbt": "⎣",
    "bracketrighttp": "⎤", "bracketrightex": "⎥", "bracketrightbt": "⎦",
    "parenlefttp": "⎛", "parenleftex": "⎜", "parenleftbt": "⎝",
    "parenrighttp": "⎞", "parenrightex": "⎟", "parenrightbt": "⎠",
    "radicalbt": "⎷", "radicalvertex": "⎸", "radicaltp": "⎺",
    "arrowvertex": "⏐", "arrowtp": "↑", "arrowbt": "↓", "arrowdblvertex": "‖", "arrowdbltp": "⇑", "arrowdblbt": "⇓",
    "vextendsingle": "|", "vextenddouble": "‖", "mapstochar": "↦", "negationslash": "̸",
    "summation": "∑", "product": "∏", "coproduct": "∐", "integral": "∫", "contintegral": "∮",
    "union": "⋃", "intersection": "⋂", "unionmulti": "⨄", "unionsq": "⨆",
    "logicaland": "⋀", "logicalor": "⋁", "circledot": "⨀", "circleplus": "⨁", "circlemultiply": "⨂",
    "radical": "√", "angbracketleft": "⟨", "angbracketright": "⟩",
    "floorleft": "⌊", "floorright": "⌋", "ceilingleft": "⌈", "ceilingright": "⌉",
    "hatwide": "ˆ", "hatwider": "ˆ", "hatwidest": "ˆ", "tildewide": "˜", "tildewider": "˜", "tildewidest": "˜",
    "slash": "/", "backslash": "\\", "dotlessi": "ı", "dotlessj": "ȷ", "ff": "ﬀ", "fi": "ﬁ", "fl": "ﬂ",
    "ffi": "ﬃ", "ffl": "ﬄ", "visiblespace": "␣", "emptyset": "∅", "prime": "′", "infinity": "∞",
}


def _agl(name: str) -> str | None:
    """Adobe Glyph List lookup (also uniXXXX, uXXXXX and ligature_names)."""
    text = agl.toUnicode(name)
    return text if text and "\ufffd" not in text and "\x00" not in text else None


def glyph_to_unicode(name: str) -> str | None:
    if not name or name == ".notdef":
        return None
    if name in _TEX:
        return _TEX[name]
    if text := _agl(name):
        return text
    base = name
    for _ in range(3):  # parenleftBigg -> parenleft, summationdisplay -> summation
        stripped = _SIZE_SUFFIX.sub("", base)
        if stripped == base or not stripped:
            break
        base = stripped
        if base in _TEX:
            return _TEX[base]
        if text := _agl(base):
            return text
    return None


def _builtin_encoding(font: Dictionary) -> dict[int, str]:
    """Code -> glyph name from an embedded Type1 program's built-in /Encoding."""
    desc = font.get("/FontDescriptor")
    ff = desc.get("/FontFile") if isinstance(desc, Dictionary) else None
    if not isinstance(ff, pikepdf.Stream):
        return {}
    try:
        data = ff.read_bytes()
    except pikepdf.PdfError:
        return {}
    clear = data[: int(ff.get("/Length1", len(data)))]
    return {int(c): n.decode("latin-1") for c, n in _DUP.findall(clear) if int(c) < 256}


def _code_names(font: Dictionary) -> dict[int, str]:
    names = _builtin_encoding(font)
    enc = font.get("/Encoding")
    if isinstance(enc, Dictionary) and isinstance(enc.get("/Differences"), Array):
        code = 0
        for item in enc.Differences:
            if isinstance(item, int):
                code = item
            else:
                names[code] = str(item)[1:]
                code += 1
    return names


_HEX = re.compile(r"<([0-9A-Fa-f]+)>")


def _utf16(hexstr: str) -> str:
    try:
        return bytes.fromhex(hexstr).decode("utf-16-be")
    except (ValueError, UnicodeDecodeError):
        return ""


def parse_tounicode(data: bytes) -> dict[int, str]:
    """Single-byte ToUnicode CMap -> {code: text} (bfchar, bfrange with start value or array)."""
    text = data.decode("latin-1")
    out: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        hexes = _HEX.findall(block)
        for src, dst in zip(hexes[0::2], hexes[1::2]):
            out[int(src, 16)] = _utf16(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for line in block.strip().splitlines():
            arr = re.search(r"\[(.*)\]", line)
            hexes = _HEX.findall(line.split("[")[0])
            if len(hexes) < 2:
                continue
            lo, hi = int(hexes[0], 16), int(hexes[1], 16)
            if arr:
                for k, dst in enumerate(_HEX.findall(arr.group(1))):
                    out[lo + k] = _utf16(dst)
            elif len(hexes) >= 3:
                base = _utf16(hexes[2])
                for k in range(hi - lo + 1):
                    out[lo + k] = base[:-1] + chr(ord(base[-1]) + k) if base else ""
    return {c: t for c, t in out.items() if c < 256 and t}


def _cmap(mapping: dict[int, str]) -> bytes:
    lines = [
        "/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
        "1 begincodespacerange", "<00> <FF>", "endcodespacerange",
    ]
    items = sorted(mapping.items())
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for code, text in chunk:
            hexes = "".join(f"{b:02X}" for b in text.encode("utf-16-be"))
            lines.append(f"<{code:02X}> <{hexes}>")
        lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return "\n".join(lines).encode()


def add_missing_tounicode(src: Path, dst: Path) -> list[str]:
    """Write dst with ToUnicode maps added where they can be derived. Returns the fonts fixed."""
    fixed: list[str] = []
    with pikepdf.open(src) as pdf:
        for obj in pdf.objects:
            if not isinstance(obj, Dictionary) or obj.get("/Type") != Name.Font:
                continue
            if obj.get("/Subtype") not in (Name.Type1, Name.MMType1):
                continue
            enc = obj.get("/Encoding")
            existing: dict[int, str] = {}
            if "/ToUnicode" in obj:
                try:
                    existing = parse_tounicode(obj.ToUnicode.read_bytes())
                except (pikepdf.PdfError, AttributeError):
                    continue
            elif isinstance(enc, Name) and enc in _STD_ENC:
                continue  # standard encodings are already mappable
            names = _code_names(obj)
            # Keep every existing entry; only fill codes the existing map leaves out.
            added = {code: u for code, n in names.items() if code not in existing and (u := glyph_to_unicode(n))}
            if not added:
                continue
            obj.ToUnicode = pdf.make_stream(_cmap({**existing, **added}))
            fixed.append(f"{str(obj.get('/BaseFont', '/?'))[1:]} (+{len(added)} of {len(names)} glyphs"
                         + (", existing map kept" if existing else "") + ")")
        if fixed:
            pdf.save(dst)
    log.info("added ToUnicode maps to %d font(s)", len(fixed))
    return fixed
