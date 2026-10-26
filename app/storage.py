"""Job working-directory layout on the shared volume.

/data/jobs/{job_id}/
    job.json        submission metadata (flags, provider, filename)
    input.pdf       uploaded file
    output/         files exposed for download
    work/           intermediates (removed unless debug)
    result.json     written on success
    error.json      written on failure
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from app.config import get_settings

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_stem(filename: str) -> str:
    stem = Path(filename or "document.pdf").stem
    stem = _SAFE_NAME.sub("_", stem).strip("._") or "document"
    return stem[:120]


class JobStore:
    def __init__(self, job_id: str, root: Path | None = None):
        if not re.fullmatch(r"[0-9a-f-]{36}", job_id):
            raise ValueError("invalid job id")
        self.job_id = job_id
        self.dir = (root or get_settings().jobs_dir) / job_id

    # --- paths -----------------------------------------------------------
    @property
    def input_pdf(self) -> Path:
        return self.dir / "input.pdf"

    @property
    def output_dir(self) -> Path:
        return self.dir / "output"

    @property
    def work_dir(self) -> Path:
        return self.dir / "work"

    def create(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return (self.dir / "job.json").exists()

    # --- json state ------------------------------------------------------
    def _write_json(self, name: str, data: Any) -> None:
        tmp = self.dir / f".{name}.tmp"
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.dir / name)

    def _read_json(self, name: str) -> Any | None:
        p = self.dir / name
        return json.loads(p.read_text()) if p.exists() else None

    def write_meta(self, meta: dict) -> None:
        self._write_json("job.json", meta)

    def read_meta(self) -> dict | None:
        return self._read_json("job.json")

    def write_result(self, result: dict) -> None:
        """result.json, with the version of the engine that made it (Run details)."""
        version = get_settings().engine_version
        if version:
            result.setdefault("metrics", {})["engine_version"] = version
        self._write_json("result.json", result)

    def read_result(self) -> dict | None:
        return self._read_json("result.json")

    def write_partial(self, partial: dict) -> None:
        """What a job can already give while it's still working (#428): an edit's file and tag map, before its
        checks."""
        self._write_json("partial.json", partial)

    def read_partial(self) -> dict | None:
        return self._read_json("partial.json")

    def write_error(self, error: dict) -> None:
        self._write_json("error.json", error)

    def read_error(self) -> dict | None:
        return self._read_json("error.json")

    def count_attempt(self) -> int:
        """Record that a worker is starting this job; returns which start this is (1 for the first)."""
        n = int((self._read_json("attempts.json") or {}).get("starts", 0)) + 1
        self._write_json("attempts.json", {"starts": n, "at": time.time()})
        return n

    def read_attempts(self) -> dict | None:
        """{"starts": how many times a worker started this job, "at": when the last start was (epoch seconds)}."""
        return self._read_json("attempts.json")

    def beat(self) -> None:
        """The worker running this job is alive (written every HEARTBEAT_S while it runs)."""
        self._write_json("heartbeat.json", {"at": time.time()})

    def last_beat(self) -> float | None:
        return (self._read_json("heartbeat.json") or {}).get("at")

    # --- outputs ---------------------------------------------------------
    def output_file(self, name: str) -> Path | None:
        """Resolve a download name, refusing anything outside output/."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        p = (self.output_dir / name).resolve()
        if p.parent != self.output_dir.resolve() or not p.is_file():
            return None
        return p

    def render_file_ref(self, name: str | None, as_base64: bool) -> str | None:
        if not name:
            return None
        if as_base64:
            p = self.output_file(name)
            return base64.b64encode(p.read_bytes()).decode() if p else None
        base = get_settings().public_base_url.rstrip("/")
        return f"{base}/api/v1/jobs/{self.job_id}/files/{name}"

    def cleanup_work(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)
        self.input_pdf.unlink(missing_ok=True)


def running_job_ids(fresh_s: float, root: Path | None = None) -> set[str]:
    """The jobs a worker is running now: not finished or stopped, with a heartbeat in the last fresh_s seconds."""
    root = root or get_settings().jobs_dir
    now, out = time.time(), set()
    try:
        dirs = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return out
    for d in dirs:
        try:
            store = JobStore(d.name, root)  # (a folder that isn't a job's is refused here)
            beat = store.last_beat()
            if beat is not None and now - beat < fresh_s and store.read_result() is None and store.read_error() is None:
                out.add(d.name)
        except (OSError, ValueError, AttributeError):
            continue  # not a job's folder, or one being written or removed: not counted this time
    return out


def purge_expired_jobs(max_age_hours: int | None = None) -> int:
    settings = get_settings()
    max_age = (max_age_hours or settings.job_ttl_hours) * 3600
    root = settings.jobs_dir
    if not root.exists():
        return 0
    removed = 0
    now = time.time()
    for d in root.iterdir():
        if d.is_dir() and now - d.stat().st_mtime > max_age:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed
