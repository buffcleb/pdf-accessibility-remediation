"""Signed and permission-protected PDFs (#339).

Remediating rewrites the file, which used to break a signature and drop a PDF's restrictions without a word.

- A signed or certified PDF: the portal holds it for the person's confirmation before it's sent; the report says the
  remediated copy carries no valid signature.
- A PDF that opens without a password but restricts what can be done with it (an owner password): every output gets
  the same restrictions back, under a new owner password nobody knows, with access for assistive technology on, as
  PDF/UA requires (ISO 14289-1 7.16). It's put back after veraPDF has checked the file, so validation never sees it.
- A check only ("audit") produces no file: the report says what it found, including whether assistive technology may
  read the document.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

import pikepdf

PERMISSIONS = ("accessibility", "extract", "modify_annotation", "modify_assembly", "modify_form", "modify_other",
               "print_lowres", "print_highres")
_WORDS = {"extract": "copying text", "modify_annotation": "comments", "modify_assembly": "assembling pages",
          "modify_form": "filling in forms", "modify_other": "editing", "print_lowres": "printing",
          "print_highres": "high-quality printing"}


def _signed(pdf: pikepdf.Pdf) -> bool:
    root = pdf.Root
    perms = root.get("/Perms")
    if isinstance(perms, pikepdf.Dictionary) and any(k in perms for k in ("/DocMDP", "/UR3")):
        return True  # certified, or with usage rights
    form = root.get("/AcroForm")
    if not isinstance(form, pikepdf.Dictionary):
        return False
    try:
        if int(form.get("/SigFlags", 0)) & 1:
            return True
    except (TypeError, ValueError):
        pass
    seen: set[tuple[int, int]] = set()
    stack = list(form.get("/Fields") or [])
    while stack:  # iterative: a deep or looping field tree can't exhaust the stack
        f = stack.pop()
        if not isinstance(f, pikepdf.Dictionary):
            continue
        if f.is_indirect:
            if f.objgen in seen:
                continue
            seen.add(f.objgen)
        if f.get("/FT") == pikepdf.Name.Sig and f.get("/V") is not None:
            return True
        stack.extend(f.get("/Kids") or [])
    return False


def xfa_kind(pdf: pikepdf.Pdf) -> str | None:
    """"dynamic" (drawn by the XFA engine, no standard form to tag), "static" (an XFA part beside a standard form), or
    None (#360)."""
    form = pdf.Root.get("/AcroForm")
    if not isinstance(form, pikepdf.Dictionary) or "/XFA" not in form:
        return None
    if pdf.Root.get("/NeedsRendering") is True or not list(form.get("/Fields") or []):
        return "dynamic"
    return "static"


def inspect(path: Path) -> dict:
    """{"signed": bool, "restricted": {permission: allowed} or None, "xfa": xfa_kind()}. Restricted only when it opens
    without a password and something is actually refused. A file pikepdf can't read gives {} (the run then fails its
    own way)."""
    try:
        return _inspect(path)
    except (pikepdf.PdfError, OSError, ValueError):
        return {}


def _inspect(path: Path) -> dict:
    with pikepdf.open(path) as pdf:
        restricted = None
        if pdf.is_encrypted:
            allow = {k: bool(getattr(pdf.allow, k)) for k in PERMISSIONS}
            if not all(allow.values()):
                restricted = allow
        return {"signed": _signed(pdf), "restricted": restricted, "xfa": xfa_kind(pdf)}


def notes(info: dict, report_only: bool) -> list[str]:
    """What the report says about it."""
    out: list[str] = []
    if info.get("signed"):
        out.append("This PDF is signed (or certified). " + (
            "Checking it changed nothing." if report_only else
            "The remediated copy doesn't carry a valid signature: sign it again if one is needed."))
    if info.get("xfa") == "static":
        out.append("This PDF also had an XFA form (Adobe LiveCycle). " + (
            "It should be re-saved as a standard PDF form: XFA can't be made accessible." if report_only else
            "Its XFA part was removed; the standard form the PDF also carries is what was tagged."))
    elif info.get("xfa") == "dynamic":
        out.append("This PDF is a dynamic XFA form (Adobe LiveCycle), which can't be made accessible: re-save it as a "
                   "standard PDF form.")
    r = info.get("restricted")
    if r:
        refused = [w for k, w in _WORDS.items() if not r.get(k)]
        what = ", ".join(refused) if refused else "some uses"
        if report_only:
            out.append(f"This PDF restricts {what}" + (
                "." if r.get("accessibility") else
                ", and doesn't let assistive technology read it, which PDF/UA requires (screen readers may refuse it)."))
        else:
            out.append(f"This PDF restricts {what}. The remediated copy keeps the same restrictions, with access for "
                       "assistive technology allowed, as PDF/UA requires.")
    return out


def reapply(path: Path, restricted: dict) -> None:
    """Put the restrictions back on an output, with assistive technology allowed."""
    allow = pikepdf.Permissions(**{**{k: bool(restricted.get(k, True)) for k in PERMISSIONS}, "accessibility": True})
    tmp = path.with_name(path.name + ".protect.tmp")
    with pikepdf.open(path) as pdf:
        pdf.save(tmp, encryption=pikepdf.Encryption(owner=secrets.token_urlsafe(32), user="", allow=allow))
    os.replace(tmp, path)


def finish(output_dir: Path, result: dict, info: dict) -> None:
    """After a run: the input's restrictions back on every PDF it delivers (the files the result names)."""
    if not info.get("restricted"):
        return
    for name in (result.get("files") or {}).values():
        if isinstance(name, str) and name.lower().endswith(".pdf") and (output_dir / name).is_file():
            reapply(output_dir / name, info["restricted"])
