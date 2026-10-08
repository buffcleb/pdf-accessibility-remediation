"""Internal accessibility audit used for the before/after comparison (independent of veraPDF)."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import pikepdf
import pymupdf as fitz
from pikepdf import Array, ContentStreamInstruction, Dictionary, Name

from app.pipeline.analyzer import _font_issues
from app.tagging.existing_tags import _is_struct_elem, _role_map, resolve_role

_CONTENT_OPS = {"Tj", "TJ", "'", '"', "Do", "S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "sh", "INLINE IMAGE"}


def _content_coverage(stream_obj) -> tuple[int, int]:
    """(units inside MCID/Artifact marked content, total content units) for one content stream."""
    stack: list[bool] = []
    inside = total = 0
    try:
        instructions = pikepdf.parse_content_stream(stream_obj)
    except pikepdf.PdfError:
        return (0, 0)
    for ins in instructions:
        op = "INLINE IMAGE" if not isinstance(ins, ContentStreamInstruction) else str(ins.operator)
        if op in ("BDC", "BMC"):
            args = list(ins.operands)
            tagging = (op == "BDC" and len(args) == 2 and isinstance(args[1], Dictionary) and "/MCID" in args[1]) or (
                bool(args) and args[0] == Name.Artifact)
            stack.append(tagging)
        elif op == "EMC":
            if stack:
                stack.pop()
        elif op in _CONTENT_OPS:
            total += 1
            if any(stack):
                inside += 1
    return inside, total


def audit_pdf(path: Path | str) -> dict:
    out: dict = {}
    with pikepdf.open(path) as pdf:
        root = pdf.Root
        tree = root.get("/StructTreeRoot")
        out["tagged"] = tree is not None
        mark = root.get("/MarkInfo")
        out["marked"] = bool(isinstance(mark, Dictionary) and mark.get("/Marked") is True)
        out["language"] = str(root.get("/Lang")) if root.get("/Lang") else None
        vp = root.get("/ViewerPreferences")
        out["display_doc_title"] = bool(isinstance(vp, Dictionary) and vp.get("/DisplayDocTitle") is True)
        title = None
        try:
            with pdf.open_metadata() as meta:
                title = meta.get("dc:title")
                out["pdfua_identifier"] = meta.get("pdfuaid:part")
        except Exception:  # noqa: BLE001 - malformed XMP is itself an audit finding
            out["pdfua_identifier"] = None
        title = title or (str(pdf.docinfo.get("/Title")) if pdf.docinfo.get("/Title") else None)
        out["title"] = title

        roles: Counter = Counter()
        figures_missing_alt = 0
        tables_without_th = 0
        heading_levels: list[int] = []
        if tree is not None:
            rm = _role_map(tree)
            for it in iter_struct_elems(tree):
                role = resolve_role(str(it.S)[1:], rm)
                roles[role] += 1
                if role in ("Figure", "Formula") and not (it.get("/Alt") or it.get("/ActualText")):
                    figures_missing_alt += 1
                if role == "Table" and not _has_descendant(it, "TH", rm):
                    tables_without_th += 1
                if len(role) == 2 and role[0] == "H" and role[1].isdigit():
                    heading_levels.append(int(role[1]))
        skips, prev = 0, 0
        for lvl in heading_levels:
            if lvl > prev + 1:
                skips += 1
            prev = lvl
        out["struct_elements"] = sum(roles.values())
        out["roles"] = dict(roles.most_common(20))
        out["headings"] = len(heading_levels)
        out["heading_level_skips"] = skips
        out["figures"] = roles["Figure"] + roles["Formula"]
        out["figures_missing_alt"] = figures_missing_alt
        out["tables"] = roles["Table"]
        out["tables_without_headers"] = tables_without_th

        inside = total = 0
        untagged_annots = 0
        tabs_ok = 0
        for page in pdf.pages:
            i, t = _content_coverage(page.obj)
            inside += i
            total += t
            if page.obj.get("/Tabs") == Name.S:
                tabs_ok += 1
            for a in page.obj.get("/Annots") or []:
                if isinstance(a, Dictionary) and a.get("/Subtype") not in (Name.Popup,) and "/StructParent" not in a:
                    untagged_annots += 1
        out["content_units"] = total
        out["content_units_tagged"] = inside
        out["content_tagged_ratio"] = round(inside / total, 4) if total else 1.0
        out["annotations_untagged"] = untagged_annots
        out["pages_with_structure_tab_order"] = tabs_ok
        out["pages"] = len(pdf.pages)

    doc = fitz.open(path)
    try:
        issues = []
        for n in range(doc.page_count):
            issues += _font_issues(doc, doc[n], n)
        out["font_problems"] = len({(i["xref"], tuple(i["problems"])) for i in issues})
    finally:
        doc.close()
    return out


def iter_struct_elems(node, seen: set | None = None):
    """Structure elements in document (pre-)order."""
    seen = set() if seen is None else seen
    k = node.get("/K")
    for it in (list(k) if isinstance(k, Array) else [k]):
        if not _is_struct_elem(it):
            continue
        if it.is_indirect:
            if it.objgen in seen:
                continue
            seen.add(it.objgen)
        yield it
        yield from iter_struct_elems(it, seen)


def _has_descendant(elem, role: str, rm) -> bool:
    stack = [elem.get("/K")]
    seen = set()
    while stack:
        k = stack.pop()
        for it in (list(k) if isinstance(k, Array) else [k]):
            if _is_struct_elem(it):
                if it.is_indirect:
                    if it.objgen in seen:
                        continue
                    seen.add(it.objgen)
                if resolve_role(str(it.S)[1:], rm) == role:
                    return True
                if "/K" in it:
                    stack.append(it.K)
    return False
