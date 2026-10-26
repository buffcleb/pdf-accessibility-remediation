"""A tag's type changed, and the structure reshaped around it when the new type needs that."""
from __future__ import annotations

from pikepdf import Array, Dictionary, Name

from app.tagging.editor._ctx import EditError, _Ctx, _give_id, _insert_after, _parent_obj, _reparent_items, register
from app.tagging.role_rules import BLOCKS, check_role
from app.tagging.tree import STANDARD_TYPES as STANDARD
from app.tagging.tree import items as _items
from app.verification.integrity import Elem

def _set_role(ctx: _Ctx, e: Elem, role: str, n: int) -> str:
    """Change a tag's type, reshaping the structure around it when the new type needs that (see
    role_rules), or refuse a change that can't make a valid tree."""
    parent = ctx.elems[e.parent].role if e.parent is not None else None
    verdict = check_role(role, e.role, parent, [ctx.elems[k].role for k in e.kids],
                         has_content=bool(e.content_refs or e.objrs))
    if not verdict.ok:
        raise EditError(f"Edit {n}: {verdict.reason}")
    old = e.role
    if verdict.action == "collapse_list":
        made = _collapse_list(ctx, e, role, n)
        return f"element {e.index}: list -> {made} {role} tag(s), one per item"
    if verdict.action in ("wrap_list", "wrap_lbody"):
        _wrap(ctx, e, role, n)
        return f"element {e.index}: {old} -> {role} (content wrapped in {'LI > LBody' if role == 'L' else 'LBody'})"
    e.obj.S = Name("/" + role)
    if role == "Note" and e.obj.is_indirect:  # PDF/UA: a note has an ID
        _give_id(ctx, e.obj, f"note-{e.obj.objgen[0]}")
    return f"element {e.index}: {old} -> {role}"


def _kids_of(ctx: _Ctx, e: Elem) -> dict[int, Elem]:
    """Child elements by their position-free identity, to find a /K item's Elem."""
    return {ctx.elems[k].obj.objgen: ctx.elems[k] for k in e.kids if ctx.elems[k].obj.is_indirect}


def _collapse_list(ctx: _Ctx, e: Elem, role: str, n: int) -> int:
    """L -> text: each LI becomes a `role` tag holding its Lbl and LBody content (e itself becomes the
    first); lists and other blocks nested in an item, and the list's caption, follow as tags of their own."""
    def child_of(owner: Elem, item):
        if isinstance(item, Dictionary) and item.is_indirect:
            return _kids_of(ctx, owner).get(item.objgen)
        return None

    seq: list[tuple[str, list]] = []  # ("text", [(owner, item)]) or ("block", [item])
    loose: list = []  # content sitting directly in the L
    for item in _items(e.obj.get("/K")):
        child = child_of(e, item)
        if child is None:
            loose.append((e, item))
            continue
        if child.role != "LI":
            seq.append(("block", [item]))
            continue
        text, after = [], []
        for sub in _items(child.obj.get("/K")):
            part = child_of(child, sub)
            if part is not None and part.role in ("Lbl", "LBody"):
                for x in _items(part.obj.get("/K")):
                    xe = child_of(part, x)
                    if xe is not None and xe.role in BLOCKS:
                        after.append(x)
                    else:
                        text.append((part, x))
            elif part is not None and part.role in BLOCKS:
                after.append(sub)
            else:
                text.append((child, sub))
        if text:
            seq.append(("text", text))
        seq += [("block", [b]) for b in after]
    if loose:
        seq.insert(0, ("text", loose))
    if not any(kind == "text" for kind, _ in seq):
        raise EditError(f"Edit {n}: this list has no text of its own to keep as a {role}.")
    parent = _parent_obj(ctx, e)
    made: list[Dictionary] = []
    first = True
    for kind, items in seq:
        if kind == "block":
            items[0].P = parent
            made.append(items[0])
            continue
        target = e.obj if first else ctx.pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name("/" + role), P=parent))
        if not first and "/Pg" in e.obj:
            target.Pg = e.obj.Pg
        k: list = []
        for owner, x in items:
            k += _reparent_items(ctx, owner, [x], target)
        target.K = Array(k)
        if not first:  # the list's own tag becomes the first item's; a caption before it moves after it
            made.append(target)
        first = False
    e.obj.S = Name("/" + role)
    if "/A" in e.obj:
        del e.obj["/A"]  # the list attributes (ListNumbering) don't apply to text
    _insert_after(ctx, e, made, n)
    return 1 + sum(1 for m in made if m.get("/S") == Name("/" + role))


def _wrap(ctx: _Ctx, e: Elem, role: str, n: int) -> None:
    """Text -> L (L > LI > LBody) or, inside a list, text -> LI (LI > LBody), keeping the content."""
    body = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LBody))
    if "/Pg" in e.obj:
        body.Pg = e.obj.Pg
    body.K = Array(_reparent_items(ctx, e, _items(e.obj.get("/K")), body))
    if "/ActualText" in e.obj:  # it describes the text, which now sits in the LBody
        body.ActualText = e.obj.ActualText
        del e.obj["/ActualText"]
    if role == "L":
        li = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LI, P=e.obj, K=Array([body])))
        body.P = li
        e.obj.K = Array([li])
        e.obj.A = Dictionary(O=Name.List, ListNumbering=Name("/None"))
    else:
        body.P = e.obj
        e.obj.K = Array([body])
    e.obj.S = Name("/" + role)


def _check_role(op: dict, n: int) -> None:
    if op.get("role") not in STANDARD:
        raise EditError(f"Edit {n}: {op.get('role')!r} isn't a standard PDF tag.")


register("set_role", lambda ctx, op, e, t, n: _set_role(ctx, e, op["role"], n), _check_role)
