"""Layer 1: structural integrity of the tag tree (ISO 32000-1 §14.7, PDF/UA-1 7.1).

Reads the finished PDF back with its own tree walk and content-stream scan. Nothing here is
shared with the tagging code, so a bug in the writer cannot hide itself. Every "error" means
assistive technology would get wrong or missing structure, and fails the job.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pikepdf
from pikepdf import Array, ContentStreamInstruction, Dictionary, Name

from app.tagging.mathml_tags import in_mathml as _in_mathml
from app.verification.model import Finding

STANDARD = {
    "Document", "Part", "Art", "Sect", "Div", "BlockQuote", "Caption", "TOC", "TOCI", "Index", "NonStruct",
    "Private", "P", "H", "H1", "H2", "H3", "H4", "H5", "H6", "L", "LI", "Lbl", "LBody", "Table", "TR", "TH",
    "TD", "THead", "TBody", "TFoot", "Span", "Quote", "Note", "Reference", "BibEntry", "Code", "Link",
    "Annot", "Ruby", "RB", "RT", "RP", "Warichu", "WT", "WP", "Figure", "Formula", "Form",
}
# ISO 32000-2 Annex L parent -> allowed children (checked as warnings for PDF 1.x files).
ALLOWED_KIDS = {
    "Table": {"TR", "THead", "TBody", "TFoot", "Caption"},
    "THead": {"TR"}, "TBody": {"TR"}, "TFoot": {"TR"},
    "TR": {"TH", "TD"},
    "L": {"L", "LI", "Caption"},
    "LI": {"Lbl", "LBody"},
    "TOC": {"TOC", "TOCI", "Caption"},
}
CHECKS = ("content_marks", "references", "parent_tree", "annotations", "parents", "roles", "nesting")

ObjGen = tuple[int, int]


@dataclass
class Elem:
    index: int
    obj: pikepdf.Object
    role: str
    raw_role: str
    page: int | None  # 0-based
    parent: int | None
    kids: list[int] = field(default_factory=list)
    content_refs: int = 0
    objrs: int = 0


@dataclass
class TagIndex:
    """Everything Layers 2 and 3 need about the tree, built once here."""
    elements: list[Elem] = field(default_factory=list)
    # (owner objgen, mcid) -> element indexes referencing it
    refs: dict[tuple[ObjGen, int], list[int]] = field(default_factory=dict)
    page_of_owner: dict[ObjGen, int] = field(default_factory=dict)
    page_objgens: list[ObjGen] = field(default_factory=list)


def _og(o) -> ObjGen | None:
    return o.objgen if isinstance(o, pikepdf.Object) and o.is_indirect else None


def _items(k) -> list:
    if k is None:
        return []
    return list(k) if isinstance(k, Array) else [k]


def _is_elem(o) -> bool:
    return isinstance(o, Dictionary) and "/S" in o and o.get("/Type") not in (Name.MCR, Name.OBJR)


def _mcid(item) -> int | None:
    """The marked-content id a BDC's properties or a tree's reference names; None when it isn't an integer."""
    value = item.get("/MCID") if isinstance(item, Dictionary) else item
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class IntegrityChecker:
    def __init__(self, pdf: pikepdf.Pdf):
        self.pdf = pdf
        self.findings: list[Finding] = []
        self.index = TagIndex()
        self.stream_mcids: dict[ObjGen, set[int]] = {}
        self.stream_page: dict[ObjGen, int] = {}
        self.stream_obj: dict[ObjGen, pikepdf.Object] = {}

    def err(self, issue: str, **kw) -> None:
        self.findings.append(Finding(layer="integrity", severity="error", issue=issue, **kw))

    def warn(self, issue: str, **kw) -> None:
        self.findings.append(Finding(layer="integrity", severity="warning", issue=issue, **kw))

    # ------------------------------------------------------------------ entry
    def run(self) -> tuple[list[Finding], TagIndex]:
        root = self.pdf.Root.get("/StructTreeRoot")
        if not isinstance(root, Dictionary):
            self.err("document has no structure tree")
            return self.findings, self.index
        self.index.page_objgens = [p.objgen for p in self.pdf.pages]
        for i, page in enumerate(self.pdf.pages):
            self._scan_stream(page.obj, i, self._resources(page.obj))
        self.index.page_of_owner = dict(self.stream_page)
        self._walk_tree(root)
        self._check_references()
        self._check_parent_tree(root)
        return self.findings, self.index

    # ------------------------------------------------------------------ content streams
    @staticmethod
    def _resources(node):
        # Each node once: a /Parent chain that loops would be walked for ever (this layer shares no code with
        # the tagger, so the same guard is written here)
        seen = set()
        while isinstance(node, Dictionary):
            if "/Resources" in node:
                return node.Resources
            if node.is_indirect:
                if node.objgen in seen:
                    break
                seen.add(node.objgen)
            node = node.get("/Parent")
        return Dictionary()

    def _scan_stream(self, owner, page_no: int, resources) -> None:
        og = owner.objgen
        if og in self.stream_mcids:
            return
        mcids: set[int] = set()
        self.stream_mcids[og] = mcids
        self.stream_page[og] = page_no
        self.stream_obj[og] = owner
        try:
            ops = pikepdf.parse_content_stream(owner)
        except pikepdf.PdfError as exc:
            self.err("content stream could not be parsed", page=page_no + 1, detail=str(exc))
            return
        stack: list[int | None] = []
        xobjs = resources.get("/XObject") if isinstance(resources, Dictionary) else None
        for ins in ops:
            if not isinstance(ins, ContentStreamInstruction):
                continue
            op = str(ins.operator)
            if op in ("BDC", "BMC"):
                mcid = None
                args = list(ins.operands)
                if op == "BDC" and len(args) == 2 and isinstance(args[1], Dictionary) and "/MCID" in args[1]:
                    mcid = _mcid(args[1])
                    if mcid is None:
                        self.err("marked-content id is not an integer", page=page_no + 1, detail=str(args[1].MCID))
                        stack.append(None)
                        continue
                    if any(m is not None for m in stack):
                        self.err("marked-content sequence with an MCID nested inside another MCID sequence",
                                 page=page_no + 1, detail=f"MCID {mcid}")
                    if mcid in mcids:
                        self.err("MCID used more than once in one content stream", page=page_no + 1,
                                 detail=f"MCID {mcid}")
                    mcids.add(mcid)
                stack.append(mcid)
            elif op == "EMC":
                if not stack:
                    self.err("EMC without a matching BDC/BMC", page=page_no + 1)
                else:
                    stack.pop()
            elif op == "Do" and ins.operands and isinstance(xobjs, Dictionary):
                x = xobjs.get(ins.operands[0])
                if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == Name.Form:
                    self._scan_stream(x, page_no, x.get("/Resources") or resources)
        if stack:
            self.err("marked-content sequence not closed by EMC", page=page_no + 1, detail=f"{len(stack)} open")

    # ------------------------------------------------------------------ tree
    def _walk_tree(self, root) -> None:
        role_map = {}
        rm = root.get("/RoleMap")
        if isinstance(rm, Dictionary):
            role_map = {str(k)[1:]: str(v)[1:] for k, v in rm.items() if isinstance(v, Name)}
        page_index = {og: i for i, og in enumerate(self.index.page_objgens)}
        seen: set[ObjGen] = set()
        self.annot_owner: dict[ObjGen, list[int]] = {}

        def resolve(role: str) -> str:
            chain = []
            while role not in STANDARD and role in role_map:
                if role in chain:
                    self.err("RoleMap contains a cycle", detail=" -> ".join(chain + [role]))
                    return role
                chain.append(role)
                role = role_map[role]
            return role

        # explicit stack: (object, parent element index, inherited page objgen, expected parent obj)
        stack = [(k, None, None, root) for k in reversed(_items(root.get("/K"))) if _is_elem(k)]
        elems = self.index.elements
        while stack:
            obj, parent_idx, inherited_pg, expected_parent = stack.pop()
            og = _og(obj)
            if og is not None:
                if og in seen:
                    self.err("structure element appears twice in the tree (cycle or shared element)",
                             detail=f"object {og[0]}")
                    continue
                seen.add(og)
            raw = str(obj.get("/S", "/?"))[1:]
            role = resolve(raw)
            pg = obj.get("/Pg")
            pg_og = _og(pg) or inherited_pg
            e = Elem(index=len(elems), obj=obj, role=role, raw_role=raw,
                     page=page_index.get(pg_og) if pg_og else None, parent=parent_idx)
            elems.append(e)
            if parent_idx is not None:
                elems[parent_idx].kids.append(e.index)
            if role not in STANDARD and not _in_mathml(obj):  # MathML says what it is by its namespace (#403)
                self.err("structure type is not standard and not role-mapped", role=raw, element=e.index,
                         page=(e.page + 1) if e.page is not None else None)
            p = obj.get("/P")
            if og is not None and (p is None or _og(p) != _og(expected_parent)):
                self.err("element's /P does not point to the element that contains it", role=raw, element=e.index,
                         page=(e.page + 1) if e.page is not None else None)
            children = []
            for item in _items(obj.get("/K")):
                if _is_elem(item):
                    children.append(item)
                elif isinstance(item, int):
                    if pg_og is None:
                        self.err("marked-content reference without a page (/Pg)", role=raw, element=e.index)
                        continue
                    self.index.refs.setdefault((pg_og, item), []).append(e.index)
                    e.content_refs += 1
                elif isinstance(item, Dictionary) and "/MCID" in item:
                    owner = _og(item.get("/Stm")) or _og(item.get("/Pg")) or pg_og
                    mcid = _mcid(item)
                    if owner is None:
                        self.err("marked-content reference without a page or stream", role=raw, element=e.index)
                        continue
                    if mcid is None:
                        self.err("marked-content reference whose id is not an integer", role=raw, element=e.index)
                        continue
                    self.index.refs.setdefault((owner, mcid), []).append(e.index)
                    e.content_refs += 1
                elif isinstance(item, Dictionary) and "/Obj" in item:
                    a = _og(item.Obj)
                    if a is not None:
                        self.annot_owner.setdefault(a, []).append(e.index)
                    e.objrs += 1
            for c in reversed(children):
                stack.append((c, e.index, pg_og, obj))
        # kids lists were appended in pre-order; fine for nesting checks
        self._check_nesting()

    def _check_nesting(self) -> None:
        els = self.index.elements
        for e in els:
            pg = (e.page + 1) if e.page is not None else None
            allowed = ALLOWED_KIDS.get(e.role)
            if allowed:
                bad = sorted({els[k].role for k in e.kids if els[k].role not in allowed})
                if bad:
                    self.warn(f"/{e.role} contains {', '.join('/' + b for b in bad)}", role=e.role,
                              element=e.index, page=pg)
                if e.role in ("Table", "TR", "L", "TOC") and e.content_refs:
                    self.warn(f"/{e.role} holds content directly instead of in child elements", role=e.role,
                              element=e.index, page=pg)
            if e.role == "Caption" and e.parent is not None and els[e.parent].role in ("Figure", "Table"):
                siblings = els[e.parent].kids
                if siblings and e.index not in (siblings[0], siblings[-1]):
                    self.warn("Caption is not the first or last child of its Figure/Table", element=e.index, page=pg)
            if e.role == "Link" and not e.objrs:
                self.warn("Link element has no link annotation (OBJR)", element=e.index, page=pg)
            if not e.kids and not e.content_refs and not e.objrs and e.role not in ("TD", "TH", "Document"):
                self.warn("element has no content", role=e.role, element=e.index, page=pg)

    # ------------------------------------------------------------------ references
    def _check_references(self) -> None:
        refs = self.index.refs
        for (owner, mcid), users in refs.items():
            page = self.stream_page.get(owner)
            pg = (page + 1) if page is not None else None
            if owner not in self.stream_mcids or mcid not in self.stream_mcids[owner]:
                self.err("structure element points at content that does not exist", page=pg, element=users[0],
                         role=self.index.elements[users[0]].role, detail=f"MCID {mcid}")
            if len(users) > 1:
                self.err("content belongs to more than one structure element", page=pg, element=users[0],
                         detail=f"MCID {mcid} referenced by {len(users)} elements")
        for owner, mcids in self.stream_mcids.items():
            missing = [m for m in mcids if (owner, m) not in refs]
            if missing:
                self.err("tagged content is not connected to any structure element",
                         page=self.stream_page[owner] + 1, detail=f"MCIDs {sorted(missing)[:10]}")

    # ------------------------------------------------------------------ parent tree
    def _number_tree(self, node, out: dict, depth: int = 0) -> None:
        if depth > 32 or not isinstance(node, Dictionary):
            return
        nums = node.get("/Nums")
        if isinstance(nums, Array):
            for i in range(0, len(nums) - 1, 2):
                out[int(nums[i])] = nums[i + 1]
        for kid in _items(node.get("/Kids")):
            self._number_tree(kid, out, depth + 1)

    def _check_parent_tree(self, root) -> None:
        tree: dict[int, object] = {}
        pt = root.get("/ParentTree")
        if pt is None:
            if any(self.stream_mcids.values()):
                self.err("structure tree has no ParentTree although content is tagged")
            return
        self._number_tree(pt, tree)
        elem_by_og = {_og(e.obj): e.index for e in self.index.elements if _og(e.obj)}
        used_keys: dict[int, str] = {}

        def claim(key: int, who: str) -> None:
            if key in used_keys:
                self.err("two objects share one ParentTree key", detail=f"key {key}: {used_keys[key]} and {who}")
            used_keys[key] = who

        for owner, mcids in self.stream_mcids.items():
            if not mcids:
                continue
            obj = self.stream_obj[owner]
            page = self.stream_page[owner] + 1
            key = obj.get("/StructParents")
            if key is None:
                self.err("tagged page or form has no /StructParents", page=page)
                continue
            claim(int(key), f"stream {owner[0]}")
            arr = tree.get(int(key))
            if not isinstance(arr, Array):
                self.err("ParentTree has no entry for this page", page=page, detail=f"key {int(key)}")
                continue
            for mcid in mcids:
                entry = arr[mcid] if mcid < len(arr) else None
                users = self.index.refs.get((owner, mcid), [])
                if not isinstance(entry, Dictionary) or not users or elem_by_og.get(_og(entry)) not in users:
                    self.err("ParentTree maps content to the wrong element", page=page, detail=f"MCID {mcid}")
        for i, page in enumerate(self.pdf.pages):
            for annot in _items(page.obj.get("/Annots")):
                if not isinstance(annot, Dictionary):
                    continue
                aog = _og(annot)
                owners = self.annot_owner.get(aog, [])
                key = annot.get("/StructParent")
                if len(owners) > 1:
                    self.err("annotation belongs to more than one structure element", page=i + 1)
                if owners and key is None:
                    self.err("tagged annotation has no /StructParent", page=i + 1)
                if key is not None:
                    claim(int(key), f"annotation {aog[0] if aog else '?'}")
                    entry = tree.get(int(key))
                    if not owners:
                        self.err("annotation has /StructParent but no structure element refers to it", page=i + 1)
                    elif not isinstance(entry, Dictionary) or elem_by_og.get(_og(entry)) not in owners:
                        self.err("ParentTree maps an annotation to the wrong element", page=i + 1)


def check_integrity(pdf: pikepdf.Pdf) -> tuple[list[Finding], TagIndex]:
    return IntegrityChecker(pdf).run()
