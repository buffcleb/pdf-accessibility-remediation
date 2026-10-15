"""Check marks in form fields, drawn as shapes.

A checkbox or radio button's "on" appearance is usually one ZapfDingbats character (a check, a cross, a dot). ZapfDingbats
is one of the fonts every viewer has, so forms almost never embed it, and PDF/UA wants every font used for rendering
embedded with a Unicode mapping (7.21.4.1, 7.21.7). No metric-compatible ZapfDingbats is free to embed, so the mark is
drawn as a shape of the same size in the same place.

Only appearances that aren't showing are touched (the "on" state of a box that isn't checked), so the pages look
exactly the same; a box that's already checked keeps its glyph, and the validator's finding, for a person to decide.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
from pikepdf import ContentStreamInstruction, Dictionary, Name, Stream

_K = 0.5523  # control-point distance for a quarter circle


def _n(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _shape(char: str, x: float, y: float, s: float, stroke: bytes) -> bytes | None:
    """The mark as path operators: origin (x, y), font size s, in the current fill color (stroke: the same color as
    a stroking operator). Proportions follow ZapfDingbats' own glyphs."""
    p = lambda fx, fy: f"{_n(x + fx * s)} {_n(y + fy * s)}"  # noqa: E731
    if char == "4":  # check mark
        return (stroke + f" {_n(0.12 * s)} w 1 J 1 j {p(0.10, 0.33)} m {p(0.33, 0.07)} l {p(0.78, 0.68)} l S".encode())
    if char in "58":  # cross
        return (stroke + f" {_n(0.12 * s)} w 1 J {p(0.10, 0.07)} m {p(0.68, 0.65)} l S "
                         f"{p(0.10, 0.65)} m {p(0.68, 0.07)} l S".encode())
    if char == "l":  # filled circle (radio buttons)
        cx, cy, r = x + 0.395 * s, y + 0.36 * s, 0.35 * s
        k = _K * r
        return (f"{_n(cx + r)} {_n(cy)} m {_n(cx + r)} {_n(cy + k)} {_n(cx + k)} {_n(cy + r)} {_n(cx)} {_n(cy + r)} c "
                f"{_n(cx - k)} {_n(cy + r)} {_n(cx - r)} {_n(cy + k)} {_n(cx - r)} {_n(cy)} c "
                f"{_n(cx - r)} {_n(cy - k)} {_n(cx - k)} {_n(cy - r)} {_n(cx)} {_n(cy - r)} c "
                f"{_n(cx + k)} {_n(cy - r)} {_n(cx + r)} {_n(cy - k)} {_n(cx + r)} {_n(cy)} c f").encode()
    if char == "n":  # filled square
        return f"{_n(x + 0.035 * s)} {_n(y + 0.014 * s)} {_n(0.69 * s)} {_n(0.69 * s)} re f".encode()
    if char == "u":  # filled diamond
        return f"{p(0.395, 0.71)} m {p(0.755, 0.35)} l {p(0.395, -0.01)} l {p(0.035, 0.35)} l h f".encode()
    return None


def _is_dingbats(stream: Stream, font: bytes) -> bool:
    res = stream.get("/Resources")
    fonts = res.get("/Font") if res is not None else None
    f = fonts.get("/" + font.decode("latin-1")) if fonts is not None else None
    if f is None:
        return False
    desc = f.get("/FontDescriptor")
    embedded = desc is not None and any(k in desc for k in ("/FontFile", "/FontFile2", "/FontFile3"))
    return "ZapfDingbats" in str(f.get("/BaseFont", "")) and not embedded


_FILL_OPS = {"g": "G", "rg": "RG", "k": "K"}


def _redraw(stream: Stream) -> bool:
    """Replace the stream's one text object, if it shows a single ZapfDingbats glyph, with the glyph's shape."""
    try:
        ops = list(pikepdf.parse_content_stream(stream))
    except pikepdf.PdfError:
        return False
    names = [str(i.operator) if isinstance(i, ContentStreamInstruction) else "" for i in ops]
    if names.count("BT") != 1 or names.count("ET") != 1:
        return False
    start, end = names.index("BT"), names.index("ET")
    font, size, x, y, char = None, 0.0, 0.0, 0.0, None
    for ins in ops[start + 1:end]:
        op, args = str(ins.operator), list(ins.operands)
        if op == "Tf" and len(args) == 2 and font is None:
            font, size = str(args[0])[1:].encode("latin-1"), float(args[1])
        elif op in ("Td", "TD") and len(args) == 2:
            x, y = x + float(args[0]), y + float(args[1])
        elif op == "Tm" and len(args) == 6 and [float(v) for v in args[:4]] == [1, 0, 0, 1]:
            x, y = float(args[4]), float(args[5])
        elif op == "Tj" and len(args) == 1 and char is None and len(bytes(args[0])) == 1:
            char = bytes(args[0]).decode("latin-1")
        elif op not in ("TL", "g", "rg", "k"):
            return False  # more than one glyph, a scaled text matrix, or something else going on
    if font is None or char is None or not size or not _is_dingbats(stream, font):
        return False
    # The mark takes the fill color in force where the text was; black if none was set
    fill = next((i for i in reversed(ops[:end]) if isinstance(i, ContentStreamInstruction)
                 and str(i.operator) in _FILL_OPS), None)
    if fill is None:
        stroke = b"0 G"
    else:
        stroke = " ".join(_n(float(v)) for v in fill.operands).encode() + b" " + _FILL_OPS[str(fill.operator)].encode()
    shape = _shape(char, x, y, size, stroke)
    if shape is None:
        return False
    before = pikepdf.unparse_content_stream(ops[:start])
    after = pikepdf.unparse_content_stream(ops[end + 1:])
    stream.write(before + b"\nq " + shape + b" Q\n" + after)
    fonts = stream.Resources.get("/Font")
    del fonts["/" + font.decode("latin-1")]
    if not list(fonts.keys()):
        del stream.Resources["/Font"]
    return True


def redraw_dingbat_marks(src: Path, dst: Path) -> int:
    """Rewrite the "on" appearances of unchecked boxes that show one unembedded ZapfDingbats glyph as shapes. Returns
    how many fields were changed (dst is only written when some were)."""
    changed = 0
    with pikepdf.open(src) as pdf:
        # Appearance streams can be shared between fields: one that's showing anywhere is left alone everywhere
        seen: set[tuple[int, int]] = set()
        for page in pdf.pages:
            for annot in page.get("/Annots") or []:
                ap = annot.get("/AP") if isinstance(annot, Dictionary) else None
                normal = ap.get("/N") if isinstance(ap, Dictionary) else None
                if isinstance(normal, Dictionary) and not isinstance(normal, Stream):
                    shown = normal.get(str(annot.get("/AS", "/Off")))
                    if isinstance(shown, Stream):
                        seen.add(shown.objgen)
        redrawn: set[tuple[int, int]] = set()
        for page in pdf.pages:
            for annot in page.get("/Annots") or []:
                if not isinstance(annot, Dictionary) or annot.get("/Subtype") != Name.Widget:
                    continue
                ap = annot.get("/AP")
                normal = ap.get("/N") if isinstance(ap, Dictionary) else None
                if not isinstance(normal, Dictionary) or isinstance(normal, Stream):
                    continue  # a single appearance (text fields, buttons): always showing
                showing = str(annot.get("/AS", "/Off"))
                field_changed = False
                # The normal appearance's other states, and every state of the pressed and rollover appearances
                # (shown only while someone is pressing or pointing at the box)
                for which in ("/N", "/D", "/R"):
                    states = ap.get(which)
                    if not isinstance(states, Dictionary) or isinstance(states, Stream):
                        continue
                    for state in list(states.keys()):
                        stream = states[state]
                        if (which == "/N" and state == showing) or not isinstance(stream, Stream) or stream.objgen in seen:
                            continue
                        seen.add(stream.objgen)
                        if _redraw(stream):
                            redrawn.add(stream.objgen)
                    field_changed |= any(isinstance(st, Stream) and st.objgen in redrawn
                                         for st in (states[k] for k in states.keys()))
                changed += field_changed
        if changed:
            pdf.save(dst)
    return changed
