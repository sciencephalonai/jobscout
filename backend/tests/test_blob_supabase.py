"""SupabaseBlobStore — Storage REST calls, with httpx mocked (no live Supabase)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from jobscout.blob import SupabaseBlobStore

BUCKET = "jobscout-files"


def _make_store() -> tuple[SupabaseBlobStore, dict[str, bytes]]:
    """A store backed by an in-memory dict via an httpx MockTransport."""
    objects: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        obj_prefix = f"/storage/v1/object/{BUCKET}/"
        info_prefix = f"/storage/v1/object/info/{BUCKET}/"
        if method == "POST" and path.startswith(obj_prefix):
            objects[path[len(obj_prefix):]] = request.content
            return httpx.Response(200, json={"Key": path[len(obj_prefix):]})
        if method == "GET" and path.startswith(info_prefix):
            key = path[len(info_prefix):]
            return httpx.Response(200 if key in objects else 404)
        if method == "GET" and path.startswith(obj_prefix):
            key = path[len(obj_prefix):]
            return httpx.Response(200, content=objects[key]) if key in objects else httpx.Response(404)
        if method == "DELETE" and path.startswith(obj_prefix):
            objects.pop(path[len(obj_prefix):], None)
            return httpx.Response(200)
        return httpx.Response(404)

    client = httpx.Client(
        base_url="https://ref.supabase.co/storage/v1",
        transport=httpx.MockTransport(handler),
    )
    return SupabaseBlobStore("https://ref.supabase.co", "service-key", BUCKET, client=client), objects


class TestSupabaseBlobStore:
    def test_write_then_read_roundtrip(self) -> None:
        store, _ = _make_store()
        path = Path("data/resumes/p1/r1.pdf")
        store.write(path, b"%PDF-fake")
        assert store.read(path) == b"%PDF-fake"

    def test_exists(self) -> None:
        store, _ = _make_store()
        path = Path("data/tailored/p1/j1.docx")
        assert store.exists(path) is False
        store.write(path, b"docx")
        assert store.exists(path) is True

    def test_delete_is_idempotent(self) -> None:
        store, objects = _make_store()
        path = Path("data/resumes/p1/r1.pdf")
        store.write(path, b"x")
        store.delete(path)
        assert store.exists(path) is False
        store.delete(path)  # second delete must not raise

    def test_local_path_is_none(self) -> None:
        store, _ = _make_store()
        assert store.local_path(Path("data/resumes/p1/r1.pdf")) is None

    def test_read_missing_raises(self) -> None:
        store, _ = _make_store()
        with pytest.raises(httpx.HTTPStatusError):
            store.read(Path("data/nope.pdf"))


def test_make_blob_store_selects_supabase(monkeypatch) -> None:  # noqa: ANN001
    from jobscout import blob
    from jobscout.config import settings
    monkeypatch.setattr(settings, "storage_backend", "auto")
    monkeypatch.setattr(settings, "supabase_url", "https://ref.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_key", "key")
    monkeypatch.setattr(settings, "supabase_storage_bucket", BUCKET)
    assert isinstance(blob.make_blob_store(), SupabaseBlobStore)

    monkeypatch.setattr(settings, "supabase_url", "")  # not configured → local
    assert isinstance(blob.make_blob_store(), blob.LocalBlobStore)
