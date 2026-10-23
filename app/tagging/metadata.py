"""Document-level PDF/UA requirements: MarkInfo, Lang, title, XMP identifier, tab order, OC configs, and the
names of embedded files."""
from __future__ import annotations

import datetime as dt
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
    fixed = fix_dates(pdf)
    if fixed:
        changes.append(f"{fixed} date(s) that weren't valid PDF dates rewritten or removed")
    rewritten = _drop_old_style_metadata(pdf)
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        if rewritten:
            # Author, dates and the rest come back from the document information
            try:
                meta.load_from_docinfo(pdf.docinfo)
            except Exception:  # noqa: BLE001 - an odd date or encoding there mustn't cost the title and identifier
                pass
        meta["dc:title"] = title
        if meta.get("xmp:ModifyDate"):  # in step with the document information's, which fix_dates just set
            meta["xmp:ModifyDate"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
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



# A PDF date (ISO 32000-2 7.9.4): D:YYYY, then optionally MM DD HH mm SS each in turn, then Z or +/-HH'mm
_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:[Zz]|[+-]\d{2}(?:'?\d{2}'?)?)?")


def _date_text(value) -> str | None:
    """A date string's text, read the way producers get it wrong: UTF-16 with or without its byte-order mark (seen
    as every other byte zero), padding, a missing "D:". None when it isn't a date at all."""
    try:
        raw = bytes(value)
    except Exception:  # noqa: BLE001
        return None
    if raw[:2] in (b"\xfe\xff", b"\xff\xfe"):
        text = raw[2:].decode("utf-16-be" if raw[:1] == b"\xfe" else "utf-16-le", errors="ignore")
    else:
        text = raw.replace(b"\x00", b"").decode("latin-1")
    text = text.strip().replace(" ", "")
    if text[:1].isdigit():
        text = "D:" + text
    m = _DATE.fullmatch(text)
    if not m:
        return None
    month, day, hour, minute, second = (int(g) if g else None for g in m.groups()[1:])
    ok = (month is None or 1 <= month <= 12) and (day is None or 1 <= day <= 31) and (hour is None or hour <= 23) \
        and (minute is None or minute <= 59) and (second is None or second <= 59)
    return text if ok else None


def _fix_date(holder: Dictionary, key: str) -> int:
    """One date entry as plain text, or removed when it can't be read. 1 when it changed."""
    value = holder.get(key)
    if not isinstance(value, String):
        return 0
    text = _date_text(value)
    if text is None:
        del holder[key]
        return 1
    if bytes(value) != text.encode("ascii"):
        holder[key] = String(text)
        return 1
    return 0


def fix_dates(pdf: pikepdf.Pdf, now: dt.datetime | None = None) -> int:
    """Dates a checker can read (#483). PAC fails a date stored as UTF-16 (one producer wrote the document's
    modification date that way, cut short), and stopped altogether on it; veraPDF doesn't check dates. The document
    information's ModDate becomes now (the file is being changed); every other date (its CreationDate, an
    annotation's /M, an embedded file's dates) is written back as plain text when it can be read, and removed when it
    can't (none of them is required). Returns how many were changed or removed, not counting the new ModDate unless
    the old one wasn't a plain, valid date."""
    now = now or dt.datetime.now(dt.timezone.utc)
    changed = 0
    info = pdf.trailer.get("/Info")
    if isinstance(info, Dictionary):
        if "/ModDate" in info:  # counted when it wasn't a plain, valid date
            text = _date_text(info.ModDate)
            changed += text is None or bytes(info.ModDate) != text.encode("ascii")
        info.ModDate = String(now.strftime("D:%Y%m%d%H%M%SZ"))
        changed += _fix_date(info, "/CreationDate")
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Stream):  # an embedded file: its dates are in /Params
            params = obj.get("/Params")
            if isinstance(params, Dictionary):
                changed += _fix_date(params, "/CreationDate") + _fix_date(params, "/ModDate")
        elif isinstance(obj, Dictionary) and (obj.get("/Type") == Name.Annot or ("/Subtype" in obj and "/Rect" in obj)):
            changed += _fix_date(obj, "/M") + _fix_date(obj, "/CreationDate")
    return changed


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
