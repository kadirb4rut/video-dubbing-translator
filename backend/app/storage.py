from __future__ import annotations

import mimetypes
import secrets
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import settings


class ObjectStore(Protocol):
    def put(self, object_key: str, source: BinaryIO, *, content_type: str) -> int: ...
    def path(self, object_key: str) -> Path: ...
    def download(self, object_key: str, destination: Path) -> None: ...
    def delete(self, object_key: str) -> None: ...


class LocalObjectStore:
    def __init__(self, root: Path | None = None):
        self.root = root or settings.local_storage_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, object_key: str) -> Path:
        candidate = (self.root / object_key).resolve()
        if self.root.resolve() not in candidate.parents:
            raise ValueError("Invalid object key")
        return candidate

    def put(self, object_key: str, source: BinaryIO, *, content_type: str) -> int:
        path = self.path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with path.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                target.write(chunk)
        return size

    def delete(self, object_key: str) -> None:
        path = self.path(object_key)
        if path.exists():
            path.unlink()

    def download(self, object_key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.path(object_key).read_bytes())


class S3ObjectStore:
    def __init__(self):
        import boto3

        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required for S3 storage")
        self.bucket = settings.s3_bucket
        self.client = boto3.client("s3", region_name=settings.s3_region)

    def path(self, object_key: str) -> Path:
        raise RuntimeError("S3 objects do not have local paths")

    def put(self, object_key: str, source: BinaryIO, *, content_type: str) -> int:
        import tempfile

        with tempfile.NamedTemporaryFile() as temp:
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                temp.write(chunk)
            temp.flush()
            self.client.upload_file(temp.name, self.bucket, object_key, ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256"})
            return size

    def delete(self, object_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)

    def download(self, object_key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, object_key, str(destination))

    def presigned_get(self, object_key: str, expires: int = 900) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": object_key}, ExpiresIn=expires)


def object_store() -> ObjectStore:
    return S3ObjectStore() if settings.storage_backend == "s3" else LocalObjectStore()


def object_key(user_id: str, category: str, filename: str) -> str:
    suffix = Path(filename).suffix.lower()[:10]
    return f"users/{user_id}/{category}/{secrets.token_urlsafe(18)}{suffix}"
