"""Blob/file storage seam."""

from __future__ import annotations

from jobscout.blob import LocalBlobStore, make_blob_store


def test_local_blob_store_round_trip(tmp_path):  # noqa: ANN001
    store = make_blob_store()
    assert isinstance(store, LocalBlobStore)
    p = tmp_path / "resumes" / "pid" / "rid.pdf"  # parents don't exist yet

    assert store.exists(p) is False
    assert store.local_path(p) is None
    store.write(p, b"PDF-BYTES")          # creates parent dirs
    assert store.exists(p) is True
    assert store.local_path(p) == p       # local path so FileResponse/toolkit work
    assert store.read(p) == b"PDF-BYTES"

    store.delete(p)
    assert store.exists(p) is False
    store.delete(p)                       # idempotent (missing_ok)

    store.write(p, b"x")
    store.delete_tree(tmp_path / "resumes")
    assert not (tmp_path / "resumes").exists()
