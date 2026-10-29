"""Areas of content: text split inside a piece (in the content stream), content moved between tags, an area cut out of
a tag's content, and artifacts tagged."""
from __future__ import annotations

from pathlib import Path

import pikepdf
from pikepdf import Array, ContentStreamInstruction, Dictionary, Name, Operator, String

from app.tagging.content_marker import apply as apply_matrix
from app.tagging.content_marker import mat_mul, mcid_of
from app.tagging.editor._ctx import (
    CONTENT_ROLES, MAX_TEXT, NEW_AREA_ROLES, EditError, _Ctx, _detach, _insert_after, _MARK, _new_sibling, _page_obj,
    _parent_obj, _reparent_items, _role_of, _same, register, WAS_MCID,
)
from app.tagging.role_rules import GROUPING, check_move
from app.tagging.tree import is_elem, new_mcr, number_tree_get
from app.tagging.tree import items as _items
from app.verification.integrity import Elem

# --- splitting inside a piece of text -----------------------------------------------------------------

def _split_inside(ctx: _Ctx, e: Elem, k: int, char: int, n: int) -> str:
    """Split e inside its k-th /K item, a piece of text, before character `char` of that piece's text.
    The marked-content sequence is closed before the glyph that starts there and a new one (new MCID)
    opened, splitting the text-showing operator if needed; the new sequence and e's later items become
    a new tag right after e."""
    cant = f"Edit {n}: this text can't be split inside; split between its pieces instead."
    items = _items(e.obj.get("/K"))
    if not 0 <= k < len(items):
        raise EditError(f"Edit {n}: this tag can't be split there.")
    item = items[k]
    if isinstance(item, int):
        mcid, pg = item, _page_obj(ctx, e)
    elif isinstance(item, Dictionary) and mcid_of(item) is not None and item.get("/Stm") is None:
        mcid, pg = mcid_of(item), item.get("/Pg") if item.get("/Pg") is not None else _page_obj(ctx, e)
    else:
        raise EditError(cant)
    if pg is None or ctx.path is None:
        raise EditError(cant)
    page_no = next((i for i, p in enumerate(ctx.pdf.pages) if p.obj.objgen == pg.objgen), None)
    key = pg.get("/StructParents")
    if page_no is None or key is None:
        raise EditError(cant)
    glyphs = _mcid_glyphs(ctx.path, page_no, mcid)
    g = _glyph_at(glyphs, char)
    if g is None:
        raise EditError(f"Edit {n}: pick a point inside the text, not at its start or end.")

    page = ctx.pdf.pages[page_no]
    instrs = list(pikepdf.parse_content_stream(page))
    used = {int(ins.operands[1].MCID) for ins in instrs
            if isinstance(ins, ContentStreamInstruction) and str(ins.operator) == "BDC" and len(ins.operands) == 2
            and isinstance(ins.operands[1], Dictionary) and "/MCID" in ins.operands[1]}
    new_mcid = max(used | {mcid}) + 1
    fonts = page.resources.get("/Font")
    out, counted = _split_stream(instrs, mcid, g, new_mcid, fonts if isinstance(fonts, Dictionary) else Dictionary())
    if out is None or counted != ctx.cut_glyphs.get((pg.objgen, mcid), len(glyphs)):
        raise EditError(cant)
    ctx.cut_glyphs[(pg.objgen, mcid)] = g  # what's left of it: the glyphs before the cut
    pg.Contents = ctx.pdf.make_stream(pikepdf.unparse_content_stream(out))

    pt = ctx.root.get("/ParentTree")
    arr = number_tree_get(pt, int(key)) if isinstance(pt, Dictionary) else None
    if not isinstance(arr, Array):
        raise EditError(cant)

    def give(new: Dictionary) -> list:
        while len(arr) <= new_mcid:
            arr.append(pikepdf.Object.parse(b"null"))
        arr[new_mcid] = new
        return [new_mcr(pg, new_mcid)] + _reparent_items(ctx, e, items[k + 1:], new)

    _new_sibling(ctx, e, items[:k + 1], give, n)
    note = ""
    if "/ActualText" in e.obj:  # it described the whole text; it no longer matches either half
        del e.obj["/ActualText"]
        note = "; its read-aloud text was removed, set it again on each part if needed"
    return f"element {e.index} ({e.role}) split inside its text at character {char}{note}"


def _mcid_glyphs(path: Path, page_no: int, mcid: int) -> list[dict]:
    """The glyphs of one marked-content sequence, in drawing order (as the tag map reads them)."""
    import importlib

    import pdfplumber

    importlib.import_module("app.verification.semantic")  # patches pdfplumber's marked-content tracking

    with pdfplumber.open(path, pages=[page_no + 1]) as plumb:
        return [c for c in plumb.pages[0].chars if c.get("mcid") == mcid and c.get("tag") != "Artifact"]


def _glyph_at(glyphs: list[dict], char: int) -> int | None:
    """The glyph that starts at character `char` of the piece's text (as the tag map shows it: whitespace collapsed,
    a space where a line ends), or the next one; None at either end."""
    from app.verification.semantic import glyph_text

    _, starts = glyph_text(glyphs)
    return next((gi for gi, at in enumerate(starts) if gi > 0 and at >= char), None)


def _codes(font) -> int | None:
    """Bytes per character code for a font, or None when it can't be known without decoding its CMap."""
    if not isinstance(font, Dictionary):
        return None
    if font.get("/Subtype") == Name.Type0:
        return 2 if font.get("/Encoding") in (Name("/Identity-H"), Name("/Identity-V")) else None
    return 1


def _split_stream(instrs: list, mcid: int, g: int, new_mcid: int, fonts: Dictionary):
    """Close marked-content `mcid` before its g-th glyph and open `new_mcid` there. Returns the new
    instructions and the number of glyphs counted in the sequence, or (None, 0) when it can't be done."""
    out: list = []
    stack: list[bool] = []  # one entry per open marked-content section: is it the target?
    font_stack: list = []
    font = None
    seen = 0
    done = False
    props = None
    for ins in instrs:
        if not isinstance(ins, ContentStreamInstruction):
            out.append(ins)
            continue
        op = str(ins.operator)
        if op in ("BDC", "BMC"):
            target = (op == "BDC" and len(ins.operands) == 2 and isinstance(ins.operands[1], Dictionary)
                      and mcid_of(ins.operands[1]) == mcid)
            if target:
                props = (ins.operands[0], ins.operands[1])
            stack.append(target)
        elif op == "EMC":
            if stack:
                stack.pop()
        elif op == "q":
            font_stack.append(font)
        elif op == "Q":
            font = font_stack.pop() if font_stack else font
        elif op == "Tf" and ins.operands:
            font = fonts.get(ins.operands[0])
        inside = any(stack)
        if op in _SHOW and inside:
            bpc = _codes(font)
            if bpc is None:
                return None, 0
            if op == "TJ":
                strings = [bytes(x) for x in ins.operands[0] if isinstance(x, pikepdf.String)]
            else:
                strings = [bytes(ins.operands[-1])]
            count = sum(len(b) // bpc for b in strings)
            if not done and seen + count > g:
                if not stack[-1]:  # the split point is inside a nested section (e.g. a ligature's Span)
                    return None, 0
                cut = (g - seen) * bpc
                before, after = _cut_show(ins, op, cut)
                if before is not None:
                    out.append(before)
                out.append(ContentStreamInstruction([], Operator("EMC")))
                out.append(ContentStreamInstruction([props[0], _with_mcid(props[1], new_mcid)], Operator("BDC")))
                out.append(after)
                done = True
                seen += count
                continue
            seen += count
        elif op == "Do" and inside:
            return None, 0  # content drawn through a form: its glyphs aren't in this stream
        out.append(ins)
    return (out, seen) if done else (None, 0)


def _with_mcid(props: Dictionary, mcid: int) -> Dictionary:
    d = Dictionary({k: v for k, v in props.items() if k not in ("/MCID", "/ActualText", "/Alt")})
    d.MCID = mcid
    return d


def _cut_show(ins, op: str, cut: int):
    """Split a text-showing instruction `cut` bytes into its text. Drawing the two parts one after the
    other is the same as drawing the original: the pen moves on after each."""
    if op == "TJ":
        first, second, left = [], [], cut
        for x in ins.operands[0]:
            if left <= 0 and (second or isinstance(x, pikepdf.String)):
                second.append(x)
            elif isinstance(x, pikepdf.String):
                b = bytes(x)
                if len(b) <= left:
                    first.append(x)
                    left -= len(b)
                else:
                    if left:
                        first.append(pikepdf.String(b[:left]))
                    second.append(pikepdf.String(b[left:]))
                    left = 0
            else:
                first.append(x)
        before = ContentStreamInstruction([Array(first)], Operator("TJ")) if first else None
        return before, ContentStreamInstruction([Array(second)], Operator("TJ"))
    b = bytes(ins.operands[-1])
    head, tail = pikepdf.String(b[:cut]), pikepdf.String(b[cut:])
    after = ContentStreamInstruction([tail], Operator("Tj"))
    if cut == 0:
        # Split before the operator: it still has to do its line move and spacing ('/")
        return None, ContentStreamInstruction(list(ins.operands[:-1]) + [tail], ins.operator)
    return ContentStreamInstruction(list(ins.operands[:-1]) + [head], ins.operator), after


# --- areas: moving content between tags, tagging artifacts ------------------------------------------

def _page_array(ctx: _Ctx, page, create: bool = False):
    """The ParentTree array for a page's content (MCID -> tag); made if create and the page has none."""
    pt = ctx.root.get("/ParentTree")
    if not isinstance(pt, Dictionary):
        if not create:
            return None
        pt = ctx.pdf.make_indirect(Dictionary(Nums=Array()))
        ctx.root.ParentTree = pt
    key = page.get("/StructParents")
    if key is not None:
        arr = number_tree_get(pt, int(key))
        if isinstance(arr, Array) or not create:
            return arr
    if not create:
        return None
    # A new key, one past the highest in use
    used = []

    def collect(node, depth=0):
        if depth > 32 or not isinstance(node, Dictionary):
            return
        nums = node.get("/Nums")
        if isinstance(nums, Array):
            used.extend(int(nums[i]) for i in range(0, len(nums) - 1, 2))
        for kid in _items(node.get("/Kids")):
            collect(kid, depth + 1)
    collect(pt)
    new_key = int(key) if key is not None else max(used + [int(ctx.root.get("/ParentTreeNextKey", 0)) - 1]) + 1
    arr = ctx.pdf.make_indirect(Array())
    holder = pt
    while not isinstance(holder.get("/Nums"), Array) and _items(holder.get("/Kids")):
        holder = _items(holder.get("/Kids"))[-1]
    if not isinstance(holder.get("/Nums"), Array):
        holder.Nums = Array()
    holder.Nums.extend([new_key, arr])
    if isinstance(holder.get("/Limits"), Array):
        holder.Limits = Array([min(int(holder.Limits[0]), new_key), max(int(holder.Limits[1]), new_key)])
    page.StructParents = new_key
    ctx.root.ParentTreeNextKey = max(int(ctx.root.get("/ParentTreeNextKey", 0)), new_key + 1)
    return arr


def _set_parent(arr, mcid: int, elem) -> None:
    while len(arr) <= mcid:
        arr.append(pikepdf.Object.parse(b"null"))
    arr[mcid] = elem


def _assign(ctx: _Ctx, e: Elem, pieces: list[dict], n: int) -> str:
    """Change the area a tag covers: pieces of tagged content move from their tags into this one. Content a tag
    deleted earlier in this save held (an artifact until the save) is marked content again, in this tag (#692)."""
    if e.role not in CONTENT_ROLES:
        raise EditError(f"Edit {n}: a {e.role} holds other tags, not content of its own. Select a paragraph, heading, "
                        "figure, cell or list item body to give it this content.")
    moved, emptied = 0, []
    back: dict = {}  # untagged content to mark again, by page
    for piece in pieces:
        if piece["page"] > len(ctx.pdf.pages):
            raise EditError(f"Edit {n}: page {piece['page']} doesn't exist.")
        page = ctx.pdf.pages[piece["page"] - 1].obj
        users = ctx.checker.index.refs.get((page.objgen, piece["mcid"]))
        if not users and (page.objgen, piece["mcid"]) in ctx.untagged:
            back.setdefault(page.objgen, (page, set()))[1].add(piece["mcid"])
            continue
        if not users:
            raise EditError(f"Edit {n}: that piece of content on page {piece['page']} isn't tagged (an artifact can "
                            "be tagged with \"Tag as\" instead).")
        owner = ctx.elems[users[0]]
        if owner.index == e.index:
            continue
        # Take the reference out of the tag holding it
        kept = []
        for it in _items(owner.obj.get("/K")):
            is_it = False
            if isinstance(it, int) or (not isinstance(it, Dictionary) and not isinstance(it, Array)):
                try:
                    is_it = int(it) == piece["mcid"] and _same_page(owner.obj, page)
                except (TypeError, ValueError):
                    is_it = False
            elif isinstance(it, Dictionary) and "/MCID" in it and "/Stm" not in it:
                pg = it.get("/Pg") or owner.obj.get("/Pg")
                is_it = mcid_of(it) == piece["mcid"] and pg is not None and pg.objgen == page.objgen
            if not is_it:
                kept.append(it)
        owner.obj.K = Array(kept)
        # ...and into this one: a bare MCID on its own page, else a reference naming the page
        if _same_page(e.obj, page):
            e.obj.K = Array(_items(e.obj.get("/K")) + [piece["mcid"]])
        else:
            e.obj.K = Array(_items(e.obj.get("/K")) + [new_mcr(page, piece["mcid"])])
            if "/Pg" not in e.obj:
                e.obj.Pg = page
        arr = _page_array(ctx, page)
        if isinstance(arr, Array):
            _set_parent(arr, piece["mcid"], e.obj)
        ctx.checker.index.refs[(page.objgen, piece["mcid"])] = [e.index]
        moved += 1
        if not kept and owner not in emptied:
            emptied.append(owner)
    for page, mcids in back.values():
        _retag(ctx, page, mcids, e.role, n)
        arr = _page_array(ctx, page, create=True)
        for m in sorted(mcids):
            e.obj.K = Array(_items(e.obj.get("/K")) + [m if _same_page(e.obj, page) else new_mcr(page, m)])
            if "/Pg" not in e.obj:
                e.obj.Pg = page
            _set_parent(arr, m, e.obj)
            ctx.checker.index.refs[(page.objgen, m)] = [e.index]
            ctx.untagged.discard((page.objgen, m))
            moved += 1
    # Content in the order it's drawn, when the tag holds only content
    items = _items(e.obj.get("/K"))
    if items and not any(is_elem(it) for it in items):
        page_no = {p.obj.objgen: i for i, p in enumerate(ctx.pdf.pages)}

        def order(it):
            pg = (it.get("/Pg") if isinstance(it, Dictionary) else None) or e.obj.get("/Pg")
            mcid = mcid_of(it)
            return (page_no.get(pg.objgen, 0) if pg is not None else 0, -1 if mcid is None else mcid)
        if all(isinstance(it, Dictionary) and "/MCID" in it and "/Stm" not in it or not isinstance(it, Dictionary)
               for it in items):
            e.obj.K = Array(sorted(items, key=order))
    for gone in emptied:
        if not _items(gone.obj.get("/K")) and not gone.objrs:
            _detach(ctx, gone)
    return (f"element {e.index} ({e.role}) now covers {moved} more piece(s) of content"
            + (f"; {len(emptied)} tag(s) left empty were removed" if emptied else ""))


def _take_pieces(ctx: _Ctx, e: Elem, op: dict, n: int) -> str:
    """Some of e's pieces of content become a new tag right after it (one drawing out of a figure of several)."""
    pieces = op["pieces"]
    for piece in pieces:
        page = ctx.pdf.pages[piece["page"] - 1].obj if piece["page"] <= len(ctx.pdf.pages) else None
        users = ctx.checker.index.refs.get((page.objgen, piece["mcid"])) if page is not None else None
        if page is not None and (page.objgen, piece["mcid"]) in ctx.untagged:
            raise EditError(f"Edit {n}: a piece of content on page {piece['page']} isn't in this {e.role} any more: an "
                            "earlier change in this save deleted the tag with its content. Save first, then tag the "
                            "content from what's untagged.")
        if not users or users[0] != e.index:
            raise EditError(f"Edit {n}: a piece of content on page {piece['page']} isn't part of this {e.role}.")
    mine = {(p, m) for (p, m), users in ctx.checker.index.refs.items() if users and users[0] == e.index}
    if len(mine) <= len({(p["page"], p["mcid"]) for p in pieces}):
        raise EditError(f"Edit {n}: that would take everything out of this {e.role}. Leave some of its content in it, "
                        "or change the tag itself instead.")
    parent = _parent_obj(ctx, e)
    new = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + op["role"]), P=parent, K=Array([])))
    for key in ("/Pg", "/Lang"):
        if key in e.obj:
            new[key] = e.obj[key]
    if str(op.get("alt") or "").strip():
        new.Alt = String(op["alt"].strip())
    _insert_after(ctx, e, [new], n)
    # A record for the new tag, so later edits in this save find the pieces' new owner
    made = Elem(index=len(ctx.elems), obj=new, role=op["role"], raw_role=op["role"], page=e.page, parent=e.parent)
    ctx.elems.append(made)
    ctx.new_tags = [new]
    _assign(ctx, made, pieces, n)
    return f"element {e.index} ({e.role}): {len(pieces)} piece(s) of content made into a new {op['role']} after it"


# --- cutting an area out of a tag's content ---------------------------------------------------------------

_PATH = {"m", "l", "c", "v", "y", "h", "re"}
_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}
_SHOW = {"Tj", "TJ", "'", '"'}


def _cut_objects(instrs: list, start: int, end: int, ctm: list[float]) -> list[tuple[int, int, tuple[float, float] | None]]:
    """The instructions start..end-1 (inside one marked section) as drawn things: a path up to its painting, a
    q..Q group, a text object, an image. Each is (first, last+1, its middle in PDF space, or None for what draws
    nothing on its own: colours, line widths, which go with the thing after them)."""
    out = []
    i = start
    stack = [ctm]
    pending = None  # the first of the state instructions waiting for the next drawn thing
    while i < end:
        op, _ = _op(instrs[i])
        first = pending if pending is not None else i
        if op == "q" or op == "BT":
            # a group: to its matching Q or ET, with everything it draws
            close, depth, j = ("Q" if op == "q" else "ET"), 0, i
            pts: list[tuple[float, float]] = []
            m, tm = stack[-1], [1.0, 0, 0, 1.0, 0, 0]
            inner = [m]
            while j < end:
                o, ops = _op(instrs[j])
                if o == op:
                    depth += 1
                    if o == "q":
                        inner.append(inner[-1])
                elif o == close:
                    depth -= 1
                    if o == "Q" and len(inner) > 1:
                        inner.pop()
                    if depth == 0:
                        break
                elif o == "cm" and len(ops) == 6:
                    inner[-1] = mat_mul(ops, inner[-1])
                elif o in _PATH and ops:
                    pts += [apply_matrix(inner[-1], ops[k], ops[k + 1]) for k in range(0, len(ops) - 1, 2)]
                    if o == "re" and len(ops) == 4:
                        pts.append(apply_matrix(inner[-1], ops[0] + ops[2], ops[1] + ops[3]))
                elif o == "Tm" and len(ops) == 6:
                    tm = ops
                elif o in ("Td", "TD") and len(ops) == 2:
                    tm = mat_mul([1, 0, 0, 1, ops[0], ops[1]], tm)
                elif o in _SHOW:
                    pts.append(apply_matrix(mat_mul(tm, inner[-1]), 0, 0))  # where the text starts
                elif o in ("Do", "INLINE"):
                    pts += [apply_matrix(inner[-1], 0, 0), apply_matrix(inner[-1], 1, 1)]  # an image fills the unit square
                j += 1
            out.append((first, j + 1, _middle(pts)))
            pending, i = None, j + 1
            continue
        if op in _PATH:
            pts = []
            j = i
            while j < end:
                o, ops = _op(instrs[j])
                if o in _PATH:
                    pts += [apply_matrix(stack[-1], ops[k], ops[k + 1]) for k in range(0, len(ops) - 1, 2)]
                    if o == "re" and len(ops) == 4:
                        pts.append(apply_matrix(stack[-1], ops[0] + ops[2], ops[1] + ops[3]))
                elif o in _PAINT:
                    break
                j += 1
            out.append((first, j + 1, _middle(pts)))
            pending, i = None, j + 1
            continue
        if op in ("Do", "INLINE"):
            out.append((first, i + 1, _middle([apply_matrix(stack[-1], 0, 0), apply_matrix(stack[-1], 1, 1)])))
            pending, i = None, i + 1
            continue
        if op == "cm":
            raise EditError("cm")  # a change of coordinates outside a q..Q group: not cut (see _cut_area)
        if pending is None:
            pending = i
        i += 1
    if pending is not None:
        out.append((pending, end, None))
    return out


def _op(ins) -> tuple[str, list[float]]:
    """An instruction's operator ("INLINE" for an inline image) and its number operands."""
    if not isinstance(ins, ContentStreamInstruction):
        return "INLINE", []
    return str(ins.operator), [float(v) for v in ins.operands if _num(v)]


def _num(v) -> bool:
    try:
        float(v)
        return not isinstance(v, (pikepdf.Name, pikepdf.String, pikepdf.Array, pikepdf.Dictionary))
    except (TypeError, ValueError):
        return False


def _middle(pts: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def _cut_area(ctx: _Ctx, e: Elem, op: dict, n: int) -> str:
    """What of e's content on a page lies inside a box becomes a new tag right after e."""
    cant = f"Edit {n}: this content can't be cut by area"
    if e.role not in CONTENT_ROLES or not e.obj.is_indirect or ctx.path is None:
        raise EditError(f"{cant}; select the figure or text that holds it.")
    if op["page"] > len(ctx.pdf.pages):
        raise EditError(f"Edit {n}: page {op['page']} doesn't exist.")
    page = ctx.pdf.pages[op["page"] - 1]
    pg = page.obj
    if int(pg.get("/Rotate", 0)) % 360:
        raise EditError(f"{cant} on a rotated page yet.")
    mine = sorted(m for (og, m), users in ctx.checker.index.refs.items() if og == pg.objgen and users and users[0] == e.index)
    if not mine:
        raise EditError(f"Edit {n}: this {e.role} has nothing on page {op['page']}.")
    # The box, from the tag map's page units (top-left origin, as the viewer draws them) to PDF space
    import pymupdf
    with pymupdf.open(ctx.path) as doc:
        to_pdf = ~doc[op["page"] - 1].transformation_matrix
    x0, y0, x1, y1 = op["bbox"]
    corners = [pymupdf.Point(x, y) * to_pdf for x, y in ((x0, y0), (x1, y1))]
    bx0, bx1 = sorted(c.x for c in corners)
    by0, by1 = sorted(c.y for c in corners)
    inside = lambda pt: pt is not None and bx0 <= pt[0] <= bx1 and by0 <= pt[1] <= by1  # noqa: E731

    instrs = list(pikepdf.parse_content_stream(page))
    used = {int(i.operands[1].MCID) for i in instrs if isinstance(i, ContentStreamInstruction) and str(i.operator) == "BDC"
            and len(i.operands) == 2 and isinstance(i.operands[1], Dictionary) and "/MCID" in i.operands[1]}
    next_mcid = max(used | set(mine)) + 1
    out: list = []
    runs: list[tuple[int, int, bool]] = []  # (original MCID, MCID of this run, inside the box)
    stack = [[1.0, 0, 0, 1.0, 0, 0]]
    i = 0
    while i < len(instrs):
        ins = instrs[i]
        o = str(ins.operator) if isinstance(ins, ContentStreamInstruction) else ""
        if o == "q":
            stack.append(stack[-1])
        elif o == "Q" and len(stack) > 1:
            stack.pop()
        elif o == "cm":
            vals = [float(v) for v in ins.operands]
            stack[-1] = mat_mul(vals, stack[-1])
        mcid = (int(ins.operands[1].MCID) if o == "BDC" and len(ins.operands) == 2 and isinstance(ins.operands[1], Dictionary)
                and "/MCID" in ins.operands[1] else None)
        if mcid is None or mcid not in mine:
            out.append(ins)
            i += 1
            continue
        # e's section: find its EMC, cut what's inside it into runs that are in or out of the box
        depth, j = 0, i + 1
        while j < len(instrs):
            oj = str(instrs[j].operator) if isinstance(instrs[j], ContentStreamInstruction) else ""
            if oj in ("BDC", "BMC"):
                depth += 1
            elif oj == "EMC":
                if depth == 0:
                    break
                depth -= 1
            j += 1
        if depth:
            raise EditError(f"{cant}: a marked section on that page isn't closed.")
        try:
            things = _cut_objects(instrs, i + 1, j, stack[-1])
        except EditError:
            raise EditError(f"{cant}: it changes its coordinates part way through.") from None
        sides: list[bool] = []
        for k, (_, _, mid) in enumerate(things):
            if mid is None:  # draws nothing itself: with the next thing drawn, else the last
                nxt = next((m for _, _, m in things[k + 1:] if m is not None), None)
                sides.append(inside(nxt) if nxt is not None else (sides[-1] if sides else False))
            else:
                sides.append(inside(mid))
        props = ins.operands[1]
        tag = ins.operands[0]
        k = 0
        first = True
        while k < len(things):
            side = sides[k]
            m2 = k
            while m2 + 1 < len(things) and sides[m2 + 1] == side:
                m2 += 1
            run_mcid = mcid if first else next_mcid
            if not first:
                next_mcid += 1
            p2 = Dictionary({key: props[key] for key in props.keys()})
            p2.MCID = run_mcid
            out.append(ContentStreamInstruction([tag, p2], Operator("BDC")))
            out += instrs[things[k][0]:things[m2][1]]
            out.append(ContentStreamInstruction([], Operator("EMC")))
            runs.append((mcid, run_mcid, side))
            first, k = False, m2 + 1
        if not things:
            out += instrs[i:j + 1]
            runs.append((mcid, mcid, False))
        i = j + 1
    if not any(side for _, _, side in runs):
        raise EditError(f"Edit {n}: the box holds none of this {e.role}'s content on page {op['page']}.")
    if all(side for _, _, side in runs):
        raise EditError(f"Edit {n}: the box holds all of this {e.role}'s content on page {op['page']}; change the tag "
                        "itself instead.")
    pg.Contents = ctx.pdf.make_stream(pikepdf.unparse_content_stream(out))

    # The new tag, right after e, and each run's MCID pointed at the tag that holds it
    parent = _parent_obj(ctx, e)
    new = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + op["role"]), P=parent, K=Array([]), Pg=pg))
    if "/Lang" in e.obj:
        new.Lang = e.obj.Lang
    if str(op.get("alt") or "").strip():
        new.Alt = String(op["alt"].strip())
    _insert_after(ctx, e, [new], n)
    made = Elem(index=len(ctx.elems), obj=new, role=op["role"], raw_role=op["role"], page=op["page"] - 1, parent=e.parent)
    ctx.elems.append(made)
    ctx.new_tags = [new]
    arr = _page_array(ctx, pg, create=True)
    ref = lambda m, owner: m if _same_page(owner, pg) else new_mcr(pg, m)  # noqa: E731
    kept: list = []
    for item in _items(e.obj.get("/K")):
        m = mcid_of(item) if not isinstance(item, int) else int(item)
        on_page = isinstance(item, int) and _same_page(e.obj, pg) or (isinstance(item, Dictionary) and "/MCID" in item
                                                                         and item.get("/Stm") is None
                                                                         and (item.get("/Pg") or e.obj.get("/Pg")) is not None
                                                                         and (item.get("/Pg") or e.obj.get("/Pg")).objgen == pg.objgen)
        if not on_page or m not in mine:
            kept.append(item)
            continue
        for orig, run, side in runs:
            if orig != m:
                continue
            if side:
                new.K = Array(_items(new.get("/K")) + [ref(run, new)])
                _set_parent(arr, run, new)
                ctx.checker.index.refs[(pg.objgen, run)] = [made.index]
            else:
                kept.append(ref(run, e.obj))
                _set_parent(arr, run, e.obj)
                ctx.checker.index.refs[(pg.objgen, run)] = [e.index]
    e.obj.K = Array(kept)
    inside_n = sum(1 for _, _, side in runs if side)
    return f"element {e.index} ({e.role}): the content inside the box on page {op['page']} made into a new {op['role']} after it ({inside_n} section(s))"


def _same_page(obj, page) -> bool:
    pg = obj.get("/Pg")
    return pg is not None and pg.is_indirect and page.is_indirect and pg.objgen == page.objgen


def _check_place(ctx: _Ctx, role: str, parent, n: int) -> None:
    """A new tag of this role may go under parent."""
    at_root = parent is ctx.root or (parent.is_indirect and ctx.root.is_indirect and parent.objgen == ctx.root.objgen)
    v = check_move(role, "Document", None if at_root else _role_of(ctx, parent), False)
    if not v.ok:
        raise EditError(f"Edit {n}: a new {role} can't go there. {v.reason}")


def _mark_sections(ctx: _Ctx, page_no: int, wanted: set[int], role: str, n: int):
    """Artifact sections of a page's stream (numbered as in the tag map) become marked content of this role, each
    with a new MCID. Returns the page and the MCIDs, for the tag that is to hold them."""
    from app.tagging.content_marker import existing_mcids

    if page_no > len(ctx.pdf.pages):
        raise EditError(f"Edit {n}: page {page_no} doesn't exist.")
    # Sections are numbered as in the tag map; ones tagged by earlier edits are no longer artifacts
    if ctx.tagged_sections is None:
        ctx.tagged_sections = {}
    done = ctx.tagged_sections.setdefault(page_no, set())
    if wanted & done:
        raise EditError(f"Edit {n}: artifact section {min(wanted & done)} on page {page_no} is already tagged by an "
                        "earlier change.")
    sections = {s - sum(1 for d in done if d < s) for s in wanted}
    page = ctx.pdf.pages[page_no - 1]
    ops = list(pikepdf.parse_content_stream(page.obj))
    next_mcid = max(existing_mcids(ops) or {-1}) + 1
    new_mcids, seq, stack, found = [], 0, [], set()
    for i, ins in enumerate(ops):
        if not isinstance(ins, ContentStreamInstruction):
            continue
        name = str(ins.operator)
        if name in ("BDC", "BMC"):
            args = list(ins.operands)
            has_mcid = name == "BDC" and len(args) == 2 and isinstance(args[1], Dictionary) and "/MCID" in args[1]
            is_art = bool(args) and isinstance(args[0], Name) and str(args[0]) == "/Artifact" and not has_mcid
            chosen = False
            if is_art:
                if seq in sections:
                    if any(tag for tag, _ in stack):
                        raise EditError(f"Edit {n}: artifact section {seq} on page {page_no} is inside another "
                                        "marked section; select the section around it instead.")
                    ops[i] = ContentStreamInstruction([Name("/" + role), Dictionary(MCID=next_mcid)], Operator("BDC"))
                    new_mcids.append(next_mcid)
                    next_mcid += 1
                    found.add(seq)
                    chosen = True
                seq += 1
            elif has_mcid and any(c for _, c in stack):
                raise EditError(f"Edit {n}: an artifact section you chose on page {page_no} has tagged content "
                                "inside it, so it can't become a tag of its own.")
            stack.append((has_mcid or is_art, chosen))
        elif name == "EMC" and stack:
            stack.pop()
    missing = sections - found
    if missing:
        raise EditError(f"Edit {n}: page {page_no} has no artifact section {min(missing)} (the document may have "
                        "changed; reload and try again).")
    done |= wanted
    page.obj.Contents = ctx.pdf.make_stream(pikepdf.unparse_content_stream(ops))
    return page, new_mcids


def _tag_artifacts(ctx: _Ctx, anchor: Elem, op: dict, n: int) -> str:
    """Tag a new area: artifact sections of a page's stream become one new tag, next to the anchor tag."""
    role = op["role"]
    parent = _parent_obj(ctx, anchor)
    _check_place(ctx, role, parent, n)
    page, new_mcids = _mark_sections(ctx, op["page"], set(op["sections"]), role, n)
    elem = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + role), P=parent, Pg=page.obj,
                                            K=Array(new_mcids)))
    alt = str(op.get("alt") or "").strip()
    if alt:
        elem.Alt = String(alt)
    arr = _page_array(ctx, page.obj, create=True)
    for m in new_mcids:
        _set_parent(arr, m, elem)
    if not anchor.obj.is_indirect:
        anchor.obj[_MARK] = True
    kids = _items(parent.get("/K"))
    at = next((k for k, it in enumerate(kids) if _same(it, anchor.obj)), None)
    if _MARK in anchor.obj:
        del anchor.obj[_MARK]
    if at is None:
        raise EditError(f"Edit {n}: the tag to place it next to wasn't found under its parent.")
    kids.insert(at if op["position"] == "before" else at + 1, elem)
    parent.K = Array(kids)
    ctx.new_tags = [elem]
    return (f"page {op['page']}: {len(new_mcids)} artifact section(s) tagged as a new {role}, "
            f"{op['position']} element {anchor.index} ({anchor.role})")


def _regroup(ctx: _Ctx, anchor: Elem, op: dict, n: int) -> str:
    """An area's content regrouped into new tags, in order (the AI's proposal for an area, checked by a person): each
    group's pieces of tagged content move from their tags into it, a tag left with nothing being removed, and its
    untagged sections are tagged into it. A figure's box is a part of a picture the page draws (a poster's artwork):
    that part is drawn again as the figure's own content (picture_parts). The new tags go before the tag, at block
    level, that held the first of the pieces; with no pieces, next to the anchor as tag_artifacts places a new tag,
    or at the end of it ("in": the document's top tag, when every tag near the area has been deleted)."""
    page_no, groups = op["page"], op["groups"]
    if page_no > len(ctx.pdf.pages):
        raise EditError(f"Edit {n}: page {page_no} doesn't exist.")
    page = ctx.pdf.pages[page_no - 1].obj
    tagged = lambda m: bool(ctx.checker.index.refs.get((page.objgen, m)))  # noqa: E731
    for g in groups:
        for m in g.get("pieces") or []:
            # Content of a tag deleted earlier in this save is tagged again from its artifact section
            if not tagged(m) and (page.objgen, m) not in ctx.untagged:
                raise EditError(f"Edit {n}: a piece of content on page {page_no} isn't tagged any more: an earlier "
                                "change in this save took it out. Ask for new tags after saving.")
    at, position = anchor, op["position"]
    first = next((ctx.elems[ctx.checker.index.refs[(page.objgen, m)][0]] for g in groups for m in g.get("pieces") or []
                  if tagged(m)), None)
    if first is not None:
        # The tag holding the first piece, or the tag around it that sits straight in a grouping tag (a Link or Span
        # in a paragraph: the paragraph), so a new paragraph doesn't go inside a paragraph
        e = first
        while e.parent is not None and ctx.elems[e.parent].role not in GROUPING:
            e = ctx.elems[e.parent]
        if e.parent is not None:
            at, position = e, "before"
    parent = at.obj if position == "in" else _parent_obj(ctx, at)
    # Still in the tree: each tag up from it is among its parent's kids (a deleted tag keeps its /P)
    obj, attached = at.obj, not at.obj.is_indirect  # a direct one can't be told apart this way: trusted
    for _ in range(0 if attached else 10000):
        up = obj.get("/P") if isinstance(obj, Dictionary) else None
        if not isinstance(up, Dictionary) or not any(_same(k, obj) for k in _items(up.get("/K"))):
            break
        if up.get("/Type") == Name.StructTreeRoot:
            attached = True
            break
        obj = up
    if not attached:
        raise EditError(f"Edit {n}: the tag to place the new tags next to was removed by an earlier change in this "
                        "save; choose the area again.")
    for g in groups:
        _check_place(ctx, g["role"], parent, n)
    # The tags around each piece now, to remove those the move leaves with nothing in them (a list whose items all
    # went, a section of only a figure that went)
    around = []
    for g in groups:
        for m in [m for m in g.get("pieces") or [] if tagged(m)]:
            e = ctx.elems[ctx.checker.index.refs[(page.objgen, m)][0]]
            chain = []
            while e.parent is not None:
                e = ctx.elems[e.parent]
                chain.append(e.obj)
            around.append(chain)
    made = []
    for g in groups:
        new = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + g["role"]), P=parent, Pg=page,
                                               K=Array([])))
        if str(g.get("alt") or "").strip():
            new.Alt = String(g["alt"].strip())
        if "/Lang" in at.obj:
            new.Lang = at.obj.Lang
        made.append(new)
    # Placed first, all together: moving the pieces can empty (and so remove) the tag they were placed by
    if not at.obj.is_indirect:
        at.obj[_MARK] = True
    kids = _items(parent.get("/K"))
    pos = len(kids) if position == "in" else next((k for k, it in enumerate(kids) if _same(it, at.obj)), None)
    if _MARK in at.obj:
        del at.obj[_MARK]
    if pos is None:
        raise EditError(f"Edit {n}: the tag to place them next to wasn't found under its parent.")
    pos = pos if position in ("before", "in") else pos + 1
    parent.K = Array(kids[:pos] + made + kids[pos:])
    ctx.new_tags = list(made)
    for g, new in zip(groups, made):
        elem = Elem(index=len(ctx.elems), obj=new, role=g["role"], raw_role=g["role"], page=page_no - 1,
                    parent=at.parent)
        ctx.elems.append(elem)
        if g.get("box"):
            from app.tagging.editor.picture_parts import redraw_part
            m = redraw_part(ctx, page_no, g["box"], g["role"], n)
            new.K = Array(_items(new.get("/K")) + [m])
            _set_parent(_page_array(ctx, page, create=True), m, new)
            ctx.checker.index.refs[(page.objgen, m)] = [elem.index]
        if g.get("sections"):
            _, mcids = _mark_sections(ctx, page_no, set(g["sections"]), g["role"], n)
            new.K = Array(_items(new.get("/K")) + mcids)
            arr = _page_array(ctx, page, create=True)
            for m in mcids:
                _set_parent(arr, m, new)
                ctx.checker.index.refs[(page.objgen, m)] = [elem.index]
        back = [m for m in g.get("pieces") or [] if not tagged(m)]
        if back:
            _retag(ctx, page, set(back), g["role"], n)
            new.K = Array(_items(new.get("/K")) + back)
            arr = _page_array(ctx, page, create=True)
            for m in back:
                _set_parent(arr, m, new)
                ctx.checker.index.refs[(page.objgen, m)] = [elem.index]
                ctx.untagged.discard((page.objgen, m))
        if [m for m in g.get("pieces") or [] if m not in back]:
            _assign(ctx, elem, [{"page": page_no, "mcid": m} for m in g["pieces"] if m not in back], n)
    removed = 0
    for chain in around:
        for obj in chain:
            up = obj.get("/P")
            if _items(obj.get("/K")) or not isinstance(up, Dictionary) or obj.get("/S") is None:
                break
            up.K = Array([k for k in _items(up.get("/K")) if not _same(k, obj)])
            removed += 1
    names = ", ".join(g["role"] for g in groups)
    return f"page {page_no}: the area's content regrouped into {len(groups)} new tag(s) ({names})" + (
        f"; {removed} tag(s) left empty were removed" if removed else "")


def _retag(ctx: _Ctx, page, mcids: set[int], role: str, n: int) -> None:
    """Content an earlier edit in this save made artifacts (its tag deleted) is marked content again, with its MCID."""
    ops = list(pikepdf.parse_content_stream(page))
    found = set()
    for i, ins in enumerate(ops):
        if (isinstance(ins, ContentStreamInstruction) and str(ins.operator) == "BDC" and len(ins.operands) == 2
                and isinstance(ins.operands[1], Dictionary) and ins.operands[1].get(WAS_MCID) is not None
                and int(ins.operands[1][WAS_MCID]) in mcids):
            m = int(ins.operands[1][WAS_MCID])
            ops[i] = ContentStreamInstruction([Name("/" + role), Dictionary(MCID=m)], Operator("BDC"))
            found.add(m)
    if mcids - found:
        raise EditError(f"Edit {n}: a piece of content a deleted tag held wasn't found on its page; save first, then "
                        "tag it from what's untagged.")
    page.Contents = ctx.pdf.make_stream(pikepdf.unparse_content_stream(ops))


def _check_regroup(op: dict, n: int) -> None:
    groups = op.get("groups")
    if (not isinstance(op.get("page"), int) or op["page"] < 1 or op.get("position") not in ("before", "after", "in")
            or not isinstance(groups, list) or not 1 <= len(groups) <= 300):
        raise EditError(f"Edit {n}: new tags for an area need the page, the tags and where they go.")
    pieces, sections = set(), set()
    for g in groups:
        if not isinstance(g, dict):
            raise EditError(f"Edit {n}: each new tag needs the content it holds.")
        ps, ss, box = g.get("pieces") or [], g.get("sections") or [], g.get("box")
        if box is not None and (g.get("role") not in ("Figure", "Formula") or not isinstance(box, list) or len(box) != 4
                                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)
                                or box[2] - box[0] < 1 or box[3] - box[1] < 1):
            raise EditError(f"Edit {n}: a part of a picture is made a figure or formula, from a box (left, top, right, "
                            "bottom).")
        if (not isinstance(ps, list) or not isinstance(ss, list) or not (ps or ss or box)
                or not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in ps + ss)):
            raise EditError(f"Edit {n}: each new tag needs the content it holds.")
        if pieces & set(ps) or sections & set(ss) or len(set(ps)) < len(ps) or len(set(ss)) < len(ss):
            raise EditError(f"Edit {n}: a piece of content can go in only one new tag.")
        pieces |= set(ps)
        sections |= set(ss)
        _check_new_tag(g, n)
    if len(pieces) > 2000 or len(sections) > 500:
        raise EditError(f"Edit {n}: that's too much content for one area; draw a smaller one.")


def _check_pieces(op: dict, n: int) -> None:
    """assign and take_pieces: the pieces of content, from the tag map."""
    pieces = op.get("pieces")
    if (not isinstance(pieces, list) or not pieces or len(pieces) > 2000
            or not all(isinstance(x, dict) and isinstance(x.get("page"), int) and x["page"] >= 1
                       and isinstance(x.get("mcid"), int) and x["mcid"] >= 0 for x in pieces)):
        raise EditError(f"Edit {n}: changing a tag's area needs the pieces of content to give it."
                        if op["op"] == "assign" else f"Edit {n}: making a tag from pieces of another needs the pieces.")


def _check_new_tag(op: dict, n: int) -> None:
    """take_pieces and cut_area: the new tag's type, and the alt text a figure or formula needs."""
    if op.get("role") not in NEW_AREA_ROLES:
        raise EditError(f"Edit {n}: the new tag can be {', '.join(sorted(NEW_AREA_ROLES))}.")
    if op["role"] in ("Figure", "Formula") and not str(op.get("alt") or "").strip():
        raise EditError(f"Edit {n}: a {op['role'].lower()} needs alt text describing it.")
    if len(str(op.get("alt") or "")) > MAX_TEXT:
        raise EditError(f"Edit {n}: text must be at most {MAX_TEXT} characters.")


def _check_take_pieces(op: dict, n: int) -> None:
    _check_pieces(op, n)
    _check_new_tag(op, n)


def _check_cut_area(op: dict, n: int) -> None:
    box = op.get("bbox")
    if (not isinstance(op.get("page"), int) or op["page"] < 1 or not isinstance(box, list) or len(box) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)
            or box[2] - box[0] < 1 or box[3] - box[1] < 1):
        raise EditError(f"Edit {n}: cutting an area needs the page and a box (left, top, right, bottom).")
    _check_new_tag(op, n)


def _check_tag_artifacts(op: dict, n: int) -> None:
    sections = op.get("sections")
    if (not isinstance(op.get("page"), int) or op["page"] < 1 or not isinstance(sections, list) or not sections
            or len(sections) > 500 or not all(isinstance(x, int) and x >= 0 for x in sections)
            or op.get("position") not in ("before", "after")):
        raise EditError(f"Edit {n}: tagging an area needs the page, the sections and where the new tag goes.")
    if op.get("role") not in NEW_AREA_ROLES:
        raise EditError(f"Edit {n}: a new area can be tagged as {', '.join(sorted(NEW_AREA_ROLES))}.")
    if op["role"] in ("Figure", "Formula") and not str(op.get("alt") or "").strip():
        raise EditError(f"Edit {n}: a {op['role'].lower()} needs alt text describing it.")
    if len(str(op.get("alt") or "")) > MAX_TEXT:
        raise EditError(f"Edit {n}: text must be at most {MAX_TEXT} characters.")


register("assign", lambda ctx, op, e, t, n: _assign(ctx, e, op["pieces"], n), _check_pieces)
register("take_pieces", lambda ctx, op, e, t, n: _take_pieces(ctx, e, op, n), _check_take_pieces)
register("cut_area", lambda ctx, op, e, t, n: _cut_area(ctx, e, op, n), _check_cut_area)
register("tag_artifacts", lambda ctx, op, e, t, n: _tag_artifacts(ctx, e, op, n), _check_tag_artifacts)
register("regroup", lambda ctx, op, e, t, n: _regroup(ctx, e, op, n), _check_regroup)
