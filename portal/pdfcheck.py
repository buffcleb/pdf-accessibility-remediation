"""Checks on uploaded files before they are accepted and charged."""
from __future__ import annotations

import hashlib
import sys
import tempfile
from dataclasses import dataclass

from fastapi import UploadFile

from portal.errors import ApiError

CHUNK = 1024 * 1024


@dataclass
class CheckedPdf:
    file: tempfile.SpooledTemporaryFile
    size: int
    sha256: str
    pages: int
    signed: bool = False  # a signature or certification: remediating breaks it, so the upload is held (#339)
    xfa: str | None = None  # "static" or "dynamic" (#360); a dynamic one is refused


def receive_pdf(upload: UploadFile, max_bytes: int, max_pages: int) -> CheckedPdf:
    """Copy the upload to a temporary file, enforcing the size limit while reading, then check it
    is a readable, unencrypted PDF and count its pages."""
    out = tempfile.SpooledTemporaryFile(max_size=8 * CHUNK)
    digest = hashlib.sha256()
    size = 0
    try:
        upload.file.seek(0)
        while chunk := upload.file.read(CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise ApiError(413, "file_too_large", f"Files can be at most {max_bytes // CHUNK} MB.")
            digest.update(chunk)
            out.write(chunk)
        out.seek(0)
        if size == 0 or not out.read(1024).lstrip(b"\x00\t\r\n ").startswith(b"%PDF-"):
            raise ApiError(415, "not_pdf", "This file isn't a PDF.")
        out.seek(0)
        pages, signed, xfa = _read(out)
        if xfa == "dynamic":
            raise ApiError(422, "xfa_dynamic", "This is a dynamic XFA form (made with Adobe LiveCycle), which can't be "
                                               "made accessible. Open it in Acrobat, save it as a standard PDF form "
                                               "(not XFA), and upload that.")
        if pages == 0:
            raise ApiError(422, "empty_pdf", "This PDF has no pages.")
        if pages > max_pages:
            raise ApiError(413, "too_many_pages", f"Documents can have at most {max_pages} pages; this one has {pages}.")
        out.seek(0)
        return CheckedPdf(out, size, digest.hexdigest(), pages, signed, xfa)
    except Exception:
        out.close()
        raise


def _signed(pdf) -> bool:
    """Signed or certified: a signature field with a value, the form's "signatures exist" flag, or /Perms (DocMDP,
    usage rights). The engine's app/pipeline/protection.py looks the same way."""
    import pikepdf

    root = pdf.Root
    perms = root.get("/Perms")
    if isinstance(perms, pikepdf.Dictionary) and any(k in perms for k in ("/DocMDP", "/UR3")):
        return True
    form = root.get("/AcroForm")
    if not isinstance(form, pikepdf.Dictionary):
        return False
    try:
        if int(form.get("/SigFlags", 0)) & 1:
            return True
    except (TypeError, ValueError):
        pass
    seen, stack = set(), list(form.get("/Fields") or [])
    while stack and len(seen) < 100_000:
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


def _xfa(pdf) -> str | None:
    """"dynamic", "static" or None, as the engine's app/pipeline/protection.py tells them (#360)."""
    import pikepdf

    form = pdf.Root.get("/AcroForm")
    if not isinstance(form, pikepdf.Dictionary) or "/XFA" not in form:
        return None
    if pdf.Root.get("/NeedsRendering") is True or not list(form.get("/Fields") or []):
        return "dynamic"
    return "static"


def _inspect(path) -> dict:
    """In the child: its page count and whether it's signed, or why it can't be read."""
    import pikepdf

    try:
        with pikepdf.open(path) as pdf:
            return {"pages": len(pdf.pages), "signed": _signed(pdf), "xfa": _xfa(pdf)}
    except pikepdf.PasswordError:
        return {"error": "encrypted_pdf"}
    except pikepdf.PdfError:
        return {"error": "unreadable_pdf"}


_ERRORS = {
    "encrypted_pdf": "This PDF is password-protected. Remove the password and upload it again.",
    "unreadable_pdf": "This PDF is damaged and can't be read.",
    "too_complex": "This PDF couldn't be checked: it is damaged, or too large or complex to open safely.",
}


def _read(fileobj) -> tuple[int, bool, str | None]:
    """Its page count, and whether it's signed. Opened in a child process with its own memory and CPU limits and a
    time limit (#356): a hostile file (a decompression bomb, a loop of objects) ends the child and fails this upload,
    not the API that serves everyone."""
    import json
    import subprocess
    import sys

    from portal.config import get_portal_settings

    s = get_portal_settings()
    if not s.pdfcheck_isolated:
        out = _inspect(fileobj)
        fileobj.seek(0)
        if "pages" in out:
            return int(out["pages"]), bool(out.get("signed")), out.get("xfa")
        raise ApiError(422, out["error"], _ERRORS[out["error"]])
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        fileobj.seek(0)
        while chunk := fileobj.read(CHUNK):
            tmp.write(chunk)
        tmp.flush()
        fileobj.seek(0)
        try:
            run = subprocess.run([sys.executable, "-m", "portal.pdfcheck", tmp.name, str(s.pdfcheck_memory_mb),
                                  str(s.pdfcheck_cpu_s)], capture_output=True, timeout=s.pdfcheck_timeout_s, check=False)
            out = json.loads(run.stdout.decode().strip().splitlines()[-1]) if run.returncode == 0 else {}
        except (subprocess.TimeoutExpired, ValueError, IndexError):
            out = {}
    if "pages" in out:
        return int(out["pages"]), bool(out.get("signed")), out.get("xfa")
    code = out.get("error") or "too_complex"
    raise ApiError(422, code, _ERRORS.get(code, _ERRORS["too_complex"]))


def _limit(memory_mb: int, cpu_s: int) -> None:
    """The child's own limits, set before it opens anything. Where the system doesn't allow one (macOS caps address
    space differently), the time limit still holds."""
    import resource

    for which, value in ((resource.RLIMIT_AS, memory_mb * 1024 * 1024), (resource.RLIMIT_CPU, cpu_s)):
        try:
            resource.setrlimit(which, (value, value))
        except (ValueError, OSError):
            pass


if __name__ == "__main__":  # the child: python -m portal.pdfcheck PATH MEMORY_MB CPU_S
    import json as _json

    _limit(int(sys.argv[2]), int(sys.argv[3]))
    print(_json.dumps(_inspect(sys.argv[1])))
