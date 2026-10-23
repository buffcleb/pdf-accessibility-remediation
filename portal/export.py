"""Many documents' files as one zip (#481), made as it's sent.

Everything the zip holds is decided first, from the database (which documents, which of their files, the names in
the zip), so that streaming reads only the object store: each file is read a chunk at a time and written into the
response, with nothing kept on disk or in memory beyond one chunk. PDFs and the other compressed files are stored,
not deflated (as fast, and no bigger); text is deflated. Zip64 takes over by itself past 4 GB.

What goes in for each document is what its page lets that person download, and no more: the job's files but the
tag manager's own (tag maps, saved versions' working files), the engine's JSON and Markdown reports for super admins
only, and the newest saved (reviewed) version beside the original output. Documents whose files are gone are left
out and listed, with why, in README.txt at the end of the zip.
"""
from __future__ import annotations

import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.models import DocumentVersion, Job, JobFile
from portal.storage import Storage

# The engine's own reports, for the people who run the service: the JSON report carries the AI's cost in dollars and its
# tokens (re-billing is by pages, so only super admins see dollars), and neither is a document anyone else needs. The
# web app shows the Markdown report in the Report tab through /jobs/{id}/report, and the PDF report is the one for
# people; veraPDF's XML stays readable, since the Accessibility check tab draws it.
STAFF_ONLY_FILES = {"report_json", "report_md"}
# The tag manager's working files, never offered as downloads
HIDDEN_PREFIXES = ("tagmap_", "reviewed_", "corrected_")
# Already compressed: stored as they are
STORED_SUFFIXES = (".pdf", ".zip", ".xlsx", ".png", ".jpg", ".jpeg")

_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')


@dataclass
class Entry:
    """One file in the zip: its name there, and where it's read from."""
    name: str
    key: str
    size: int
    file: JobFile


@dataclass
class Document:
    job: Job
    folder: str
    entries: list[Entry] = field(default_factory=list)


@dataclass
class Plan:
    documents: list[Document]
    left_out: list[tuple[Job, str]]  # (document, why)

    @property
    def files(self) -> int:
        return sum(len(d.entries) for d in self.documents)

    @property
    def size(self) -> int:
        return sum(e.size for d in self.documents for e in d.entries)


def zip_name(text: str, limit: int = 100) -> str:
    """A name for a folder or file in the zip: as the person wrote it, without what would make it a path or that
    some systems refuse in a file name."""
    clean = _UNSAFE.sub("_", text).strip().strip(".") or "document"
    if len(clean) > limit:
        stem, dot, ext = clean.rpartition(".")
        clean = (stem[:limit - len(ext) - 1] + dot + ext) if dot and len(ext) <= 8 else clean[:limit]
    return clean


def _unique(name: str, taken: set[str]) -> str:
    """`name`, or "name (2)" and so on (before the extension), so no two entries in one folder collide."""
    out, n = name, 1
    stem, dot, ext = name.rpartition(".") if "." in name else (name, "", "")
    while out.lower() in taken:
        n += 1
        out = f"{stem} ({n}){dot}{ext}"
    taken.add(out.lower())
    return out


def downloadable(job: Job, is_super_admin: bool) -> list[JobFile]:
    """The job's files its page offers this viewer."""
    return [f for f in job.files if not f.kind.startswith(HIDDEN_PREFIXES)
            and (is_super_admin or f.kind not in STAFF_ONLY_FILES)]


def newest_version(db: Session, job: Job) -> tuple[DocumentVersion, JobFile] | None:
    """The newest saved (reviewed or corrected) version that's ready, with its PDF."""
    v = db.scalar(select(DocumentVersion).where(
        DocumentVersion.job_id == job.id, DocumentVersion.kind.in_(("reviewed", "corrected")),
        DocumentVersion.superseded_at.is_(None), DocumentVersion.status == "ready",
        DocumentVersion.pdf_file_id.is_not(None)).order_by(DocumentVersion.number.desc()).limit(1))
    if v is None:
        return None
    f = next((f for f in job.files if f.id == v.pdf_file_id), None)
    return (v, f) if f is not None else None


def _gone(job: Job, retention_days: int) -> str:
    when = job.files_deleted_at.strftime("%Y-%m-%d") if job.files_deleted_at else "?"
    if job.removal_requested_at is not None:
        return f"Marked done: its files were removed on {when}."
    return f"Its files were removed on {when} (files are kept {retention_days} days)."


def plan(db: Session, jobs: list[Job], *, is_super_admin: bool, time_zone: str, retention_days: int) -> Plan:
    """What the zip will hold: one folder per document, named from its date and file name ("2026-10-21 syllabus"),
    so two uploads of the same name don't collide."""
    try:
        tz = ZoneInfo(time_zone)
    except Exception:  # noqa: BLE001 - a bad setting shouldn't stop an export
        tz = ZoneInfo("UTC")
    documents, left_out, folders = [], [], set()
    for job in jobs:
        if job.files_deleted_at is not None:
            left_out.append((job, _gone(job, retention_days)))
            continue
        files = downloadable(job, is_super_admin)
        newest = newest_version(db, job)
        if not files and newest is None:
            left_out.append((job, "No files yet."))
            continue
        stem = job.filename[:-4] if job.filename.lower().endswith(".pdf") else job.filename
        folder = _unique(zip_name(f"{job.created_at.astimezone(tz):%Y-%m-%d} {stem}", 80), folders)
        doc, names = Document(job, folder), set()
        for f in sorted(files, key=lambda f: (f.kind != "input", f.kind != "primary", f.kind, f.name)):
            doc.entries.append(Entry(f"{folder}/{_unique(zip_name(f.name), names)}", f.s3_key, f.size_bytes, f))
        if newest is not None:
            f = newest[1]  # named as the Versions tab downloads it ("syllabus_human_reviewed_2.pdf")
            doc.entries.append(Entry(f"{folder}/{_unique(zip_name(f.name), names)}", f.s3_key, f.size_bytes, f))
        documents.append(doc)
    return Plan(documents, left_out)


def readme(plan: Plan, missing: list[Entry], *, made: datetime, by: str) -> str:
    lines = [f"Exported {made.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC by {by}.", "",
             f"{len(plan.documents)} documents, {plan.files - len(missing)} files: one folder per document, named from "
             "the date it was submitted and its file name.", ""]
    if plan.left_out:
        lines += ["Left out:"] + [f"- {job.filename} (submitted {job.created_at:%Y-%m-%d}): {why}"
                                  for job, why in plan.left_out] + [""]
    if missing:
        lines += ["Couldn't be read from storage, so not included:"] + [f"- {e.name}" for e in missing] + [""]
    return "\r\n".join(lines)


class _Sink:
    """What the zip is written to: its bytes are handed to the response as they come, never kept. It can say where
    it is but can't seek, so zipfile writes each file's sizes after its data."""

    def __init__(self) -> None:
        self.parts: list[bytes] = []
        self.pos = 0

    def write(self, data) -> int:
        self.parts.append(bytes(data))
        self.pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self.pos

    def flush(self) -> None:
        pass

    def take(self) -> bytes:
        out = b"".join(self.parts)
        self.parts.clear()
        return out


def stream(plan: Plan, storage: Storage, *, by: str, now: datetime | None = None) -> Iterator[bytes]:
    """The zip, a chunk at a time. Reads only the object store. A file that can't be opened is skipped and listed in
    README.txt; one that fails part-way ends the download (its browser shows it failed)."""
    made = now or datetime.now(timezone.utc)
    stamp = made.astimezone(timezone.utc).timetuple()[:6]
    sink = _Sink()
    missing: list[Entry] = []
    with zipfile.ZipFile(sink, "w", allowZip64=True) as zf:
        for doc in plan.documents:
            for e in doc.entries:
                chunks = iter(storage.get_stream(e.key))
                try:
                    first = next(chunks, b"")
                except Exception:  # noqa: BLE001 - gone from the store: say so in the README instead
                    missing.append(e)
                    continue
                info = zipfile.ZipInfo(e.name, date_time=stamp)
                info.compress_type = zipfile.ZIP_STORED if e.name.lower().endswith(STORED_SUFFIXES) \
                    else zipfile.ZIP_DEFLATED
                info.file_size = e.size  # zipfile decides on Zip64 for this file from it
                with zf.open(info, "w") as out:
                    out.write(first)
                    yield sink.take()
                    for chunk in chunks:
                        out.write(chunk)
                        yield sink.take()
                yield sink.take()
        info = zipfile.ZipInfo("README.txt", date_time=stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, readme(plan, missing, made=made, by=by))
    yield sink.take()
