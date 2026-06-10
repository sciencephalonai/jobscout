"""Shared test fixtures — an in-process API client with fake Weaviate + real
in-memory DuckDB, so endpoint tests run with NO cloud Weaviate and NO API keys.

This is the safety net for the api/main.py refactor: it exercises the real
route → handler paths (and real DuckDB CRUD for profiles/saved-searches/job-state)
while stubbing the vector store + embeddings.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import jobscout.api.main as main
import jobscout.services.query_service as query_service
from jobscout.models import Job, JobsResponse
from jobscout.relational import RelationalStore


def _job(jid: str, title: str = "Data Engineer", company: str = "Acme") -> Job:
    return Job(job_id=jid, source="ashby", title=title, url=f"http://x/{jid}", company=company)


_FAKE_JOBS = [_job("j1", "Data Engineer", "Acme"), _job("j2", "ML Engineer", "Beta")]


class _FakeAggResult:
    total_count = 0
    groups: list = []


class _FakeColl:
    @property
    def aggregate(self) -> "_FakeColl":
        return self

    def over_all(self, **_: object) -> _FakeAggResult:
        return _FakeAggResult()


class _FakeCollections:
    def get(self, _name: str) -> _FakeColl:
        return _FakeColl()


class _FakeClient:
    collections = _FakeCollections()

    def close(self) -> None:
        pass


class FakeWeaviateStore:
    """Implements only the surface the API touches; returns canned data."""

    def __init__(self, *_: object, **__: object) -> None:
        self._client = _FakeClient()
        self.jobs = list(_FAKE_JOBS)

    def get_by_id(self, jid: str) -> Job | None:
        return next((j for j in self.jobs if j.job_id == jid), None)

    def search_near_vector(self, vector, filters=None, limit=5):  # noqa: ANN001
        return self.jobs[:limit]

    def near_vector_scores(self, vector, filters=None, limit=500):  # noqa: ANN001
        return {j.job_id: 0.8 for j in self.jobs}

    def purge_older_than(self, cutoff) -> int:  # noqa: ANN001
        return 0

    def upsert(self, *_: object, **__: object) -> None:
        pass

    def update_fields(self, *_: object, **__: object) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch):  # noqa: ANN001, ANN201
    """A TestClient with fake Weaviate + in-memory DuckDB (lifespan builds them)."""
    monkeypatch.setattr(main, "WeaviateStore", FakeWeaviateStore)
    monkeypatch.setattr(main, "RelationalStore", lambda *_a, **_k: RelationalStore(":memory:"))
    # embeddings are referenced inside the query service now.
    monkeypatch.setattr(query_service, "embed_query", lambda *_a, **_k: [0.0] * 8)

    def _fake_execute_search(*, store, q=None, alpha=0.5, filters=None,
                             sort="relevance", page=1, page_size=20, **_):  # noqa: ANN001
        jobs = list(getattr(store, "jobs", _FAKE_JOBS))
        return JobsResponse(jobs=jobs, total=len(jobs), page=page,
                            page_size=page_size, facets={})

    # list_jobs calls execute_search via main; _count_matches via the query service.
    monkeypatch.setattr(main, "execute_search", _fake_execute_search)
    monkeypatch.setattr(query_service, "execute_search", _fake_execute_search)

    with TestClient(main.app) as c:
        yield c
