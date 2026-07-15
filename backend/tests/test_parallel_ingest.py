"""Parallel fetch phase of _run_ingestion: all sources land, errors isolate,
each worker gets its own http client, workers=1 stays sequential."""

from __future__ import annotations

import time

import pytest

from jobscout.config import settings
from jobscout.services import ingestion_service


class _FakeStore:
    """Weaviate stand-in: remembers upserts, everything is new."""

    def __init__(self):
        self.saved = []

    def get_by_id(self, job_id):
        return None

    def upsert(self, job, vector):
        self.saved.append(job)


class _FakeRelational:
    def __init__(self):
        self.runs = []
        self.finished = []

    def start_run(self, source):
        self.runs.append(source)

        class _R:
            id = f"run-{source}"

        return _R()

    def finish_run(self, run_id, **kw):
        self.finished.append((run_id, kw))

    def upsert_job_source(self, js):
        pass


class _Adapter:
    def __init__(self, name, titles, delay=0.0, explode=False):
        self.name = name
        self._titles = titles
        self._delay = delay
        self._explode = explode
        self.seen_http_ids = []

    def search(self, keywords, location, results_wanted, since, http):
        self.seen_http_ids.append(id(http))
        time.sleep(self._delay)
        if self._explode:
            raise RuntimeError("boom")
        for i, t in enumerate(self._titles):
            yield {
                "title": t,
                "company": f"{self.name}-co",
                "url": f"https://example.com/{self.name}/{i}",
                "location": "New York, NY",
                "description": None,
            }


@pytest.fixture
def _no_embed(monkeypatch):
    monkeypatch.setattr(ingestion_service, "embed_job", lambda **kw: [0.0] * 4)
    monkeypatch.setattr(ingestion_service, "llm_is_configured", lambda: False)
    monkeypatch.setattr(ingestion_service, "is_known_h1b_sponsor", lambda c: False)
    monkeypatch.setattr(ingestion_service, "is_everify_employer", lambda c: False)


def _run(adapters, monkeypatch, workers=4):
    monkeypatch.setattr(settings, "ingest_fetch_workers", workers)
    monkeypatch.setattr(settings, "export_after_ingest", False)
    monkeypatch.setattr(ingestion_service, "_load_sources_cfg", lambda: {"sources": {}})
    monkeypatch.setattr(ingestion_service, "_build_adapters", lambda cfg: adapters)
    store, rel = _FakeStore(), _FakeRelational()
    ingestion_service._run_ingestion(
        ["engineer"], None, 50, store, rel  # type: ignore[arg-type]
    )
    return store, rel


def test_all_sources_land_in_parallel(monkeypatch, _no_embed):
    adapters = [
        _Adapter("a", ["Engineer A1", "Engineer A2"], delay=0.05),
        _Adapter("b", ["Engineer B1"], delay=0.01),
        _Adapter("c", ["Engineer C1", "Engineer C2", "Engineer C3"]),
    ]
    store, rel = _run(adapters, monkeypatch, workers=4)
    assert len(store.saved) == 6
    assert sorted(rel.runs) == ["a", "b", "c"]
    # each adapter got its own client instance (no shared-session races)
    ids = [a.seen_http_ids[0] for a in adapters]
    assert len(set(ids)) == 3


def test_one_exploding_adapter_does_not_sink_the_rest(monkeypatch, _no_embed):
    adapters = [
        _Adapter("ok", ["Engineer OK"]),
        _Adapter("bad", ["never"], explode=True),
    ]
    store, rel = _run(adapters, monkeypatch, workers=4)
    assert [j.company for j in store.saved] == ["ok-co"]
    bad_finish = next(kw for rid, kw in rel.finished if rid == "run-bad")
    assert bad_finish["error"] and "boom" in bad_finish["error"]
    ok_finish = next(kw for rid, kw in rel.finished if rid == "run-ok")
    assert ok_finish["count_ingested"] == 1 and not ok_finish["error"]


def test_workers_one_processes_in_submission_order(monkeypatch, _no_embed):
    adapters = [_Adapter(n, [f"Engineer {n}"]) for n in ("first", "second", "third")]
    _, rel = _run(adapters, monkeypatch, workers=1)
    assert rel.runs == ["first", "second", "third"]


def test_profile_refill_source_set_includes_curated_feeds():
    """The For You refill must fetch curated new-grad feeds, not just ATS boards."""
    import inspect

    from jobscout.source_intelligence import CURATED_SOURCES, PRIMARY_SOURCES

    src = inspect.getsource(ingestion_service._profile_autofetch_and_clear)
    assert "CURATED_SOURCES" in src
    assert "simplify" in (PRIMARY_SOURCES | CURATED_SOURCES)


def test_prefilter_uses_verdict_role_families_not_categories():
    """BI Analyst must survive the ingest pre-filter for an analyst profile
    (the old derive_category gate dropped it; the verdict would recommend it)."""
    from jobscout.models import Job, UserProfile
    from jobscout.services.ingestion_service import _is_profile_candidate

    profile = UserProfile(label="t", target_titles=["data analyst"], seniority_max="junior")
    bi = Job(job_id="x1", source="greenhouse", title="Business Intelligence Analyst",
             url="https://e.com/1")
    assert _is_profile_candidate(bi, profile) is True
    nurse = Job(job_id="x2", source="workday", title="Registered Nurse",
                url="https://e.com/2")
    swe_profile = UserProfile(label="t2", target_titles=["software engineer"],
                              seniority_max="junior")
    pm = Job(job_id="x3", source="greenhouse", title="Product Manager", url="https://e.com/3")
    assert _is_profile_candidate(pm, swe_profile) is False  # both map, no overlap
    # Licensed/direct-care titles sit outside the family taxonomy but must NOT
    # ride fail-open into paid enrichment for a technical profile.
    assert _is_profile_candidate(nurse, profile) is False
    # ...unless the profile explicitly targets that occupation.
    nurse_profile = UserProfile(label="t3", target_titles=["registered nurse"])
    assert _is_profile_candidate(nurse, nurse_profile) is True
