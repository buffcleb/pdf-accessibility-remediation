"""Lists made from several tags, built from a tag's lines, and their levels; a list made a table of contents."""
from __future__ import annotations

from pikepdf import Array, Dictionary, Name

from app.tagging.editor._ctx import (
    LIST_ITEM_FROM, EditError, _absorbed, _ancestors, _at_root, _Ctx, _detach, _parent_obj, _reparent_items, _role_of,
    _elem, _same, register,
)
from app.tagging.editor.roles import _wrap
from app.tagging.editor.structure import _check_absorb
from app.tagging.role_rules import BLOCKS, check_move, check_role
from app.tagging.tree import is_elem
from app.tagging.tree import items as _items
from app.verification.integrity import Elem

# What a row of build_list can become apart from a list item
ROW_ROLES = {"P", "H1", "H2", "H3", "H4", "H5", "H6"}
MAX_ROWS = 500
MAX_LIST_LEVEL = 9


# --- lists made from several tags, and their levels ----------------------------------------------------

def _make_list(ctx: _Ctx, e: Elem, others: list[Elem], n: int) -> str:
    """e and `others` become the items of one list, in that order, where e was. A text tag becomes an item
    itself (LI > LBody), so later edits that name it find the item; a list gives its items and goes."""
    tags = [e] + others
    for x in tags:
        if not x.obj.is_indirect:
            raise EditError(f"Edit {n}: element {x.index} can't become a list item.")
        if _role_of(ctx, x.obj) == "L":
            continue
        if x.role not in LIST_ITEM_FROM:
            raise EditError(f"Edit {n}: a {x.role} can't become a list item. Select paragraphs, headings or lists.")
        v = check_role("LI", x.role, "L", [ctx.elems[k].role for k in x.kids], has_content=bool(x.content_refs or x.objrs))
        if not v.ok:
            raise EditError(f"Edit {n}: element {x.index} ({x.role}): {v.reason}")
    for x in tags:
        inside = _ancestors(ctx, x)
        if any(y.index in inside for y in tags):
            raise EditError(f"Edit {n}: a tag can't join a list with a tag that contains it.")
    parent = _parent_obj(ctx, e)
    if not _at_root(ctx, parent):
        v = check_move("L", _role_of(ctx, parent), _role_of(ctx, parent), True)
        if not v.ok:
            raise EditError(f"Edit {n}: a list can't be made here. {v.reason}")

    if _role_of(ctx, e.obj) == "L":
        lst, items = e.obj, _items(e.obj.get("/K"))
    else:
        # The new list takes e's place under its parent, and e becomes its first item
        lst = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.L, P=parent,
                                               A=Dictionary(O=Name.List, ListNumbering=Name("/None"))))
        if "/Pg" in e.obj:
            lst.Pg = e.obj.Pg
        siblings = _items(parent.get("/K"))
        pos = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
        if pos is None:
            raise EditError(f"Edit {n}: the tag wasn't found under its parent.")
        siblings[pos] = lst
        parent.K = Array(siblings)
        ctx.new_tags = [lst]
        _as_item(ctx, e, n)
        e.obj.P = lst
        items = [e.obj]
    for o in others:
        if _role_of(ctx, o.obj) == "L":
            items += _reparent_items(ctx, o, _items(o.obj.get("/K")), lst)
            o.obj.K = Array([])
            _detach(ctx, o)
        else:
            _detach(ctx, o)
            _as_item(ctx, o, n)
            o.obj.P = lst
            items.append(o.obj)
    lst.K = Array(items)
    return f"element {e.index}: one list made of {len(tags)} tags"


def _as_item(ctx: _Ctx, e: Elem, n: int) -> None:
    """Turn a text tag into a list item holding what it held."""
    if e.content_refs or e.objrs or not all(ctx.elems[k].role in ("Lbl", "LBody") for k in e.kids) or not e.kids:
        _wrap(ctx, e, "LI", n)
    else:  # it already holds only a label and a body
        e.obj.S = Name.LI


def _build_list(ctx: _Ctx, e: Elem, others: list[Elem], rows: list[dict], n: int) -> str:
    """Cut the content of e (and of `others`, which follow it) into rows, and make each row a tag of its own or
    an item of a list, nested by level. Everything takes e's place under its parent, in order."""
    tags = [e] + others
    for x in tags:
        if not x.obj.is_indirect or x.role not in LIST_ITEM_FROM:
            raise EditError(f"Edit {n}: a {x.role} can't be built into a list. Select paragraphs or headings.")
        v = check_role("LI", x.role, "L", [ctx.elems[k].role for k in x.kids], has_content=bool(x.content_refs or x.objrs))
        if not v.ok:
            raise EditError(f"Edit {n}: element {x.index} ({x.role}): {v.reason}")
        for key, what in (("/ActualText", "read-aloud text"), ("/Alt", "alt text")):
            if key in x.obj:  # it stands for all of the tag's content, and would be wrong on any one piece
                raise EditError(f"Edit {n}: element {x.index} has {what} that stands for all of it. Remove that "
                                "first, then build the list.")
    for x in tags:
        inside = _ancestors(ctx, x)
        if any(y.index in inside for y in tags):
            raise EditError(f"Edit {n}: a tag can't be built into a list with a tag that contains it.")
    parent = _parent_obj(ctx, e)
    if not _at_root(ctx, parent):
        for role in sorted({"L" if "level" in r else r["as"] for r in rows}):
            v = check_move(role, _role_of(ctx, parent), _role_of(ctx, parent), True)
            if not v.ok:
                raise EditError(f"Edit {n}: that can't be built here. {v.reason}")

    # Every /K item of the tags, in order, and where each row starts among them
    content = [(x, item) for x in tags for item in _items(x.obj.get("/K"))]
    place, at = {}, 0
    for x in tags:
        for k in range(len(_items(x.obj.get("/K")))):
            place[(x.index, k)] = at
            at += 1
    starts = []
    for row in rows:
        pos = place.get((row.get("el", e.index), row["at"]))
        if pos is None or (starts and pos <= starts[-1]) or (not starts and pos != 0):
            raise EditError(f"Edit {n}: the rows don't match this tag's content: each has to start after the one "
                            "before it, the first at the start. Reload and try again.")
        starts.append(pos)

    def holder(role: str, owner: Elem, under: Dictionary, reuse: bool) -> Dictionary:
        """A new tag for a row (or e's own tag, for the first row), with the language its content was in."""
        if reuse:
            e.obj.S = Name("/" + role)
            e.obj.P = under
            return e.obj
        new = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + role), P=under))
        for key in ("/Lang", "/Pg"):
            if key in owner.obj:
                new[key] = owner.obj[key]
        return new

    def take(chunk: list, into: Dictionary) -> Array:
        k: list = []
        for owner, item in chunk:
            k += _reparent_items(ctx, owner, [item], into)
        return Array(k)

    out: list[Dictionary] = []          # what takes e's place
    open_lists: list[tuple[Dictionary, list]] = []   # the list being filled at each level, and its items
    last_body: Dictionary | None = None  # the body of the item made last, where a deeper list goes
    made_tags = made_items = 0
    # The tags it makes, in the order the tag manager shows them (refs.py): each row's tag, and a list where one opens
    ctx.new_tags = []
    for i, (row, start) in enumerate(zip(rows, starts)):
        chunk = content[start:starts[i + 1] if i + 1 < len(rows) else len(content)]
        owner = chunk[0][0]
        if "as" in row:
            tag = holder(row["as"], owner, parent, reuse=i == 0)
            tag.K = take(chunk, tag)
            out.append(tag)
            ctx.new_tags.append(tag)
            open_lists, last_body = [], None
            made_tags += 1
            continue
        level = row["level"]
        if level > len(open_lists) + 1:
            raise EditError(f"Edit {n}: a list item can only be one level deeper than the item before it.")
        del open_lists[level:]
        if level > len(open_lists):  # a new list: at the top, or nested at the end of the item before
            under = parent if not open_lists else last_body
            lst = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.L, P=under, K=Array([]),
                                                   A=Dictionary(O=Name.List, ListNumbering=Name("/None"))))
            if "/Pg" in owner.obj:
                lst.Pg = owner.obj.Pg
            if open_lists:
                under.K = Array(_items(under.get("/K")) + [lst])
            else:
                out.append(lst)
            open_lists.append((lst, []))
            ctx.new_tags.append(lst)
        lst, items = open_lists[-1]
        li = holder("LI", owner, lst, reuse=i == 0)
        ctx.new_tags.append(li)
        body = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LBody, P=li))
        if "/Pg" in owner.obj:
            body.Pg = owner.obj.Pg
        body.K = take(chunk, body)
        li.K = Array([body])
        items.append(li)
        lst.K = Array(items)
        last_body = body
        made_items += 1

    for o in others:
        o.obj.K = Array([])
        _detach(ctx, o)
    siblings = _items(parent.get("/K"))
    pos = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
    if pos is None:
        raise EditError(f"Edit {n}: the tag wasn't found under its parent.")
    parent.K = Array(siblings[:pos] + out + siblings[pos + 1:])
    if "level" in rows[0] and "/A" in e.obj:
        del e.obj["/A"]  # a paragraph's layout attributes don't belong on a list item
    return (f"element {e.index}: built into {made_items} list item(s)"
            + (f" and {made_tags} other tag(s)" if made_tags else ""))


def _list_item(ctx: _Ctx, e: Elem, n: int, what: str) -> tuple[Dictionary, list, int]:
    """The list holding item e as it is now, that list's items, and e's place among them."""
    lst = e.obj.get("/P")
    if _role_of(ctx, e.obj) != "LI" or not e.obj.is_indirect or not isinstance(lst, Dictionary) or _role_of(ctx, lst) != "L":
        raise EditError(f"Edit {n}: only a list item can be {what}.")
    items = _items(lst.get("/K"))
    pos = next((i for i, k in enumerate(items) if _same(k, e.obj)), None)
    if pos is None:
        raise EditError(f"Edit {n}: the item wasn't found in its list.")
    return lst, items, pos


def _kid_elems(ctx: _Ctx, obj: Dictionary, role: str) -> list[Dictionary]:
    return [k for k in _items(obj.get("/K")) if is_elem(k) and _role_of(ctx, k) == role]


def _sublist_of(ctx: _Ctx, item: Dictionary, like: Dictionary) -> Dictionary:
    """The list nested at the end of a list item's body, made (with `like`'s list attributes) if there is none.
    Nested lists go inside the LBody, after its text, as the tagger builds them."""
    bodies = _kid_elems(ctx, item, "LBody")
    if bodies:
        body = bodies[-1]
    else:
        body = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LBody, P=item, K=Array([])))
        item.K = Array(_items(item.get("/K")) + [body])
    kids = _items(body.get("/K"))
    if kids and is_elem(kids[-1]) and _role_of(ctx, kids[-1]) == "L":
        return kids[-1]
    sub = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.L, P=body, K=Array([])))
    sub.A = like["/A"] if "/A" in like else Dictionary(O=Name.List, ListNumbering=Name("/None"))
    body.K = Array(kids + [sub])
    return sub


def _indent(ctx: _Ctx, e: Elem, n: int) -> str:
    """A list item goes one level deeper: to the end of the list nested under the item before it."""
    lst, items, pos = _list_item(ctx, e, n, "indented")
    before = items[pos - 1] if pos else None
    role = _role_of(ctx, before) if is_elem(before) else None
    if role == "LI":
        sub = _sublist_of(ctx, before, lst)
    elif role == "L":  # a list nested directly in the list (L > L), as some producers write it
        sub = before
    else:
        raise EditError(f"Edit {n}: the first item of a list can't be indented: there's no item before it to go under.")
    lst.K = Array(items[:pos] + items[pos + 1:])
    sub.K = Array(_items(sub.get("/K")) + [e.obj])
    e.obj.P = sub
    return f"element {e.index}: list item moved one level in"


def _outdent(ctx: _Ctx, e: Elem, n: int) -> str:
    """An item of a nested list goes one level up, right after the item it was under. The items after it in
    the nested list go under it, so nothing is read in a different order."""
    sub, items, pos = _list_item(ctx, e, n, "moved out a level")
    holder = sub.get("/P")
    holder_role = _role_of(ctx, holder) if isinstance(holder, Dictionary) and not _at_root(ctx, holder) else None
    if holder_role == "LBody":
        over = holder.get("/P")       # the item it's under
        outer = over.get("/P") if isinstance(over, Dictionary) else None
        anchor = over
        tail = _items(holder.get("/K"))
        if not tail or not _same(tail[-1], sub):
            raise EditError(f"Edit {n}: the item above has text after this nested list, so moving the item out "
                            "would change the reading order. Move that text first.")
    elif holder_role == "L":
        outer, anchor = holder, sub
    else:
        outer = None
    if not isinstance(outer, Dictionary) or _role_of(ctx, outer) != "L" or (holder_role == "LBody" and _role_of(ctx, anchor) != "LI"):
        raise EditError(f"Edit {n}: this item is already at the top level of its list.")
    rest = items[pos + 1:]
    sub.K = Array(items[:pos])
    outer_items = _items(outer.get("/K"))
    at = next((i for i, k in enumerate(outer_items) if _same(k, anchor)), None)
    if at is None:
        raise EditError(f"Edit {n}: the item wasn't found in its list.")
    outer_items.insert(at + 1, e.obj)
    e.obj.P = outer
    if rest:
        under = _sublist_of(ctx, e.obj, sub)
        for item in rest:
            item.P = under
        under.K = Array(_items(under.get("/K")) + rest)
    if not _items(sub.get("/K")):  # nothing left in the nested list
        if holder_role == "LBody":
            holder.K = Array([k for k in _items(holder.get("/K")) if not _same(k, sub)])
        else:
            outer_items = [k for k in outer_items if not _same(k, sub)]
    outer.K = Array(outer_items)
    return f"element {e.index}: list item moved one level out"


# --- a list made into a table of contents -------------------------------------------------------------

def _make_toc(ctx: _Ctx, e: Elem, n: int) -> str:
    """A list becomes a table of contents. The tags change type where they are, so nothing is read in a different
    order: L -> TOC, LI -> TOCI (its label stays), the item's body -> Reference, and a list nested in the body moves
    out of it to follow the Reference in the same entry, as a TOC of its own."""
    if not e.obj.is_indirect or _role_of(ctx, e.obj) != "L":
        raise EditError(f"Edit {n}: only a list can be made into a table of contents.")
    parent = _parent_obj(ctx, e)
    if not _at_root(ctx, parent) and _role_of(ctx, parent) in ("L", "LI", "LBody", "TOC", "TOCI"):
        raise EditError(f"Edit {n}: this list is nested in another one. Make the outer list a table of contents; "
                        "its nested lists come with it.")
    elems = {x.obj.objgen: x for x in ctx.elems if x.obj.is_indirect}
    entries = _toc_from(ctx, e.obj, elems, n)
    return f"element {e.index}: list made into a table of contents of {entries} entr{'y' if entries == 1 else 'ies'}"


def _drop_list_layout(obj: Dictionary) -> None:
    """Remove the List attributes (bullet style and the like), which mean nothing on a table of contents."""
    is_list = lambda a: isinstance(a, Dictionary) and a.get("/O") == Name.List  # noqa: E731
    attrs = obj.get("/A")
    if is_list(attrs):
        del obj["/A"]
    elif isinstance(attrs, Array):
        kept, skip = [], False
        for a in attrs:
            if skip and isinstance(a, int):  # the revision number that follows an attribute dictionary
                skip = False
                continue
            skip = is_list(a)
            if not skip:
                kept.append(a)
        if kept:
            obj.A = Array(kept)
        else:
            del obj["/A"]


def _toc_from(ctx: _Ctx, lst: Dictionary, elems: dict, n: int) -> int:
    """Make a list and its items a TOC in place; returns the number of entries, nested ones included."""
    lst.S = Name.TOC
    _drop_list_layout(lst)
    entries = 0
    for item in _items(lst.get("/K")):
        role = _role_of(ctx, item) if is_elem(item) else None
        if role == "L":  # a list nested directly in the list (L > L)
            entries += _toc_from(ctx, item, elems, n)
        elif role == "LI":
            entries += 1 + _toci_from(ctx, item, elems, n)
        # A caption stays; content held directly by the list is already reported by the checks
    return entries


def _toci_from(ctx: _Ctx, li: Dictionary, elems: dict, n: int) -> int:
    li.S = Name.TOCI
    entries, kids = 0, []
    for kid in _items(li.get("/K")):
        role = _role_of(ctx, kid) if is_elem(kid) else None
        if role == "LBody":
            parts, more = _entry_parts(ctx, kid, li, elems, n)
            kids += parts
            entries += more
        else:
            if role == "L":
                entries += _toc_from(ctx, kid, elems, n)
            kids.append(kid)  # the label, or what the item held directly
    li.K = Array(kids)
    return entries


def _entry_parts(ctx: _Ctx, body: Dictionary, li: Dictionary, elems: dict, n: int) -> tuple[list, int]:
    """What an item's body becomes in its entry: its text a Reference (the body itself, retyped), a paragraph in it
    stays a paragraph, and a nested list a TOC. When text follows a nested list, that text gets a Reference of its
    own, so the order stays as it was."""
    runs: list[list] = []   # each is ["ref", items] or ["keep", tag]
    for k in _items(body.get("/K")):
        role = _role_of(ctx, k) if is_elem(k) else None
        if role in ("L", "P"):
            runs.append(["keep", k])
        elif role in BLOCKS:
            raise EditError(f"Edit {n}: a list item holds a {role}; an entry in a table of contents can hold only "
                            "a label, its text, paragraphs and nested entries. Change that tag first.")
        elif runs and runs[-1][0] == "ref":
            runs[-1][1].append(k)
        else:
            runs.append(["ref", [k]])
    out, entries, reused = [], 0, False
    owner = elems.get(body.objgen) if body.is_indirect else None
    for kind, val in runs:
        if kind == "keep":
            val.P = li
            if _role_of(ctx, val) == "L":
                entries += _toc_from(ctx, val, elems, n)
            out.append(val)
        elif not reused:
            body.S = Name.Reference
            body.K = Array(val)
            reused = True
            out.append(body)
        else:
            if owner is None:  # a body made earlier in this save: its content already names its page
                owner = Elem(-1, body, "LBody", "LBody", None, None)
            ref = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Reference, P=li))
            for key in ("/Lang", "/Pg"):
                if key in body:
                    ref[key] = body[key]
            ref.K = Array(_reparent_items(ctx, owner, val, ref))
            out.append(ref)
    if reused:
        body.P = li
    return out, entries


def _check_build_list(op: dict, n: int) -> None:
    absorb, rows = op.get("absorb") or [], op.get("rows")
    if (not isinstance(absorb, list) or len(absorb) > 500 or op["element"] in absorb or len(set(absorb)) != len(absorb)
            or not all(isinstance(a, int) and not isinstance(a, bool) and a >= 0 for a in absorb)):
        raise EditError(f"Edit {n}: building a list needs the other tags whose lines it takes.")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise EditError(f"Edit {n}: building a list needs its rows (at most {MAX_ROWS}).")
    for row in rows:
        whole = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
        if (not isinstance(row, dict) or not whole(row.get("at")) or row["at"] < 0
                or ("el" in row and row["el"] != op["element"] and row["el"] not in absorb)
                or ("level" in row) == ("as" in row)
                or ("level" in row and not (whole(row["level"]) and 1 <= row["level"] <= MAX_LIST_LEVEL))
                or ("as" in row and row["as"] not in ROW_ROLES)):
            raise EditError(f"Edit {n}: each row of a list being built needs where it starts and either a "
                            f"list level (1 to {MAX_LIST_LEVEL}) or a type ({', '.join(sorted(ROW_ROLES))}).")


register("make_list", lambda ctx, op, e, t, n: _make_list(ctx, e, t, n), _check_absorb, _absorbed)
register("build_list", lambda ctx, op, e, t, n: _build_list(ctx, e, t, op["rows"], n), _check_build_list, _absorbed)
register("indent", lambda ctx, op, e, t, n: _indent(ctx, e, n))
register("outdent", lambda ctx, op, e, t, n: _outdent(ctx, e, n))
register("make_toc", lambda ctx, op, e, t, n: _make_toc(ctx, e, n))


def _set_toc_target(ctx: _Ctx, e: Elem, target: Elem | None, n: int) -> str:
    """A table of contents entry's target (/Ref, PDF/UA-2 8.2.5.8): the tag it goes to, or none when target is None
    (#631)."""
    if _role_of(ctx, e.obj) != "TOCI" or not e.obj.is_indirect:
        raise EditError(f"Edit {n}: only a table of contents entry (TOCI) goes to a heading.")
    if target is None:
        if "/Ref" in e.obj:
            del e.obj["/Ref"]
        return f"element {e.index}: goes nowhere"
    if not target.obj.is_indirect or _role_of(ctx, target.obj) in ("TOC", "TOCI"):
        raise EditError(f"Edit {n}: element {target.index} is part of a table of contents. Choose the heading the "
                        "entry goes to.")
    e.obj.Ref = Array([target.obj])
    return f"element {e.index}: goes to element {target.index}"


def _check_toc_target(op: dict, n: int) -> None:
    t = op.get("target")
    if t is not None and (not isinstance(t, int) or isinstance(t, bool) or t < 0 or t == op["element"]):
        raise EditError(f"Edit {n}: a contents entry's target is another tag (or null for none).")


register("set_toc_target", lambda ctx, op, e, t, n: _set_toc_target(ctx, e, t, n), _check_toc_target,
         lambda ctx, op, n: _elem(ctx, op["target"], None, n) if op.get("target") is not None else None)
