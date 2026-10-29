"""What every edit operation shares: the edit's context, the table of operations, and the tree primitives."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.tagging.content_marker import mcid_of
from app.tagging.table_headers import id_of
from app.tagging.tree import STANDARD_TYPES as STANDARD
from app.tagging.tree import is_elem, new_mcr, number_tree_get, walk
from app.tagging.tree import items as _items
from app.verification.integrity import Elem, IntegrityChecker

# Text tags that can become a list item as they are. A figure or formula would lose its alt text, and the inline
# ones (Span, Quote, Reference) belong to the text around them
LIST_ITEM_FROM = {"H", "H1", "H2", "H3", "H4", "H5", "H6", "P", "Caption", "BlockQuote", "Note", "FENote", "Code",
                  "BibEntry"}
# Tags that hold content of their own (not only other tags): the ones a piece of content can be given to
CONTENT_ROLES = {"H", "H1", "H2", "H3", "H4", "H5", "H6", "P", "Caption", "BlockQuote", "Note", "FENote", "Code", "Quote",
                 "Span", "Reference", "BibEntry", "Figure", "Formula", "TD", "TH", "LBody", "Lbl", "Link", "Form", "TOCI"}
# A footnote or endnote: PDF 1.7's Note, or PDF 2.0's FENote, as a PDF/UA-2 file has them (#633)
NOTES = {"Note", "FENote"}
# Tags a new area can become (a cell, list part or link needs its structure, so isn't made from artifacts)
NEW_AREA_ROLES = {"H1", "H2", "H3", "H4", "H5", "H6", "P", "Caption", "BlockQuote", "Note", "Code", "Quote",
                  "Figure", "Formula"}
MAX_TEXT = 4000
WAS_MCID = Name("/RemediationWasMCID")
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
    # Artifact sections already tagged by earlier edits, per page (numbered as in the tag map)
    tagged_sections: dict | None = None
    ids_changed: bool = False  # an element ID was given or taken away: the IDTree is rebuilt before saving
    made: Dictionary | None = None  # the tag the last split made
    # Content earlier edits made artifacts (a tag deleted with what's in it), as (page objgen, mcid); its artifact
    # sections say which MCID they were (WAS_MCID) until the save, so a later edit can tag them again
    untagged: set = field(default_factory=set)
    artifacted: dict = field(default_factory=dict)  # the streams those sections are in, by objgen
    # How many of a piece of text's glyphs are still its own after cuts earlier in this save, by (page objgen, mcid):
    # a piece cut twice (a table cell's words into three cells) is cut from its end, each cut leaving it shorter
    cut_glyphs: dict = field(default_factory=dict)
    # Tags a save makes and its later edits name (refs.py, #692): the tags each named edit made, by its id; the tags
    # named by number, by that number; and where each object is in the tag index, kept until the index changes
    new_tags: list = field(default_factory=list)  # the tags the edit running makes, in order
    named: dict = field(default_factory=dict)
    pinned: dict = field(default_factory=dict)
    where: tuple | None = None


@dataclass(frozen=True)
class Operation:
    """One edit operation. validate(op, n) checks its shape before the PDF is opened; resolve(ctx, op, n) finds the
    other tags it names before any edit is applied; apply(ctx, op, e, t, n) makes the edit and says what it did."""
    apply: Callable[..., str]
    validate: Callable[[dict, int], None] | None = None
    resolve: Callable[..., object] | None = None
    element: bool = True  # False: the document's own, naming no element


# Every operation by name; each module of the package adds its own
OPERATIONS: dict[str, Operation] = {}


def register(name: str, apply, validate=None, resolve=None, element: bool = True) -> None:
    OPERATIONS[name] = Operation(apply, validate, resolve, element)


def _elem(ctx: _Ctx, index: int, expect_role: str | None, n: int) -> Elem:
    if index >= len(ctx.elems):
        raise EditError(f"Edit {n}: element {index} doesn't exist in this version. Reload and try again.")
    e = ctx.elems[index]
    if expect_role and expect_role not in (e.role, e.raw_role):
        raise EditError(f"Edit {n}: element {index} is a {e.role}, not a {expect_role}. "
                        "The document changed; reload and try again.")
    return e


def _absorbed(ctx: _Ctx, op: dict, n: int) -> list[Elem]:
    """The other tags an op takes in ("absorb")."""
    return [_elem(ctx, a, None, n) for a in op.get("absorb") or []]


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


def _give_id(ctx: _Ctx, cell: Dictionary, want: str) -> bytes:
    if id_of(cell):
        return id_of(cell)
    used = {id_of(x) for x in walk(ctx.root)}
    name, k = want.encode(), 1
    while name in used:
        k += 1
        name = f"{want}-{k}".encode()
    cell.ID = String(name)
    ctx.ids_changed = True
    return name


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


def _role_of(ctx: _Ctx, obj) -> str | None:
    """A tag's standard role, through the tree's RoleMap (PowerPoint's "Textbox" is a Sect)."""
    if not isinstance(obj, Dictionary) or obj.get("/S") is None:
        return None
    role = str(obj.S)[1:]
    rm = ctx.root.get("/RoleMap")
    seen = set()
    while role not in STANDARD and isinstance(rm, Dictionary) and isinstance(rm.get("/" + role), Name) and role not in seen:
        seen.add(role)
        role = str(rm["/" + role])[1:]
    return role


def _clear_parent_tree(ctx: _Ctx, owner, mcids: set[int]) -> None:
    key = owner.get("/StructParents") if owner is not None else None
    pt = ctx.root.get("/ParentTree")
    if key is None or not isinstance(pt, Dictionary):
        return
    arr = number_tree_get(pt, int(key))
    if isinstance(arr, Array):
        for m in mcids:
            if m < len(arr):
                arr[m] = pikepdf.Object.parse(b"null")


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
        if is_elem(item):
            item.P = new_parent
            out.append(item)
            continue
        if isinstance(item, int) or (isinstance(item, Dictionary) and "/MCID" in item):
            if isinstance(item, int):
                if page is None:
                    raise EditError(f"element {owner_elem.index} has content without a page; it can't be moved")
                item = new_mcr(page, item)
            elif "/Pg" not in item and "/Stm" not in item and page is not None:
                item.Pg = page
            owner = item.get("/Stm") if item.get("/Stm") is not None else item.get("/Pg")
            key = owner.get("/StructParents") if owner is not None else None
            if key is not None and isinstance(pt, Dictionary):
                arr = number_tree_get(pt, int(key))
                mcid = mcid_of(item)
                if mcid is not None and isinstance(arr, Array) and mcid < len(arr):
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


def _at_root(ctx: _Ctx, obj) -> bool:
    return obj is ctx.root or (obj.is_indirect and ctx.root.is_indirect and obj.objgen == ctx.root.objgen)


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
    ctx.made = new
    return new
