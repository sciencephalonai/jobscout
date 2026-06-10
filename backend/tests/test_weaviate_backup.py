"""Tests for the Weaviate export/import backup (jobscout.backup).

Key guarantees:
- export writes a header + every job WITH its vector,
- import restores with **zero embedding calls** (vectors come from the file),
- importing into an index of a different vector dimension is refused (no mixing).
All run against a tiny in-memory fake store — no network, no torch, no Gemini.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import jobscout.backup as backup
import jobscout.embed as embed_mod
from jobscout.models import Job


# ── minimal fake Weaviate surface the backup code touches ──────────────────────
class _Obj:
    def __init__(self, props, vector):
        self.properties = props
        self.vector = {"default": vector} if vector is not None else None


class _FetchResult:
    def __init__(self, objects):
        self.objects = objects


class _FakeData:
    def __init__(self, store):
        self._store = store

    def insert(self, properties, uuid, vector=None):
        self._store._objs[uuid] = _Obj(properties, vector)

    def replace(self, uuid, properties, vector=None):
        self._store._objs[uuid] = _Obj(properties, vector)


class _FakeQuery:
    def __init__(self, store):
        self._store = store

    def fetch_object_by_id(self, uid):
        return self._store._objs.get(uid)

    def fetch_objects(self, limit=1, include_vector=False):
        return _FetchResult(list(self._store._objs.values())[:limit])


class _FakeCollection:
    def __init__(self, store):
        self._store = store
        self.data = _FakeData(store)
        self.query = _FakeQuery(store)

    def iterator(self, include_vector=False):
        return iter(list(self._store._objs.values()))


class _FakeCollections:
    def __init__(self, store):
        self._store = store

    def get(self, _name):
        return _FakeCollection(self._store)


class _FakeClient:
    def __init__(self, store):
        self.collections = _FakeCollections(store)


class FakeStore:
    """Implements only what backup.py + store.upsert touch."""

    def __init__(self, seed: list[tuple[Job, list[float]]] | None = None):
        self._objs: dict = {}
        self._client = _FakeClient(self)
        for job, vec in (seed or []):
            self.upsert(job, vec)

    def upsert(self, job: Job, vector=None) -> None:
        from jobscout.store import _job_to_props, _job_uuid
        uid = _job_uuid(job.job_id)
        coll = self._client.collections.get("Job")
        if uid in self._objs:
            coll.data.replace(uuid=uid, properties=_job_to_props(job), vector=vector)
        else:
            coll.data.insert(properties=_job_to_props(job), uuid=uid, vector=vector)


def _job(jid: str, title: str = "Data Engineer") -> Job:
    return Job(job_id=jid, source="greenhouse", title=title,
               url=f"http://x/{jid}", company="Acme", skills=["python", "sql"])


def test_export_import_roundtrip_no_embedding(tmp_path: Path, monkeypatch):
    # Any embedding call during backup/restore is a bug → make them explode.
    monkeypatch.setattr(embed_mod, "embed_job", lambda *a, **k: pytest.fail("embed_job called"))
    monkeypatch.setattr(embed_mod, "embed_query", lambda *a, **k: pytest.fail("embed_query called"))

    src = FakeStore(seed=[(_job("j1"), [0.1] * 8), (_job("j2", "ML Engineer"), [0.2] * 8)])
    out = tmp_path / "export.jsonl.gz"

    header = backup.export_index(src, out)
    assert header["count"] == 2
    assert header["dim"] == 8
    assert out.exists()

    dst = FakeStore()
    n = backup.import_index(dst, out)
    assert n == 2
    assert len(dst._objs) == 2  # both restored, with their vectors


def test_import_refuses_dimension_mismatch(tmp_path: Path):
    # Export an 8-dim index…
    src = FakeStore(seed=[(_job("j1"), [0.1] * 8)])
    out = tmp_path / "export.jsonl.gz"
    backup.export_index(src, out)

    # …then try to import into an index that already holds a 4-dim vector.
    dst = FakeStore(seed=[(_job("j9"), [0.9] * 4)])
    with pytest.raises(ValueError, match="different embedding models|cannot be mixed"):
        backup.import_index(dst, out)


def test_read_header(tmp_path: Path):
    src = FakeStore(seed=[(_job("j1"), [0.1] * 8)])
    out = tmp_path / "export.jsonl.gz"
    backup.export_index(src, out)
    h = backup.read_header(out)
    assert h["dim"] == 8 and h["count"] == 1 and h["embed_backend"] == "gemini"
