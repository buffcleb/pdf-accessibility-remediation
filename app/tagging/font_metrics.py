"""Pixel-identical fix for PDF/UA 7.21.5 (glyph widths inconsistent with the embedded font program).

Text is positioned by the font dictionary's /Widths, not by the font program. So the Widths entry
can be corrected to the program's value as long as every occurrence of the glyph is followed by a
TJ spacing adjustment equal to the difference: the glyph draws identically and everything after
it lands exactly where it did before.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pikepdf
from pikepdf import Array, ContentStreamInstruction, Dictionary, Name, Operator

log = logging.getLogger(__name__)

# (font base name without subset prefix, character code) -> (program width, dictionary width)
Mismatches = dict[tuple[str, int], tuple[float, float]]


def _base(name: str) -> str:
    return name.split("+", 1)[-1]


def _exact(v) -> Decimal:
    """Width as a 3-decimal Decimal: the same value goes into the width table and the TJ
    compensation, so the two cancel exactly."""
    return Decimal(str(round(float(v), 3)))


def _parse_w(w) -> dict[int, object]:
    """CIDFont /W array -> {cid: width}, keeping each original number object untouched (any
    re-rounding would shift every glyph after it). Forms: c [w1 w2 ...]  and  c_first c_last w."""
    out: dict[int, object] = {}
    items = list(w) if isinstance(w, Array) else []
    i = 0
    while i < len(items):
        first = int(items[i])
        nxt = items[i + 1] if i + 1 < len(items) else None
        if isinstance(nxt, Array):
            for k, val in enumerate(nxt):
                out[first + k] = val
            i += 2
        elif i + 2 < len(items):
            for cid in range(first, int(nxt) + 1):
                out[cid] = items[i + 2]
            i += 3
        else:
            break
    return out


def _build_w(widths: dict[int, object]) -> Array:
    """{cid: width} -> /W array of consecutive runs, values written exactly as given."""
    out = Array()
    run_start, run = None, []
    for cid in sorted(widths):
        if run_start is not None and cid == run_start + len(run):
            run.append(widths[cid])
            continue
        if run_start is not None:
            out.append(run_start)
            out.append(Array(run))
        run_start, run = cid, [widths[cid]]
    if run_start is not None:
        out.append(run_start)
        out.append(Array(run))
    return out


def fix_width_mismatches(src: Path, dst: Path, mismatches: Mismatches) -> int:
    """Returns the number of glyph occurrences compensated (0 -> dst not written)."""
    by_font: dict[str, dict[int, float]] = {}
    for (font, code), (prog, dictw) in mismatches.items():
        by_font.setdefault(_base(font), {})[code] = (prog, dictw)
    fixed_fonts: set[tuple[int, int]] = set()
    count = 0
    with pikepdf.open(src) as pdf:
        seen_streams: set[tuple[int, int]] = set()

        def font_table(resources) -> dict[str, dict]:
            fonts = resources.get("/Font") if isinstance(resources, Dictionary) else None
            out = {}
            for key, fd in (fonts.items() if isinstance(fonts, Dictionary) else []):
                two_byte = False
                names = [_base(str(fd.get("/BaseFont", "/"))[1:])]
                if fd.get("/Subtype") == Name.Type0:
                    # CID fonts: only Identity encodings, where the 2-byte code is the CID.
                    if fd.get("/Encoding") not in (Name("/Identity-H"), Name("/Identity-V")):
                        continue
                    two_byte = True
                    desc = (fd.get("/DescendantFonts") or [None])[0]
                    if desc is not None:
                        names.append(_base(str(desc.get("/BaseFont", "/"))[1:]))
                table = next((by_font[n] for n in names if n in by_font), None)
                if table:
                    out[str(key)] = (fd, table, two_byte)
            return out

        def process(owner, resources) -> None:
            nonlocal count
            if owner.is_indirect:
                if owner.objgen in seen_streams:
                    return
                seen_streams.add(owner.objgen)
            fonts = font_table(resources)
            xobjs = resources.get("/XObject") if isinstance(resources, Dictionary) else None
            if isinstance(xobjs, Dictionary):
                for _, x in xobjs.items():
                    if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == Name.Form:
                        process(x, x.get("/Resources") or resources)
            if not fonts:
                return
            ops = list(pikepdf.parse_content_stream(owner))
            out, current, changed = [], None, False
            for ins in ops:
                if not isinstance(ins, ContentStreamInstruction):
                    out.append(ins)
                    continue
                op = str(ins.operator)
                args = list(ins.operands)
                if op == "Tf" and args:
                    current = fonts.get(str(args[0]))
                if current is None or op not in ("Tj", "TJ", "'", '"'):
                    out.append(ins)
                    continue
                table = current[1]
                arr = args[-1] if isinstance(args[-1], Array) else Array([args[-1]])
                new_arr, hits = Array(), 0
                for item in arr:
                    if not isinstance(item, pikepdf.String):
                        new_arr.append(item)
                        continue
                    raw = bytes(item)
                    start = 0
                    step = 2 if current[2] else 1
                    for i in range(0, len(raw) - step + 1, step):
                        b = (raw[i] << 8 | raw[i + 1]) if step == 2 else raw[i]
                        if b in table:
                            prog, dictw = table[b]
                            new_arr.append(pikepdf.String(raw[start:i + step]))
                            delta = _exact(prog) - _exact(dictw)
                            new_arr.append(delta)  # TJ n: next glyph moves by -n/1000 em
                            start = i + step
                            hits += 1
                    if start < len(raw):
                        new_arr.append(pikepdf.String(raw[start:]))
                if not hits:
                    out.append(ins)
                    continue
                count += hits
                changed = True
                fixed_fonts.add(current[0].objgen)
                if op == '"':  # aw ac string "  ==  aw Tw ac Tc T* [..] TJ
                    out += [ContentStreamInstruction([args[0]], Operator("Tw")),
                            ContentStreamInstruction([args[1]], Operator("Tc")),
                            ContentStreamInstruction([], Operator("T*"))]
                elif op == "'":
                    out.append(ContentStreamInstruction([], Operator("T*")))
                out.append(ContentStreamInstruction([new_arr], Operator("TJ")))
            if changed:
                data = pikepdf.unparse_content_stream(out)
                if isinstance(owner, pikepdf.Stream):
                    owner.write(data)
                else:
                    owner.Contents = pdf.make_stream(data)

        for page in pdf.pages:
            res = page.obj.get("/Resources")
            node = page.obj
            while res is None and node.get("/Parent") is not None:
                node = node.Parent
                res = node.get("/Resources")
            process(page.obj, res or Dictionary())

        # Correct the width tables of the fonts we compensated.
        for obj in pdf.objects:
            if isinstance(obj, Dictionary) and obj.is_indirect and obj.objgen in fixed_fonts \
                    and obj.get("/Subtype") == Name.Type0:
                desc = obj.DescendantFonts[0]
                table = next((by_font[n] for n in (_base(str(obj.BaseFont)[1:]), _base(str(desc.get("/BaseFont", "/"))[1:]))
                              if n in by_font), {})
                widths = _parse_w(desc.get("/W"))
                for cid, (prog, _) in table.items():
                    widths[cid] = _exact(prog)
                desc.W = _build_w(widths)
        for obj in pdf.objects:
            if isinstance(obj, Dictionary) and obj.is_indirect and obj.objgen in fixed_fonts \
                    and obj.get("/Subtype") != Name.Type0:
                table = by_font[_base(str(obj.BaseFont)[1:])]
                first = int(obj.get("/FirstChar", 0))
                widths = obj.Widths
                for code, (prog, _) in table.items():
                    if 0 <= code - first < len(widths):
                        widths[code - first] = _exact(prog)
        if count:
            pdf.save(dst)
    log.info("compensated %d glyph width mismatch(es)", count)
    return count
