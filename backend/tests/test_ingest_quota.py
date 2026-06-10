"""Tests for quota-aware ingestion (the "jobs not saving / flat count" fix).

- When the embedding provider 429s (EmbeddingQuotaError), `_run_ingestion` stops
  cleanly and records WHY on the run (so /api/sources/status shows it) instead of
  silently dropping every remaining job.
- A single run never embeds more than `settings.embed_daily_budget` jobs, so one
  "Get latest jobs" click can't exhaust the day's Gemini free-tier quota.
"""

from __future__ import annotations

import jobscout.embed as embed_mod
import jobscout.services.ingestion_service as ing
from jobscout.embed import EmbeddingQuotaError
from jobscout.relational import RelationalStore


class _RecordingStore:
    """Minimal Weaviate stand-in: every job is new; records what gets saved."""

    def __init__(self) -> None:
        self.upserts: list[str] = []

    def get_by_id(self, _jid):  # noqa: ANN001
        return None

    def upsert(self, job, vector=None) -> None:  # noqa: ANN001
        self.upserts.append(job.job_id)


class _FakeAdapter:
    name = "greenhouse"

    def __init__(self, n: int) -> None:
        self._n = n

    def search(self, keywords, location, results_wanted, since, http):  # noqa: ANN001
        for i in range(self._n):
            yield {
                "title": f"Data Engineer {i}", "company": "Acme",
                "url": f"http://x/{i}", "location": "New York, NY",
                "country": "us", "source_job_id": str(i),
            }


def _setup(monkeypatch, adapter):  # noqa: ANN001
    monkeypatch.setattr(ing, "_load_sources_cfg", lambda: {"sources": {}})
    monkeypatch.setattr(ing, "_build_adapters", lambda _cfg: [adapter])
    monkeypatch.setattr(ing, "_company_size_map", lambda _cfg: {})

    class _NoopHttp:
        def close(self) -> None:
            pass

    monkeypatch.setattr(ing, "CompliantHttpClient", lambda *a, **k: _NoopHttp())


def test_already_indexed_jobs_are_skipped_no_embed(monkeypatch):
    """The core efficiency fix: jobs already in the index (enriched) must NOT be
    re-embedded — the embed budget should be spent only on genuinely-new jobs."""
    from jobscout.models import Job

    class _ExistingStore:
        """get_by_id returns a 'done' job for everything → all are already indexed."""

        def __init__(self):
            self.upserts = []

        def get_by_id(self, jid):  # noqa: ANN001
            return Job(job_id=jid, source="greenhouse", title="x",
                       url="http://x", company="Acme", enrichment_status="done")

        def upsert(self, job, vector=None):  # noqa: ANN001
            self.upserts.append(job.job_id)

    def trip_wire(*a, **k):
        raise AssertionError("embed_job called for an already-indexed job!")

    monkeypatch.setattr(ing, "embed_job", trip_wire)
    _setup(monkeypatch, _FakeAdapter(5))
    store = _ExistingStore()
    rel = RelationalStore(":memory:")
    try:
        ing._run_ingestion(["data"], None, 10, store, rel)  # must NOT raise
        assert store.upserts == []   # nothing re-embedded / re-upserted
    finally:
        rel.close()


def test_new_jobs_are_embedded(monkeypatch):
    """A genuinely-new job (get_by_id → None) IS embedded + saved."""
    calls = {"n": 0}
    monkeypatch.setattr(ing, "embed_job", lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1) or [0.0] * 8))
    _setup(monkeypatch, _FakeAdapter(3))
    store = _RecordingStore()  # get_by_id → None (all new)
    rel = RelationalStore(":memory:")
    try:
        ing._run_ingestion(["data"], None, 10, store, rel)
        assert len(store.upserts) == 3 and calls["n"] == 3   # all 3 embedded + saved
    finally:
        rel.close()


def test_quota_error_stops_run_and_records_reason(monkeypatch):
    def boom(*a, **k):
        raise EmbeddingQuotaError(
            "Gemini embedding quota exhausted (free tier = 1,000/day); resets daily."
        )

    monkeypatch.setattr(ing, "embed_job", boom)
    store = _RecordingStore()
    rel = RelationalStore(":memory:")
    _setup(monkeypatch, _FakeAdapter(3))
    try:
        ing._run_ingestion(["data"], None, 10, store, rel)
        assert store.upserts == []  # nothing saved when the quota is gone
        errs = [s["last_error"] for s in rel.get_sources_status() if s["last_error"]]
        assert any("quota" in (e or "").lower() for e in errs)
    finally:
        rel.close()


def test_embed_quota_flag_sets_on_429_and_clears_on_success(monkeypatch):
    import jobscout.embed as e

    calls = {"n": 0}

    class _FakeEmbedding:
        values = [0.0] * 8

    class _FakeResult:
        embeddings = [_FakeEmbedding()]

    class _FakeModels:
        def embed_content(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("429 You exceeded your current quota")
            return _FakeResult()

    class _FakeClient:
        models = _FakeModels()

    monkeypatch.setattr(e, "_get_client", lambda: _FakeClient())
    e._mark_quota(False)

    # First call 429s → flag set + EmbeddingQuotaError.
    try:
        e.embed_text("x")
        raise AssertionError("expected EmbeddingQuotaError")
    except EmbeddingQuotaError:
        pass
    assert embed_mod.embedding_quota_hit() is True

    # Next call succeeds → flag clears (quota recovered).
    e.embed_text("y")
    assert embed_mod.embedding_quota_hit() is False


def test_refresh_watchlist_stops_on_quota_without_crashing(monkeypatch):
    from jobscout.models import Company

    def boom(*a, **k):
        raise EmbeddingQuotaError("quota exhausted")

    monkeypatch.setattr(ing, "embed_job", boom)
    monkeypatch.setattr(ing, "extract_enrichment", lambda *a, **k: {})

    class _NoopHttp:
        def close(self) -> None:
            pass

    monkeypatch.setattr(ing, "CompliantHttpClient", lambda *a, **k: _NoopHttp())
    monkeypatch.setattr(ing, "_REFRESH_ADAPTER", {"greenhouse": lambda slugs: _FakeAdapter(3)})

    store = _RecordingStore()
    rel = RelationalStore(":memory:")
    rel.upsert_company(Company(ats="greenhouse", slug="acme", name="Acme", enabled=True))
    try:
        result = ing._refresh_watchlist(store, rel, 800, ["data"])
        assert store.upserts == []            # nothing embedded/saved
        assert result["stopped_early"] is True  # stopped cleanly, no crash
    finally:
        rel.close()


def test_run_stops_at_embed_budget(monkeypatch):
    calls = {"n": 0}

    def fake_embed(*a, **k):
        calls["n"] += 1
        return [0.0] * 8

    monkeypatch.setattr(ing, "embed_job", fake_embed)
    monkeypatch.setattr(ing.settings, "embed_daily_budget", 2)
    store = _RecordingStore()
    rel = RelationalStore(":memory:")
    _setup(monkeypatch, _FakeAdapter(5))
    try:
        ing._run_ingestion(["data"], None, 10, store, rel)
        assert len(store.upserts) == 2          # stopped at the budget
        assert calls["n"] == 2                  # only 2 embeds spent
        errs = [s["last_error"] for s in rel.get_sources_status() if s["last_error"]]
        assert any("budget" in (e or "").lower() for e in errs)
    finally:
        rel.close()
