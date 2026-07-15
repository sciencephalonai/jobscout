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


def make_blob_store() -> BlobStore:
    """Construct the file-storage backend selected by ``settings.blob_backend``.

    Only ``local`` exists today; an ``s3``/``gcs`` backend is a future drop-in that
    implements :class:`BlobStore` — callers never change. See docs/multi-tenancy.md.
    """
    backend = settings.blob_backend
    if backend == "local":
        return LocalBlobStore()
    raise ValueError(f"Unknown blob_backend: {backend!r}")


# Module-level default so callers don't re-instantiate. Swapping the backend is a
# config change picked up on next process start.
blob_store: BlobStore = make_blob_store()
