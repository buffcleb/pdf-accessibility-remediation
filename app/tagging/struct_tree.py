"""Builds (or extends) the logical structure tree and always regenerates the ParentTree."""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.pipeline.lists import fix_row_order, label_order, nest
from app.pipeline.models import DocumentAnalysis, ImageRegion, PageAnalysis, TableRegion, TextBlock, bbox_intersection
from app.pipeline.structure import row_top
from app.tagging.content_marker import StreamMarks, apply
from app.tagging.existing_tags import _is_struct_elem

ObjGen = tuple[int, int]
log = logging.getLogger(__name__)


@dataclass
class Ref:
    page_index: int
    owner: pikepdf.Object
    is_form: bool
    mcid: int


@dataclass
class BuildStats:
    elements: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    figures_with_alt: int = 0
    figures_without_alt: int = 0
    tables: int = 0
    links: int = 0
    annotations: int = 0
    contents_added: int = 0
    unmatched_paragraphs: int = 0
    headings_renumbered: int = 0
    leftover_content: int = 0


def _iter_elems(node, seen: set | None = None):
    seen = set() if seen is None else seen
    k = node.get("/K")
    for it in (list(k) if isinstance(k, Array) else [k]):
        if isinstance(it, Dictionary) and _is_struct_elem(it):
            if it.is_indirect:
                if it.objgen in seen:
                    continue
                seen.add(it.objgen)
            yield it
            yield from _iter_elems(it, seen)


def _list_numbering(label: str | None) -> str:
    if not label:
        return "Disc"
    core = label.strip("().")
    if core.isdigit():
        return "Decimal"
    if re.fullmatch(r"[ivxlc]+", core):
        return "LowerRoman"
    if re.fullmatch(r"[IVXLC]+", core):
        return "UpperRoman"
    if re.fullmatch(r"[a-z]", core):
        return "LowerAlpha"
    if re.fullmatch(r"[A-Z]", core):
        return "UpperAlpha"
    return "Disc" if core in ("•", "●", "·", "•") else "None" if core in ("-", "–", "*") else "Disc"


class StructTreeBuilder:
    def __init__(self, pdf: pikepdf.Pdf, analysis: DocumentAnalysis, layout_tables: set[str]):
        self.pdf = pdf
        self.analysis = analysis
        self.layout_tables = layout_tables
        self.stats = BuildStats()
        self.page_objs = [p.obj for p in pdf.pages]
        self.page_index = {p.objgen: i for i, p in enumerate(self.page_objs)}
        self.refs: dict[str, list[Ref]] = defaultdict(list)
        self.key_elems: dict[str, pikepdf.Object] = {}  # leaf key -> element (for annotation placement)
        self.key_page: dict[str, int] = {}

    # ------------------------------------------------------------------ entry
    def build(self, marks: dict[int, list[StreamMarks]], retain_root: bool) -> pikepdf.Object:
        for page_no, stream_marks in marks.items():
            for sm in stream_marks:
                for mcid, key in sm.mcids:
                    self.refs[key].append(Ref(page_no, sm.owner, sm.is_form, mcid))

        root, doc = self._root(retain_root)
        for page in self.analysis.pages:
            new = self._page_elements(page, doc)
            self._insert_for_page(doc, page.number, new)
        self._attach_leftovers(doc)
        self._annotations(doc)
        self._normalize_headings(doc)
        if not retain_root:
            self._sectionize(doc)
        self._parent_tree(root)
        return root

    def _sectionize(self, doc) -> None:
        """Group each heading with the content that follows it into nested Sect elements, so the
        tree states which heading a figure, table or paragraph belongs to, even when the heading
        and its content land on different pages."""
        kids = list(doc.K) if isinstance(doc.get("/K"), Array) else []

        def level_of(k) -> int | None:
            s = str(k.get("/S", ""))[1:] if isinstance(k, Dictionary) else ""
            return int(s[1]) if len(s) == 2 and s[0] == "H" and s[1].isdigit() else None

        if not any(level_of(k) for k in kids):
            return
        top: list = []
        children: dict[tuple[int, int], list] = {}
        stack: list[tuple[int, pikepdf.Object | None]] = [(0, None)]

        def container(sect) -> list:
            return top if sect is None else children[sect.objgen]

        for k in kids:
            level = level_of(k)
            if level is not None:
                while stack[-1][0] >= level:
                    stack.pop()
                parent = stack[-1][1]
                sect = self._elem("Sect", parent if parent is not None else doc, None)
                if "/Pg" in k:
                    sect.Pg = k.Pg
                children[sect.objgen] = []
                container(parent).append(sect)
                stack.append((level, sect))
            owner = stack[-1][1]
            if isinstance(k, Dictionary):
                k.P = owner if owner is not None else doc
            container(owner).append(k)
        for og, items in children.items():
            self.pdf.get_object(og).K = Array(items)
        doc.K = Array(top)

    def _attach_leftovers(self, doc) -> None:
        """Tagged content whose element was never placed (should not happen) still gets a tag."""
        for key, refs in self.refs.items():
            if key in self.key_elems or not refs:
                continue
            page = refs[0].page_index
            e = self._elem("P", doc, page)
            if self._attach_refs(e, key, page):
                self._insert_for_page(doc, page, [e])
                self.stats.leftover_content += 1
                log.warning("content for %s had no element; tagged as P", key)

    def _normalize_headings(self, doc) -> None:
        """PDF/UA 7.4.2: in final (reading) order the first heading is H1 and levels never skip."""
        prev = 0
        for e in _iter_elems(doc):
            s = str(e.get("/S", ""))[1:]
            if len(s) == 2 and s[0] == "H" and s[1].isdigit():
                level = min(int(s[1]), prev + 1)
                if level != int(s[1]):
                    e.S = Name(f"/H{level}")
                    self.stats.headings_renumbered += 1
                prev = level

    # ------------------------------------------------------------------ root / document
    def _root(self, retain: bool) -> tuple[pikepdf.Object, pikepdf.Object]:
        existing = self.pdf.Root.get("/StructTreeRoot")
        if retain and existing is not None:
            root = existing
            kids = [k for k in (root.K if isinstance(root.get("/K"), Array) else [root.get("/K")]) if k is not None]
            elems = [k for k in kids if _is_struct_elem(k)]
            if len(elems) == 1 and str(elems[0].S) == "/Document":
                return root, elems[0]
            doc = self._elem("Document", root, None)
            for k in elems:
                k.P = doc
            doc.K = Array(elems)
            root.K = Array([doc])
            return root, doc
        root = self.pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        self.pdf.Root.StructTreeRoot = root
        doc = self._elem("Document", root, None)
        root.K = Array([doc])
        return root, doc

    def _elem(self, role: str, parent: pikepdf.Object, page_index: int | None, **extra) -> pikepdf.Object:
        d = Dictionary(Type=Name.StructElem, S=Name("/" + role), P=parent, K=Array())
        if page_index is not None:
            d.Pg = self.page_objs[page_index]
        for k, v in extra.items():
            d[Name("/" + k)] = v
        self.stats.elements[role] += 1
        return self.pdf.make_indirect(d)

    def _attach_refs(self, elem: pikepdf.Object, key: str, page_index: int) -> bool:
        refs = self.refs.get(key)
        if not refs:
            return False
        page_obj = self.page_objs[page_index]
        k = elem.K
        for r in sorted(refs, key=lambda r: (r.page_index, r.is_form, r.mcid)):
            if not r.is_form and r.page_index == page_index:
                k.append(r.mcid)
            else:
                mcr = Dictionary(Type=Name.MCR, Pg=self.page_objs[r.page_index], MCID=r.mcid)
                if r.is_form:
                    mcr.Stm = r.owner
                k.append(mcr)
        elem.Pg = page_obj
        self.key_elems[key] = elem
        self.key_page[key] = page_index
        return True

    # ------------------------------------------------------------------ per page
    def _page_elements(self, page: PageAnalysis, doc: pikepdf.Object) -> list[pikepdf.Object]:
        out: list[pikepdf.Object] = []
        run: list[TextBlock] = []  # consecutive list items, built into (nested) lists when the run ends

        def flush_run() -> None:
            if not run:
                return
            items = fix_row_order(run, lambda b: b.list_label, row_top, lambda b: b.font_size)
            levels = nest(items, lambda b: b.list_label, lambda b: b.bbox[0], lambda b: b.font_size)
            label_order(levels, lambda b: b.list_label)
            for level in levels:
                lst = self._list(level, doc, page.number)
                if lst is not None:
                    out.append(lst)
            run.clear()

        for eid in page.reading_order:
            el = page.element(eid)
            if isinstance(el, TextBlock) and el.role == "LI":
                run.append(el)
                continue
            flush_run()

            if isinstance(el, TextBlock):
                if el.role == "Artifact":
                    continue
                role = "P" if el.role == "Caption" else el.role
                e = self._elem(role, doc, page.number)
                if role == "Formula":
                    # Math written as text: the spoken form ("10 to the power of 4 = 10 000") is its alt text
                    e.Alt = String(el.actual_text or el.text)
                elif el.actual_text:
                    e.ActualText = String(el.actual_text)
                if self._attach_refs(e, el.id, page.number):
                    out.append(e)
            elif isinstance(el, ImageRegion):
                if el.decorative:
                    continue
                out.extend(self._figure(el, doc))
            elif isinstance(el, TableRegion):
                out.extend(self._table(el, doc))
        flush_run()

        unmatched = f"p{page.number}:unmatched"
        if unmatched in self.refs:
            e = self._elem("P", doc, page.number)
            if self._attach_refs(e, unmatched, page.number):
                out.append(e)
                self.stats.unmatched_paragraphs += 1
        return out

    def _list(self, level, parent, page_no: int) -> pikepdf.Object | None:
        """One list level: L > LI > LBody, with each item's nested lists inside its LBody after its text."""
        first = level.items[0][0].list_label if level.items else None
        lst = self._elem("L", parent, page_no,
                         A=Dictionary(O=Name.List, ListNumbering=Name("/" + _list_numbering(first))))
        for el, children in level.items:
            li = self._elem("LI", lst, page_no)
            body = self._elem("LBody", li, page_no)
            if el.actual_text and children:
                # ActualText stands for everything inside its element: on the LBody it would hide the nested
                # list from screen readers, so it goes on a Span around the item's own text
                own = self._elem("Span", body, page_no, ActualText=String(el.actual_text))
                has_text = self._attach_refs(own, el.id, page_no)
                if has_text:
                    body.K.append(own)
            else:
                if el.actual_text:
                    body.ActualText = String(el.actual_text)
                has_text = self._attach_refs(body, el.id, page_no)
            for child in children:
                sub = self._list(child, body, page_no)
                if sub is not None:
                    body.K.append(sub)
            if has_text or len(body.K):
                li.K.append(body)
                lst.K.append(li)
        return lst if len(lst.K) else None

    def _figure(self, img: ImageRegion, doc) -> list[pikepdf.Object]:
        role = "Formula" if img.kind == "formula" else "Figure"
        e = self._elem(role, doc, img.page)
        if not self._attach_refs(e, img.id, img.page):
            return []
        if img.alt:
            e.Alt = String(img.alt)
            self.stats.figures_with_alt += 1
        else:
            self.stats.figures_without_alt += 1
        return [e]

    def _table(self, t: TableRegion, doc) -> list[pikepdf.Object]:
        if t.id in self.layout_tables:
            out = []
            for c in sorted(t.cells, key=lambda c: (c.row, c.col)):
                e = self._elem("P", doc, t.page)
                if c.actual_text:
                    e.ActualText = String(c.actual_text)
                if self._attach_refs(e, t.cell_target_id(c), t.page):
                    out.append(e)
            return out
        attrs = Dictionary(O=Name.Table)
        if t.summary:
            attrs.Summary = String(t.summary)
        table = self._elem("Table", doc, t.page, A=attrs)
        by_row: dict[int, list] = defaultdict(list)
        for c in t.normalized_cells():
            by_row[c.row].append(c)
        any_content = False
        for r in sorted(by_row):
            tr = self._elem("TR", table, t.page)
            for c in sorted(by_row[r], key=lambda c: c.col):
                header_row = c.row < t.header_rows
                header_col = c.col < t.header_cols
                role = "TH" if (header_row or header_col) else "TD"
                cattrs = Dictionary(O=Name.Table)
                if role == "TH":
                    cattrs.Scope = Name.Both if (header_row and header_col) else Name.Column if header_row else Name.Row
                if c.row_span > 1:
                    cattrs.RowSpan = c.row_span
                if c.col_span > 1:
                    cattrs.ColSpan = c.col_span
                cell = self._elem(role, tr, t.page)
                if len(cattrs) > 1:
                    cell.A = cattrs
                if c.actual_text:
                    cell.ActualText = String(c.actual_text)
                if self._attach_refs(cell, t.cell_target_id(c), t.page):
                    any_content = True
                tr.K.append(cell)
            table.K.append(tr)
        if not any_content:
            return []
        self.stats.tables += 1
        return [table]

    def _insert_for_page(self, doc, page_no: int, new: list[pikepdf.Object]) -> None:
        """Append new elements after the last existing top-level element on the same or an earlier page."""
        if not new:
            return
        for e in new:
            e.P = doc
        kids = list(doc.K) if isinstance(doc.get("/K"), Array) else ([doc.K] if doc.get("/K") is not None else [])
        pos = len(kids)
        if kids:
            pos = 0
            for i, k in enumerate(kids):
                pg = k.get("/Pg") if isinstance(k, Dictionary) else None
                idx = self.page_index.get(pg.objgen) if pg is not None else None
                if idx is None or idx <= page_no:
                    pos = i + 1
        doc.K = Array(kids[:pos] + new + kids[pos:])

    # ------------------------------------------------------------------ annotations
    def _annotations(self, doc) -> None:
        referenced = self._referenced_annots()
        for pi, page_obj in enumerate(self.page_objs):
            annots = page_obj.get("/Annots")
            if not isinstance(annots, Array):
                continue
            pa = self.analysis.pages[pi]
            for annot in annots:
                if not isinstance(annot, Dictionary) or not annot.is_indirect:
                    continue
                subtype = str(annot.get("/Subtype", ""))
                if subtype in ("/Popup", "/PrinterMark", "/TrapNet") or int(annot.get("/F", 0)) & 2:
                    continue
                role = "Link" if subtype == "/Link" else "Form" if subtype == "/Widget" else "Annot"
                if annot.objgen in referenced:
                    # Already in the (retained) tree: it still needs its description (PDF/UA 7.18.1/7.18.5).
                    self._ensure_annot_text(annot, role, pa)
                    continue
                parent, at = self._annot_parent(pa, annot, doc)
                e = self._elem(role, parent, pi)
                e.K = Array([Dictionary(Type=Name.OBJR, Obj=annot, Pg=page_obj)])
                annot.StructParent = -1  # assigned in _parent_tree
                if at is None:
                    self._insert_for_page(doc, pi, [e])
                else:
                    at.K.append(e)
                self._ensure_annot_text(annot, role, pa)
                if role == "Link":
                    self.stats.links += 1
                else:
                    self.stats.annotations += 1

    def _referenced_annots(self) -> set[ObjGen]:
        found: set[ObjGen] = set()
        root = self.pdf.Root.get("/StructTreeRoot")
        stack = [root.get("/K")] if root is not None else []
        seen = set()
        while stack:
            k = stack.pop()
            items = list(k) if isinstance(k, Array) else [k]
            for it in items:
                if not isinstance(it, Dictionary):
                    continue
                if it.is_indirect:
                    if it.objgen in seen:
                        continue
                    seen.add(it.objgen)
                if "/Obj" in it and it.Obj.is_indirect:
                    found.add(it.Obj.objgen)
                if "/K" in it:
                    stack.append(it.K)
        return found

    def _annot_parent(self, pa: PageAnalysis, annot, doc):
        rect = [float(v) for v in annot.get("/Rect", [0, 0, 0, 0])]
        m = pa.pdf_to_page
        x0, y0 = apply(m, rect[0], rect[1])
        x1, y1 = apply(m, rect[2], rect[3])
        bbox = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if str(annot.get("/Subtype")) != "/Link":
            return doc, None
        best, best_ov = None, 0.0
        for b in pa.blocks:
            if b.role == "Artifact" or b.id not in self.key_elems:
                continue
            ov = bbox_intersection(bbox, b.bbox)
            if ov > best_ov:
                best, best_ov = self.key_elems[b.id], ov
        return (best, best) if best is not None else (doc, None)

    def _ensure_annot_text(self, annot, role: str, pa: PageAnalysis) -> None:
        if role == "Form":
            target = annot if "/T" in annot else annot.get("/Parent")
            if target is not None and "/TU" not in target:
                name = str(target.get("/T", "")) or "Form field"
                target.TU = String(name)
                self.stats.contents_added += 1
            return
        if "/Contents" in annot and str(annot.Contents).strip():
            return
        text = None
        if role == "Link":
            action = annot.get("/A")
            uri = action.get("/URI") if isinstance(action, Dictionary) else None
            text = f"Link to {uri}" if uri is not None else "Link to another location in this document"
        else:
            text = f"{str(annot.get('/Subtype', '/Annotation'))[1:]} annotation"
        annot.Contents = String(text)
        self.stats.contents_added += 1

    # ------------------------------------------------------------------ parent tree
    def _parent_tree(self, root) -> None:
        """Regenerate ParentTree from the final tree so it is always consistent."""
        streams: dict[ObjGen, dict[int, pikepdf.Object]] = defaultdict(dict)
        owners: dict[ObjGen, pikepdf.Object] = {}
        annots: list[tuple[pikepdf.Object, pikepdf.Object]] = []
        seen: set[ObjGen] = set()

        def walk(elem) -> None:
            if elem.is_indirect:
                if elem.objgen in seen:
                    return
                seen.add(elem.objgen)
            pg = elem.get("/Pg")
            k = elem.get("/K")
            items = list(k) if isinstance(k, Array) else ([] if k is None else [k])
            for it in items:
                if isinstance(it, int) and pg is not None:
                    streams[pg.objgen][it] = elem
                    owners[pg.objgen] = pg
                elif isinstance(it, Dictionary):
                    if "/MCID" in it and not _is_struct_elem(it):
                        owner = it.get("/Stm") or it.get("/Pg") or pg
                        if owner is not None:
                            streams[owner.objgen][int(it.MCID)] = elem
                            owners[owner.objgen] = owner
                    elif "/Obj" in it and not _is_struct_elem(it):
                        annots.append((it.Obj, elem))
                    elif _is_struct_elem(it):
                        if it.get("/P") is None:
                            it.P = elem
                        walk(it)

        top = root.get("/K")
        for it in (list(top) if isinstance(top, Array) else [top]):
            if isinstance(it, Dictionary) and _is_struct_elem(it):
                walk(it)

        nums = Array()
        key = 0
        # pages first, in page order, then forms
        ordered = sorted(owners, key=lambda og: (og not in self.page_index, self.page_index.get(og, 0), og))
        for og in ordered:
            owner = owners[og]
            mapping = streams[og]
            arr = Array([mapping.get(i) for i in range(max(mapping) + 1)]) if mapping else Array()
            for i in range(len(arr)):
                if arr[i] is None:
                    arr[i] = pikepdf.Object.parse(b"null")
            owner.StructParents = key
            nums.append(key)
            nums.append(arr)
            key += 1
        # Pages without marked content must not keep stale StructParents keys.
        for og, i in self.page_index.items():
            if og not in owners and "/StructParents" in self.page_objs[i]:
                del self.page_objs[i]["/StructParents"]
        for annot, elem in annots:
            annot.StructParent = key
            nums.append(key)
            nums.append(elem)
            key += 1
        root.ParentTree = self.pdf.make_indirect(Dictionary(Nums=nums))
        root.ParentTreeNextKey = key
