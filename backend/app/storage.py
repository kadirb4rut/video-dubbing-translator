from __future__ import annotations

import secrets
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import settings


class ObjectStore(Protocol):
    def put(self, object_key: str, source: BinaryIO, *, content_type: str, max_bytes: int | None = None) -> int: ...
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

    def put(self, object_key: str, source: BinaryIO, *, content_type: str, max_bytes: int | None = None) -> int:
        path = self.path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        try:
            with path.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise ValueError("File exceeds the configured upload limit")
                    target.write(chunk)
        except Exception:
            path.unlink(missing_ok=True)
            raise
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
        self.client = boto3.client("s3", region_name=settings.s3_region, endpoint_url=settings.s3_endpoint_url)
        self.presign_client = self.client if not settings.s3_presign_endpoint_url else boto3.client(
            "s3", region_name=settings.s3_region, endpoint_url=settings.s3_presign_endpoint_url
        )

    def path(self, object_key: str) -> Path:
        raise RuntimeError("S3 objects do not have local paths")

    def put(self, object_key: str, source: BinaryIO, *, content_type: str, max_bytes: int | None = None) -> int:
        import tempfile

        with tempfile.NamedTemporaryFile() as temp:
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if max_bytes is not None and size > max_bytes:
                    raise ValueError("File exceeds the configured upload limit")
                temp.write(chunk)
            temp.flush()
            self.client.upload_file(temp.name, self.bucket, object_key, ExtraArgs={"ContentType": content_type, "ServerSideEncryption": "AES256", "Tagging": f"lingowave-category={self._category(object_key)}"})
            return size

    @staticmethod
    def _category(object_key: str) -> str:
        parts = object_key.split("/")
        return parts[2] if object_key.startswith("users/") and len(parts) > 2 else "unknown"

    def delete(self, object_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)

    def download(self, object_key: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, object_key, str(destination))

    def presigned_get(self, object_key: str, expires: int = 900) -> str:
        client = getattr(self, "presign_client", self.client)
        return client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": object_key}, ExpiresIn=expires)

    def presigned_put(self, object_key: str, *, content_type: str, size_bytes: int | None = None, expires: int = 900) -> str:
        client = getattr(self, "presign_client", self.client)
        params = {"Bucket": self.bucket, "Key": object_key, "ContentType": content_type, "ServerSideEncryption": "AES256", "Tagging": f"lingowave-category={self._category(object_key)}"}
        if size_bytes is not None:
            params["ContentLength"] = size_bytes
        return client.generate_presigned_url("put_object", Params=params, ExpiresIn=expires, HttpMethod="PUT")

    def head(self, object_key: str) -> dict:
        return self.client.head_object(Bucket=self.bucket, Key=object_key)


def object_store() -> ObjectStore:
    return S3ObjectStore() if settings.storage_backend == "s3" else LocalObjectStore()


def object_key(user_id: str, category: str, filename: str) -> str:
    suffix = Path(filename).suffix.lower()[:10]
    return f"users/{user_id}/{category}/{secrets.token_urlsafe(18)}{suffix}"
