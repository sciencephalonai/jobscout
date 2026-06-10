"""API smoke tests — the behavior safety net for the api/main.py refactor.

Hits real route → handler paths with a fake vector store + in-memory DuckDB
(see conftest). Asserts status + response shape, so a refactor that moves code
between modules can't silently break an endpoint's contract.
"""

from __future__ import annotations

import jobscout.services.ingestion_service as ingestion_service


class _FakeAdapter:
    """Minimal JobSourceAdapter that yields canned US raw dicts (no network)."""

    name = "greenhouse"  # a real source name so authority/registry logic is happy

    def search(self, keywords, location, results_wanted, since, http):  # noqa: ANN001
        yield {
            "title": "Data Engineer", "company": "Acme", "url": "http://x/de1",
            "location": "New York, NY", "description": "Build pipelines.",
            "source_job_id": "de1",
        }


def _patch_ingestion(monkeypatch):  # noqa: ANN001
    """Make _run_ingestion run fully offline: fake adapters + enrich + embed.
    _run_ingestion now lives in ingestion_service, so patch its namespace."""
    monkeypatch.setattr(ingestion_service, "_build_adapters", lambda _cfg: [_FakeAdapter()])
    monkeypatch.setattr(ingestion_service, "extract_enrichment", lambda *a, **k: {
        "yoe_min": 1, "yoe_max": 3, "visa_sponsorship": "not_mentioned",
        "skills": ["python", "sql"], "seniority": "mid",
        "security_clearance": "none", "citizenship_required": False,
        "employer_type": "for_profit", "company_size_bucket": "51-200",
    })
    monkeypatch.setattr(ingestion_service, "embed_job", lambda *a, **k: [0.0] * 8)


def test_search_run_ingestion_offline(client, monkeypatch):
    """POST /api/search/run runs the ingestion orchestration end-to-end against the
    fake store (background task executes under TestClient) without network/LLM."""
    _patch_ingestion(monkeypatch)
    r = client.post("/api/search/run", json={"keywords": ["data engineer"], "results_wanted": 2})
    assert r.status_code == 200
    assert isinstance(r.json(), list)  # RunLog stubs returned immediately


def test_jobs_list(client):
    r = client.get("/api/jobs?page_size=5")
    assert r.status_code == 200
    body = r.json()
    assert "jobs" in body and "total" in body and isinstance(body["jobs"], list)


def test_jobs_filters_and_match_sort_with_profile(client):
    pid = client.post("/api/profiles", json={"label": "t", "skills": ["python"]}).json()["id"]
    r = client.get(f"/api/jobs?profile_id={pid}&sort=match&exp=entry&everify=true")
    assert r.status_code == 200
    assert "jobs" in r.json()


def test_profiles_crud(client):
    created = client.post("/api/profiles", json={"label": "p1", "skills": ["sql"]})
    assert created.status_code == 200
    pid = created.json()["id"]
    assert any(p["id"] == pid for p in client.get("/api/profiles").json())
    assert client.get(f"/api/profiles/{pid}").status_code == 200
    assert client.delete(f"/api/profiles/{pid}").status_code == 200


def test_job_state_and_by_state(client):
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    r = client.post(f"/api/profiles/{pid}/job-state", json={"job_id": "j1", "status": "saved"})
    assert r.status_code == 200
    listed = client.get(f"/api/jobs/by-state?profile_id={pid}&status=saved")
    assert listed.status_code == 200
    assert any(j["job_id"] == "j1" for j in listed.json()["jobs"])


def test_saved_searches_crud(client):
    created = client.post("/api/saved-searches", json={"label": "DE", "filters": {"q": "data engineer"}})
    assert created.status_code == 200
    sid = created.json()["id"]
    rows = client.get("/api/saved-searches").json()
    assert any(s["id"] == sid and "new_count" in s for s in rows)
    assert client.post(f"/api/saved-searches/{sid}/seen").status_code == 200
    assert client.delete(f"/api/saved-searches/{sid}").status_code == 200


def test_pipeline_stages_and_note(client):
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    # Apply, add a note, then advance to interview.
    client.post(f"/api/profiles/{pid}/job-state", json={"job_id": "j1", "status": "applied", "note": "referred by X"})
    client.post(f"/api/profiles/{pid}/job-state", json={"job_id": "j1", "status": "interview"})
    pipe = client.get(f"/api/profiles/{pid}/pipeline").json()
    assert any(j["job_id"] == "j1" for j in pipe["jobs"])
    assert pipe["stages"]["j1"]["stage"] == "interview"
    assert pipe["stages"]["j1"]["note"] == "referred by X"   # note preserved across stage change


def test_operations_endpoints(client):
    assert client.get("/api/scheduler").json()["enabled"] is False
    assert client.get("/api/sources/overrides").status_code == 200
    assert client.post("/api/sources/overrides", json={"jobspy": True}).json().get("jobspy") is True
    client.post("/api/sources/overrides", json={"jobspy": False})  # reset
    assert client.get("/api/stats").status_code == 200
    purged = client.post("/api/maintenance/purge", json={"days": 99999})
    assert purged.status_code == 200 and purged.json()["deleted"] == 0
