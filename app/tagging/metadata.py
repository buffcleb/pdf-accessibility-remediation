"""Document-level PDF/UA requirements: MarkInfo, Lang, title, XMP identifier, tab order, OC configs, and the
names of embedded files."""
from __future__ import annotations

import re

import pikepdf
from pikepdf import Dictionary, Name, String


def apply_document_metadata(pdf: pikepdf.Pdf, *, title: str, language: str, pdfua_part: int) -> list[str]:
    changes: list[str] = []
    root = pdf.Root

    # An XFA form (Adobe LiveCycle) beside the standard one: the XFA part goes, so readers use the standard form that
    # was tagged (#360; Matterhorn 25-001). A dynamic XFA form has no standard form and is refused at upload.
    form = root.get("/AcroForm")
    if isinstance(form, Dictionary) and "/XFA" in form:
        del form["/XFA"]
        changes.append("XFA form data removed (the standard form stays)")
    if "/NeedsRendering" in root:
        del root["/NeedsRendering"]

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
    rewritten = _drop_old_style_metadata(pdf)
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        if rewritten:
            # Author, dates and the rest come back from the document information
            try:
                meta.load_from_docinfo(pdf.docinfo)
            except Exception:  # noqa: BLE001 - an odd date or encoding there mustn't cost the title and identifier
                pass
        meta["dc:title"] = title
        meta["pdfuaid:part"] = str(pdfua_part)
        if pdfua_part == 2:
            meta["pdfuaid:rev"] = "2024"  # PDF/UA-2's revision (ISO 14289-2:2024)
        if language:
            meta["dc:language"] = [language]
    changes.append(f"title and PDF/UA-{pdfua_part} identifier written to XMP"
                   + (" (the file's metadata was in an early form of XMP that checkers don't read: written afresh "
                      "from the document information)" if rewritten else ""))

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
    named = name_embedded_files(pdf)
    if named:
        changes.append(f"{named} embedded file(s) given the name entries they lacked")
    return changes


def name_embedded_files(pdf: pikepdf.Pdf) -> int:
    """An embedded file's specification needs its name twice, as /F and as /UF (PDF/UA 7.11). Producers often
    write one (Distiller attaches its settings as "folder.joboptions" with /F alone): the other is copied from it.
    With neither, the file gets a plain name. Returns how many specifications were completed."""
    done = 0
    for obj in pdf.objects:
        if not isinstance(obj, Dictionary) or not isinstance(obj.get("/EF"), Dictionary):
            continue
        f, uf = obj.get("/F"), obj.get("/UF")
        have_f, have_uf = f is not None and str(f).strip() != "", uf is not None and str(uf).strip() != ""
        if have_f and have_uf:
            continue
        name = str(uf if have_uf else f) if (have_f or have_uf) else "attachment"
        obj.F = String(name)
        obj.UF = String(name)
        done += 1
    return done



_OLD_ABOUT = re.compile(rb"<rdf:Description[^>]*\sabout\s*=")


def _drop_old_style_metadata(pdf: pikepdf.Pdf) -> bool:
    """Remove a metadata stream written in the form XMP had before it was a standard (Acrobat 5 and earlier): no
    x:xmpmeta wrapper, "about" without its rdf: prefix, the title as plain text. A title and a PDF/UA identifier
    added to such a packet aren't found by validators, so the file failed for having neither. Also one that
    can't be parsed at all. True when it was removed (a new one is then written)."""
    stream = pdf.Root.get("/Metadata")
    if stream is None:
        return False
    try:
        raw = stream.read_bytes()
    except Exception:  # noqa: BLE001
        raw = b""
    old = not raw.strip() or b"<x:xmpmeta" not in raw or bool(_OLD_ABOUT.search(raw))
    if old:
        del pdf.Root["/Metadata"]
    return old


def document_info(pdf: pikepdf.Pdf) -> dict:
    """The title and language a reader announces: the XMP title (what PDF/UA requires) or the document
    information's, and the catalog's /Lang. For the tag manager's Document panel (#363)."""
    title = None
    try:
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            title = meta.get("dc:title")
    except Exception:  # noqa: BLE001 - unreadable XMP: the document information still says
        title = None
    if not title:
        title = str(pdf.docinfo.get("/Title") or "") or None
    lang = str(pdf.Root.get("/Lang") or "").strip() or None
    return {"title": title, "lang": lang}


def set_title(pdf: pikepdf.Pdf, title: str) -> None:
    """A person's title from the tag manager: the document information, the XMP title, and shown in the title bar."""
    pdf.docinfo[Name.Title] = String(title)
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        meta["dc:title"] = title
    vp = pdf.Root.get("/ViewerPreferences")
    if not isinstance(vp, Dictionary):
        pdf.Root.ViewerPreferences = vp = Dictionary()
    vp.DisplayDocTitle = True


def set_language(pdf: pikepdf.Pdf, language: str) -> None:
    """A person's document language from the tag manager: the catalog's /Lang and the XMP language."""
    pdf.Root.Lang = String(language)
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        meta["dc:language"] = [language]
