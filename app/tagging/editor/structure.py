"""Tags moved, made decorative, deleted, merged and split."""
from __future__ import annotations

import pikepdf
from pikepdf import Array, ContentStreamInstruction, Dictionary, Name, Operator

from app.tagging.content_marker import mcid_of, write_ops
from app.tagging.editor._ctx import (
    CONTENT_ROLES, NEW_AREA_ROLES, NOTES, EditError, _absorbed, _ancestors, _clear_parent_tree, _Ctx, _detach,
    _elem, _give_id, _MARK, _new_sibling, _parent_obj, _reparent_items, _role_of, _same, _subtree, register, WAS_MCID,
)
from app.tagging.editor.areas import _split_inside
from app.tagging.role_rules import check_move
from app.tagging.tree import is_elem
from app.tagging.tree import items as _items
from app.verification.integrity import Elem

# What the new part of a split text tag can be (#427: chosen before saving, as the tag manager shows it)
SPLIT_ROLES = NEW_AREA_ROLES - {"Figure", "Formula"}
# What a tag made an artifact is: the artifact's /Type and /Subtype (#364)
ARTIFACT_KINDS = {"layout": (Name.Layout, None), "header": (Name.Pagination, Name.Header),
                  "footer": (Name.Pagination, Name.Footer), "page_number": (Name.Pagination, Name("/PageNum"))}


def _within(ctx: _Ctx, obj: Dictionary, ancestor: Dictionary) -> bool:
    """Whether `obj` is `ancestor` or under it in the tree as it is now, earlier edits included: the tag map's
    elements (ctx.elems) keep the tree as it was before any edit, so an earlier move would be missed."""
    mark = not ancestor.is_indirect  # a direct object is told apart by a mark while it's looked for (_same)
    if mark:
        ancestor[_MARK] = True
    try:
        for _ in range(512):
            if not isinstance(obj, Dictionary) or _same(obj, ctx.root):
                return False
            if obj is ancestor or _same(obj, ancestor):
                return True
            obj = obj.get("/P")
        return False
    finally:
        if mark:
            del ancestor[_MARK]


def _move(ctx: _Ctx, e: Elem, t: Elem, position: str, n: int) -> str:
    """Move a tag before or after another, or "in" one: as its last child (into a Sect, say)."""
    if _within(ctx, t.obj, e.obj):
        raise EditError(f"Edit {n}: an element can't be moved inside itself.")
    old_parent = _parent_obj(ctx, e)
    new_parent = t.obj if position == "in" else _parent_obj(ctx, t)
    at_root = lambda p: p is ctx.root or (p.is_indirect and ctx.root.is_indirect and p.objgen == ctx.root.objgen)  # noqa: E731
    v = check_move(e.role, None if at_root(old_parent) else _role_of(ctx, old_parent),
                   None if at_root(new_parent) else _role_of(ctx, new_parent), _same(old_parent, new_parent))
    if not v.ok:
        raise EditError(f"Edit {n}: element {e.index} ({e.role}) can't go there. {v.reason}")
    _detach(ctx, e)
    if position == "in":
        kids = _items(t.obj.get("/K"))
        kids.append(e.obj)
        t.obj.K = Array(kids)
        e.obj.P = t.obj
        return f"element {e.index} ({e.role}) moved into element {t.index} ({t.role})"
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


def _make_decorative(ctx: _Ctx, e: Elem, n: int, kind: str = "layout") -> str:
    """Turn an element and everything under it into an artifact: its content's marked-content
    sequences become /Artifact (no MCID), the ParentTree entries are cleared and the tag is removed."""
    if e.parent is None and len(_items(ctx.root.get("/K"))) <= 1:
        raise EditError(f"Edit {n}: the document's only top-level tag can't be made decorative.")
    members = _subtree(ctx, e)
    if any(x.objrs for x in members):
        raise EditError(f"Edit {n}: this tag contains a link or form field, which can't be decorative.")
    changed = _to_artifacts(ctx, {x.index for x in members}, kind)
    _detach(ctx, e)
    what = {"layout": "decorative", "header": "a page header", "footer": "a page footer",
            "page_number": "a page number"}[kind]
    return f"element {e.index} ({e.role}) marked {what} ({changed} content item(s) now artifacts)"


def _to_artifacts(ctx: _Ctx, member_ids: set[int], kind: str = "layout") -> int:
    """The content these tags hold directly becomes /Artifact (no MCID), and leaves the ParentTree."""
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
                    and isinstance(ins.operands[1], Dictionary) and mcid_of(ins.operands[1]) in mcids):
                typ, sub = ARTIFACT_KINDS[kind]
                props = Dictionary(Type=typ, **({"Subtype": sub} if sub is not None else {}))
                props[WAS_MCID] = mcid_of(ins.operands[1])  # until the save: a later edit may tag it again
                ops[i] = ContentStreamInstruction([Name.Artifact, props], Operator("BDC"))
                touched = True
                changed += 1
        if touched:
            write_ops(ctx.pdf, owner, ops)
            ctx.artifacted[owner_og] = owner
        _clear_parent_tree(ctx, owner, mcids)
    for key in [k for k, users in ctx.checker.index.refs.items() if any(u in member_ids for u in users)]:
        del ctx.checker.index.refs[key]
        ctx.untagged.add(key)
    return changed


def _delete(ctx: _Ctx, e: Elem, children: str, n: int) -> str:
    """Delete a tag. "delete": with everything in it (its content becomes artifacts). "lift": only the tag; its
    child tags take its place under its parent, and content it holds directly joins the parent if the parent holds
    content, else becomes an artifact. Never leaves content untagged or a tree that isn't valid."""
    parent = _parent_obj(ctx, e)
    at_root = parent is ctx.root or (parent.is_indirect and ctx.root.is_indirect and parent.objgen == ctx.root.objgen)
    if children == "delete":
        if at_root and len(_items(ctx.root.get("/K"))) <= 1:
            raise EditError(f"Edit {n}: the document's only top-level tag can't be deleted.")
        members = _subtree(ctx, e)
        if e.objrs:
            raise EditError(f"Edit {n}: this tag is a link or form field itself, which has to stay tagged.")
        # Links and form fields have to stay tagged: each is kept, whole, and takes the deleted tag's place
        kept = _kept_holders(ctx, e)
        parent_role = _role_of(ctx, parent)
        for h in kept:
            v = check_move(h.role, _role_of(ctx, _parent_obj(ctx, h)) or "", parent_role, False)
            if not v.ok:
                raise EditError(f"Edit {n}: this tag holds a {h.role} tag (a link or form field), which has to stay "
                                f"tagged and can't move up into the {parent_role}. {v.reason} Move it out first.")
        kept_ids = {x.index for h in kept for x in _subtree(ctx, h)}
        kept_objs = [h.obj for h in kept]
        for h in kept:
            _detach(ctx, h)
        changed = _to_artifacts(ctx, {x.index for x in members} - kept_ids)
        if not e.obj.is_indirect:
            e.obj[_MARK] = True
        siblings = _items(parent.get("/K"))
        at = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
        if _MARK in e.obj:
            del e.obj[_MARK]
        if at is None:
            raise EditError(f"element {e.index} wasn't found under its parent; the tag tree may be damaged")
        for obj in kept_objs:
            obj.P = parent
        parent.K = Array(siblings[:at] + kept_objs + siblings[at + 1:])
        gone = len(members) - 1 - len(kept_ids)
        return (f"element {e.index} ({e.role}) deleted with {gone} tag(s) inside it "
                f"({changed} content item(s) now artifacts)"
                + (f"; {len(kept)} link(s) and form field(s) kept and moved up into the {parent_role}" if kept else ""))

    if at_root:
        raise EditError(f"Edit {n}: the document's top tag can't be removed.")
    parent_role = _role_of(ctx, parent)
    items = _items(e.obj.get("/K"))
    kids = [it for it in items if is_elem(it)]
    own = [it for it in items if not is_elem(it)]
    for kid in kids:
        v = check_move(_role_of(ctx, kid) or "", e.role, parent_role, False)
        if not v.ok:
            raise EditError(f"Edit {n}: the tags inside element {e.index} ({e.role}) can't move up a level. {v.reason} "
                            "Delete it with everything in it, or change what's around it first.")
    if any(isinstance(it, Dictionary) and "/Obj" in it for it in own):
        raise EditError(f"Edit {n}: this tag holds a link or form field directly, which has to stay tagged.")
    keeps_content = bool(own) and parent_role in CONTENT_ROLES
    moved = _reparent_items(ctx, e, items if keeps_content else kids, parent)
    artifacts = 0
    if own and not keeps_content:
        e.obj.K = Array(own)  # only what it holds directly: the child tags have already moved
        artifacts = _to_artifacts(ctx, {e.index})
    if not e.obj.is_indirect:
        e.obj[_MARK] = True
    siblings = _items(parent.get("/K"))
    at = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
    if _MARK in e.obj:
        del e.obj[_MARK]
    if at is None:
        raise EditError(f"element {e.index} wasn't found under its parent; the tag tree may be damaged")
    parent.K = Array(siblings[:at] + moved + siblings[at + 1:])
    if keeps_content:
        for key, users in ctx.checker.index.refs.items():
            if e.index in users and e.parent is not None:
                ctx.checker.index.refs[key] = [e.parent if u == e.index else u for u in users]
    return (f"element {e.index} ({e.role}) removed; {len(kids)} tag(s) moved up a level"
            + (f", its own content now part of the {parent_role}" if keeps_content
               else f", {artifacts} content item(s) of its own now artifacts" if artifacts else ""))


def _kept_holders(ctx: _Ctx, e: Elem) -> list[Elem]:
    """The tags under e that hold a link or a form field (the outermost of each), in reading order."""
    out: list[Elem] = []

    def walk(x: Elem) -> None:
        for k in x.kids:
            kid = ctx.elems[k]
            if kid.objrs:
                out.append(kid)  # with everything inside it (the link's own text)
            else:
                walk(kid)
    walk(e)
    return out


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


def _apply_split(ctx: _Ctx, op: dict, e: Elem, t, n: int) -> str:
    said = (_split_inside(ctx, e, op["at"], op["char"], n) if op.get("char") is not None
            else _split(ctx, e, op["at"], n))
    role = op.get("role")
    ctx.new_tags = [ctx.made] if ctx.made is not None else []
    if role and ctx.made is not None and role != e.role:  # the new part's own type (#427)
        ctx.made.S = Name("/" + role)
        if role in NOTES:
            _give_id(ctx, ctx.made, f"note-{ctx.made.objgen[0]}")
        said += f"; the new part is a {role}"
    return said


def _check_decorative(op: dict, n: int) -> None:
    if op.get("as", "layout") not in ARTIFACT_KINDS:
        raise EditError(f"Edit {n}: an artifact is one of {', '.join(ARTIFACT_KINDS)}.")


def _check_move(op: dict, n: int) -> None:
    if not isinstance(op.get("target"), int) or op.get("position") not in ("before", "after", "in"):
        raise EditError(f"Edit {n}: a move needs a target element and before/after/in.")
    if op["target"] == op["element"]:
        raise EditError(f"Edit {n}: an element can't be moved next to itself.")


def _check_absorb(op: dict, n: int) -> None:
    """merge and make_list: the other tags, each once."""
    absorb = op.get("absorb")
    if (not isinstance(absorb, list) or not absorb or len(absorb) > 500
            or not all(isinstance(a, int) and a >= 0 for a in absorb) or op["element"] in absorb
            or len(set(absorb)) != len(absorb)):
        raise EditError(f"Edit {n}: a merge needs a list of other tags to join this one." if op["op"] == "merge"
                        else f"Edit {n}: making a list needs the other tags that become its items.")


def _check_delete(op: dict, n: int) -> None:
    if op.get("children") not in ("lift", "delete"):
        raise EditError(f"Edit {n}: deleting a tag needs to say what happens to what's inside it: "
                        "\"lift\" (keep it, a level up) or \"delete\" (remove it all).")


def _check_split(op: dict, n: int) -> None:
    inside = op.get("char") is not None
    if (not isinstance(op.get("at"), int) or op["at"] < (0 if inside else 1)
            or (inside and (not isinstance(op["char"], int) or isinstance(op["char"], bool) or op["char"] < 1))):
        raise EditError(f"Edit {n}: a split needs the position to split at.")
    if op.get("role") is not None and op["role"] not in SPLIT_ROLES:
        raise EditError(f"Edit {n}: the new part can be {', '.join(sorted(SPLIT_ROLES))}.")


register("set_decorative", lambda ctx, op, e, t, n: _make_decorative(ctx, e, n, op.get("as", "layout")),
         _check_decorative)
register("move", lambda ctx, op, e, t, n: _move(ctx, e, t, op["position"], n), _check_move,
         lambda ctx, op, n: _elem(ctx, op["target"], op.get("expect_target_role"), n))
register("merge", lambda ctx, op, e, t, n: _merge(ctx, e, t, n), _check_absorb, _absorbed)
register("split", _apply_split, _check_split)
register("delete", lambda ctx, op, e, t, n: _delete(ctx, e, op["children"], n), _check_delete)
