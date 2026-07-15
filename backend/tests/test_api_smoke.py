"""API smoke tests — the behavior safety net for the api/main.py refactor.

Hits real route → handler paths with a fake vector store + in-memory DuckDB
(see conftest). Asserts status + response shape, so a refactor that moves code
between modules can't silently break an endpoint's contract.
"""

from __future__ import annotations

import jobscout.resume as resume
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


def test_profile_edit_persists_the_full_canonical_resume_text(client):
    original = "Alex Example\nExperience: Python and SQL\n" + ("evidence " * 4_000)
    created = client.post("/api/profiles", json={"label": "p1", "resume_text": original})
    assert created.status_code == 200
    profile = created.json()
    profile["skills"] = ["python", "sql"]
    profile["target_titles"] = ["data engineer"]
    profile["resume_text"] = original + "\nNew project: FastAPI."

    updated = client.put(f"/api/profiles/{profile['id']}", json=profile)
    assert updated.status_code == 200
    saved = client.get(f"/api/profiles/{profile['id']}").json()
    assert saved["resume_text"] == profile["resume_text"]
    assert saved["skills"] == ["python", "sql"]


def test_structured_stale_flips_on_raw_text_edit_and_clears_on_structured_edit(client):
    # A profile that has both raw text and a typed structured view.
    created = client.post("/api/profiles", json={
        "label": "p", "resume_text": "Original text.", "structured_resume": {"summary": "orig"},
    }).json()
    assert created["structured_stale"] is False

    # Raw-text edit (structured unchanged) → typed cards now lag → flag True.
    created["resume_text"] = "Edited text with new content."
    client.put(f"/api/profiles/{created['id']}", json=created).raise_for_status()
    assert client.get(f"/api/profiles/{created['id']}").json()["structured_stale"] is True

    # A structured edit makes structured the source again → flag clears.
    p = client.get(f"/api/profiles/{created['id']}").json()
    p["structured_resume"]["summary"] = "revised"
    client.put(f"/api/profiles/{created['id']}", json=p).raise_for_status()
    assert client.get(f"/api/profiles/{created['id']}").json()["structured_stale"] is False


def test_profile_can_attach_a_saved_resume_without_losing_preferences(client):
    source = client.post(
        "/api/profiles",
        json={
            "label": "complete resume",
            "skills": ["python", "sql", "pytorch"],
            "target_titles": ["machine learning engineer"],
            "resume_text": "EDUCATION\nExample University\n\nPROJECTS\nBuilt a model.",
        },
    ).json()
    target = client.post(
        "/api/profiles",
        json={
            "label": "matcher preferences",
            "skills": ["docker"],
            "target_titles": ["data engineer"],
            "needs_sponsorship": True,
        },
    ).json()

    attached = client.post(
        f"/api/profiles/{target['id']}/attach-resume/{source['id']}"
    )
    assert attached.status_code == 200
    saved = attached.json()
    assert saved["resume_text"] == source["resume_text"]
    assert [section["heading"] for section in saved["resume_sections"]] == ["Education", "Projects"]
    assert saved["skills"] == ["python", "sql", "pytorch", "docker"]
    assert saved["target_titles"] == ["data engineer", "machine learning engineer"]
    assert saved["needs_sponsorship"] is True


def test_original_resume_can_be_downloaded_locally(client, monkeypatch, tmp_path):
    monkeypatch.setattr(resume.settings, "resume_storage_dir", str(tmp_path))
    created = client.post(
        "/api/profiles",
        json={"label": "p1", "resume_filename": "resume.txt", "resume_content_type": "text/plain"},
    ).json()
    resume.store_original_resume(created["id"], "resume.txt", b"original resume")
    downloaded = client.get(f"/api/profiles/{created['id']}/resume")
    assert downloaded.status_code == 200
    assert downloaded.content == b"original resume"
    assert downloaded.headers["content-type"].startswith("text/plain")


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
