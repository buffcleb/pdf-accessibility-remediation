"""Apply people's tag edits from the portal's tag viewer.

Edits change the structure tree only. Content streams are untouched, so pages look exactly the same.
The one exception is "decorative", which relabels the element's marked content as an artifact; that
changes no drawing operators either.

Each operation names an element by its index in the source file's tag map (the pre-order index the
verifier and the tag map use) and the role it expects there. Every element is resolved to its PDF
object before any edit is applied, so moves can't shift the targets of later operations.

Operations:
    {"op": "set_role", "element": 12, "expect_role": "P", "role": "H2"}
    {"op": "set_alt", "element": 30, "expect_role": "Figure", "alt": "Bar chart of ..."}   ("" removes it)
    {"op": "set_actual_text", "element": 31, "expect_role": "Formula", "text": "x squared"}
    {"op": "set_decorative", "element": 30, "expect_role": "Figure"}
    {"op": "set_lang", "element": 40, "expect_role": "P", "lang": "fr-CA"}                ("" removes it)
    {"op": "set_cell", "element": 77, "expect_role": "TD", "role": "TH", "scope": "Column"}
    {"op": "move", "element": 15, "expect_role": "P", "target": 9, "position": "before"}
    {"op": "merge", "element": 20, "expect_role": "P", "absorb": [21, 22]}   (21 and 22 join 20, in that order)
    {"op": "split", "element": 30, "expect_role": "P", "at": 2}   (the /K items from position 2 become a new tag)
    {"op": "split", "element": 30, "expect_role": "P", "at": 0, "char": 14}
        (split inside text: /K item 0 is split before character 14 of its text, as the tag map shows it,
        and that remainder plus any later items become a new tag. The content stream is split at that
        glyph, which draws exactly the same.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Array, ContentStreamInstruction, Dictionary, Name, Operator, String

from app.tagging.role_rules import BLOCKS, check_role
from app.verification.integrity import STANDARD, Elem, IntegrityChecker, _items

OPS = {"set_role", "set_alt", "set_actual_text", "set_decorative", "set_lang", "set_cell", "move", "merge", "split"}
SCOPES = {"Row", "Column", "Both"}
MAX_OPS = 5000
MAX_TEXT = 4000
_LANG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")
_MARK = Name("/RemediationEditMark")


class EditError(ValueError):
    """An edit can't be applied; the message is safe to show to the person who made it."""


@dataclass
class _Ctx:
    pdf: pikepdf.Pdf
    root: Dictionary
    checker: IntegrityChecker
    elems: list[Elem]
    path: Path | None = None


def validate_ops(ops) -> list[dict]:
    """Shape checks that don't need the PDF (the portal runs these before queueing)."""
    if not isinstance(ops, list) or not ops:
        raise EditError("No edits to apply.")
    if len(ops) > MAX_OPS:
        raise EditError(f"Too many edits at once (at most {MAX_OPS}).")
    clean = []
    for n, op in enumerate(ops, 1):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise EditError(f"Edit {n}: unknown operation.")
        if not isinstance(op.get("element"), int) or op["element"] < 0:
            raise EditError(f"Edit {n}: missing element.")
        kind = op["op"]
        if kind == "set_role" and op.get("role") not in STANDARD:
            raise EditError(f"Edit {n}: {op.get('role')!r} isn't a standard PDF tag.")
        if kind in ("set_alt", "set_actual_text"):
            text = op.get("alt" if kind == "set_alt" else "text")
            if not isinstance(text, str) or len(text) > MAX_TEXT:
                raise EditError(f"Edit {n}: text must be at most {MAX_TEXT} characters.")
        if kind == "set_lang" and op.get("lang") and not _LANG.match(str(op["lang"])):
            raise EditError(f"Edit {n}: {op.get('lang')!r} isn't a language code like en-US.")
        if kind == "set_cell":
            if op.get("role") not in ("TH", "TD"):
                raise EditError(f"Edit {n}: a cell must be TH or TD.")
            if op["role"] == "TH" and op.get("scope") not in SCOPES:
                raise EditError(f"Edit {n}: a header cell needs a scope of Row, Column or Both.")
        if kind == "move":
            if not isinstance(op.get("target"), int) or op.get("position") not in ("before", "after"):
                raise EditError(f"Edit {n}: a move needs a target element and before/after.")
            if op["target"] == op["element"]:
                raise EditError(f"Edit {n}: an element can't be moved next to itself.")
        if kind == "merge":
            absorb = op.get("absorb")
            if (not isinstance(absorb, list) or not absorb or len(absorb) > 500
                    or not all(isinstance(a, int) and a >= 0 for a in absorb) or op["element"] in absorb
                    or len(set(absorb)) != len(absorb)):
                raise EditError(f"Edit {n}: a merge needs a list of other tags to join this one.")
        if kind == "split":
            inside = op.get("char") is not None
            if (not isinstance(op.get("at"), int) or op["at"] < (0 if inside else 1)
                    or (inside and (not isinstance(op["char"], int) or isinstance(op["char"], bool) or op["char"] < 1))):
                raise EditError(f"Edit {n}: a split needs the position to split at.")
        clean.append(op)
    return clean


def apply_edits(src: Path, dst: Path, ops: list[dict]) -> list[str]:
    """Apply the edits to src and save dst. Returns one plain-language line per edit."""
    ops = validate_ops(ops)
    with pikepdf.open(src) as pdf:
        root = pdf.Root.get("/StructTreeRoot")
        if not isinstance(root, Dictionary):
            raise EditError("This file has no tags to edit.")
        checker = IntegrityChecker(pdf)
        checker.run()
        ctx = _Ctx(pdf, root, checker, checker.index.elements, Path(src))
        # Resolve every reference first; later moves change pre-order indices but not objects.
        resolved = []
        for n, op in enumerate(ops, 1):
            e = _elem(ctx, op["element"], op.get("expect_role"), n)
            if op["op"] == "move":
                t = _elem(ctx, op["target"], op.get("expect_target_role"), n)
            elif op["op"] == "merge":
                t = [_elem(ctx, a, None, n) for a in op["absorb"]]
            else:
                t = None
            resolved.append((op, e, t))
        log = []
        for n, (op, e, t) in enumerate(resolved, 1):
            log.append(_apply(ctx, op, e, t, n))
        pdf.save(dst)
    return log


def _elem(ctx: _Ctx, index: int, expect_role: str | None, n: int) -> Elem:
    if index >= len(ctx.elems):
        raise EditError(f"Edit {n}: element {index} doesn't exist in this version. Reload and try again.")
    e = ctx.elems[index]
    if expect_role and expect_role not in (e.role, e.raw_role):
        raise EditError(f"Edit {n}: element {index} is a {e.role}, not a {expect_role}. "
                        "The document changed; reload and try again.")
    return e


def _apply(ctx: _Ctx, op: dict, e: Elem, t: Elem | None, n: int) -> str:
    kind, obj = op["op"], e.obj
    if kind == "set_role":
        return _set_role(ctx, e, op["role"], n)
    if kind == "set_alt":
        return _set_text(obj, "/Alt", op["alt"], e, "alt text")
    if kind == "set_actual_text":
        return _set_text(obj, "/ActualText", op["text"], e, "actual text")
    if kind == "set_lang":
        lang = (op.get("lang") or "").strip()
        if lang:
            obj.Lang = String(lang)
        elif "/Lang" in obj:
            del obj["/Lang"]
        return f"element {e.index}: language {'set to ' + lang if lang else 'removed'}"
    if kind == "set_cell":
        return _set_cell(e, op["role"], op.get("scope"))
    if kind == "set_decorative":
        return _make_decorative(ctx, e, n)
    if kind == "move":
        return _move(ctx, e, t, op["position"], n)
    if kind == "merge":
        return _merge(ctx, e, t, n)
    if kind == "split":
        if op.get("char") is not None:
            return _split_inside(ctx, e, op["at"], op["char"], n)
        return _split(ctx, e, op["at"], n)
    raise EditError(f"Edit {n}: unknown operation.")  # validate_ops prevents this


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


def _insert_after(ctx: _Ctx, e: Elem, new: list, n: int) -> None:
    if not new:
        return
    parent = _parent_obj(ctx, e)
    if not e.obj.is_indirect:
        e.obj[_MARK] = True
    siblings = _items(parent.get("/K"))
    pos = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
    if _MARK in e.obj:
        del e.obj[_MARK]
    if pos is None:
        raise EditError(f"Edit {n}: the tag wasn't found under its parent.")
    parent.K = Array(siblings[:pos + 1] + new + siblings[pos + 1:])


def _set_text(obj: Dictionary, key: str, text: str, e: Elem, label: str) -> str:
    text = text.strip()
    if text:
        obj[key] = String(text)
        return f"element {e.index} ({e.role}): {label} set"
    if key in obj:
        del obj[key]
    return f"element {e.index} ({e.role}): {label} removed"


def _set_cell(e: Elem, role: str, scope: str | None) -> str:
    obj = e.obj
    obj.S = Name("/" + role)
    attrs = obj.get("/A")
    items = list(attrs) if isinstance(attrs, Array) else ([attrs] if attrs is not None else [])
    keep = [a for a in items if not (isinstance(a, Dictionary) and a.get("/O") == Name.Table and "/Scope" in a)]
    if role == "TH":
        keep.append(Dictionary(O=Name.Table, Scope=Name("/" + scope)))
    if not keep:
        if "/A" in obj:
            del obj["/A"]
    else:
        obj.A = keep[0] if len(keep) == 1 else Array(keep)
    return f"element {e.index}: {role}" + (f" (scope {scope})" if role == "TH" else "")


def _subtree(ctx: _Ctx, e: Elem) -> list[Elem]:
    out, stack = [], [e.index]
    while stack:
        x = ctx.elems[stack.pop()]
        out.append(x)
        stack.extend(x.kids)
    return out


def _parent_obj(ctx: _Ctx, e: Elem) -> Dictionary:
    p = e.obj.get("/P")
    if isinstance(p, Dictionary) and p is not None:
        return p
    return ctx.root if e.parent is None else ctx.elems[e.parent].obj


def _same(a, b) -> bool:
    if isinstance(a, Dictionary) and isinstance(b, Dictionary):
        if a.is_indirect and b.is_indirect:
            return a.objgen == b.objgen
        return bool(a.get(_MARK)) and bool(b.get(_MARK))
    return False


def _detach(ctx: _Ctx, e: Elem) -> Dictionary:
    """Remove e from its parent's /K; returns the parent."""
    parent = _parent_obj(ctx, e)
    if not e.obj.is_indirect:
        e.obj[_MARK] = True
    kids = _items(parent.get("/K"))
    remaining = [k for k in kids if not _same(k, e.obj)]
    if len(remaining) == len(kids):
        raise EditError(f"element {e.index} wasn't found under its parent; the tag tree may be damaged")
    parent.K = Array(remaining)
    if _MARK in e.obj:
        del e.obj[_MARK]
    return parent


def _move(ctx: _Ctx, e: Elem, t: Elem, position: str, n: int) -> str:
    if t.index in {x.index for x in _subtree(ctx, e)}:
        raise EditError(f"Edit {n}: an element can't be moved inside itself.")
    _detach(ctx, e)
    new_parent = _parent_obj(ctx, t)
    if not t.obj.is_indirect:
        t.obj[_MARK] = True
    kids = _items(new_parent.get("/K"))
    at = next((i for i, k in enumerate(kids) if _same(k, t.obj)), None)
    if _MARK in t.obj:
        del t.obj[_MARK]
    if at is None:
        raise EditError(f"Edit {n}: the target element wasn't found under its parent.")
    kids.insert(at if position == "before" else at + 1, e.obj)
    new_parent.K = Array(kids)
    e.obj.P = new_parent
    return f"element {e.index} ({e.role}) moved {position} element {t.index} ({t.role})"


def _make_decorative(ctx: _Ctx, e: Elem, n: int) -> str:
    """Turn an element and everything under it into an artifact: its content's marked-content
    sequences become /Artifact (no MCID), the ParentTree entries are cleared and the tag is removed."""
    if e.parent is None and len(_items(ctx.root.get("/K"))) <= 1:
        raise EditError(f"Edit {n}: the document's only top-level tag can't be made decorative.")
    members = _subtree(ctx, e)
    if any(x.objrs for x in members):
        raise EditError(f"Edit {n}: this tag contains a link or form field, which can't be decorative.")
    member_ids = {x.index for x in members}
    targets: dict[tuple, set[int]] = {}
    for (owner, mcid), users in ctx.checker.index.refs.items():
        if any(u in member_ids for u in users):
            targets.setdefault(owner, set()).add(mcid)
    changed = 0
    for owner_og, mcids in targets.items():
        owner = ctx.checker.stream_obj.get(owner_og)
        if owner is None:
            continue
        ops = list(pikepdf.parse_content_stream(owner))
        touched = False
        for i, ins in enumerate(ops):
            if (isinstance(ins, ContentStreamInstruction) and str(ins.operator) == "BDC" and len(ins.operands) == 2
                    and isinstance(ins.operands[1], Dictionary) and int(ins.operands[1].get("/MCID", -1)) in mcids):
                ops[i] = ContentStreamInstruction([Name.Artifact, Dictionary(Type=Name.Layout)], Operator("BDC"))
                touched = True
                changed += 1
        if touched:
            data = pikepdf.unparse_content_stream(ops)
            if isinstance(owner, pikepdf.Stream):
                owner.write(data)
            else:
                owner.Contents = ctx.pdf.make_stream(data)
        _clear_parent_tree(ctx, owner, mcids)
    _detach(ctx, e)
    return f"element {e.index} ({e.role}) marked decorative ({changed} content item(s) now artifacts)"


def _clear_parent_tree(ctx: _Ctx, owner, mcids: set[int]) -> None:
    key = owner.get("/StructParents") if owner is not None else None
    pt = ctx.root.get("/ParentTree")
    if key is None or not isinstance(pt, Dictionary):
        return
    arr = _number_tree_get(pt, int(key))
    if isinstance(arr, Array):
        for m in mcids:
            if m < len(arr):
                arr[m] = pikepdf.Object.parse(b"null")


def _number_tree_get(node, key: int, depth: int = 0):
    if depth > 32 or not isinstance(node, Dictionary):
        return None
    nums = node.get("/Nums")
    if isinstance(nums, Array):
        for i in range(0, len(nums) - 1, 2):
            if int(nums[i]) == key:
                return nums[i + 1]
    for kid in _items(node.get("/Kids")):
        lim = kid.get("/Limits") if isinstance(kid, Dictionary) else None
        if isinstance(lim, Array) and len(lim) == 2 and not (int(lim[0]) <= key <= int(lim[1])):
            continue
        found = _number_tree_get(kid, key, depth + 1)
        if found is not None:
            return found
    return None


# --- merge and split --------------------------------------------------------------------------------

def _page_obj(ctx: _Ctx, e: Elem):
    return ctx.pdf.pages[e.page].obj if e.page is not None else None


def _ancestors(ctx: _Ctx, e: Elem) -> set[int]:
    out, p = set(), e.parent
    while p is not None:
        out.add(p)
        p = ctx.elems[p].parent
    return out


def _set_number_tree(node, key: int, value, depth: int = 0) -> bool:
    if depth > 32 or not isinstance(node, Dictionary):
        return False
    nums = node.get("/Nums")
    if isinstance(nums, Array):
        for i in range(0, len(nums) - 1, 2):
            if int(nums[i]) == key:
                nums[i + 1] = value
                return True
    for kid in _items(node.get("/Kids")):
        if _set_number_tree(kid, key, value, depth + 1):
            return True
    return False


def _reparent_items(ctx: _Ctx, owner_elem: Elem, items: list, new_parent: Dictionary) -> list:
    """Point everything in items at new_parent: content (ParentTree), links (ParentTree) and child tags (/P).
    Integer MCIDs become explicit marked-content references with their page, since the new parent may be
    on another page."""
    pt = ctx.root.get("/ParentTree")
    page = _page_obj(ctx, owner_elem)
    out = []
    for item in items:
        if _is_elem_dict(item):
            item.P = new_parent
            out.append(item)
            continue
        if isinstance(item, int) or (isinstance(item, Dictionary) and "/MCID" in item):
            if isinstance(item, int):
                if page is None:
                    raise EditError(f"element {owner_elem.index} has content without a page; it can't be moved")
                item = Dictionary(Type=Name.MCR, Pg=page, MCID=item)
            elif "/Pg" not in item and "/Stm" not in item and page is not None:
                item.Pg = page
            owner = item.get("/Stm") if item.get("/Stm") is not None else item.get("/Pg")
            key = owner.get("/StructParents") if owner is not None else None
            if key is not None and isinstance(pt, Dictionary):
                arr = _number_tree_get(pt, int(key))
                mcid = int(item.MCID)
                if isinstance(arr, Array) and mcid < len(arr):
                    arr[mcid] = new_parent
            out.append(item)
            continue
        if isinstance(item, Dictionary) and "/Obj" in item:
            key = item.Obj.get("/StructParent")
            if key is not None and isinstance(pt, Dictionary):
                _set_number_tree(pt, int(key), new_parent)
            out.append(item)
            continue
        out.append(item)
    return out


def _is_elem_dict(item) -> bool:
    return isinstance(item, Dictionary) and "/S" in item and item.get("/Type") not in (Name.MCR, Name.OBJR)


def _merge(ctx: _Ctx, e: Elem, others: list[Elem], n: int) -> str:
    """Move the content of `others` into e, in order, and remove their tags."""
    if not e.obj.is_indirect:
        raise EditError(f"Edit {n}: this tag can't take content from others.")
    blocked = _ancestors(ctx, e) | {x.index for x in _subtree(ctx, e)}
    for o in others:
        if o.index in blocked or e.index in _ancestors(ctx, o):
            raise EditError(f"Edit {n}: a tag can't be merged with a tag that contains it or sits inside it.")
    kids = _items(e.obj.get("/K"))
    for o in others:
        moved = _reparent_items(ctx, o, _items(o.obj.get("/K")), e.obj)
        kids += moved
        o.obj.K = Array([])
        _detach(ctx, o)
    e.obj.K = Array(kids)
    return f"element {e.index} ({e.role}) merged with " + ", ".join(f"{o.index} ({o.role})" for o in others)


def _split(ctx: _Ctx, e: Elem, at: int, n: int) -> str:
    """Move e's /K items from position `at` on into a new tag of the same type, placed right after e."""
    items = _items(e.obj.get("/K"))
    if not 1 <= at < len(items):
        raise EditError(f"Edit {n}: this tag can't be split there.")
    _new_sibling(ctx, e, items[:at], lambda new: _reparent_items(ctx, e, items[at:], new), n)
    return f"element {e.index} ({e.role}) split into two at item {at}"


def _new_sibling(ctx: _Ctx, e: Elem, keep: list, give, n: int) -> Dictionary:
    """A new tag of e's type right after e, holding give(new); e keeps `keep`."""
    parent = _parent_obj(ctx, e)
    new = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=e.obj.S, P=parent))
    for key in ("/Lang", "/A"):
        if key in e.obj:
            new[key] = e.obj[key]
    if "/Pg" in e.obj:
        new.Pg = e.obj.Pg
    new.K = Array(give(new))
    e.obj.K = Array(keep)
    if not e.obj.is_indirect:
        e.obj[_MARK] = True
    siblings = _items(parent.get("/K"))
    pos = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
    if _MARK in e.obj:
        del e.obj[_MARK]
    if pos is None:
        raise EditError(f"Edit {n}: the tag wasn't found under its parent.")
    siblings.insert(pos + 1, new)
    parent.K = Array(siblings)
    return new


# --- splitting inside a piece of text -----------------------------------------------------------------

_SHOW = {"Tj", "'", '"', "TJ"}


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
    elif isinstance(item, Dictionary) and "/MCID" in item and item.get("/Stm") is None:
        mcid, pg = int(item.MCID), item.get("/Pg") if item.get("/Pg") is not None else _page_obj(ctx, e)
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
    if out is None or counted != len(glyphs):
        raise EditError(cant)
    pg.Contents = ctx.pdf.make_stream(pikepdf.unparse_content_stream(out))

    pt = ctx.root.get("/ParentTree")
    arr = _number_tree_get(pt, int(key)) if isinstance(pt, Dictionary) else None
    if not isinstance(arr, Array):
        raise EditError(cant)

    def give(new: Dictionary) -> list:
        while len(arr) <= new_mcid:
            arr.append(pikepdf.Object.parse(b"null"))
        arr[new_mcid] = new
        return [Dictionary(Type=Name.MCR, Pg=pg, MCID=new_mcid)] + _reparent_items(ctx, e, items[k + 1:], new)

    _new_sibling(ctx, e, items[:k + 1], give, n)
    note = ""
    if "/ActualText" in e.obj:  # it described the whole text; it no longer matches either half
        del e.obj["/ActualText"]
        note = "; its read-aloud text was removed, set it again on each part if needed"
    return f"element {e.index} ({e.role}) split inside its text at character {char}{note}"


def _mcid_glyphs(path: Path, page_no: int, mcid: int) -> list[str]:
    """The text of each glyph in one marked-content sequence, in drawing order (as the tag map reads it)."""
    import pdfplumber

    import app.verification.semantic  # noqa: F401 - patches pdfplumber's marked-content tracking

    with pdfplumber.open(path, pages=[page_no + 1]) as plumb:
        return [c["text"] for c in plumb.pages[0].chars if c.get("mcid") == mcid and c.get("tag") != "Artifact"]


def _glyph_at(glyphs: list[str], char: int) -> int | None:
    """The glyph that starts at character `char` of the piece's text (whitespace collapsed, as the tag map
    shows it), or the next one; None at either end."""
    out = 0
    last_space = True
    for gi, t in enumerate(glyphs):
        if out >= char and gi > 0:
            return gi if gi < len(glyphs) else None
        for ch in t:
            if ch.isspace():
                if not last_space:
                    out += 1
                    last_space = True
            else:
                out += 1
                last_space = False
    return None


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
                      and int(ins.operands[1].get("/MCID", -1)) == mcid)
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
