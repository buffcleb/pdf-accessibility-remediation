"""Split a large PDF into parts Adobe accepts, and merge the tagged parts back into one document.

Splitting keeps every page as it is; each part is the original with the other pages removed, so it
keeps the fonts, resources and document settings. Existing tags are dropped from parts (Autotag
replaces tags anyway, and a part's tag tree would point at pages it no longer has).

Merging grafts each tagged part onto the ORIGINAL document rather than concatenating the parts, so
bookmarks, links, form fields and metadata that point at pages keep working:
  * each original page takes its tagged part's content, resources and StructParents;
  * annotations keep their identity and take the part's StructParent (and descriptions it added);
  * each part's tag tree goes under one Document element, with page and annotation references
    pointed at the original pages and annotations, and its parent-tree keys shifted so they don't
    collide with the earlier parts'.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name

from app.tagging.tree import items as _items

_PG, _OBJ = "/AdobeMergePage", "/AdobeMergeAnnot"  # temporary markers while a part's tree is copied


@dataclass
class Part:
    first: int  # 0-based index of the part's first page in the original
    pages: int
    path: Path


def _strip_tags(pdf: pikepdf.Pdf) -> None:
    root = pdf.Root
    for k in ("/StructTreeRoot",):
        if k in root:
            del root[k]
    for page in pdf.pages:
        if "/StructParents" in page.obj:
            del page.obj["/StructParents"]
        for annot in page.obj.get("/Annots", Array()):
            if isinstance(annot, Dictionary) and "/StructParent" in annot:
                del annot["/StructParent"]


def _prune_part(pdf: pikepdf.Pdf) -> None:
    """Drop references to removed pages (bookmarks, named destinations, links, form fields), which
    would otherwise keep those pages, and everything they use, in the part. The merge takes these
    from the original, so nothing is lost."""
    root = pdf.Root
    for k in ("/Outlines", "/OpenAction"):
        if k in root:
            del root[k]
    if "/Names" in root and "/Dests" in root["/Names"]:
        del root["/Names"]["/Dests"]
    if "/Dests" in root:
        del root["/Dests"]
    kept = {p.obj.objgen for p in pdf.pages}
    first = pdf.pages[0].obj

    def fix_dest(d) -> None:
        if isinstance(d, Array) and len(d) and isinstance(d[0], Dictionary) and d[0].objgen not in kept:
            d[0] = first

    for page in pdf.pages:
        for annot in page.obj.get("/Annots", Array()):
            if not isinstance(annot, Dictionary):
                continue
            fix_dest(annot.get("/Dest"))
            action = annot.get("/A")
            if isinstance(action, Dictionary):
                fix_dest(action.get("/D"))
    form = root.get("/AcroForm")
    if isinstance(form, Dictionary) and "/Fields" in form:
        def on_kept_page(field) -> bool:
            if "/Kids" in field:
                field["/Kids"] = Array([k for k in field["/Kids"] if on_kept_page(k)])
                return len(field["/Kids"]) > 0
            p = field.get("/P")
            return not isinstance(p, Dictionary) or p.objgen in kept

        form["/Fields"] = Array([f for f in form["/Fields"] if isinstance(f, Dictionary) and on_kept_page(f)])


def split(src: Path, work: Path, max_pages: int, max_mb: float) -> list[Part]:
    """One part (the original, untouched) if it's within the limits; otherwise parts of at most
    max_pages pages, halved further until each is under max_mb."""
    with pikepdf.open(src) as pdf:
        total = len(pdf.pages)
    if total <= max_pages and src.stat().st_size <= max_mb * 1024 * 1024:
        return [Part(0, total, src)]
    parts: list[Part] = []
    ranges = [(a, min(total, a + max_pages)) for a in range(0, total, max_pages)]
    while ranges:
        a, b = ranges.pop(0)
        path = work / f"adobe_part_{a + 1:05d}_{b:05d}.pdf"
        with pikepdf.open(src) as pdf:
            for i in range(total - 1, -1, -1):
                if not a <= i < b:
                    del pdf.pages[i]
            _strip_tags(pdf)
            _prune_part(pdf)
            pdf.remove_unreferenced_resources()
            pdf.save(path)
        if path.stat().st_size > max_mb * 1024 * 1024:
            if b - a == 1:
                raise ValueError(f"page {a + 1} alone is larger than {max_mb} MB, Adobe's size limit per request")
            mid = (a + b) // 2
            ranges[:0] = [(a, mid), (mid, b)]
            path.unlink()
            continue
        parts.append(Part(a, b - a, path))
    return parts


# --- merge -----------------------------------------------------------------------------------

def _copy(out: pikepdf.Pdf, obj):
    """copy_foreign for any object: indirect objects are copied (once per source document), direct
    containers are rebuilt around copies of what they reference."""
    if isinstance(obj, pikepdf.Object) and obj.is_indirect:
        return out.copy_foreign(obj)
    if isinstance(obj, Dictionary):
        return Dictionary({k: _copy(out, v) for k, v in obj.items()})
    if isinstance(obj, Array):
        return Array([_copy(out, v) for v in obj])
    return obj


def _number_tree(node, out: dict[int, object]) -> None:
    if "/Nums" in node:
        nums = node["/Nums"]
        for i in range(0, len(nums) - 1, 2):
            out[int(nums[i])] = nums[i + 1]
    for kid in node.get("/Kids", Array()):
        _number_tree(kid, out)


def _shift_struct_parents(part: pikepdf.Pdf, offset: int) -> None:
    """Add offset to every StructParents/StructParent key in the part (pages, annotations, XObjects)."""
    seen: set[tuple[int, int]] = set()

    def xobjects(res) -> None:
        for _, xo in (res.get("/XObject") or Dictionary()).items():
            if not isinstance(xo, pikepdf.Stream) or xo.objgen in seen:
                continue
            seen.add(xo.objgen)
            if "/StructParents" in xo:
                xo["/StructParents"] = int(xo["/StructParents"]) + offset
            if "/Resources" in xo:
                xobjects(xo["/Resources"])

    for page in part.pages:
        obj = page.obj
        if "/StructParents" in obj:
            obj["/StructParents"] = int(obj["/StructParents"]) + offset
        for annot in obj.get("/Annots", Array()):
            if isinstance(annot, Dictionary) and "/StructParent" in annot and annot.objgen not in seen:
                seen.add(annot.objgen)
                annot["/StructParent"] = int(annot["/StructParent"]) + offset
        if "/Resources" in obj:
            xobjects(obj["/Resources"])


def _mark_refs(part: pikepdf.Pdf, top: list) -> None:
    """Replace page and annotation references in the part's tag tree with index markers, so copying
    the tree into the merged file doesn't drag the part's pages along."""
    page_idx = {p.obj.objgen: i for i, p in enumerate(part.pages)}
    annot_idx = {a.objgen: (i, j) for i, p in enumerate(part.pages)
                 for j, a in enumerate(p.obj.get("/Annots", Array())) if isinstance(a, Dictionary)}
    seen: set[tuple[int, int]] = set()

    def walk(node) -> None:
        if not isinstance(node, Dictionary):
            return
        if node.is_indirect:
            if node.objgen in seen:
                return
            seen.add(node.objgen)
        pg = node.get("/Pg")
        if isinstance(pg, Dictionary) and pg.objgen in page_idx:
            node[_PG] = page_idx[pg.objgen]
            del node["/Pg"]
        obj = node.get("/Obj")
        if node.get("/Type") == Name.OBJR and isinstance(obj, Dictionary) and obj.objgen in annot_idx:
            node[_OBJ] = Array(annot_idx[obj.objgen])
            del node["/Obj"]
        for kid in _items(node.get("/K")):
            walk(kid)

    for t in top:
        walk(t)


def _resolve_refs(top: list, pages: list, annots: dict[tuple[int, int], object]) -> None:
    seen: set[tuple[int, int]] = set()

    def walk(node) -> None:
        if not isinstance(node, Dictionary):
            return
        if node.is_indirect:
            if node.objgen in seen:
                return
            seen.add(node.objgen)
        if _PG in node:
            node["/Pg"] = pages[int(node[_PG])]
            del node[_PG]
        if _OBJ in node:
            i, j = (int(x) for x in node[_OBJ])
            node["/Obj"] = annots.get((i, j), None)
            del node[_OBJ]
        for kid in _items(node.get("/K")):
            walk(kid)

    for t in top:
        walk(t)


def merge(original: Path, parts: list[tuple[Part, Path]], dst: Path) -> list[str]:
    """Graft the tagged parts onto the original. Returns notes about anything that didn't line up."""
    notes: list[str] = []
    out = pikepdf.open(original)
    try:
        _strip_tags(out)
        doc = out.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, K=Array()))
        nums = Array()
        tree = out.make_indirect(Dictionary(Type=Name.StructTreeRoot, K=Array([doc]),
                                            ParentTree=out.make_indirect(Dictionary(Nums=nums))))
        doc["/P"] = tree
        role_map, class_map = Dictionary(), Dictionary()
        next_key = 0
        for part, tagged_path in sorted(parts, key=lambda p: p[0].first):
            with pikepdf.open(tagged_path) as tp:
                if len(tp.pages) != part.pages:
                    raise ValueError(f"Adobe returned {len(tp.pages)} pages for a part of {part.pages}")
                src_root = tp.Root.get("/StructTreeRoot")
                if src_root is None:
                    notes.append(f"pages {part.first + 1}-{part.first + part.pages}: Adobe returned no tags")
                    continue
                entries: dict[int, object] = {}
                if "/ParentTree" in src_root:
                    _number_tree(src_root["/ParentTree"], entries)
                keys = [int(p.obj["/StructParents"]) for p in tp.pages if "/StructParents" in p.obj]
                offset = next_key
                _shift_struct_parents(tp, offset)
                top = _items(src_root.get("/K"))
                part_doc = None
                if len(top) == 1 and isinstance(top[0], Dictionary) and top[0].get("/S") == Name.Document:
                    part_doc = top[0].objgen
                    top = _items(top[0].get("/K"))  # the part's own Document element is replaced by ours
                for t in top:
                    if isinstance(t, Dictionary) and "/P" in t:
                        del t["/P"]
                _mark_refs(tp, top)

                pages = [out.pages[part.first + i] for i in range(part.pages)]
                annots: dict[tuple[int, int], object] = {}
                for i, (page, tpage) in enumerate(zip(pages, tp.pages)):
                    for key in ("/Contents", "/Resources"):
                        if key in tpage.obj:
                            page.obj[key] = _copy(out, tpage.obj[key])
                    if "/StructParents" in tpage.obj:
                        page.obj["/StructParents"] = int(tpage.obj["/StructParents"])
                    page.obj["/Tabs"] = Name.S
                    mine = [a for a in page.obj.get("/Annots", Array()) if isinstance(a, Dictionary)]
                    theirs = [a for a in tpage.obj.get("/Annots", Array()) if isinstance(a, Dictionary)]
                    if len(mine) != len(theirs):
                        notes.append(f"page {part.first + i + 1}: Adobe returned {len(theirs)} annotation(s) for "
                                     f"{len(mine)}; their tags were not carried over")
                        continue
                    for j, (a, b) in enumerate(zip(mine, theirs)):
                        if "/StructParent" in b:
                            a["/StructParent"] = int(b["/StructParent"])
                        if "/Contents" in b and "/Contents" not in a:
                            a["/Contents"] = pikepdf.String(str(b["/Contents"]))
                        annots[(i, j)] = a

                copied = [_copy(out, t) for t in top if isinstance(t, Dictionary)]
                _resolve_refs(copied, [p.obj for p in pages], annots)
                for t in copied:
                    t["/P"] = doc
                    doc["/K"].append(t)
                def parent(e):
                    if not (isinstance(e, Dictionary) and e.is_indirect):
                        return None
                    return doc if e.objgen == part_doc else out.copy_foreign(e)

                for k in sorted(entries):
                    v = entries[k]
                    nums.append(k + offset)
                    nums.append(out.make_indirect(Array([parent(e) for e in v])) if isinstance(v, Array) else parent(v))
                for src_map, dst_map in ((src_root.get("/RoleMap"), role_map), (src_root.get("/ClassMap"), class_map)):
                    for name, val in (src_map or Dictionary()).items():
                        if name not in dst_map:
                            dst_map[name] = _copy(out, val)
                if part.first == 0:
                    # Document-level settings the tagger set (language, metadata, title display) come
                    # from the first part; every part started from the same original.
                    if "/Lang" in tp.Root:
                        out.Root["/Lang"] = pikepdf.String(str(tp.Root["/Lang"]))
                    if "/Metadata" in tp.Root:
                        out.Root["/Metadata"] = _copy(out, tp.Root["/Metadata"])
                    if "/ViewerPreferences" in tp.Root:
                        vp = out.Root.get("/ViewerPreferences") or Dictionary()
                        for k, v in tp.Root["/ViewerPreferences"].items():
                            vp[k] = v
                        out.Root["/ViewerPreferences"] = vp
                elif "/Lang" not in out.Root and "/Lang" in tp.Root:
                    out.Root["/Lang"] = pikepdf.String(str(tp.Root["/Lang"]))
                span = max([*entries, *keys], default=-1) + 1
                next_key = offset + span
        if len(role_map):
            tree["/RoleMap"] = role_map
        if len(class_map):
            tree["/ClassMap"] = class_map
        tree["/ParentTreeNextKey"] = next_key
        out.Root["/StructTreeRoot"] = tree
        mi = out.Root.get("/MarkInfo") or Dictionary()
        mi["/Marked"] = True
        out.Root["/MarkInfo"] = mi
        out.save(dst)
    finally:
        out.close()
    return notes
