"""Backup helpers used by scripts/backup.sh and scripts/restore.sh (through `python -m portal.cli`).

Files are read and written through the portal's own storage settings (PORTAL_S3_*), so the same commands
work with SeaweedFS locally and with AWS S3 or another S3 service elsewhere. A file backup is a tar
stream whose member names are the object keys; content types are restored from the file extension.

Off-machine copies go to any S3 bucket with their own credentials (BACKUP_S3_ACCESS_KEY,
BACKUP_S3_SECRET_KEY, BACKUP_S3_ENDPOINT_URL, BACKUP_S3_REGION), falling back to the standard AWS
environment variables or the machine's role.
"""
from __future__ import annotations

import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from portal.storage import get_storage

CONTENT_TYPES = {".pdf": "application/pdf", ".xml": "application/xml", ".md": "text/markdown; charset=utf-8",
                 ".json": "application/json",
                 ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _safe_key(name: str) -> bool:
    p = PurePosixPath(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts


def export_files(out: BinaryIO, prefix: str = "") -> tuple[int, int]:
    """Write every object under prefix to a tar stream. Returns (objects, bytes)."""
    storage = get_storage()
    count = total = 0
    with tarfile.open(fileobj=out, mode="w|") as tar:
        for key, _size in storage.list_keys(prefix):
            with tempfile.TemporaryFile() as tmp:
                storage.get_to_file(key, tmp)
                size = tmp.tell()
                tmp.seek(0)
                info = tarfile.TarInfo(key)
                info.size, info.mtime, info.mode = size, int(time.time()), 0o600
                tar.addfile(info, tmp)
            count += 1
            total += size
            if count % 500 == 0:
                _log(f"  {count} files, {total / 1e6:.1f} MB")
    return count, total


def import_files(src: BinaryIO, overwrite: bool = False, dry_run: bool = False,
                 prefix: str = "") -> dict[str, int]:
    """Put every file from a tar stream back into storage. Existing objects are skipped unless overwrite."""
    storage = get_storage()
    stats = {"restored": 0, "skipped_existing": 0, "skipped_other": 0, "bytes": 0}
    with tarfile.open(fileobj=src, mode="r|") as tar:
        for member in tar:
            if not member.isfile() or not _safe_key(member.name) or not member.name.startswith(prefix):
                stats["skipped_other"] += 1
                continue
            if not overwrite and storage.exists(member.name):
                stats["skipped_existing"] += 1
                continue
            if dry_run:
                stats["restored"] += 1
                stats["bytes"] += member.size
                continue
            fh = tar.extractfile(member)
            with tempfile.TemporaryFile() as tmp:
                while chunk := fh.read(1 << 20):
                    tmp.write(chunk)
                ctype = CONTENT_TYPES.get(Path(member.name).suffix.lower(), "application/octet-stream")
                storage.put_file(member.name, tmp, ctype)
            stats["restored"] += 1
            stats["bytes"] += member.size
    return stats


# --- off-machine copies --------------------------------------------------------------------------

def _split_url(url: str) -> tuple[str, str]:
    if not url.startswith("s3://"):
        raise ValueError("expected s3://bucket/prefix")
    bucket, _, prefix = url[5:].partition("/")
    return bucket, prefix.strip("/")


def _backup_client():
    import boto3

    e = os.environ
    return boto3.client(
        "s3",
        endpoint_url=e.get("BACKUP_S3_ENDPOINT_URL") or None,
        region_name=e.get("BACKUP_S3_REGION") or e.get("AWS_DEFAULT_REGION") or "us-east-1",
        aws_access_key_id=e.get("BACKUP_S3_ACCESS_KEY") or None,
        aws_secret_access_key=e.get("BACKUP_S3_SECRET_KEY") or None,
    )


def upload_dir(directory: Path, url: str) -> int:
    """Copy a backup folder to s3://bucket/prefix/<folder name>/. Returns files uploaded."""
    bucket, prefix = _split_url(url)
    client = _backup_client()
    n = 0
    for f in sorted(directory.rglob("*")):
        if f.is_file():
            key = "/".join(x for x in (prefix, directory.name, f.relative_to(directory).as_posix()) if x)
            client.upload_file(str(f), bucket, key)
            _log(f"  uploaded s3://{bucket}/{key}")
            n += 1
    return n


def download_dir(url: str, directory: Path) -> int:
    """Copy s3://bucket/prefix/<backup>/ into directory (created). Returns files downloaded."""
    bucket, prefix = _split_url(url)
    client = _backup_client()
    directory.mkdir(parents=True, exist_ok=True)
    n = 0
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix + "/"):
        for o in page.get("Contents", []):
            rel = o["Key"][len(prefix) + 1:]
            if not _safe_key(rel):
                continue
            dst = directory / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, o["Key"], str(dst))
            n += 1
    if n == 0:
        raise FileNotFoundError(f"nothing found at {url}")
    return n
