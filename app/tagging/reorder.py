"""Put kept tags in reading order.

When a document's existing tags are kept, their order is the producer's. PowerPoint's is often far from how a slide
reads: the title after the body, the tabs at the end. This reorders the children of a container on one page by the
page's reading order (the AI's where it ran, else the layout's), but only where the kept order clearly disagrees with
it (a share of sibling pairs out of order), and only on slide-like (landscape) pages. On portrait pages a careful
author's order (footnotes, figures placed where they're discussed) is more trustworthy than a layout reading of two
columns, so it's kept and only reported, as before. Tables, lists and other elements move as whole units; only
siblings move, never content between elements. Elements with no position of their own (a Link with only its
annotation) stay right after the element they followed.

New elements for a page (content the old tags left out) are first moved into that page's own container when the page
has one (a slide's Sect), so they're ordered with the rest of the slide.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary

from app.pipeline.models import DocumentAnalysis, bbox_area, bbox_intersection
from app.tagging.content_marker import mcid_of
from app.tagging.existing_tags import _is_struct_elem, resolve_role
from app.tagging.tree import items as _items

CONTAINERS = {"Document", "Part", "Art", "Sect", "Div", "NonStruct", "BlockQuote", "Aside"}
DISAGREEMENT = 0.15  # reorder a run of siblings only when at least this share of its pairs is out of reading order
MIN_RUN = 3


class Reorderer:
    def __init__(self, pdf: pikepdf.Pdf, analysis: DocumentAnalysis, marks: dict, role_map: dict[str, str]):
        self.analysis = analysis
        self.role_map = role_map
        self.page_index = {p.obj.objgen: i for i, p in enumerate(pdf.pages)}
        # (stream objgen, mcid) -> (page, bbox of the content)
        self.boxes: dict[tuple, tuple[int, tuple]] = {}
        for page_no, streams in marks.items():
            pa = analysis.pages[page_no]
            for sm in streams:
                og = sm.owner.objgen
                for mcid, bbox in sm.existing.items():
                    if bbox:
                        self.boxes[(og, mcid)] = (page_no, bbox)
                for mcid, key in sm.mcids:
                    el = pa.element(key.split(":")[0])
                    if el is not None:
                        self.boxes[(og, mcid)] = (page_no, el.bbox)
        self.order: dict[int, list[tuple[int, tuple]]] = {}
        for pa in analysis.pages:
            self.order[pa.number] = [(n, el.bbox) for n, eid in enumerate(pa.reading_order)
                                     if (el := pa.element(eid)) is not None]
        self._memo: dict = {}
        self.reordered = 0
        self.slides = {pa.number for pa in analysis.pages if pa.width > pa.height}

    def role(self, e) -> str:
        return resolve_role(str(e.get("/S", "/NonStruct"))[1:], self.role_map)

    def _rank(self, page: int, bbox) -> float | None:
        """The place in the page's reading order of the block this content belongs to; content in no block (a
        banner, a footer's box) is placed by height, among the blocks above and below it."""
        order = self.order.get(page, [])
        if not order:
            return None
        area = bbox_area(bbox) or 1.0
        best, best_ov = None, 0.0
        for n, b in order:
            ov = bbox_intersection(bbox, b) / area
            if ov > best_ov:
                best, best_ov = n, ov
        if best_ov > 0.3:
            return float(best)
        cy = (bbox[1] + bbox[3]) / 2
        # A mark beside a block (an item's bullet or legend mark) goes just before it
        beside = [(b[0] - bbox[2], n) for n, b in order if b[1] - 2 <= cy <= b[3] + 2 and 0 <= b[0] - bbox[2] <= 30]
        if beside:
            return min(beside)[1] - 0.5
        return sum(1 for _, b in order if (b[1] + b[3]) / 2 < cy) - 0.5

    def info(self, e) -> tuple[set[int], float | None]:
        """The pages an element's content is on, and its earliest place in the reading order."""
        key = e.objgen if e.is_indirect else id(e)
        if key in self._memo:
            return self._memo[key]
        pages: set[int] = set()
        ranks: list[float] = []
        pg = e.get("/Pg")
        own_owner = pg.objgen if pg is not None and pg.is_indirect else None
        for it in _items(e.get("/K")):
            target = None
            if isinstance(it, int) or (not isinstance(it, Dictionary) and not isinstance(it, pikepdf.Array)):
                try:
                    target = (own_owner, int(it))
                except (TypeError, ValueError):
                    continue
            elif isinstance(it, Dictionary) and _is_struct_elem(it):
                p, r = self.info(it)
                pages |= p
                if r is not None:
                    ranks.append(r)
                continue
            elif isinstance(it, Dictionary) and "/MCID" in it:
                stm, ipg = it.get("/Stm"), it.get("/Pg")
                owner = stm.objgen if stm is not None else (ipg.objgen if ipg is not None else own_owner)
                target = (owner, mcid_of(it))
            if target is None or target not in self.boxes:
                continue
            page, bbox = self.boxes[target]
            pages.add(page)
            r = self._rank(page, bbox)
            if r is not None:
                ranks.append(r)
        out = (pages, min(ranks) if ranks else None)
        self._memo[key] = out
        return out

    # ------------------------------------------------------------------ moving
    def gather_page_elements(self, parent) -> None:
        """Elements right after a page's own container, on that page only, join it (a slide's Sect)."""
        kids = _items(parent.get("/K"))
        out: list = []
        holder = None
        for k in kids:
            if not _is_struct_elem(k):
                out.append(k)
                holder = None
                continue
            pages, _ = self.info(k)
            if holder is not None and self.role(k) not in CONTAINERS and len(pages) == 1 \
                    and pages == self.info(holder)[0] and pages <= self.slides:
                holder.K = Array(_items(holder.get("/K")) + [k])
                k.P = holder
                continue
            out.append(k)
            holder = k if self.role(k) in CONTAINERS and len(pages) == 1 else None
        if len(out) != len(kids):
            parent.K = Array(out)
            self._memo.clear()

    def reorder(self, parent) -> None:
        kids = _items(parent.get("/K"))
        if len(kids) < 2 or not all(_is_struct_elem(k) for k in kids):
            for k in kids:
                if _is_struct_elem(k) and self.role(k) in CONTAINERS:
                    self.reorder(k)
            return
        for k in kids:
            if self.role(k) in CONTAINERS:
                self.reorder(k)
        # Runs of siblings on one page; an element with no position goes with the one before it
        runs: list[list[list]] = []  # run -> groups -> elements
        run_page = None
        for k in kids:
            pages, rank = self.info(k)
            if len(pages) > 1:
                runs.append([[k]])
                run_page = None
                continue
            page = next(iter(pages)) if pages else None
            if rank is None or page is None:
                if runs and runs[-1]:
                    runs[-1][-1].append(k)
                else:
                    runs.append([[k]])
                continue
            if page != run_page:
                runs.append([])
                run_page = page
            runs[-1].append([k])
        new: list = []
        changed = False
        for run in runs:
            ranked = [self.info(g[0])[1] for g in run]
            pages_in_run = set().union(*(self.info(g[0])[0] for g in run)) if run else set()
            if len(run) >= MIN_RUN and all(r is not None for r in ranked) and len(pages_in_run) == 1 \
                    and pages_in_run <= self.slides:
                pairs = [(ranked[i], ranked[j]) for i in range(len(ranked)) for j in range(i + 1, len(ranked))]
                inverted = sum(1 for a, b in pairs if a > b) / len(pairs)
                if inverted >= DISAGREEMENT:
                    order = sorted(range(len(run)), key=lambda i: (ranked[i], i))
                    if order != list(range(len(run))):
                        run = [run[i] for i in order]
                        changed = True
            for g in run:
                new.extend(g)
        if changed:
            parent.K = Array(new)
            self.reordered += 1


    # ------------------------------------------------------------------ marks
    def own_box(self, e) -> tuple[int | None, tuple | None]:
        """The page and bounds of an element's own content (not its children's)."""
        pg = e.get("/Pg")
        owner = pg.objgen if pg is not None and pg.is_indirect else None
        page, box = None, None
        for it in _items(e.get("/K")):
            if isinstance(it, Dictionary) and _is_struct_elem(it):
                continue
            if isinstance(it, Dictionary) and "/MCID" in it:
                stm, ipg = it.get("/Stm"), it.get("/Pg")
                key = (stm.objgen if stm is not None else (ipg.objgen if ipg is not None else owner), mcid_of(it))
            else:
                try:
                    key = (owner, int(it))
                except (TypeError, ValueError):
                    continue
            if key in self.boxes:
                page, b = self.boxes[key]
                box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
        return page, box

    def place_marks(self, root_elem) -> int:
        """A small figure beside a block of text (a legend mark, an item's bullet picture) is read just before that
        text: it moves to sit right before the element holding it, even inside a table cell."""
        leaves, marks = [], []

        def walk(e):
            for k in _items(e.get("/K")):
                if not _is_struct_elem(k):
                    continue
                page, box = self.own_box(k)
                if page is not None and page in self.slides and box is not None:
                    w, h = box[2] - box[0], box[3] - box[1]
                    if self.role(k) == "Figure" and max(w, h) <= 30:
                        marks.append((k, page, box))
                    elif self.role(k) not in ("Figure", "Artifact"):
                        leaves.append((k, page, box))
                walk(k)
        walk(root_elem)
        moved = 0
        for mark, page, mb in marks:
            cy = (mb[1] + mb[3]) / 2
            near = [(lb[0] - mb[2], leaf) for leaf, lp, lb in leaves
                    if lp == page and lb[1] - 2 <= cy <= lb[3] + 2 and 0 <= lb[0] - mb[2] <= 30]
            if not near:
                continue
            target = min(near, key=lambda t: t[0])[1]
            parent = target.get("/P")
            if not isinstance(parent, Dictionary):
                continue
            old = mark.get("/P")
            if isinstance(old, Dictionary):
                old.K = Array([k for k in _items(old.get("/K")) if not (_is_struct_elem(k) and k.objgen == mark.objgen)])
            kids = [k for k in _items(parent.get("/K")) if not (_is_struct_elem(k) and k.objgen == mark.objgen)]
            at = next(i for i, k in enumerate(kids) if _is_struct_elem(k) and k.objgen == target.objgen)
            parent.K = Array(kids[:at] + [mark] + kids[at:])
            mark.P = parent
            moved += 1
        if moved:
            self._memo.clear()
        return moved


def reorder_retained(pdf: pikepdf.Pdf, analysis: DocumentAnalysis, marks: dict, role_map: dict[str, str]) -> int:
    """Reorder kept tags by the reading order where they clearly disagree; the number of containers reordered."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return 0
    r = Reorderer(pdf, analysis, marks, role_map)
    for top in _items(root.get("/K")):
        if _is_struct_elem(top):
            r.gather_page_elements(top)
            r.reorder(top)
            r.reordered += r.place_marks(top)
    return r.reordered
