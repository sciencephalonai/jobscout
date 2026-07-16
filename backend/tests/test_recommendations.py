"""High-precision safety tests for the profile-driven For You feed."""

from __future__ import annotations

from jobscout.models import Job, JobsResponse, UserProfile
from jobscout.services.query_service import _profile_search_query
from jobscout.verdict import score


def _job(jid: str, title: str, **overrides: object) -> Job:
    base: dict[str, object] = {
        "job_id": jid,
        "source": "greenhouse",
        "title": title,
        "url": f"https://example.com/{jid}",
        "company": "Example",
        "country": "us",
        "skills": ["python", "sql"],
        "seniority": "junior",
        "yoe_min": 1,
        "visa_sponsorship": "yes",
        "security_clearance": "none",
    }
    base.update(overrides)
    return Job(**base)  # type: ignore[arg-type]


def test_profile_search_query_uses_targets_then_verified_skills() -> None:
    profile = UserProfile(
        label="candidate",
        target_titles=["Data Scientist", "ML Engineer"],
        interests=["Computer Vision"],
        skills=["Python", "SQL", "Python"],
    )
    query = _profile_search_query(profile)
    assert query is not None
    assert query.startswith("Data Scientist ML Engineer")
    assert "Computer Vision" in query
    assert query.count("Python") == 1
    assert "SQL" in query


def test_for_you_returns_only_genuinely_applicable_profile_matches(
    client, monkeypatch,
) -> None:  # noqa: ANN001
    store = client.app.state.weaviate_store
    store.jobs = [
        _job("good", "Junior Data Scientist"),
        # Legacy/stale metadata says unclear and only 1 YoE; title must still win.
        _job("senior", "Senior Data Scientist", seniority="unclear", yoe_min=1),
        _job("principal", "Principal Data Scientist", seniority="unclear", yoe_min=None),
        # Same generic tools are not enough to cross the user's role-interest gate.
        _job("unrelated", "Product Manager"),
        # A perfect semantic score must not invent unsupported technical evidence.
        _job("skills", "Junior Data Scientist", skills=["rust", "kubernetes"]),
    ]
    seen_queries: list[str | None] = []

    def _search(*, store, q=None, page=1, page_size=20, **kwargs):  # noqa: ANN001
        del kwargs
        seen_queries.append(q)
        return JobsResponse(
            jobs=list(store.jobs), total=len(store.jobs), page=page,
            page_size=page_size, facets={},
        )

    import jobscout.api.main as main

    monkeypatch.setattr(main, "execute_search", _search)
    profile = client.post(
        "/api/profiles",
        json={
            "label": "junior data profile",
            "target_titles": ["data scientist", "machine learning engineer"],
            "seniority_max": "junior",
            "yoe_max": 2,
            "needs_sponsorship": False,
            "skills": ["python", "sql", "pytorch"],
            "countries": ["us"],
        },
    ).json()

    response = client.get(
        "/api/jobs",
        params={
            "profile_id": profile["id"],
            "apply_only": "true",
            "sort": "match",
            "date_range": "14d",
            "page_size": 20,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [job["job_id"] for job in body["jobs"]] == ["good"]
    assert body["verdicts"]["good"]["verdict"] == "apply"
    # First call builds facets/recent state; the recommendation candidate call
    # must use profile evidence rather than another arbitrary recent slice.
    assert any(
        query and "data scientist" in query.lower() and "python" in query.lower()
        for query in seen_queries
    )


def test_profile_recommendations_allow_only_quality_caveats_not_fit_mismatches(
    client, monkeypatch,
) -> None:  # noqa: ANN001
    store = client.app.state.weaviate_store
    store.jobs = [
        _job(
            "good-unknown-sponsor",
            "Junior Data Scientist",
            visa_sponsorship="not_mentioned",
        ),
        _job(
            "nurse",
            "Registered Nurse, Women's Health Clinic",
            skills=[],
            description="Use Excel and Tableau in a hospital clinic.",
            employer_type="hospital",
            cap_exempt="likely",
            enrichment_status="failed",
            seniority="unclear",
            yoe_min=None,
        ),
        _job("senior", "Senior Data Scientist", seniority="unclear", yoe_min=None),
        _job("principal", "Principal Data Scientist", seniority="unclear", yoe_min=None),
    ]

    def _search(*, store, q=None, page=1, page_size=20, **kwargs):  # noqa: ANN001, ARG001
        return JobsResponse(
            jobs=list(store.jobs), total=len(store.jobs), page=page,
            page_size=page_size, facets={},
        )

    import jobscout.api.main as main

    monkeypatch.setattr(main, "execute_search", _search)
    monkeypatch.setattr(main, "_semantic_scores", lambda *_args, **_kwargs: {})
    profile = client.post(
        "/api/profiles",
        json={
            "label": "junior data profile",
            "target_titles": ["data scientist", "machine learning engineer"],
            "seniority_max": "junior",
            "yoe_max": 2,
            "needs_sponsorship": True,
            "skills": ["python", "sql", "excel", "tableau"],
            "countries": ["us"],
        },
    ).json()

    response = client.get(
        "/api/jobs",
        params={
            "profile_id": profile["id"],
            "recommendation_only": "true",
            "sort": "match",
            "date_range": "1m",
            "page_size": 20,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert [job["job_id"] for job in body["jobs"]] == ["good-unknown-sponsor"]
    verdict = body["verdicts"]["good-unknown-sponsor"]
    assert verdict["verdict"] == "flag"
    assert verdict["recommendable"] is True


def test_profile_recommendation_total_is_deduplicated_before_pagination(
    client, monkeypatch,
) -> None:  # noqa: ANN001
    store = client.app.state.weaviate_store
    store.jobs = [
        _job("direct", "Junior Data Scientist", source="greenhouse"),
        _job("repost", "Junior Data Scientist", source="jobspy"),
    ]

    def _search(*, store, q=None, page=1, page_size=20, **kwargs):  # noqa: ANN001, ARG001
        return JobsResponse(
            jobs=list(store.jobs), total=len(store.jobs), page=page,
            page_size=page_size, facets={},
        )

    import jobscout.api.main as main

    monkeypatch.setattr(main, "execute_search", _search)
    monkeypatch.setattr(main, "_semantic_scores", lambda *_args, **_kwargs: {})
    profile = client.post(
        "/api/profiles",
        json={
            "label": "junior data profile",
            "target_titles": ["data scientist"],
            "seniority_max": "junior",
            "yoe_max": 2,
            "needs_sponsorship": False,
            "skills": ["python", "sql"],
            "countries": ["us"],
        },
    ).json()

    response = client.get(
        "/api/jobs",
        params={
            "profile_id": profile["id"],
            "recommendation_only": "true",
            "sort": "match",
            "date_range": "1m",
            "page_size": 20,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [job["job_id"] for job in body["jobs"]] == ["direct"]
    assert set(body["verdicts"]) == {"direct"}


def test_personalized_search_does_not_start_generic_keyword_ingestion(
    client, monkeypatch,
) -> None:  # noqa: ANN001
    store = client.app.state.weaviate_store
    store.jobs = [_job("unrelated", "Registered Nurse", skills=[])]

    def _search(*, store, q=None, page=1, page_size=20, **kwargs):  # noqa: ANN001, ARG001
        return JobsResponse(
            jobs=list(store.jobs), total=len(store.jobs), page=page,
            page_size=page_size, facets={},
        )

    import jobscout.api.main as main

    generic_refills: list[list[str]] = []

    def _generic_refill(keywords, *_args):  # noqa: ANN001
        generic_refills.append(list(keywords))

    monkeypatch.setattr(main, "execute_search", _search)
    monkeypatch.setattr(main, "_semantic_scores", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(main, "_autofetch_and_clear", _generic_refill)
    main._autofetch_inflight.clear()
    profile = client.post(
        "/api/profiles",
        json={
            "label": "junior data profile",
            "target_titles": ["data scientist"],
            "seniority_max": "junior",
            "yoe_max": 2,
            "needs_sponsorship": False,
            "skills": ["python", "sql"],
            "countries": ["us"],
        },
    ).json()

    response = client.get(
        "/api/jobs",
        params={
            "profile_id": profile["id"],
            "q": "Vanderbilt",
            "recommendation_only": "true",
            "date_range": "1m",
        },
    )

    assert response.status_code == 200
    assert response.json()["total"] == 0
    assert generic_refills == []


def test_for_you_accepts_verbose_us_country_alias() -> None:
    profile = UserProfile(
        label="candidate",
        countries=["us"],
        target_titles=["data scientist"],
        skills=["python", "sql"],
        seniority_max="junior",
        yoe_max=2,
    )
    job = _job(
        "us-alias",
        "Junior Data Scientist",
        country="United States of America",
        location_raw="New York, NY",
    )

    assert score(job, profile).verdict == "apply"


def test_for_you_rejects_legacy_us_stamp_on_vietnam_workday_role() -> None:
    profile = UserProfile(
        label="candidate",
        countries=["us"],
        target_titles=["software engineer"],
        skills=["python", "docker"],
        seniority_max="junior",
        yoe_max=2,
    )
    job = _job(
        "nvidia-vietnam",
        "System Software Engineer, AI Data Platform",
        source="workday",
        company="NVIDIA",
        country="us",
        location_raw="Vietnam, Ho Chi Minh City; Vietnam, Hanoi",
        skills=["python", "docker"],
    )

    verdict = score(job, profile)
    assert verdict.verdict == "reject"
    assert any("Outside target countries" in flag for flag in verdict.red_flags)


def test_stated_interests_are_a_soft_positive_signal() -> None:
    profile = UserProfile(
        label="candidate",
        countries=["us"],
        target_titles=["data scientist"],
        interests=["computer vision"],
        skills=["python", "pytorch"],
        seniority_max="junior",
        yoe_max=2,
    )
    job = _job(
        "vision",
        "Junior Data Scientist",
        skills=["python", "pytorch"],
        description="Build computer vision models for image classification.",
    )

    verdict = score(job, profile)
    assert verdict.verdict == "apply"
    assert "Aligns with stated interests" in verdict.reasons


def test_description_skill_evidence_recovers_unenriched_job() -> None:
    profile = UserProfile(
        label="candidate",
        countries=["us"],
        target_titles=["software engineer"],
        skills=["python", "docker", "kubernetes"],
        seniority_max="junior",
        yoe_max=2,
    )
    job = _job(
        "legacy-skills",
        "Junior Software Engineer",
        skills=[],
        description="Build Python automation services deployed with Docker and Kubernetes.",
    )

    verdict = score(job, profile)
    assert verdict.verdict == "apply"
    assert set(verdict.matched) == {"python", "docker", "kubernetes"}
    assert any("explicitly named" in reason for reason in verdict.reasons)
