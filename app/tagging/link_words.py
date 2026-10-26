"""A kept Link tag that holds only its annotation gets the link's words (#489).

A producer that attaches a link annotation straight to the paragraph holding its words leaves the words in the
paragraph; the kept-tags repair wraps the annotation alone in a Link tag (it can't tell which words are the link's).
A screen reader then may announce "link" without saying which words. Here the paragraph's own runs that lie inside the
link's box (each box, for a link that wraps across lines: its QuadPoints) move into the Link, in reading order, and
the Link takes their place in the paragraph. A run only partly inside (a whole line tagged as one run) stays: a run
can't be split, and the words around the link aren't the link's. So does anything that isn't text alone.

Some producers (Word, for a table's links) put the Links in a paragraph of their own, holding nothing but Links,
while the words are elsewhere: in the table's cells. A Link still without words after the above moves into the one
tag that holds its words, around them, the same way; a paragraph left empty goes. When the runs inside the link's box
belong to more than one tag, the Link stays where it is: which one it belongs to isn't certain.

Run before the parent tree is regenerated (struct_tree rebuilds it from the final tree).
"""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.models import BBox, bbox_area, bbox_intersection
from app.tagging.content_marker import Matrix, apply, bbox_of_points, mcid_of
from app.tagging.existing_tags import _is_struct_elem, resolve_role
from app.tagging.tree import items as _items, new_mcr

INSIDE = 0.6  # this share of a run's box inside the link's box makes it the link's


def link_boxes(annot: Dictionary, m: Matrix) -> list[BBox]:
    """The link's boxes in page space: one per quadrilateral of /QuadPoints, else its /Rect."""
    out: list[BBox] = []
    quads = annot.get("/QuadPoints")
    try:
        nums = [float(v) for v in quads] if isinstance(quads, Array) else []
    except (TypeError, ValueError):
        nums = []
    for i in range(0, len(nums) - 7, 8):
        b = bbox_of_points(apply(m, nums[i + j], nums[i + j + 1]) for j in range(0, 8, 2))
        if b is not None:
            out.append(b)
    if not out:
        try:
            x0, y0, x1, y1 = (float(v) for v in annot.Rect)
        except (AttributeError, TypeError, ValueError):
            return []
        b = bbox_of_points(apply(m, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)))
        if b is not None:
            out.append(b)
    return out


def move_link_words(pdf: pikepdf.Pdf, existing_bbox: dict, existing_kinds: dict, matrices: dict,
                    role_map: dict[str, str]) -> int:
    """existing_bbox / existing_kinds: page objgen -> {MCID: bbox in page space / what it draws}, from the content
    marker; matrices: page objgen -> PDF space to page space. Returns how many Links got their words."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return 0
    done = 0

    def run_box(item, pg) -> tuple | None:
        """((page objgen, MCID), bbox) of a run of text that's one of this tag's own, else None."""
        if isinstance(item, Dictionary):
            if "/MCID" not in item or "/Stm" in item or _is_struct_elem(item):
                return None
            owner, mcid = item.get("/Pg") or pg, mcid_of(item)
        else:
            try:
                owner, mcid = pg, int(item)
            except (TypeError, ValueError):
                return None
        if owner is None or mcid is None or not owner.is_indirect:
            return None
        if existing_kinds.get(owner.objgen, {}).get(mcid, set()) != {"text"}:
            return None
        box = existing_bbox.get(owner.objgen, {}).get(mcid)
        return ((owner.objgen, mcid), box) if box is not None and bbox_area(box) > 0 else None

    def links_of(e) -> list[tuple[Dictionary, list[BBox]]]:
        """The Link tags among e's kids that hold annotations and no words, with the annotations' boxes."""
        out = []
        for k in _items(e.get("/K")):
            if not (isinstance(k, Dictionary) and _is_struct_elem(k) and k.is_indirect):
                continue
            if resolve_role(str(k.get("/S", ""))[1:], role_map) != "Link":
                continue
            kids = _items(k.get("/K"))
            objrs = [x for x in kids if isinstance(x, Dictionary) and x.get("/Type") == Name.OBJR]
            if not objrs or len(objrs) != len(kids):
                continue
            boxes = []
            for r in objrs:
                annot = r.get("/Obj")
                page = r.get("/Pg") or k.get("/Pg") or e.get("/Pg")
                if not isinstance(annot, Dictionary) or annot.get("/Subtype") != Name.Link or page is None:
                    continue
                m = matrices.get(page.objgen)
                if m is not None:
                    boxes += link_boxes(annot, m)
            if boxes:
                out.append((k, boxes))
        return out

    def place(link, words: list, target, pg) -> None:
        """Put the Link where its first word was in `target`'s kids, holding the words (in order) and its annotations."""
        items = _items(target.get("/K"))
        first = min(i for i, item in enumerate(items) if any(_same(item, w) for w in words))
        out = items[:first] + [link]
        out += [item for item in items[first:] if not any(_same(item, w) for w in words)]
        if pg is not None and "/Pg" not in link:
            link.Pg = pg
        same_page = pg is None or link.Pg.objgen == pg.objgen
        words = [w if isinstance(w, Dictionary) or same_page else new_mcr(pg, int(w))
                 for w in words]
        link.K = Array(words + _items(link.get("/K")))
        target.K = Array(out)

    def visit(e, depth: int = 0) -> None:
        nonlocal done
        if depth > 200:
            return
        items = _items(e.get("/K"))
        links = links_of(e) if _is_struct_elem(e) else []
        if links:
            pg = e.get("/Pg")
            owner_of: dict[int, int] = {}  # item index -> link index
            for i, item in enumerate(items):
                rb = run_box(item, pg)
                if rb is None:
                    continue
                best, share = None, 0.0
                for n, (_, boxes) in enumerate(links):
                    inside = sum(bbox_intersection(rb[1], b) for b in boxes) / bbox_area(rb[1])
                    if inside > share:
                        best, share = n, inside
                if best is not None and share >= INSIDE:
                    owner_of[i] = best
            if owner_of:
                moved: dict[int, list] = {}
                for i in sorted(owner_of):
                    moved.setdefault(owner_of[i], []).append(items[i])
                index_of = {link.objgen: n for n, (link, _) in enumerate(links)}
                placed, out = set(), []
                for i, item in enumerate(items):
                    if i in owner_of:
                        n = owner_of[i]
                        if n not in placed:  # the Link goes where its first word was
                            out.append(links[n][0])
                            placed.add(n)
                    elif not (isinstance(item, Dictionary) and item.is_indirect
                              and index_of.get(item.objgen) in moved):
                        out.append(item)  # (a Link that got words is already placed)
                for n, words in moved.items():
                    link = links[n][0]
                    if pg is not None and "/Pg" not in link:
                        link.Pg = pg
                    same_page = pg is None or link.Pg.objgen == pg.objgen
                    words = [w if isinstance(w, Dictionary) or same_page else new_mcr(pg, int(w))
                             for w in words]
                    link.K = Array(words + _items(link.get("/K")))
                    done += 1
                e.K = Array(out)
        for k in _items(e.get("/K")):
            if isinstance(k, Dictionary) and _is_struct_elem(k):
                visit(k, depth + 1)

    def elsewhere() -> None:
        """Links still without words, whose words are the runs of one other tag on their page: into that tag."""
        nonlocal done
        runs: dict[tuple, list] = {}  # page objgen -> [(tag, item, key, box)], every tag's own runs of text
        waiting: list = []  # (parent, link, boxes, page objgen)

        def index(e, depth: int = 0) -> None:
            if depth > 200:
                return
            role = resolve_role(str(e.get("/S", ""))[1:], role_map)
            pg = e.get("/Pg")
            for item in _items(e.get("/K")):
                if isinstance(item, Dictionary) and _is_struct_elem(item):
                    index(item, depth + 1)
                elif role != "Link":
                    rb = run_box(item, pg)
                    if rb is not None:
                        runs.setdefault(rb[0][0], []).append((e, item, rb[0], rb[1]))
            if _is_struct_elem(e):
                for link, boxes in links_of(e):
                    page = next((r.get("/Pg") for r in _items(link.K) if isinstance(r, Dictionary)
                                 and r.get("/Pg") is not None), None) or link.get("/Pg") or pg
                    if page is not None:
                        waiting.append((e, link, boxes, page.objgen))

        for top in _items(root.get("/K")):
            if isinstance(top, Dictionary):
                index(top)
        taken: set = set()
        for parent, link, boxes, page in waiting:
            inside = [(tag, item, key) for tag, item, key, box in runs.get(page, [])
                      if key not in taken
                      and sum(bbox_intersection(box, b) for b in boxes) / bbox_area(box) >= INSIDE]
            tags = {tag.objgen for tag, _, _ in inside}
            if len(tags) != 1:
                continue  # no words, or words in more than one tag: not certain whose they are
            target = inside[0][0]
            if target.objgen == parent.objgen or _within(parent, target):
                continue
            parent.K = Array([k for k in _items(parent.get("/K"))
                              if not (isinstance(k, Dictionary) and k.is_indirect and k.objgen == link.objgen)])
            place(link, [item for _, item, _ in inside], target, target.get("/Pg"))
            link.P = target
            taken |= {key for _, _, key in inside}
            done += 1
            if not _items(parent.get("/K")):
                _drop(root, parent)

    try:
        for top in _items(root.get("/K")):
            if isinstance(top, Dictionary):
                visit(top)
        elsewhere()
    except RecursionError:
        pass
    return done


def _same(a, b) -> bool:
    """The same kid of one tag: the same MCID, as a number or a marked-content reference on the same page."""
    if isinstance(a, Dictionary) or isinstance(b, Dictionary):
        if not (isinstance(a, Dictionary) and isinstance(b, Dictionary)) or a.get("/MCID") is None:
            return False
        pa, pb = a.get("/Pg"), b.get("/Pg")
        same_page = pa is None and pb is None or (pa is not None and pb is not None and pa.objgen == pb.objgen)
        return int(a.MCID) == int(b.get("/MCID", -1)) and same_page
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def _within(e, ancestor) -> bool:
    """Whether e lies inside `ancestor`."""
    p, n = e.get("/P"), 0
    while isinstance(p, Dictionary) and n < 200:
        if p.is_indirect and p.objgen == ancestor.objgen:
            return True
        p, n = p.get("/P"), n + 1
    return False


def _drop(root, e) -> None:
    """Take an emptied tag out of its parent's kids."""
    parent = e.get("/P") or root
    parent.K = Array([k for k in _items(parent.get("/K"))
                      if not (isinstance(k, Dictionary) and k.is_indirect and k.objgen == e.objgen)])
