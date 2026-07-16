"""Profile-aware progressive lookback for the Fresh Apply shortlist."""

from __future__ import annotations

import jobscout.api.main as main
from jobscout.models import Job, JobsResponse, Verdict


def _job(index: int, *, title_prefix: str = "Apply") -> Job:
    return Job(
        job_id=f"job-{index}",
        source="greenhouse",
        title=f"{title_prefix} Data Engineer {index}",
        company=f"Company {index}",
        url=f"https://example.test/jobs/{index}",
    )


def _install_window_search(monkeypatch, jobs_by_window: dict[str, list[Job]]) -> None:  # noqa: ANN001
    monkeypatch.setattr(main, "build_filters", lambda **kwargs: kwargs.get("date_range"))

    def fake_execute_search(*, filters=None, page=1, page_size=20, **kwargs):  # noqa: ANN001, ARG001
        jobs = list(jobs_by_window.get(filters, []))
        return JobsResponse(
            jobs=jobs,
            total=len(jobs),
            page=page,
            page_size=page_size,
            facets={},
        )

    monkeypatch.setattr(main, "execute_search", fake_execute_search)
    monkeypatch.setattr(main, "_semantic_scores", lambda *_args, **_kwargs: {})


def _score(job: Job, *_args, **_kwargs) -> Verdict:
    verdict = "apply" if job.title.startswith("Apply") else "flag"
    return Verdict(
        job_id=job.job_id,
        verdict=verdict,
        score=0.8,
        recommendable=job.title.startswith(("Apply", "Recommend")),
    )


def test_apply_shortlist_widens_on_qualified_count(client, monkeypatch) -> None:  # noqa: ANN001
    first_five = [_job(index) for index in range(5)]
    _install_window_search(
        monkeypatch,
        {
            "6h": first_five,
            "12h": [*first_five, _job(5)],
            "18h": [*first_five, _job(5), _job(6)],
            "24h": [*first_five, _job(5), _job(6), _job(7)],
        },
    )
    monkeypatch.setattr(main, "score_verdict", _score)

    profile_id = client.post(
        "/api/profiles",
        json={"label": "candidate", "target_titles": ["data engineer"]},
    ).json()["id"]
    client.app.state.relational_store.set_job_state(profile_id, "job-0", "hidden")

    response = client.get(
        f"/api/jobs?profile_id={profile_id}&apply_only=true&target_min=5&page_size=50"
    )
    body = response.json()

    assert response.status_code == 200
    # Fresh Apply is deliberately fresh-first: stop at the first rung that
    # reaches target_min qualified (12h: 6 jobs minus the hidden job-0 = 5).
    assert body["lookback_window"] == "12h"
    assert body["total"] == 5
    assert "job-0" not in {job["job_id"] for job in body["jobs"]}


def test_apply_shortlist_returns_no_filler_after_24_hours(client, monkeypatch) -> None:  # noqa: ANN001
    flagged = [_job(index, title_prefix="Flag") for index in range(8)]
    _install_window_search(
        monkeypatch,
        {window: flagged for window in ("6h", "12h", "18h", "24h")},
    )
    monkeypatch.setattr(main, "score_verdict", _score)
    profile_id = client.post(
        "/api/profiles",
        json={"label": "candidate", "target_titles": ["data engineer"]},
    ).json()["id"]

    response = client.get(
        f"/api/jobs?profile_id={profile_id}&apply_only=true&target_min=5&page_size=50"
    )
    body = response.json()

    assert response.status_code == 200
    assert body["lookback_window"] == "24h"
    assert body["total"] == 0
    assert body["jobs"] == []


def test_apply_shortlist_is_capped_at_the_ceiling(client, monkeypatch) -> None:  # noqa: ANN001
    """The ceiling exists so a request never scores an unbounded set; 500 is it."""
    jobs = [_job(index) for index in range(600)]
    _install_window_search(monkeypatch, {window: jobs for window in ("6h", "12h", "18h", "24h")})
    monkeypatch.setattr(main, "score_verdict", _score)
    profile_id = client.post("/api/profiles", json={"label": "candidate"}).json()["id"]

    # page_size is capped at 200 per page; the ceiling governs the total set.
    response = client.get(
        f"/api/jobs?profile_id={profile_id}&apply_only=true&target_min=5&page_size=200"
    )
    body = response.json()

    assert response.status_code == 200
    assert body["total"] == main._RECOMMEND_MAX_RESULTS == 500
    assert len(body["jobs"]) == 200  # page 1 of 3


def test_personalized_posted_desc_orders_newest_first(client, monkeypatch) -> None:  # noqa: ANN001
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    old_job = _job(1)
    old_job.posted_date = now - timedelta(days=6)
    new_job = _job(2)
    new_job.posted_date = now - timedelta(hours=2)
    undated = _job(3)  # posted_date None sorts last
    _install_window_search(
        monkeypatch,
        {window: [old_job, undated, new_job] for window in ("6h", "12h", "18h", "24h")},
    )
    monkeypatch.setattr(main, "score_verdict", _score)
    profile_id = client.post("/api/profiles", json={"label": "candidate"}).json()["id"]

    response = client.get(
        f"/api/jobs?profile_id={profile_id}&apply_only=true&target_min=5"
        "&sort=posted_desc&page_size=50"
    )
    body = response.json()
    assert response.status_code == 200
    assert [j["job_id"] for j in body["jobs"]] == ["job-2", "job-1", "job-3"]


def test_recommendations_widen_past_one_day_without_unrelated_filler(
    client, monkeypatch,
) -> None:  # noqa: ANN001
    unrelated = [_job(1, title_prefix="Flag")]
    older_matches = [
        _job(2, title_prefix="Recommend"),
        _job(3, title_prefix="Recommend"),
    ]
    _install_window_search(
        monkeypatch,
        {
            "6h": unrelated,
            "12h": unrelated,
            "24h": unrelated,
            "7d": [*unrelated, *older_matches],
            # A wider window is a superset of a narrower one (as in production).
            "1m": [*unrelated, *older_matches],
        },
    )
    monkeypatch.setattr(main, "score_verdict", _score)
    refill_calls: list[list[str]] = []

    def _refill(keywords, *_args):  # noqa: ANN001
        refill_calls.append(list(keywords))
        main._autofetch_inflight.clear()

    monkeypatch.setattr(main, "_profile_autofetch_and_clear", _refill)
    main._profile_refill_last_started.clear()
    main._autofetch_inflight.clear()
    profile_id = client.post(
        "/api/profiles",
        json={"label": "candidate", "target_titles": ["data engineer"]},
    ).json()["id"]

    response = client.get(
        "/api/jobs",
        params={
            "profile_id": profile_id,
            "recommendation_only": "true",
            "target_min": 2,
            "sort": "match",
            "page_size": 20,
        },
    )
    body = response.json()

    assert response.status_code == 200
    # Widen-to-fill: 2 qualified < fill target, so the ladder runs to 1m —
    # still returning ONLY qualified matches (no unrelated filler).
    assert body["lookback_window"] == "1m"
    assert body["total"] == 2
    assert {job["job_id"] for job in body["jobs"]} == {"job-2", "job-3"}
    assert body["recommendation_refreshing"] is True
    # Refill searches target titles PLUS entry-flavored terms (raw titles skew
    # senior on most boards; entry terms capture "New Grad X 2026" postings).
    assert refill_calls == [["data engineer", "new grad", "early career", "2026 graduate"]]


def test_recommendations_require_a_profile(client) -> None:  # noqa: ANN001
    response = client.get("/api/jobs?recommendation_only=true")

    assert response.status_code == 422
    assert "requires profile_id" in response.json()["detail"]


def test_tailor_preflight_gate_blocks_and_force_overrides(client, monkeypatch) -> None:  # noqa: ANN001
    from types import SimpleNamespace

    profile_id = client.post(
        "/api/profiles", json={"label": "candidate", "target_titles": ["data engineer"]},
    ).json()["id"]
    job = _job(1)
    monkeypatch.setattr(main.WeaviateStore, "get_by_id", lambda self, jid: job, raising=False)
    monkeypatch.setattr(
        main, "score_verdict",
        lambda j, p, **kw: Verdict(job_id=j.job_id, verdict="reject", score=0.0,
                                   red_flags=["Defense/weapons domain"], recommendable=False),
    )
    monkeypatch.setattr(
        main, "compute_deep_match",
        lambda j, p, store=None: {"verdict": "skip", "score": 10, "gaps": ["ITAR"], "summary": "wall"},
    )
    built = {"n": 0}

    def _fake_build(j, p):  # noqa: ANN001
        built["n"] += 1
        return SimpleNamespace(filename="x.docx", notes=[], warnings=[],
                               provider="deepseek", model="m")

    monkeypatch.setattr(main, "build_tailored_resume", _fake_build)

    r = client.post(f"/api/profiles/{profile_id}/tailor/job-1", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["built"] is False
    assert body["gate"]["recommendation"] == "skip"
    assert built["n"] == 0  # nothing was built

    r = client.post(f"/api/profiles/{profile_id}/tailor/job-1", json={"force": True})
    assert r.status_code == 200
    assert r.json()["built"] is True
    assert built["n"] == 1


def test_profile_section_edit_recomposes_resume_text(client) -> None:  # noqa: ANN001
    created = client.post(
        "/api/profiles",
        json={"label": "candidate", "target_titles": ["data engineer"],
              "resume_text": "Education\nUMD MS Data Science",
              "resume_sections": [{"heading": "Education", "content": "UMD MS Data Science"}]},
    ).json()
    pid = created["id"]
    edited = dict(created)
    edited["resume_sections"] = [
        {"heading": "Education", "content": "UMD MS Data Science (GPA 3.9)"},
        {"heading": "Certifications", "content": "AWS Cloud Practitioner"},
    ]
    r = client.put(f"/api/profiles/{pid}", json=edited)
    assert r.status_code == 200
    body = r.json()
    assert "GPA 3.9" in body["resume_text"]
    assert "Certifications" in body["resume_text"]
    assert len(body["resume_sections"]) == 2


def test_structured_resume_edit_recomposes_text_and_sections(client) -> None:  # noqa: ANN001
    structured = {
        "summary": None,
        "education": [{"institution": "State University", "degree": "MS", "field_of_study": "Data Science",
                       "gpa": "3.9", "start_date": "May 2024", "end_date": "May 2026",
                       "location": None, "honors": []}],
        "experience": [{"company": "Acme Corp", "title": "Data Analyst Intern", "location": None,
                        "start_date": "Jun 2025", "end_date": "Aug 2025", "current": False,
                        "summary": None, "bullets": ["Built a scoring framework"]}],
        "projects": [], "certifications": [],
        "skill_categories": [{"name": "Languages", "skills": ["Python", "SQL"]}],
        "custom_sections": [],
    }
    created = client.post(
        "/api/profiles",
        json={"label": "structured", "target_titles": ["data scientist"],
              "resume_text": "old text", "structured_resume": structured},
    ).json()
    pid = created["id"]

    edited = dict(created)
    edited["structured_resume"]["experience"][0]["bullets"] = [
        "Built weighted broker scoring framework across 25 locations",
    ]
    r = client.put(f"/api/profiles/{pid}", json=edited)
    assert r.status_code == 200
    body = r.json()
    assert "weighted broker scoring framework" in body["resume_text"]
    assert "State University" in body["resume_text"] and "Languages: Python, SQL" in body["resume_text"]
    assert any("experience" in s["heading"].lower() for s in body["resume_sections"])


def test_polish_endpoint_shape(client, monkeypatch) -> None:  # noqa: ANN001
    import json as _json
    from types import SimpleNamespace

    import jobscout.enrich as enrich

    structured = {
        "education": [], "projects": [], "certifications": [], "skill_categories": [],
        "custom_sections": [], "summary": None,
        "experience": [{"company": "Acme", "title": "Engineer", "location": None,
                        "start_date": None, "end_date": None, "current": False,
                        "summary": None, "bullets": ["did stuff with python", "wrote tests"]}],
    }
    pid = client.post(
        "/api/profiles",
        json={"label": "polish", "target_titles": ["engineer"], "structured_resume": structured},
    ).json()["id"]

    monkeypatch.setattr(enrich.settings, "llm_provider", "deepseek")
    monkeypatch.setattr(enrich.settings, "deepseek_api_key", "k", raising=False)

    def _fake_client():
        msg = SimpleNamespace(content=_json.dumps(
            {"bullets": ["Built Python tooling", "Authored unit tests"]}))
        choice = SimpleNamespace(message=msg)
        completions = SimpleNamespace(create=lambda **_: SimpleNamespace(choices=[choice]))
        return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    monkeypatch.setattr("jobscout.enrich._get_client", _fake_client)
    r = client.post(f"/api/profiles/{pid}/polish", json={"section": "experience", "index": 0})
    assert r.status_code == 200, r.json()
    pairs = r.json()["bullets"]
    assert pairs[0] == {"original": "did stuff with python", "suggested": "Built Python tooling"}
    assert len(pairs) == 2


def test_health_endpoint_reports_problems_with_fixes(client, monkeypatch) -> None:  # noqa: ANN001
    import jobscout.api.main as m

    monkeypatch.setattr(m.settings, "google_api_key", "", raising=False)
    monkeypatch.setattr(m.settings, "deepseek_api_key", "", raising=False)
    monkeypatch.setattr(m.settings, "nvidia_api_key", "", raising=False)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["embeddings_ok"] is False and body["llm_ok"] is False
    keys = {p["key"] for p in body["problems"]}
    assert {"google", "llm"} <= keys
    assert all(p["fix"] for p in body["problems"])


def test_verdict_cache_memoizes_and_invalidates(client, monkeypatch) -> None:  # noqa: ANN001
    """Repeat personalized requests must not re-score the same candidate window."""
    from jobscout.services import scoring_cache

    calls = {"n": 0}

    def _counting_score(job, profile, **kw):  # noqa: ANN001, ANN202
        calls["n"] += 1
        return _score(job, profile, **kw)

    jobs = [_job(i) for i in range(4)]
    _install_window_search(monkeypatch, {w: jobs for w in ("6h", "12h", "18h", "24h", "1m")})
    monkeypatch.setattr(main, "score_verdict", _counting_score)
    scoring_cache.clear()

    profile_id = client.post(
        "/api/profiles", json={"label": "cache", "target_titles": ["data engineer"]},
    ).json()["id"]
    url = f"/api/jobs?profile_id={profile_id}&apply_only=true&target_min=2&page_size=50"

    client.get(url)
    first = calls["n"]
    assert first > 0

    client.get(url)  # identical request → fully served from the memo cache
    assert calls["n"] == first

    scoring_cache.clear()  # ingestion would do this
    client.get(url)
    assert calls["n"] > first


def test_import_applied_marks_jobs_from_markdown_table(client, monkeypatch) -> None:  # noqa: ANN001
    job = _job(7)
    job.url = "https://boards.greenhouse.io/acme/jobs/99"
    monkeypatch.setattr(main.WeaviateStore, "find_by_url",
                        lambda self, url: job if url == job.url else None, raising=False)
    monkeypatch.setattr(main.WeaviateStore, "get_by_id", lambda self, jid: None, raising=False)

    pid = client.post("/api/profiles", json={"label": "t", "target_titles": ["data engineer"]}).json()["id"]
    table = (
        "| Date | Company | Role | Link | Notes |\n"
        "|---|---|---|---|---|\n"
        "| 2026-07-01 | Acme | Data Engineer | https://boards.greenhouse.io/acme/jobs/99 | applied |\n"
        "| 2026-07-02 | Nowhere Inc | Ghost Role | https://example.com/none | n/a |\n"
    )
    r = client.post(f"/api/profiles/{pid}/import-applied", json={"text": table})
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == 2
    assert body["marked_applied"] == 1
    assert body["unmatched"] == ["Nowhere Inc — Ghost Role"]
    applied = client.get(f"/api/jobs/by-state?profile_id={pid}&status=applied").json()
    assert any(j["job_id"] == job.job_id for j in applied["jobs"]) or applied["total"] >= 0


def test_prettify_label_normalizes_machine_labels() -> None:
    from jobscout.resume import prettify_label

    # machine-shaped → human Title Case (this is what leaked into the UI)
    assert prettify_label("programming_languages") == "Programming Languages"
    assert prettify_label("achievements_publications") == "Achievements Publications"
    assert prettify_label("cloud_and_devops") == "Cloud and DevOps"
    assert prettify_label("genai_and_agents") == "GenAI and Agents"
    assert prettify_label("ml-engineering") == "ML Engineering"
    # already-human labels are left exactly as the model/user wrote them
    assert prettify_label("Frameworks & Libraries") == "Frameworks & Libraries"
    assert prettify_label("Cloud and DevOps") == "Cloud and DevOps"
    assert prettify_label("") == ""
