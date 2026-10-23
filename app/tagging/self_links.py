"""Links that go nowhere (#484): a link annotation whose destination is its own place on its own page. Word makes one
for each caption it bookmarks ("Table 5" linking to "Table 5"). Following it does nothing, a screen reader still
announces a link, and inside a Caption PAC warns of a possibly inappropriate Link (veraPDF doesn't). The annotation
goes, with its reference in the structure tree and its parent-tree entry; a Link tag left empty goes too, and one
that held words gives them back to its parent in its place. The words stay where they were."""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name

NEAR = 4  # points: a destination this close to the link's own box is the link itself


def _named(pdf: pikepdf.Pdf, name) -> object:
    """A named destination's array (the Names tree, or the older /Dests dictionary)."""
    key = str(name)
    try:
        dests = pdf.Root.Names.Dests
        found = pikepdf.NameTree(dests).get(key)
    except (AttributeError, KeyError, TypeError, ValueError):
        found = None
    if found is None:
        old = pdf.Root.get("/Dests")
        if isinstance(old, Dictionary):
            found = old.get(Name("/" + key.lstrip("/")))
    if isinstance(found, Dictionary):
        found = found.get("/D")
    return found


def _destination(pdf: pikepdf.Pdf, annot: Dictionary) -> Array | None:
    dest = annot.get("/Dest")
    if dest is None:
        action = annot.get("/A")
        if isinstance(action, Dictionary) and action.get("/S") == Name.GoTo:
            dest = action.get("/D")
    if isinstance(dest, (pikepdf.String, pikepdf.Name)):
        dest = _named(pdf, dest)
    return dest if isinstance(dest, Array) and len(dest) >= 2 else None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def points_to_itself(pdf: pikepdf.Pdf, page: Dictionary, annot: Dictionary) -> bool:
    """A link whose destination is on its own page, at its own box (by the destination's top; and left for /XYZ)."""
    dest = _destination(pdf, annot)
    if dest is None or not isinstance(dest[0], Dictionary) or dest[0].objgen != page.objgen:
        return False
    try:
        x0, y0, x1, y1 = (float(v) for v in annot.Rect)
    except (AttributeError, TypeError, ValueError):
        return False
    lo, hi = min(y0, y1), max(y0, y1)
    kind = dest[1]
    if kind == Name.XYZ and len(dest) >= 4:
        left, top = _num(dest[2]), _num(dest[3])
        if top is None:
            return False
        return lo - NEAR <= top <= hi + NEAR and (left is None or left <= max(x0, x1) + NEAR)
    if kind in (Name.FitH, Name.FitBH) and len(dest) >= 3:
        top = _num(dest[2])
        return top is not None and lo - NEAR <= top <= hi + NEAR
    return False


def _kids(e: Dictionary) -> list:
    k = e.get("/K")
    if k is None:
        return []
    return list(k) if isinstance(k, Array) else [k]


def _is_objr_for(item, annot: Dictionary) -> bool:
    if not isinstance(item, Dictionary) or item.get("/Type") != Name.OBJR:
        return False
    obj = item.get("/Obj")
    return isinstance(obj, Dictionary) and obj.objgen == annot.objgen


def _holder(pdf: pikepdf.Pdf, annot: Dictionary) -> Dictionary | None:
    """The structure element holding the annotation's reference, from the parent tree."""
    sp = annot.get("/StructParent")
    tree = pdf.Root.get("/StructTreeRoot")
    if sp is None or not isinstance(tree, Dictionary) or "/ParentTree" not in tree:
        return None
    try:
        e = pikepdf.NumberTree(tree.ParentTree).get(int(sp))
    except (TypeError, ValueError, KeyError):
        return None
    return e if isinstance(e, Dictionary) else None


def _reassign(pdf: pikepdf.Pdf, page: Dictionary | None, mcid: int, owner: Dictionary) -> None:
    """The parent tree's entry for a page's marked content, pointed at its new element (the tree is already built)."""
    if page is None or "/StructParents" not in page:
        return
    try:
        seq = pikepdf.NumberTree(pdf.Root.StructTreeRoot.ParentTree).get(int(page.StructParents))
    except (AttributeError, KeyError, TypeError, ValueError):
        return
    if isinstance(seq, Array) and 0 <= mcid < len(seq):
        seq[mcid] = owner


def _detach(pdf: pikepdf.Pdf, annot: Dictionary) -> None:
    e = _holder(pdf, annot)
    if e is not None:
        e.K = Array([it for it in _kids(e) if not _is_objr_for(it, annot)])
        parent = e.get("/P")
        if e.get("/S") == Name.Link and isinstance(parent, Dictionary):
            rest = list(e.K)
            for r in rest:  # the words now belong to the parent
                if isinstance(r, int):
                    _reassign(pdf, e.get("/Pg"), r, parent)
                elif isinstance(r, Dictionary) and r.get("/Type") == Name.MCR and "/Stm" not in r:
                    _reassign(pdf, r.get("/Pg") or e.get("/Pg"), int(r.MCID), parent)
            new = []
            for it in _kids(parent):
                if isinstance(it, Dictionary) and it.objgen == e.objgen:
                    for r in rest:  # the Link's words take its place
                        if isinstance(r, Dictionary) and r.get("/Type") == Name.StructElem:
                            r.P = parent
                        elif isinstance(r, int) and "/Pg" in e and "/Pg" not in parent:
                            parent.Pg = e.Pg
                        new.append(r)
                else:
                    new.append(it)
            parent.K = Array(new)
    sp = annot.get("/StructParent")
    if sp is not None:
        try:
            del pikepdf.NumberTree(pdf.Root.StructTreeRoot.ParentTree)[int(sp)]
        except (AttributeError, KeyError, TypeError, ValueError):
            pass


def drop_self_links(pdf: pikepdf.Pdf) -> int:
    """Remove the links that point to themselves. Returns how many."""
    dropped = 0
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if not isinstance(annots, Array):
            continue
        gone = [a for a in annots if isinstance(a, Dictionary) and a.get("/Subtype") == Name.Link
                and points_to_itself(pdf, page.obj, a)]
        for a in gone:
            _detach(pdf, a)
        if gone:
            page.obj.Annots = Array([a for a in annots if not any(a.objgen == g.objgen for g in gone)])
            dropped += len(gone)
    return dropped
