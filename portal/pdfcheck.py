"""Checks on uploaded files before they are accepted and charged."""
from __future__ import annotations

import hashlib
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
        pages = count_pages(out)
        if pages == 0:
            raise ApiError(422, "empty_pdf", "This PDF has no pages.")
        if pages > max_pages:
            raise ApiError(413, "too_many_pages", f"Documents can have at most {max_pages} pages; this one has {pages}.")
        out.seek(0)
        return CheckedPdf(out, size, digest.hexdigest(), pages)
    except Exception:
        out.close()
        raise


def count_pages(fileobj) -> int:
    import pikepdf

    try:
        with pikepdf.open(fileobj) as pdf:
            return len(pdf.pages)
    except pikepdf.PasswordError:
        raise ApiError(422, "encrypted_pdf",
                       "This PDF is password-protected. Remove the password and upload it again.") from None
    except pikepdf.PdfError:
        raise ApiError(422, "unreadable_pdf", "This PDF is damaged and can't be read.") from None
