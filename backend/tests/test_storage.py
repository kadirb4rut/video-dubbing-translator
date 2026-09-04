from __future__ import annotations

from app.storage import S3ObjectStore


class FakeS3Client:
    def __init__(self, url="https://example.test/upload"):
        self.params = None
        self.url = url

    def generate_presigned_url(self, operation, *, Params, ExpiresIn, HttpMethod):
        self.params = {"operation": operation, "Params": Params, "ExpiresIn": ExpiresIn, "HttpMethod": HttpMethod}
        return self.url


def test_presigned_media_upload_carries_private_storage_controls():
    store = object.__new__(S3ObjectStore)
    store.bucket = "private-media"
    store.client = FakeS3Client()

    assert store.presigned_put("users/user-1/media/object.mp4", content_type="video/mp4") == "https://example.test/upload"
    assert store.client.params["Params"]["ServerSideEncryption"] == "AES256"
    assert store.client.params["Params"]["Tagging"] == "lingowave-category=media"


def test_presigned_media_upload_binds_expected_size():
    store = object.__new__(S3ObjectStore)
    store.bucket = "private-media"
    store.client = FakeS3Client()

    store.presigned_put("users/user-1/media/object.mp4", content_type="video/mp4", size_bytes=1234)

    assert store.client.params["Params"]["ContentLength"] == 1234


def test_presigned_url_uses_browser_visible_client_when_configured():
    store = object.__new__(S3ObjectStore)
    store.bucket = "private-media"
    store.client = FakeS3Client("http://minio:9000/internal")
    store.presign_client = FakeS3Client("http://localhost:9000/browser")

    assert store.presigned_put("users/user-1/media/object.mp4", content_type="video/mp4") == "http://localhost:9000/browser"
    assert store.presign_client.params["Params"]["Bucket"] == "private-media"
    assert store.client.params is None
