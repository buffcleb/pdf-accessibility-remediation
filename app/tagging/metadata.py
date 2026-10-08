"""Document-level PDF/UA requirements: MarkInfo, Lang, title, XMP identifier, tab order, OC configs."""
from __future__ import annotations

import pikepdf
from pikepdf import Dictionary, Name, String


def apply_document_metadata(pdf: pikepdf.Pdf, *, title: str, language: str, pdfua_part: int) -> list[str]:
    changes: list[str] = []
    root = pdf.Root

    mark = root.get("/MarkInfo")
    if not isinstance(mark, Dictionary):
        root.MarkInfo = mark = Dictionary()
    if mark.get("/Marked") is not True:
        mark.Marked = True
        changes.append("MarkInfo/Marked set")
    if "/Suspects" in mark:
        del mark["/Suspects"]

    if str(root.get("/Lang", "")).strip() != language:
        root.Lang = String(language)
        changes.append(f"document language set to {language}")

    vp = root.get("/ViewerPreferences")
    if not isinstance(vp, Dictionary):
        root.ViewerPreferences = vp = Dictionary()
    if vp.get("/DisplayDocTitle") is not True:
        vp.DisplayDocTitle = True
        changes.append("DisplayDocTitle enabled")

    pdf.docinfo[Name.Title] = String(title)
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        meta["dc:title"] = title
        meta["pdfuaid:part"] = str(pdfua_part)
        if language:
            meta["dc:language"] = [language]
    changes.append(f"title and PDF/UA-{pdfua_part} identifier written to XMP")

    tabs = 0
    for page in pdf.pages:
        if page.obj.get("/Tabs") != Name.S:
            page.obj.Tabs = Name.S
            tabs += 1
    if tabs:
        changes.append(f"tab order set to structure order on {tabs} page(s)")

    ocp = root.get("/OCProperties")
    if isinstance(ocp, Dictionary):
        configs = [ocp.get("/D")] + list(ocp.get("/Configs") or [])
        for i, cfg in enumerate(c for c in configs if isinstance(c, Dictionary)):
            if "/Name" not in cfg or not str(cfg.Name).strip():
                cfg.Name = String(f"Layer configuration {i + 1}")
                changes.append("optional content configuration named")
            if "/AS" in cfg:
                del cfg["/AS"]
                changes.append("optional content /AS removed")
    return changes

