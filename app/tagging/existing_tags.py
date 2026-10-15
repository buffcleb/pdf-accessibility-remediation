"""Audit an existing structure tree, keep what is valid, repair what can be repaired.

Each structure element ends up with one decision:
    retained  - valid as-is, untouched
    repaired  - kept but patched in place (role mapping, Alt, TH Scope, heading level, dangling refs)
    replaced  - removed; any real content it pointed at becomes "orphaned" and is re-tagged
                from the layout analysis (e.g. a malformed table is rebuilt from the detected grid)
If the tree as a whole is unusable it is discarded and the document is re-tagged from scratch.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterator

import pikepdf
from pikepdf import Array, Dictionary, Name

from app.tagging.content_marker import mcid_of

STANDARD_TYPES = {
    "Document", "Part", "Art", "Sect", "Div", "BlockQuote", "Caption", "TOC", "TOCI", "Index",
    "NonStruct", "Private", "P", "H", "H1", "H2", "H3", "H4", "H5", "H6", "L", "LI", "Lbl", "LBody",
    "Table", "TR", "TH", "TD", "THead", "TBody", "TFoot", "Span", "Quote", "Note", "Reference",
    "BibEntry", "Code", "Link", "Annot", "Ruby", "RB", "RT", "RP", "Warichu", "WT", "WP",
    "Figure", "Formula", "Form",
}
# Common non-standard roles (Word, InDesign, LaTeX taggers) and the standard role they mean.
KNOWN_ROLE_MAP = {
    **{r: "Span" for r in ("Strong", "Em", "B", "I", "Bold", "Italic", "Emphasis", "Underline", "Sub", "Sup",
                           "Superscript", "Subscript", "Hyperlink", "InlineShape", "Chars", "Char")},
    **{r: "Note" for r in ("Footnote", "Endnote", "FENote", "FootNote")},
    **{r: "P" for r in ("Title", "Subtitle", "Normal", "Body", "BodyText", "Body_Text", "Para", "Paragraph", "Text")},
    **{r: "Div" for r in ("Aside", "Textbox", "TextBox", "Header", "Footer", "Story", "Frame", "Shape", "Group")},
    "Heading": "H", "Heading1": "H1", "Heading2": "H2", "Heading3": "H3", "Heading4": "H4",
    "Heading5": "H5", "Heading6": "H6", "Image": "Figure", "Graphic": "Figure", "Chart": "Figure",
    "List": "L", "ListItem": "LI", "Label": "Lbl", "Lbl_": "Lbl", "ListBody": "LBody",
}
_TABLE_KIDS = {"TR", "THead", "TBody", "TFoot", "Caption"}
# The note Office adds to the alt text it writes itself (Word and PowerPoint, older and newer wording)
OFFICE_AUTO_ALT = re.compile(r"\s*(Description automatically generated(?: with (?:very high|high|medium|low) confidence)?"
                             r"|AI-generated content may be incorrect)\.?\s*", re.I)
_ROW_GROUP_KIDS = {"TR"}
MAX_SPAN = 100  # a RowSpan or ColSpan past this is a producer's error: no real table is that wide
# How far a stream's marked-content ids may run past the number of its sequences before the numbering is judged
# unusable (an id of a billion would size the ParentTree array by it): this many ids per sequence, plus this slack
MAX_MCID_PER_SEQUENCE = 64
MAX_MCID_SLACK = 65536
_ROW_KIDS = {"TH", "TD"}

ObjGen = tuple[int, int]
DECORATIVE = object()  # figure_alt_lookup result: the figure is decorative and should become an artifact
OUTLINE = object()  # figure_alt_lookup result: lines and shapes drawn around text (a text box's border, an arrow holding words)


@dataclass
class McRef:
    owner: ObjGen  # page or form stream objgen
    mcid: int
    valid: bool
    container: pikepdf.Object  # the K array/dict holding the reference
    raw: object


@dataclass
class ElemInfo:
    obj: pikepdf.Object
    parent: pikepdf.Object | None
    raw_role: str
    role: str  # resolved through RoleMap
    page: ObjGen | None
    mcrefs: list[McRef] = field(default_factory=list)
    objrefs: list[pikepdf.Object] = field(default_factory=list)
    children: list["ElemInfo"] = field(default_factory=list)
    decision: str = "retained"
    reasons: list[str] = field(default_factory=list)


@dataclass
class ExistingTagReport:
    present: bool = False
    usable: bool = False
    rebuilt_tree: bool = False
    reason: str = ""
    counts: Counter = field(default_factory=Counter)
    samples: list[dict] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "present": self.present,
            "usable": self.usable,
            "rebuilt_tree": self.rebuilt_tree,
            "reason": self.reason,
            "retained": self.counts.get("retained", 0),
            "repaired": self.counts.get("repaired", 0),
            "replaced": self.counts.get("replaced", 0),
            "removed": self.counts.get("removed", 0),
            "artifacted": self.counts.get("artifacted", 0),
            "unresolved": self.counts.get("unresolved", 0),
            "findings": self.findings[:50],
            "samples": self.samples[:40],
        }


def _role_map(root: pikepdf.Object) -> dict[str, str]:
    rm = root.get("/RoleMap")
    if not isinstance(rm, Dictionary):
        return {}
    return {str(k)[1:]: str(v)[1:] for k, v in rm.items() if isinstance(v, Name)}


def resolve_role(role: str, role_map: dict[str, str]) -> str:
    seen = set()
    while role not in STANDARD_TYPES and role in role_map and role not in seen:
        seen.add(role)
        role = role_map[role]
    return role


def _is_struct_elem(o) -> bool:
    return isinstance(o, Dictionary) and "/S" in o and o.get("/Type") not in (Name.MCR, Name.OBJR)


class ExistingTree:
    def __init__(self, pdf: pikepdf.Pdf, stream_mcids: dict[ObjGen, set[int]]):
        self.pdf = pdf
        self.stream_mcids = stream_mcids
        self.report = ExistingTagReport()
        self.root = pdf.Root.get("/StructTreeRoot")
        self.role_map = _role_map(self.root) if self.root is not None else {}
        self.elements: list[ElemInfo] = []
        self.top: list[ElemInfo] = []
        self._parent_arrays: dict[ObjGen, Array | None] = {}
        self.links_wrapped = 0  # link annotations attached to a cell or paragraph, now in Link tags
        self.outlines_released = 0  # undescribed Figures that were only the outline of some text
        self.double_claims = 0  # content the file's own tags claimed more than once (one claim kept)
        self.double_claim_pages: list[int] = []

    # ------------------------------------------------------------------ inspect
    def inspect(self) -> ExistingTagReport:
        r = self.report
        if self.root is None or not isinstance(self.root, Dictionary):
            r.reason = "no structure tree"
            return r
        r.present = True
        visited: set[ObjGen] = set()
        kids = self.root.get("/K")
        try:
            for kid in self._iter_k(kids):
                if _is_struct_elem(kid):
                    info = self._walk(kid, None, None, visited)
                    if info:
                        self.top.append(info)
        except RecursionError:
            # A tree nested deeper than the interpreter follows (no real producer makes one; a broken or hostile
            # file does) can't be audited element by element: it is rebuilt from the layout, like any unusable tree
            self.elements, self.top = [], []
            r.usable, r.rebuilt_tree = False, True
            r.reason = "structure tree is nested too deeply to be kept"
            return r

        total_refs = sum(len(e.mcrefs) for e in self.elements)
        valid_refs = sum(1 for e in self.elements for m in e.mcrefs if m.valid)
        sparse = self._sparse_numbering()
        if not self.elements:
            r.reason = "structure tree is empty"
        elif sparse is not None:
            r.reason = sparse
        elif total_refs == 0 and not any(e.objrefs for e in self.elements):
            r.reason = "structure tree references no content"
        elif total_refs and valid_refs / total_refs < 0.5:
            r.reason = f"{total_refs - valid_refs} of {total_refs} content references are dangling"
        else:
            r.usable = True
            r.reason = f"{valid_refs} of {total_refs} content references valid"
        if not r.usable:
            r.rebuilt_tree = True
        else:
            self._resolve_double_claims()
        return r

    def _sparse_numbering(self) -> str | None:
        """Why the file's marked-content numbering can't be kept, or None. The ParentTree names each sequence's tag at
        the index of its id, so a page's array is as long as its largest id: a file whose ids run far past the number
        of sequences on the page (a producer's slip, or a hostile file with one id in the billions) would have the
        tagger build an array that long. Such a tree is rebuilt, and the sequences are numbered afresh."""
        for owner, mcids in self.stream_mcids.items():
            if mcids and max(mcids) > MAX_MCID_SLACK + MAX_MCID_PER_SEQUENCE * len(mcids):
                return (f"marked-content ids run up to {max(mcids):,} on a stream with {len(mcids)} tagged "
                        "sequence(s): the numbering can't be kept")
        return None

    def _parent_tree_owner(self, page: ObjGen, mcid: int) -> ObjGen | None:
        """The element the file's parent tree names as the owner of this content on a page, if it names one."""
        if page not in self._parent_arrays:
            arr = None
            try:
                key = next(int(pg.obj.StructParents) for pg in self.pdf.pages if pg.objgen == page)
                node, depth = self.root.get("/ParentTree"), 0
                while node is not None and arr is None and depth < 50:
                    depth += 1
                    nums = node.get("/Nums")
                    if nums is not None:
                        arr = next((nums[i + 1] for i in range(0, len(nums) - 1, 2) if int(nums[i]) == key), None)
                        break
                    node = next((k for k in node.get("/Kids", [])
                                 if int(k.Limits[0]) <= key <= int(k.Limits[1])), None)
            except Exception:  # noqa: BLE001 - a missing or malformed parent tree just names no one
                arr = None
            self._parent_arrays[page] = arr if isinstance(arr, Array) else None
        arr = self._parent_arrays[page]
        if arr is None or mcid >= len(arr):
            return None
        owner = arr[mcid]
        return owner.objgen if isinstance(owner, Dictionary) and owner.is_indirect else None

    def _resolve_double_claims(self) -> None:
        """A piece of content belongs to one tag. Files re-tagged or with pages replaced in an editor can carry a
        second set of tags over the same stretch of a page, each piece claimed twice: read twice, and not a valid
        tree. One claim is kept (the one the file's parent tree names, when it is one of the claimants; otherwise
        the first in the tree's order) and the others are dropped. A tag left with nothing is removed."""
        claims: dict[tuple[ObjGen, int], list[tuple[ElemInfo, McRef]]] = {}
        for e in self.elements:
            for m in e.mcrefs:
                if m.valid:
                    claims.setdefault((m.owner, m.mcid), []).append((e, m))
        page_no = {pg.objgen: i + 1 for i, pg in enumerate(self.pdf.pages)}
        dropped: dict[int, tuple[ElemInfo, list[McRef]]] = {}
        pages: set[int] = set()
        count = 0
        for (owner, mcid), users in claims.items():
            if len({id(e) for e, _ in users}) < 2:
                continue
            named = self._parent_tree_owner(owner, mcid) if owner in page_no else None
            keep = next((e for e, _ in users if e.obj.is_indirect and e.obj.objgen == named), users[0][0])
            count += 1
            if owner in page_no:
                pages.add(page_no[owner])
            for e, m in users:
                if e is not keep:
                    dropped.setdefault(id(e), (e, []))[1].append(m)
        if not count:
            return
        for e, refs in dropped.values():
            gone = {id(m) for m in refs}
            self._remove_refs(e, refs)
            e.mcrefs = [m for m in e.mcrefs if id(m) not in gone]
            if not e.mcrefs and not e.objrefs and not e.children and e.role not in ("TD", "TH", "Document"):
                self._mark(e, "removed", "its content already belongs to another tag")
            else:
                e.reasons.append("content that already belongs to another tag taken out")
        self.double_claims, self.double_claim_pages = count, sorted(pages)
        shown = ", ".join(str(n) for n in sorted(pages)[:20]) + (" and others" if len(pages) > 20 else "")
        self.report.findings.append(
            f"{count} piece(s) of content were claimed by more than one of the file's own tags (page(s) {shown}): "
            "one claim was kept and the duplicate tags removed, so the content is read once; check those pages")

    def _iter_k(self, k) -> Iterator:
        if k is None:
            return
        if isinstance(k, Array):
            yield from k
        else:
            yield k

    def _walk(self, elem, parent, inherited_pg: ObjGen | None, visited: set) -> ElemInfo | None:
        if elem.is_indirect:
            if elem.objgen in visited:
                self.report.findings.append("cycle in structure tree ignored")
                return None
            visited.add(elem.objgen)
        raw = str(elem.get("/S", "/NonStruct"))[1:]
        pg = elem.get("/Pg")
        page = pg.objgen if pg is not None and pg.is_indirect else inherited_pg
        info = ElemInfo(obj=elem, parent=parent, raw_role=raw, role=resolve_role(raw, self.role_map), page=page)
        self.elements.append(info)
        for kid in self._iter_k(elem.get("/K")):
            if isinstance(kid, (int, pikepdf.Object)) and not isinstance(kid, Dictionary):
                try:
                    mcid = int(kid)
                except (TypeError, ValueError):
                    continue
                owner = page
                info.mcrefs.append(McRef(owner, mcid, owner is not None and mcid in self.stream_mcids.get(owner, set()),
                                         elem, kid))
            elif _is_struct_elem(kid):
                child = self._walk(kid, elem, page, visited)
                if child:
                    info.children.append(child)
            elif isinstance(kid, Dictionary) and ("/MCID" in kid):
                stm = kid.get("/Stm")
                kpg = kid.get("/Pg")
                owner = stm.objgen if stm is not None else (kpg.objgen if kpg is not None else page)
                mcid = mcid_of(kid)
                if mcid is None:
                    mcid = -1  # an id that isn't an integer: a dangling reference, removed by the repair
                info.mcrefs.append(McRef(owner, mcid, owner is not None and mcid in self.stream_mcids.get(owner, set()),
                                         elem, kid))
            elif isinstance(kid, Dictionary) and ("/Obj" in kid):
                info.objrefs.append(kid.Obj)
        return info

    # ------------------------------------------------------------------ repair
    def repair(self, figure_alt_lookup, describe=None) -> dict[ObjGen, set[int]]:
        """Apply decisions in place. `figure_alt_lookup(elem_info) -> str | None` supplies alt
        text for figures missing it (from the AI pass over the same region). `describe(elem_info) ->
        (page, bbox, list label) | None` places elements on the page, for joining lists split by a note.
        Returns the set of MCIDs per stream still referenced by the retained tree."""
        self._decide_tables()
        self._fix_headings()
        for e in self.elements:
            if e.decision in ("replaced", "removed"):
                continue
            self._repair_element(e, figure_alt_lookup)
        if describe is not None:
            self._join_interrupted_lists(describe)

        self._cascade_empty()
        for e in self.elements:
            if e.decision in ("replaced", "removed", "artifacted"):
                self._detach(e)

        referenced: dict[ObjGen, set[int]] = {}
        for e in self.elements:
            if e.decision in ("replaced", "removed", "artifacted"):
                continue
            for m in e.mcrefs:
                if m.valid:
                    referenced.setdefault(m.owner, set()).add(m.mcid)

        if self.outlines_released:
            self.report.findings.insert(
                0, f"{self.outlines_released} figure tag(s) with no description were only lines drawn around text (a text "
                "box's border, an arrow-shaped box): the lines are decoration now and the text is read as before")
        for e in self.elements:
            self.report.counts[e.decision] += 1
            if (e.decision != "retained" or e.reasons) and e.decision != "removed":
                if len(self.report.samples) < 40:
                    self.report.samples.append({"role": e.raw_role, "decision": e.decision, "reasons": e.reasons})
        return referenced

    def _repair_element(self, e: ElemInfo, figure_alt_lookup) -> None:
        # Unknown roles: map them to a standard type via RoleMap.
        if e.role not in STANDARD_TYPES:
            target = KNOWN_ROLE_MAP.get(e.raw_role) or (
                "Span" if e.parent is not None and self._role_of(e.parent) in
                ("P", "H", "H1", "H2", "H3", "H4", "H5", "H6", "Span", "Link", "LBody", "TD", "TH", "Lbl", "Caption")
                else "P")
            self._add_role_mapping(e.raw_role, target)
            e.role = target
            self._mark(e, "repaired", f"non-standard role /{e.raw_role} mapped to /{target}")

        dangling = [m for m in e.mcrefs if not m.valid]
        if dangling:
            if not any(m.valid for m in e.mcrefs) and not e.children and not e.objrefs:
                self._mark(e, "replaced", "all content references dangling")
                return
            self._remove_refs(e, dangling)
            self._mark(e, "repaired", f"{len(dangling)} dangling content reference(s) removed")

        if not e.mcrefs and not e.children and not e.objrefs and e.role not in ("TD", "TH"):
            self._mark(e, "removed", "empty element (no content)")
            return

        if e.role == "LI":
            self._repair_list_item(e)
        elif e.role == "L":
            self._repair_list(e)
        elif e.role in ("LBody", "Lbl") and e.parent is not None and self._role_of(e.parent) not in ("LI", "L"):
            self._wrap_in_list(e)
        elif e.role == "TOC":
            self._repair_toc(e)
        elif e.role == "Table":
            self._group_body_rows(e)
            self._pad_table_rows(e)

        if e.objrefs and e.role not in ("Link", "Annot", "Form"):
            self._wrap_links(e)

        if e.role == "Figure" and e.obj.get("/Alt") is not None and OFFICE_AUTO_ALT.search(str(e.obj.Alt)):
            # Office's automatic alt text, marked as such ("Description automatically generated"): use ours if
            # there is one, else keep it without the note, which screen readers would otherwise read out
            better = figure_alt_lookup(e)
            if better is OUTLINE:
                better = None  # it has a description, if an automatic one: it stays a figure
            if better is DECORATIVE:
                self._mark(e, "artifacted", "decorative figure with Office's automatic alt text: tag removed")
                return
            if better:
                e.obj.Alt = pikepdf.String(better)
                self._mark(e, "repaired", "Office's automatic alt text replaced with a new description")
            else:
                cleaned = OFFICE_AUTO_ALT.sub("", str(e.obj.Alt)).strip()
                if cleaned:
                    e.obj.Alt = pikepdf.String(cleaned)
                    self._mark(e, "repaired", "\"Description automatically generated\" note removed from the alt text")
                else:
                    del e.obj["/Alt"]

        if e.role in ("Figure", "Formula") and not (str(e.obj.get("/Alt") or "").strip()
                                                    or str(e.obj.get("/ActualText") or "").strip()):
            alt = figure_alt_lookup(e)
            if alt is OUTLINE:
                self._release_outline(e)
                return
            if alt is DECORATIVE:
                self._mark(e, "artifacted", "decorative figure: tag removed, content marked as artifact")
                return
            if alt:
                e.obj.Alt = pikepdf.String(alt)
                self._mark(e, "repaired", "missing Alt added")
            else:
                e.reasons.append("missing Alt (no description available)")
                self.report.counts["unresolved"] += 1

        if e.role == "TH":
            attrs = e.obj.get("/A")
            has_scope = False
            for a in self._iter_k(attrs):
                if isinstance(a, Dictionary) and "/Scope" in a:
                    has_scope = True
            if not has_scope and not self._table_uses_headers(e):
                scope = self._infer_scope(e)
                new_attr = Dictionary(O=Name.Table, Scope=Name("/" + scope))
                if attrs is None:
                    e.obj.A = new_attr
                elif isinstance(attrs, Array):
                    attrs.append(new_attr)
                else:
                    e.obj.A = Array([attrs, new_attr])
                self._mark(e, "repaired", f"TH Scope /{scope} added")

    def _release_outline(self, e: ElemInfo) -> None:
        """A Figure with no description that is only the lines around some text (presentation programs tag every
        text box's border and every arrow-shaped box as a Figure). The words are what a reader needs, and they
        have their own tags. The drawing stops being tagged (it becomes decoration); a Figure holding the text's
        tags becomes a plain grouping, and one holding nothing else is removed."""
        refs = list(e.mcrefs)
        gone = {id(m) for m in refs}
        self._remove_refs(e, refs)
        e.mcrefs = [m for m in e.mcrefs if id(m) not in gone]
        self.outlines_released += 1
        if e.children or e.objrefs:
            e.obj.S = Name("/Div")
            e.role = e.raw_role = "Div"
            for key in ("/Alt", "/A"):  # a Figure's placement attributes mean nothing on a grouping
                if key in e.obj:
                    del e.obj[key]
            self._mark(e, "repaired", "figure with no description that was only the outline of its text: now a grouping")
        else:
            self._mark(e, "removed", "figure with no description that was only an outline around text: decoration")

    def hide(self, mcids: set[tuple], reason: str) -> int:
        """Elements whose content is all in `mcids` ((stream objgen, mcid)) become artifacts; the count."""
        n = 0
        for e in self.elements:
            valid = [m for m in e.mcrefs if m.valid]
            if valid and not e.objrefs and all((m.owner, m.mcid) in mcids for m in valid):
                self._mark(e, "artifacted", reason)
                n += 1
        return n

    def _cascade_empty(self) -> None:
        """A container whose only children were removed is itself empty: remove it too."""
        changed = True
        while changed:
            changed = False
            for e in self.elements:
                if e.decision in ("replaced", "removed", "artifacted") or e.mcrefs or e.objrefs or not e.children:
                    continue
                if e.role in ("TD", "TH", "Document"):
                    continue
                if all(c.decision in ("replaced", "removed", "artifacted") for c in e.children):
                    self._mark(e, "removed", "empty after its empty children were removed")
                    changed = True

    def _new_elem(self, role: str, parent) -> pikepdf.Object:
        d = Dictionary(Type=Name.StructElem, S=Name("/" + role), P=parent, K=Array())
        if "/Pg" in parent:
            d.Pg = parent.Pg
        return self.pdf.make_indirect(d)

    def _wrap_links(self, e: ElemInfo) -> None:
        """PDF/UA 7.18.5: a link annotation belongs in a Link tag. Some producers attach it straight to the table
        cell or paragraph holding the link's words. When that is all the tag holds (its words and one link), the
        words go into the new Link with the annotation, as a link is tagged; otherwise the annotation alone does."""
        items = list(self._iter_k(e.obj.get("/K")))

        def is_link(it) -> bool:
            if not isinstance(it, Dictionary) or "/Obj" not in it or _is_struct_elem(it):
                return False
            try:
                return str(it.Obj.get("/Subtype")) == "/Link"
            except Exception:  # noqa: BLE001 - a reference to nothing
                return False
        links = [it for it in items if is_link(it)]
        if not links:
            return
        if len(links) == 1 and not any(_is_struct_elem(it) for it in items):
            link = self._new_elem("Link", e.obj)
            link.K = Array(items)
            e.obj.K = Array([link])
        else:
            new_k = []
            for it in items:
                if is_link(it):
                    link = self._new_elem("Link", e.obj)
                    link.K = Array([it])
                    new_k.append(link)
                else:
                    new_k.append(it)
            e.obj.K = Array(new_k)
        self.links_wrapped += len(links)
        self._mark(e, "repaired", f"link annotation attached straight to /{e.role}: wrapped in a Link tag")

    def _repair_list_item(self, e: ElemInfo) -> None:
        """PDF/UA 7.2-20: LI may contain only Lbl and LBody. Anything else is moved into an LBody."""
        items = list(self._iter_k(e.obj.get("/K")))
        ok = lambda it: _is_struct_elem(it) and resolve_role(str(it.S)[1:], self.role_map) in ("Lbl", "LBody")  # noqa: E731
        if all(ok(it) for it in items):
            return
        new_k, body = [], None
        for it in items:
            if ok(it):
                new_k.append(it)
                continue
            if body is None:
                body = self._new_elem("LBody", e.obj)
                new_k.append(body)
            if _is_struct_elem(it):
                it.P = body
            body.K.append(it)
        e.obj.K = Array(new_k)
        self._mark(e, "repaired", "content wrapped in LBody (LI may only contain Lbl/LBody)")

    def _repair_list(self, e: ElemInfo) -> None:
        """L may contain only L, LI and Caption; stray content becomes its own LI > LBody."""
        items = list(self._iter_k(e.obj.get("/K")))
        ok = lambda it: _is_struct_elem(it) and resolve_role(str(it.S)[1:], self.role_map) in ("L", "LI", "Caption")  # noqa: E731
        if all(ok(it) for it in items):
            return
        new_k = []
        for it in items:
            if ok(it):
                new_k.append(it)
                continue
            li = self._new_elem("LI", e.obj)
            role = resolve_role(str(it.S)[1:], self.role_map) if _is_struct_elem(it) else ""
            if role in ("LBody", "Lbl"):
                it.P = li  # already a list part: it only needs its LI
                li.K.append(it)
            else:
                body = self._new_elem("LBody", li)
                li.K.append(body)
                if _is_struct_elem(it):
                    it.P = body
                body.K.append(it)
            new_k.append(li)
        e.obj.K = Array(new_k)
        self._mark(e, "repaired", "stray list content wrapped in LI/LBody")

    def _replace_kid(self, parent, old_obj, new_obj) -> None:
        items = list(self._iter_k(parent.get("/K")))
        parent.K = Array([new_obj if (_is_struct_elem(it) and it.is_indirect and it.objgen == old_obj.objgen) else it
                          for it in items])

    def _wrap_in_list(self, e: ElemInfo) -> None:
        """PDF/UA 7.2-18: Lbl/LBody must sit in an LI (and LI in an L). A run of sibling Lbl/LBody
        elements becomes one L, pairing each label with the body that follows it."""
        if getattr(self, "_wrapped", None) is None:
            self._wrapped = set()
        if e.obj.objgen in self._wrapped:
            return
        parent = e.parent
        sibs = list(self._iter_k(parent.get("/K")))
        idx = next(i for i, it in enumerate(sibs) if _is_struct_elem(it) and it.is_indirect and it.objgen == e.obj.objgen)
        run_start = idx
        while run_start > 0 and _is_struct_elem(sibs[run_start - 1]) and \
                resolve_role(str(sibs[run_start - 1].S)[1:], self.role_map) in ("Lbl", "LBody"):
            run_start -= 1
        run_end = idx
        while run_end + 1 < len(sibs) and _is_struct_elem(sibs[run_end + 1]) and \
                resolve_role(str(sibs[run_end + 1].S)[1:], self.role_map) in ("Lbl", "LBody"):
            run_end += 1
        run = sibs[run_start:run_end + 1]
        lst = self._new_elem("L", parent)
        li = None
        for it in run:
            role = resolve_role(str(it.S)[1:], self.role_map)
            if li is None or role == "Lbl" or any(resolve_role(str(k.S)[1:], self.role_map) == "LBody"
                                                  for k in self._iter_k(li.get("/K")) if _is_struct_elem(k)):
                li = self._new_elem("LI", lst)
                lst.K.append(li)
            li.K.append(it)
            it.P = li
            self._wrapped.add(it.objgen)
        parent.K = Array(sibs[:run_start] + [lst] + sibs[run_end + 1:])
        for x in self.elements:
            if x.obj.is_indirect and x.obj.objgen in self._wrapped and x.decision == "retained":
                self._mark(x, "repaired", f"/{x.role} outside a list item: grouped into L/LI")

    def _join_interrupted_lists(self, describe) -> None:
        """A note under a list item ("*If no, use the other form", or an item's second line) is often tagged as a P
        between two lists: L (items 1-3), P, L (items 4-10). It belongs to the item above it, and the two lists are
        one. Joined when the numbering carries on (3 then 4; or the same bullet), the P (at most two) is indented
        under the item and sits between the two lists, and nothing else comes between: the P moves into the last
        item's LBody and the second list's items join the first. Reading order is unchanged."""
        by_obj = {e.obj.objgen: e for e in self.elements if e.obj.is_indirect}
        live = lambda e: e is not None and e.decision not in ("replaced", "removed", "artifacted")  # noqa: E731
        for parent in [self.root] + [e.obj for e in self.elements if live(e) and e.role != "L"]:
            changed = True
            while changed:
                changed = False
                kids = [by_obj.get(k.objgen) if _is_struct_elem(k) and k.is_indirect else None
                        for k in self._iter_k(parent.get("/K"))]
                for i, first in enumerate(kids):
                    if not live(first) or first.role != "L":
                        continue
                    j = i + 1
                    while j < len(kids) and j - i <= 2 and live(kids[j]) and kids[j].role == "P":
                        j += 1
                    if j == i + 1 or j >= len(kids) or not live(kids[j]) or kids[j].role != "L":
                        continue
                    if self._join_lists(first, kids[i + 1:j], kids[j], describe):
                        changed = True
                        break

    @staticmethod
    def _label_number(label: str | None) -> tuple[str, int] | None:
        core = (label or "").strip().strip("().")
        if core.isdigit():
            return ("number", int(core))
        if len(core) == 1 and core.isalpha():
            return ("letter", ord(core.lower()))
        return None

    def _join_lists(self, first: ElemInfo, notes: list[ElemInfo], second: ElemInfo, describe) -> bool:
        last_items = [c for c in first.children if c.role == "LI" and c.decision not in ("removed", "replaced")]
        next_items = [c for c in second.children if c.role == "LI" and c.decision not in ("removed", "replaced")]
        if not last_items or not next_items:
            return False
        places = [describe(x) for x in (first, *notes, second, last_items[-1], next_items[0])]
        if any(p is None or p[1] is None for p in places):
            return False
        (pg1, b1, _), *note_places, (pg2, b2, _), (_, _, lbl_a), (_, _, lbl_b) = places
        if any(p[0] != pg1 for p in note_places) or pg2 != pg1:
            return False
        a, b = self._label_number(lbl_a), self._label_number(lbl_b)
        if a and b:
            if a[0] != b[0] or b[1] != a[1] + 1:
                return False
        elif not (lbl_a and lbl_b and a is None and b is None and lbl_a.strip() == lbl_b.strip()):
            return False  # unnumbered: only the same bullet
        line = 30.0
        prev_bottom = b1[3]
        for _, nb, _ in note_places:
            indented = nb[0] >= b1[0] + 6
            if not indented or nb[1] < prev_bottom - 4 or nb[1] - prev_bottom > line:
                return False
            prev_bottom = nb[3]
        if b2[1] < prev_bottom - 4 or b2[1] - prev_bottom > line:
            return False

        item = last_items[-1]
        bodies = [c for c in item.children if c.role == "LBody"]
        if bodies:
            body = bodies[-1]
        else:
            body_obj = self._new_elem("LBody", item.obj)
            item.obj.K = Array(list(self._iter_k(item.obj.get("/K"))) + [body_obj])
            body = ElemInfo(obj=body_obj, parent=item.obj, raw_role="LBody", role="LBody", page=item.page)
            item.children.append(body)
            self.elements.append(body)
        parent = first.parent if first.parent is not None else self.root
        moved = {n.obj.objgen for n in notes} | {second.obj.objgen}
        parent.K = Array([k for k in self._iter_k(parent.get("/K"))
                          if not (_is_struct_elem(k) and k.is_indirect and k.objgen in moved)])
        body.obj.K = Array(list(self._iter_k(body.obj.get("/K"))) + [n.obj for n in notes])
        for n in notes:
            n.obj.P = body.obj
            n.parent = body.obj
            body.children.append(n)
            self._mark(n, "repaired", "note under a list item moved into the item")
        rest = list(self._iter_k(second.obj.get("/K")))
        first.obj.K = Array(list(self._iter_k(first.obj.get("/K"))) + rest)
        for c in second.children:
            c.obj.P = first.obj
            c.parent = first.obj
            first.children.append(c)
        second.children = []
        second.obj.K = Array()
        self._mark(first, "repaired", "list split by a note joined with the list that continues it")
        self._mark(second, "removed", "merged into the list before it")
        return True

    def _repair_toc(self, e: ElemInfo) -> None:
        """PDF/UA 7.2-27: TOC may contain only TOC, TOCI and Caption; stray items get a TOCI."""
        items = list(self._iter_k(e.obj.get("/K")))
        ok = lambda it: _is_struct_elem(it) and resolve_role(str(it.S)[1:], self.role_map) in ("TOC", "TOCI", "Caption")  # noqa: E731
        if all(ok(it) for it in items):
            return
        new_k = []
        for it in items:
            if ok(it):
                new_k.append(it)
                continue
            toci = self._new_elem("TOCI", e.obj)
            if _is_struct_elem(it):
                it.P = toci
            toci.K.append(it)
            new_k.append(toci)
        e.obj.K = Array(new_k)
        self._mark(e, "repaired", "stray table-of-contents content wrapped in TOCI")

    def _group_body_rows(self, e: ElemInfo) -> None:
        """PDF/UA 7.2-14: a Table with a THead needs a TBody with rows. Word tags two shapes that break this:
        - the header row as THead and the other rows directly in the Table: those rows go into one TBody, where
          the first of them was;
        - a table that is only a header row, as THead plus an empty TBody: the THead becomes the TBody (its cells
          stay TH) and the empty TBody goes."""
        kids = list(self._iter_k(e.obj.get("/K")))
        roles = [resolve_role(str(k.S)[1:], self.role_map) if _is_struct_elem(k) else None for k in kids]
        if "THead" not in roles:
            return
        has_rows = lambda k: any(_is_struct_elem(r) and resolve_role(str(r.S)[1:], self.role_map) == "TR"  # noqa: E731
                                 for r in self._iter_k(k.get("/K")))
        bodies = [k for k, r in zip(kids, roles) if r == "TBody"]
        if any(has_rows(b) for b in bodies):
            return
        if "TR" not in roles:
            heads = [k for k, r in zip(kids, roles) if r == "THead"]
            if len(heads) != 1:
                return
            heads[0].S = Name.TBody
            dropped = {b.objgen for b in bodies if b.is_indirect}
            e.obj.K = Array([k for k in kids if not (_is_struct_elem(k) and k.is_indirect and k.objgen in dropped)])
            for x in self.elements:
                if x.obj.is_indirect and x.obj.objgen in dropped:
                    self._mark(x, "removed", "empty TBody of a table that is only a header row")
                elif x.obj.is_indirect and x.obj.objgen == heads[0].objgen:
                    x.role = x.raw_role = "TBody"
            self._mark(e, "repaired", "a table of header rows only: its THead became the TBody")
            return
        body = self._new_elem("TBody", e.obj)
        new_k, placed = [], False
        for k, role in zip(kids, roles):
            if role == "TR":
                k.P = body
                body.K.append(k)
                if not placed:
                    new_k.append(body)
                    placed = True
            else:
                new_k.append(k)
        e.obj.K = Array(new_k)
        self._mark(e, "repaired", f"{len(body.K)} row(s) after the THead grouped into a TBody")

    def _pad_table_rows(self, e: ElemInfo) -> None:
        """PDF/UA 7.2-42/43: every row must span the same number of columns (counting row and
        column spans). Short rows get empty TD cells at the end."""
        def span(cell, key):
            for a in self._iter_k(cell.get("/A")):
                if isinstance(a, Dictionary) and key in a:
                    try:
                        value = int(a[key])
                    except (TypeError, ValueError):
                        return 1
                    # A span wider than any real table is the file's error (or a hostile file): padding every
                    # other row out to it would create that many cells
                    return value if 1 <= value <= MAX_SPAN else 1
            return 1

        rows = []
        for kid in self._iter_k(e.obj.get("/K")):
            if not _is_struct_elem(kid):
                continue
            role = resolve_role(str(kid.S)[1:], self.role_map)
            groups = [kid] if role == "TR" else [r for r in self._iter_k(kid.get("/K")) if _is_struct_elem(r)] \
                if role in ("THead", "TBody", "TFoot") else []
            rows += [r for r in groups if resolve_role(str(r.S)[1:], self.role_map) == "TR"]
        if not rows:
            return
        carry: dict[int, int] = {}  # row index -> columns occupied by row spans from above
        widths = []
        for i, row in enumerate(rows):
            cells = [c for c in self._iter_k(row.get("/K")) if _is_struct_elem(c)]
            w = carry.get(i, 0) + sum(span(c, "/ColSpan") for c in cells)
            widths.append(w)
            for c in cells:
                for k in range(1, span(c, "/RowSpan")):
                    carry[i + k] = carry.get(i + k, 0) + span(c, "/ColSpan")
        target = max(widths)
        padded = 0
        for row, w in zip(rows, widths):
            if target > w:
                row.K = Array(list(self._iter_k(row.get("/K"))) + [self._new_elem("TD", row) for _ in range(target - w)])
                padded += target - w
        if padded:
            self._mark(e, "repaired", f"{padded} empty cell(s) added so every row spans {target} columns")

    def _decide_tables(self) -> None:
        for e in self.elements:
            if e.role != "Table" or e.decision == "replaced":
                continue
            problems = []
            for c in e.children:
                if c.role not in _TABLE_KIDS:
                    problems.append(f"/{c.role} directly under Table")
                for row in ([c] if c.role == "TR" else c.children if c.role in ("THead", "TBody", "TFoot") else []):
                    if row.role not in _ROW_GROUP_KIDS:
                        problems.append(f"/{row.role} in row group")
                    for cell in row.children:
                        if cell.role not in _ROW_KIDS:
                            problems.append(f"/{cell.role} in TR")
            if e.mcrefs:
                problems.append("content attached directly to Table")
            if problems:
                for d in self._descendants(e):
                    d.decision = "replaced"
                self._mark(e, "replaced", "malformed table: " + "; ".join(sorted(set(problems))[:3]))

    def _fix_headings(self) -> None:
        prev = 0
        for e in self.elements:  # document order
            if e.decision == "replaced" or len(e.role) != 2 or e.role[0] != "H" or not e.role[1].isdigit():
                continue
            level = int(e.role[1])
            if level > prev + 1:
                new = prev + 1
                e.obj.S = Name(f"/H{new}")
                self._mark(e, "repaired", f"heading level H{level} -> H{new} (no skipped levels)")
                e.role = e.raw_role = f"H{new}"
                level = new
            prev = level

    # ------------------------------------------------------------------ helpers
    def _mark(self, e: ElemInfo, decision: str, reason: str) -> None:
        rank = {"retained": 0, "repaired": 1, "removed": 2, "replaced": 2, "artifacted": 2}
        if rank[decision] > rank[e.decision]:
            e.decision = decision
        e.reasons.append(reason)

    def _descendants(self, e: ElemInfo) -> Iterator[ElemInfo]:
        yield e
        for c in e.children:
            yield from self._descendants(c)

    def _role_of(self, obj) -> str:
        return resolve_role(str(obj.get("/S", "/P"))[1:], self.role_map)

    def _add_role_mapping(self, role: str, target: str) -> None:
        if "/RoleMap" not in self.root:
            self.root.RoleMap = Dictionary()
        self.root.RoleMap[Name("/" + role)] = Name("/" + target)
        self.role_map[role] = target

    def _info_of(self, obj) -> ElemInfo | None:
        """The element whose object this is. Looked up through an index made on first use: scanning every element
        for each lookup made a document with thousands of header cells spend seconds walking up to their tables."""
        if obj is None:
            return None
        index = getattr(self, "_by_obj", None)
        if index is None or len(self.elements) != index[0]:
            by_identity = {id(x.obj): x for x in self.elements}
            by_objgen = {x.obj.objgen: x for x in self.elements if x.obj.is_indirect}
            index = self._by_obj = (len(self.elements), by_identity, by_objgen)
        found = index[1].get(id(obj))
        if found is None and obj.is_indirect:
            found = index[2].get(obj.objgen)
        return found

    def _table_uses_headers(self, e: ElemInfo) -> bool:
        """True if the TH's table links cells to headers via /Headers (then Scope is optional)."""
        node = e
        while node is not None and node.role != "Table":
            node = self._info_of(node.parent)
        if node is None:
            return False
        return any("/Headers" in d.obj for d in self._descendants(node) if d.role in ("TD", "TH"))

    def _infer_scope(self, e: ElemInfo) -> str:
        parent_row = e.parent
        if parent_row is None:
            return "Column"
        siblings = [k for k in self._iter_k(parent_row.get("/K")) if _is_struct_elem(k)]
        all_th = all(str(k.get("/S"))[1:] == "TH" for k in siblings)
        return "Column" if all_th else "Row"

    def _remove_refs(self, e: ElemInfo, refs: list[McRef]) -> None:
        dead = {(r.owner, r.mcid) for r in refs}

        def is_dead(item) -> bool:
            if isinstance(item, Dictionary):
                if "/MCID" not in item:
                    return False
                stm, kpg = item.get("/Stm"), item.get("/Pg")
                owner = stm.objgen if stm is not None else (kpg.objgen if kpg is not None else e.page)
                mcid = mcid_of(item)
                return (owner, -1 if mcid is None else mcid) in dead
            try:
                return (e.page, int(item)) in dead
            except (TypeError, ValueError):
                return False

        k = e.obj.get("/K")
        items = list(k) if isinstance(k, Array) else ([] if k is None else [k])
        e.obj.K = Array([item for item in items if not is_dead(item)])
        e.mcrefs = [m for m in e.mcrefs if m.valid]

    def _detach(self, e: ElemInfo) -> None:
        """Remove e from its parent's /K. Works for direct (inline) elements too: they have no object
        number, so they are tagged with a marker key and filtered out by that."""
        # Use the element's current parent: repairs (e.g. wrapping list content in LBody) may have moved it.
        current = e.obj.get("/P")
        parent = current if isinstance(current, Dictionary) else (e.parent if e.parent is not None else self.root)
        e.obj[Name("/RemediationDrop")] = True

        def dropped(item) -> bool:
            if not isinstance(item, Dictionary):
                return False
            if item.is_indirect and e.obj.is_indirect:
                return item.objgen == e.obj.objgen
            return bool(item.get("/RemediationDrop"))

        k = parent.get("/K")
        if isinstance(k, Array):
            parent.K = Array([item for item in k if not dropped(item)])
        elif k is not None and dropped(k):
            parent.K = Array()
        if "/RemediationDrop" in e.obj:
            del e.obj["/RemediationDrop"]

    def leaf_elements(self) -> list[ElemInfo]:
        return [e for e in self.elements if e.decision not in ("replaced", "removed", "artifacted") and e.mcrefs]

    def artifact_mcids(self) -> set[tuple[ObjGen, int]]:
        return {(m.owner, m.mcid) for e in self.elements if e.decision == "artifacted" for m in e.mcrefs if m.valid}

    def strip(self) -> None:
        """Discard the whole tree (rebuild mode)."""
        for key in ("/StructTreeRoot",):
            if key in self.pdf.Root:
                del self.pdf.Root[key]
        for page in self.pdf.pages:
            if "/StructParents" in page.obj:
                del page.obj["/StructParents"]
            for annot in page.obj.get("/Annots") or []:
                if isinstance(annot, Dictionary) and "/StructParent" in annot:
                    del annot["/StructParent"]
