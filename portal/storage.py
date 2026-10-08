"""Object storage behind the S3 API (AWS S3 in production, SeaweedFS for demos).

Layout: groups/{group_id}/users/{user_id}/jobs/{job_id}/{input|output|reports}/{name}
Keys use IDs only, so no personal data appears in object names. The bucket stays private: users
download through the API, which checks permission and records access.
"""
from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Iterator
from functools import lru_cache
from typing import BinaryIO, Protocol

from portal.config import get_portal_settings

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str, default: str = "document.pdf") -> str:
    """A filename safe for an object key: no paths, no odd characters, bounded length."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = _SAFE.sub("_", base).strip("._") or default
    if len(base) > 120:
        stem, dot, ext = base.rpartition(".")
        base = (stem[:110] + dot + ext) if dot else base[:120]
    return base


def job_prefix(group_id: uuid.UUID | None, user_id: uuid.UUID, job_id: uuid.UUID) -> str:
    g = str(group_id) if group_id else "no-group"
    return f"groups/{g}/users/{user_id}/jobs/{job_id}/"


class Storage(Protocol):
    def put_file(self, key: str, fileobj: BinaryIO, content_type: str) -> int: ...
    def put_bytes(self, key: str, data: bytes, content_type: str) -> int: ...
    def get_stream(self, key: str) -> Iterator[bytes]: ...
    def get_to_file(self, key: str, fileobj: BinaryIO) -> None: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def ensure_bucket(self) -> None: ...
    def list_keys(self, prefix: str = "") -> Iterator[tuple[str, int]]: ...
    def exists(self, key: str) -> bool: ...


class S3Storage:
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        s = get_portal_settings()
        self.bucket = s.s3_bucket
        self.sse = s.s3_sse
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url or None,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key or None,
            aws_secret_access_key=s.s3_secret_key or None,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"} if s.s3_endpoint_url else {},
                          retries={"max_attempts": 4, "mode": "standard"}),
        )

    def _extra(self, content_type: str) -> dict:
        extra = {"ContentType": content_type}
        if self.sse:
            extra["ServerSideEncryption"] = self.sse
        return extra

    def put_file(self, key: str, fileobj: BinaryIO, content_type: str) -> int:
        fileobj.seek(0, 2)
        size = fileobj.tell()
        fileobj.seek(0)
        self.client.upload_fileobj(fileobj, self.bucket, key, ExtraArgs=self._extra(content_type))
        return size

    def put_bytes(self, key: str, data: bytes, content_type: str) -> int:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **self._extra(content_type))
        return len(data)

    def get_stream(self, key: str) -> Iterator[bytes]:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            yield from body.iter_chunks(chunk_size=256 * 1024)
        finally:
            body.close()

    def get_to_file(self, key: str, fileobj: BinaryIO) -> None:
        self.client.download_fileobj(self.bucket, key, fileobj)

    def delete_prefix(self, prefix: str) -> int:
        count = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs, "Quiet": True})
                count += len(objs)
        return count

    def ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)

    def list_keys(self, prefix: str = "") -> Iterator[tuple[str, int]]:
        """(key, size) of every object under prefix."""
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for o in page.get("Contents", []):
                yield o["Key"], int(o["Size"])

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False


class MemoryStorage:
    """In-process store for tests."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()

    def put_file(self, key: str, fileobj: BinaryIO, content_type: str) -> int:
        fileobj.seek(0)
        return self.put_bytes(key, fileobj.read(), content_type)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> int:
        with self._lock:
            self.objects[key] = (data, content_type)
        return len(data)

    def get_stream(self, key: str) -> Iterator[bytes]:
        data = self.objects[key][0]
        for i in range(0, len(data), 65536):
            yield data[i:i + 65536]

    def get_to_file(self, key: str, fileobj: BinaryIO) -> None:
        fileobj.write(self.objects[key][0])

    def delete_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self.objects if k.startswith(prefix)]
            for k in keys:
                del self.objects[k]
        return len(keys)

    def ensure_bucket(self) -> None:
        pass

    def list_keys(self, prefix: str = "") -> Iterator[tuple[str, int]]:
        for k in sorted(self.objects):
            if k.startswith(prefix):
                yield k, len(self.objects[k][0])

    def exists(self, key: str) -> bool:
        return key in self.objects


@lru_cache
def get_storage() -> Storage:
    return MemoryStorage() if get_portal_settings().storage_backend == "memory" else S3Storage()
