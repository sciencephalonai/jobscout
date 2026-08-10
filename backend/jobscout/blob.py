"""Blob/file storage seam — where resume + tailored files live.

Today files live on local disk (``LocalBlobStore``). This module is the single
boundary a hosted, multi-instance deployment swaps to move them to object storage
(S3/GCS) — because local disk is not shared across instances. Callers depend on the
:class:`BlobStore` Protocol, never on `Path.write_bytes` directly.

Keys are **repo-relative paths** under a storage root (e.g. ``data/resumes/<pid>/<rid>.pdf``);
``LocalBlobStore`` maps a key straight to a filesystem path, so ``local_path`` returns
a real ``Path`` and ``FileResponse``/the DOCX toolkit keep working unchanged. An
``S3BlobStore`` would implement ``write``/``read``/``delete`` against the bucket and make
``local_path`` fetch-to-temp (or callers switch to streaming) — see
docs/pre-deployment-checklist.md. ``settings.blob_backend`` selects the backend.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Protocol

import httpx

from jobscout.config import settings


class BlobStore(Protocol):
    """Contract for storing/retrieving user files (resumes, tailored DOCX)."""

    def write(self, path: Path, data: bytes) -> None: ...
    def read(self, path: Path) -> bytes: ...
    def delete(self, path: Path) -> None: ...
    def exists(self, path: Path) -> bool: ...
    def local_path(self, path: Path) -> Path | None: ...
    def delete_tree(self, directory: Path) -> None: ...


class LocalBlobStore:
    """Local-filesystem :class:`BlobStore` — the single-machine default.

    A key IS a filesystem path here (the existing ``*_resume_path`` helpers build
    them), so this is a thin, dependency-free wrapper: it just guarantees parent
    dirs exist and swallows missing-file deletes. ``local_path`` returns the real
    path so ``FileResponse`` and the external DOCX toolkit need no change.
    """

    def write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read(self, path: Path) -> bytes:
        return path.read_bytes()

    def delete(self, path: Path) -> None:
        path.unlink(missing_ok=True)

    def exists(self, path: Path) -> bool:
        return path.is_file()

    def local_path(self, path: Path) -> Path | None:
        return path if path.is_file() else None

    def delete_tree(self, directory: Path) -> None:
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)


class SupabaseBlobStore:
    """Supabase Storage :class:`BlobStore` — files live in a hosted bucket.

    A key is the repo-relative path used as the object name (e.g.
    ``data/resumes/<pid>/<rid>.pdf``). ``local_path`` returns ``None`` — files are
    remote, so download routes stream ``read()`` bytes instead of ``FileResponse``.
    Uses the service-role key server-side (never exposed to the browser).
    """

    def __init__(self, url: str, service_key: str, bucket: str,
                 client: httpx.Client | None = None) -> None:
        self._bucket = bucket
        self._client = client or httpx.Client(
            base_url=f"{url.rstrip('/')}/storage/v1",
            headers={"Authorization": f"Bearer {service_key}", "apikey": service_key},
            timeout=30.0,
        )

    @staticmethod
    def _key(path: Path) -> str:
        return str(path).lstrip("/")

    def write(self, path: Path, data: bytes) -> None:
        # x-upsert lets a re-tailor/overwrite replace an existing object.
        resp = self._client.post(
            f"/object/{self._bucket}/{self._key(path)}",
            content=data,
            headers={"content-type": "application/octet-stream", "x-upsert": "true"},
        )
        resp.raise_for_status()

    def read(self, path: Path) -> bytes:
        resp = self._client.get(f"/object/{self._bucket}/{self._key(path)}")
        resp.raise_for_status()
        return resp.content

    def delete(self, path: Path) -> None:
        # Missing objects are fine (idempotent delete), like LocalBlobStore.
        self._client.request("DELETE", f"/object/{self._bucket}/{self._key(path)}")

    def exists(self, path: Path) -> bool:
        resp = self._client.get(f"/object/info/{self._bucket}/{self._key(path)}")
        return resp.status_code == 200

    def local_path(self, path: Path) -> Path | None:  # noqa: ARG002 - remote store
        return None

    def delete_tree(self, directory: Path) -> None:
        prefix = self._key(directory)
        listing = self._client.post(
            f"/object/list/{self._bucket}", json={"prefix": prefix, "limit": 1000}
        )
        if listing.status_code != 200:
            return
        names = [f"{prefix}/{obj['name']}" for obj in listing.json() if obj.get("name")]
        if names:
            self._client.request("DELETE", f"/object/{self._bucket}", json={"prefixes": names})


def make_blob_store() -> BlobStore:
    """Construct the file-storage backend.

    ``storage_backend`` selects it: ``supabase`` (or ``auto`` when Supabase Storage
    is configured) → :class:`SupabaseBlobStore`; otherwise :class:`LocalBlobStore`.
    Callers depend on the Protocol, so nothing else changes. See docs/auth-and-hosting.md.
    """
    mode = settings.storage_backend
    if mode == "supabase" or (mode == "auto" and settings.supabase_storage_configured):
        return SupabaseBlobStore(
            settings.supabase_url, settings.supabase_service_key, settings.supabase_storage_bucket
        )
    if mode in ("auto", "local", ""):
        return LocalBlobStore()
    raise ValueError(f"Unknown storage_backend: {mode!r}")


# Module-level default so callers don't re-instantiate. Swapping the backend is a
# config change picked up on next process start.
blob_store: BlobStore = make_blob_store()
