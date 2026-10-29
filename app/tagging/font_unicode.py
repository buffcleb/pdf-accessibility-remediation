"""Add missing /ToUnicode maps to simple fonts (PDF/UA 7.21.7). Rendering is unchanged.

Fonts without a Unicode map cannot be read aloud or searched. For Type1 fonts (TeX's Computer
Modern and AMS fonts above all) the glyph names are known, either from the font dictionary's
/Differences or from the font program's built-in encoding, so the map can be derived from them:
standard Adobe glyph names directly, TeX-specific names (summationdisplay, parenleftBig,
bracelefttp, ...) through the table and size-suffix rules below. Codes whose glyph name cannot be
resolved are left unmapped rather than guessed.

A simple font (Type1 or TrueType) with WinAnsi or MacRoman encoding and no map can be read through its encoding, but
not every code: the non-breaking space (WinAnsi 0xA0, MacRoman 0xCA) and the soft hyphen are named only in a footnote
of the standard's encoding table, and PAC reports them as characters it can't map (12 on a datasheet's distributor
cover page). These fonts get a map of every printable code, from the encoding itself (cp1252, Mac Roman).

A font's own map can be wrong. macOS Quartz writes one for TeX fonts that gives pi as an arrow (U+21E1), the
multiplication sign as another, the ff ligature as a return symbol, the minus sign and Delta as U+0000 (which fails
7.21.7). Where the font's /Differences name a glyph and the map says something of another kind (an arrow for a
letter, a letter for a math sign) or nothing at all, the name is believed instead.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

import pikepdf
from fontTools import agl
from pikepdf import Array, Dictionary, Name

log = logging.getLogger(__name__)

_STD_ENC = {Name.WinAnsiEncoding, Name.MacRomanEncoding, Name.StandardEncoding}
_CODECS = {Name.WinAnsiEncoding: "cp1252", Name.MacRomanEncoding: "mac_roman"}
_SYMBOLIC, _NONSYMBOLIC = 4, 32  # FontDescriptor /Flags bits
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
    # More of TeX's symbol fonts (cmsy, msam, msbm, cmex, wasy)
    "lscript": "ℓ", "Delta": "Δ",  # (the Adobe Glyph List gives Delta as U+2206 INCREMENT, read as "increment")
    "circlecopyrt": "◯", "mapsto": "↦", "square": "□", "squaresolid": "■", "planckover2pi": "ℏ", "planckover2pi1": "ℏ",
    "rightanglenw": "⌜", "rightanglene": "⌝", "rightanglesw": "⌞", "rightanglese": "⌟",
    "archleftdown": "↶", "archrightdown": "↷", "downslope": "⟍", "upslope": "⟋", "varhexagon": "⬡",
    "bracehtipdownleft": "╮", "bracehtipdownright": "╭", "bracehtipupleft": "╯", "bracehtipupright": "╰",
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


def _differences(font: Dictionary) -> dict[int, str]:
    """Code -> glyph name from the font dictionary's /Encoding /Differences."""
    names: dict[int, str] = {}
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


def _code_names(font: Dictionary) -> dict[int, str]:
    return {**_builtin_encoding(font), **_differences(font)}


def _kind(ch: str) -> str:
    """A character's kind, for telling a map's entry from its glyph's name: letter, number, punctuation, space, and
    each kind of symbol on its own (a math sign is not an arrow)."""
    cat = unicodedata.category(ch)
    return cat if cat[0] == "S" else cat[0]


def wrong_entry(have: str, named: str) -> bool:
    """Whether a map's entry for a glyph is wrong, given the text its glyph name stands for: no text at all (U+0000,
    a byte order mark, control codes), or one character of another kind than the name's. Two spellings of the same
    thing ("ff" for the ff ligature) and the same kind ("'" for a right quote) are left as the producer wrote them, and
    so is a format character: a soft hyphen for the hyphen that breaks a word at a line's end is meant."""
    if not have.strip("\x00\ufeff\ufffe") or all(unicodedata.category(c) == "Cc" for c in have):
        return True
    if any(unicodedata.category(c) == "Cf" for c in have):
        return False
    if have == named or unicodedata.normalize("NFKC", have) == unicodedata.normalize("NFKC", named):
        return False
    return len(have) == 1 and len(named) == 1 and _kind(have) != _kind(named)


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


def _encoding_map(font: Dictionary) -> dict[int, str]:
    """Code -> text for every printable code of a non-symbolic font with WinAnsi or MacRoman encoding, from the
    encoding itself; empty for any other font."""
    enc = font.get("/Encoding")
    codec = _CODECS.get(enc) if isinstance(enc, Name) else None  # (an encoding dictionary can't be a dict key)
    fd = font.get("/FontDescriptor")
    try:
        flags = int(fd.get("/Flags", 0)) if isinstance(fd, Dictionary) else 0
    except (TypeError, ValueError):
        flags = 0
    if codec is None or (flags & _SYMBOLIC and not flags & _NONSYMBOLIC):
        return {}  # a symbolic font's codes name its own glyphs, whatever the encoding says
    out = {}
    for code in range(0x20, 0x100):
        try:
            text = bytes([code]).decode(codec)
        except UnicodeDecodeError:
            continue  # codes the encoding leaves out (WinAnsi 0x81, 0x8D, 0x8F, 0x90, 0x9D)
        if text.isprintable() or text in ("\u00a0", "\u00ad"):
            out[code] = text
    return out


_HEX_SPACED = re.compile(r"<([0-9A-Fa-f\s]+)>")  # a destination may be written in groups: <00e0 f0e0>


def _entries(text: str) -> list[tuple[str, str]]:
    """Every mapping in a ToUnicode CMap of any code width, as (source code as written in hex, text)."""
    out: list[tuple[str, str]] = []
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        hexes = [re.sub(r"\s", "", h) for h in _HEX_SPACED.findall(block)]
        out += [(src, _utf16(dst)) for src, dst in zip(hexes[0::2], hexes[1::2])]
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for line in block.strip().splitlines():
            arr = re.search(r"\[(.*)\]", line)
            hexes = _HEX.findall(line.split("[")[0])
            if len(hexes) < 2:
                continue
            width, lo, hi = len(hexes[0]), int(hexes[0], 16), int(hexes[1], 16)
            if arr:
                out += [(f"{lo + k:0{width}X}", _utf16(dst)) for k, dst in enumerate(_HEX.findall(arr.group(1)))]
            elif len(hexes) >= 3 and (base := _utf16(hexes[2])):
                out += [(f"{lo + k:0{width}X}", base[:-1] + chr(ord(base[-1]) + k)) for k in range(hi - lo + 1)]
    return out


def _embedded_symbol_codes(font: Dictionary) -> dict[str, str]:
    """For a Symbol or Wingdings Type0 font with its TrueType program embedded: each CID's text, from the program's own
    symbol table (cmap 3,0, which gives each glyph its code as U+F000 plus the code), as (CID in hex, text). Used for
    the codes the document's map leaves out, which readers otherwise take from that table as private use."""
    from io import BytesIO

    from fontTools.ttLib import TTFont

    from app.pipeline.symbol_chars import unicode_for
    try:
        cid = font.DescendantFonts[0]
        program = cid.FontDescriptor.FontFile2.read_bytes()
        tt = TTFont(BytesIO(program), lazy=True)
        table = tt["cmap"].getcmap(3, 0)
    except Exception:  # noqa: BLE001 - a font program fontTools can't read gives no codes, nothing else
        return {}
    if table is None:
        return {}
    gid_cid = {}
    to_gid = cid.get("/CIDToGIDMap")
    if isinstance(to_gid, pikepdf.Stream):
        data = to_gid.read_bytes()
        gid_cid = {int.from_bytes(data[i:i + 2], "big"): i // 2 for i in range(0, len(data) - 1, 2)}
    name = str(font.get("/BaseFont", ""))
    out = {}
    for code, glyph in table.cmap.items():
        u = unicode_for(name, chr(code if code >= 0xF000 else 0xF000 + code))
        if u == chr(code) or "\ue000" <= u <= "\uf8ff":
            continue
        try:
            gid = tt.getGlyphID(glyph)
        except (KeyError, ValueError):
            continue
        out[f"{gid_cid.get(gid, gid):04X}"] = u
    return out


def symbol_tounicode(pdf: pikepdf.Pdf, font: Dictionary) -> int:
    """A font's own ToUnicode map, where it gives Symbol or Wingdings characters (or Adobe's pieces of tall brackets)
    as private use code points, rewritten to say what they are (app.pipeline.symbol_chars, #605). The map is rebuilt
    with every other entry as it was. Returns how many entries changed."""
    from app.pipeline.symbol_chars import family, unicode_for
    try:
        data = font.ToUnicode.read_bytes()
    except (pikepdf.PdfError, AttributeError):
        return 0
    name = str(font.get("/BaseFont", ""))
    if family(name) is None and not re.search(rb"<F8[EF][0-9A-F]>", data, re.I):
        return 0
    text = data.decode("latin-1")
    entries = _entries(text)
    n_own = len(entries)
    if font.get("/Subtype") == Name.Type0 and family(name):
        have = {src.upper() for src, _ in entries}
        entries += [(cid, u) for cid, u in _embedded_symbol_codes(font).items() if cid not in have]
    def said(dst: str) -> str:
        # Word writes the letter typed and the symbol it stands for ("à" then U+F0E0): the symbol alone, where it maps
        mapped = [unicode_for(name, ch) for ch in dst]
        symbols = [m for ch, m in zip(dst, mapped) if m != ch]
        return "".join(symbols) if symbols else dst
    new = [(src, said(dst)) for src, dst in entries]
    changed = sum(1 for (_, a), (_, b) in zip(entries, new) if a != b) + len(entries) - n_own
    if not changed:
        return 0
    rewrite_map(pdf, font, text, new)
    return changed


def rewrite_map(pdf: pikepdf.Pdf, font: Dictionary, text: str, entries: list[tuple[str, str]]) -> None:
    """Replace a font's ToUnicode map with `entries` (source code as written in hex, text), keeping the map's own
    header (its code space) and trailer."""
    head = re.split(r"\d+\s+beginbf(?:char|range)", text, maxsplit=1)[0]
    tail = text[max(text.rfind("endbfchar") + 9, text.rfind("endbfrange") + 10):]
    body = []
    for i in range(0, len(entries), 100):
        chunk = entries[i:i + 100]
        body.append(f"{len(chunk)} beginbfchar")
        body += [f"<{src}> <{dst.encode('utf-16-be').hex().upper()}>" for src, dst in chunk]
        body.append("endbfchar")
    font.ToUnicode = pdf.make_stream((head + "\n".join(body) + "\n" + tail).encode("latin-1"))


def add_missing_tounicode(src: Path, dst: Path) -> list[str]:
    """Write dst with ToUnicode maps added where they can be derived, Symbol and Wingdings characters a map gives
    as private use code points said as what they are (#605), and wrong maps of composite fonts corrected
    (app.tagging.wrong_maps). Returns the fonts fixed."""
    from app.tagging.wrong_maps import repair_wrong_maps

    fixed: list[str] = []
    with pikepdf.open(src) as pdf:
        fixed += repair_wrong_maps(pdf, src)
        for obj in pdf.objects:
            if not isinstance(obj, Dictionary) or obj.get("/Type") != Name.Font:
                continue
            if "/ToUnicode" in obj and (n := symbol_tounicode(pdf, obj)):
                fixed.append(f"{str(obj.get('/BaseFont', '/?'))[1:]} ({n} symbol characters said as Unicode)")
            if "/ToUnicode" not in obj and (mapping := _encoding_map(obj)):
                obj.ToUnicode = pdf.make_stream(_cmap(mapping))
                fixed.append(f"{str(obj.get('/BaseFont', '/?'))[1:]} (its {str(obj.Encoding)[1:]}, "
                             f"{len(mapping)} codes)")
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
            # Fill codes the existing map leaves out; keep its entries, except wrong ones for glyphs /Differences name
            added = {code: u for code, n in names.items() if code not in existing and (u := glyph_to_unicode(n))}
            replaced = {code: u for code, n in _differences(obj).items()
                        if code in existing and (u := glyph_to_unicode(n)) and wrong_entry(existing[code], u)}
            if not added and not replaced:
                continue
            obj.ToUnicode = pdf.make_stream(_cmap({**existing, **added, **replaced}))
            fixed.append(f"{str(obj.get('/BaseFont', '/?'))[1:]} (+{len(added)} of {len(names)} glyphs"
                         + (f", {len(replaced)} wrong entries replaced" if replaced else "")
                         + (", existing map kept" if existing and len(existing) > len(replaced) else "") + ")")
        if fixed:
            pdf.save(dst)
    log.info("added ToUnicode maps to %d font(s)", len(fixed))
    return fixed


# --------------------------------------------------------------------------- characters that are pictures
_PRIVATE_USE = 0xE000


def map_picture_glyphs(pdf: pikepdf.Pdf, used: dict[tuple[int, int], set[int]],
                       body: dict[tuple[int, int], set[int]] | None = None,
                       counts: dict[tuple[int, int], list[int]] | None = None
                       ) -> tuple[dict[str, int], set[tuple[int, int]]]:
    """Give private-use Unicode values to characters that have no meaning as text and are part of a figure or
    decoration: the note heads of a music font, the line and arc pieces of a drawing font. `used`: font (object
    number, generation) -> the character codes shown in figures and artifacts (from the content marker).

    A font needs a Unicode value for every character it shows (PDF/UA 7.21.7). For these there is none to find, and
    nothing is lost by saying so: the figure's alt text carries what they mean, and decoration isn't read. Only a code
    whose glyph name is known and means nothing is mapped; codes the font's existing map or a glyph name account
    for, and codes with no name to go by, are left alone.

    `body` and `counts` (font -> codes shown in ordinary text; font -> [characters in pictures, in text]): a font
    that shows at least nine characters in pictures for every one in text is a font of pictures, and the few it
    shows in text (a double-sharp sign in a sentence about it) are mapped too. No reader loses by it: making the
    page a picture with a text layer, the alternative, can't say what the symbol is either. Any other font's
    unreadable characters in text are left unmapped, so the text is found and read another way.

    Returns ({font name: codes mapped}, the fonts some of whose mapped codes are shown in text)."""
    body, counts = body or {}, counts or {}
    stray: set[tuple[int, int]] = set()
    done: dict[str, int] = {}
    for objgen, codes in used.items():
        in_pictures, in_text = counts.get(objgen, [1, 0])
        in_body = body.get(objgen, set()) if in_pictures >= 9 * in_text else set()
        codes = codes | in_body
        try:
            font = pdf.get_object(objgen)
        except (ValueError, pikepdf.PdfError):
            continue
        if not isinstance(font, Dictionary) or font.get("/Subtype") not in (Name.Type1, Name.MMType1, Name.Type3,
                                                                              Name.TrueType):
            continue
        existing: dict[int, str] = {}
        if "/ToUnicode" in font:
            try:
                existing = parse_tounicode(font.ToUnicode.read_bytes())
            except (pikepdf.PdfError, AttributeError):
                continue
        names = _code_names(font)
        need = set()
        for code in codes:
            if code in existing and existing[code].strip("\x00"):
                continue
            # Only a character known by a name that means nothing ("a113", "B4"): one with no name here is read
            # through the font's own encoding, which this can't see, and may well be an ordinary letter
            name = names.get(code)
            if name is None or name == ".notdef" or glyph_to_unicode(name):
                continue
            need.add(code)
        if not need:
            continue
        added = {code: chr(_PRIVATE_USE + code) for code in need}
        # With a map of its own the font is read through that map alone: keep what glyph names give the others
        named = {code: u for code, n in names.items() if code not in existing and (u := glyph_to_unicode(n))}
        font.ToUnicode = pdf.make_stream(_cmap({**named, **existing, **added}))
        name = str(font.get("/BaseFont", font.get("/Name", f"/unnamed font {objgen[0]}")))[1:]
        done[name] = done.get(name, 0) + len(added)
        if need & in_body:
            stray.add(objgen)
    return done, stray
