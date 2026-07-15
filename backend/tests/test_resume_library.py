"""Resume library + tailored-resume catalog endpoints.

Exercises the real route → DuckDB paths with the LLM resume parse stubbed, so no
API keys are needed. File writes are redirected to a tmp dir.
"""

from __future__ import annotations

import pytest

import jobscout.api.main as main
from jobscout.config import settings
from jobscout.models import TailoredResumeRecord, UserProfile


@pytest.fixture(autouse=True)
def _tmp_storage(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """Redirect resume/tailored file writes away from the real data dir."""
    monkeypatch.setattr(settings, "resume_storage_dir", str(tmp_path / "resumes"))
    monkeypatch.setattr(settings, "tailored_resume_storage_dir", str(tmp_path / "tailored"))

    def _fake_parse(text: str, label: str) -> UserProfile:
        # Echo the extracted text so each upload is distinguishable.
        return UserProfile(
            label=label, resume_text=text, skills=["python"],
            target_titles=["data engineer"], seniority_max="mid", yoe_max=2,
        )

    monkeypatch.setattr(main, "parse_resume_to_profile", _fake_parse)


def _upload(client, pid: str, name: str, body: str):  # noqa: ANN001, ANN202
    return client.post(
        f"/api/profiles/{pid}/resumes",
        files={"file": (name, body.encode(), "text/plain")},
    )


def test_two_resumes_coexist_and_first_is_active(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]

    r1 = _upload(client, pid, "one.txt", "RESUME ONE")
    assert r1.status_code == 200
    lib = r1.json()
    assert len(lib["resumes"]) == 1
    assert lib["active_resume_id"] == lib["resumes"][0]["id"]

    r2 = _upload(client, pid, "two.txt", "RESUME TWO")
    lib = r2.json()
    # Second upload does NOT overwrite the first — both are listed.
    assert len(lib["resumes"]) == 2
    # First stays active (uploading another doesn't auto-switch).
    active = lib["active_resume_id"]
    assert active is not None
    filenames = {row["filename"] for row in lib["resumes"]}
    assert filenames == {"one.txt", "two.txt"}


def test_activate_switches_matching_text(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    _upload(client, pid, "one.txt", "RESUME ONE")
    lib = _upload(client, pid, "two.txt", "RESUME TWO").json()
    two = next(r for r in lib["resumes"] if r["filename"] == "two.txt")

    client.post(f"/api/profiles/{pid}/resumes/{two['id']}/activate").raise_for_status()
    profile = client.get(f"/api/profiles/{pid}").json()
    assert profile["active_resume_id"] == two["id"]
    assert profile["resume_text"] == "RESUME TWO"


def test_rename_resume(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    rid = _upload(client, pid, "one.txt", "RESUME ONE").json()["resumes"][0]["id"]

    renamed = client.patch(
        f"/api/profiles/{pid}/resumes/{rid}", json={"filename": "Data Eng v2.pdf"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["filename"] == "Data Eng v2.pdf"
    rows = client.get(f"/api/profiles/{pid}/resumes").json()["resumes"]
    assert rows[0]["filename"] == "Data Eng v2.pdf"


def test_delete_active_promotes_next(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    first = _upload(client, pid, "one.txt", "RESUME ONE").json()["resumes"][0]["id"]
    _upload(client, pid, "two.txt", "RESUME TWO")

    # first is active; delete it → the remaining resume takes over.
    lib = client.delete(f"/api/profiles/{pid}/resumes/{first}").json()
    assert len(lib["resumes"]) == 1
    assert lib["active_resume_id"] == lib["resumes"][0]["id"]
    assert lib["resumes"][0]["filename"] == "two.txt"


def test_download_resume_returns_bytes(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    rid = _upload(client, pid, "one.txt", "RESUME ONE").json()["resumes"][0]["id"]
    got = client.get(f"/api/profiles/{pid}/resumes/{rid}/file")
    assert got.status_code == 200
    assert got.content == b"RESUME ONE"


def test_get_put_round_trip_never_rewrites_resume_text(client):  # noqa: ANN001
    # Text whose derived sections differ from what was stored (stored sections
    # empty), mimicking list_profiles re-deriving sections in the response.
    original = "SUMMARY\nBuilt things.\n\nEXPERIENCE\n- Did work at Acme.\n"
    pid = client.post("/api/profiles", json={"label": "p", "resume_text": original}).json()["id"]

    fetched = client.get(f"/api/profiles/{pid}").json()  # response re-derives sections
    fetched["label"] = "Renamed"  # a rename-only edit
    client.put(f"/api/profiles/{pid}", json=fetched).raise_for_status()

    saved = client.get(f"/api/profiles/{pid}").json()
    assert saved["label"] == "Renamed"
    # The lossless canonical text must be byte-identical — never recomposed
    # from round-tripped sections.
    assert saved["resume_text"] == original


def test_delete_profile_removes_library_files(client):  # noqa: ANN001
    from pathlib import Path

    from jobscout.config import settings

    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    _upload(client, pid, "one.txt", "RESUME ONE")
    lib_dir = Path(settings.resume_storage_dir) / pid
    assert lib_dir.is_dir()

    client.delete(f"/api/profiles/{pid}").raise_for_status()
    assert not lib_dir.exists()


def test_renamed_resume_download_keeps_extension(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    rid = _upload(client, pid, "one.txt", "RESUME ONE").json()["resumes"][0]["id"]
    client.patch(
        f"/api/profiles/{pid}/resumes/{rid}", json={"filename": "Marriott SWE"}
    ).raise_for_status()

    got = client.get(f"/api/profiles/{pid}/resumes/{rid}/file")
    assert got.status_code == 200
    # Content-Disposition carries the display name WITH the real suffix restored
    # (spaces arrive RFC 5987 percent-encoded).
    assert "Marriott%20SWE.txt" in got.headers.get("content-disposition", "")


def test_duplicate_upload_gets_numbered_label(client):  # noqa: ANN001
    # Same file uploaded twice via the match dropzone → "one" and "one (2)",
    # never two indistinguishable profiles.
    for _ in range(2):
        r = client.post(
            "/api/match/upload",
            files={"file": ("one.txt", b"RESUME", "text/plain")},
            data={"limit": "1"},
        )
        assert r.status_code == 200
    labels = sorted(p["label"] for p in client.get("/api/profiles").json())
    assert labels == ["one", "one (2)"]


def test_duplicate_resume_filename_gets_numbered(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    _upload(client, pid, "cv.txt", "A")
    lib = _upload(client, pid, "cv.txt", "B").json()
    names = sorted(r["filename"] for r in lib["resumes"])
    assert names == ["cv (2).txt", "cv.txt"]


def test_rename_collisions_are_suffixed(client):  # noqa: ANN001
    # Resume rename onto a sibling's name.
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    _upload(client, pid, "a.txt", "A")
    rid_b = next(
        r["id"] for r in _upload(client, pid, "b.txt", "B").json()["resumes"]
        if r["filename"] == "b.txt"
    )
    renamed = client.patch(f"/api/profiles/{pid}/resumes/{rid_b}", json={"filename": "a.txt"})
    assert renamed.json()["filename"] == "a (2).txt"

    # Profile rename onto another profile's label.
    other = client.post("/api/profiles", json={"label": "taken"}).json()
    mine = client.get(f"/api/profiles/{pid}").json()
    mine["label"] = "taken"
    saved = client.put(f"/api/profiles/{pid}", json=mine).json()
    assert saved["label"] == "taken (2)"
    assert other["label"] == "taken"


def test_suggest_filters_existing_and_validates_field(client, monkeypatch):  # noqa: ANN001
    import jobscout.enrich as enrich

    pid = client.post("/api/profiles", json={
        "label": "p", "resume_text": "Python ML resume.", "interests": ["genai"],
    }).json()["id"]
    monkeypatch.setattr(enrich, "llm_is_configured", lambda: True)
    monkeypatch.setattr(
        enrich, "chat_json",
        lambda *_a, **_k: '{"suggestions": ["genai", "ml systems", "recommender systems"]}',
    )
    got = client.post(f"/api/profiles/{pid}/suggest", json={"field": "interests"}).json()
    assert got["suggestions"] == ["ml systems", "recommender systems"]  # "genai" filtered

    bad = client.post(f"/api/profiles/{pid}/suggest", json={"field": "excluded_companies"})
    assert bad.status_code == 422


def test_deep_match_persistence_and_fingerprint_staleness(client, monkeypatch):  # noqa: ANN001
    import jobscout.deep_match as deep_match
    import jobscout.enrich as enrich
    from jobscout.deep_match import compute_deep_match, deep_match_fingerprint
    from jobscout.models import Job, UserProfile

    store = client.app.state.relational_store
    job = Job(job_id="jx", source="ashby", title="ML Engineer", url="http://x/jx", company="Acme")
    profile = UserProfile(label="p", resume_text="Python, ML, pandas. " * 20)

    calls = {"n": 0}

    def _fake_chat(*_a, **_k):  # noqa: ANN002, ANN003
        calls["n"] += 1
        return '{"verdict":"apply","score":88,"strengths":["ml"],"gaps":[],"summary":"ok"}'

    monkeypatch.setattr(deep_match, "llm_is_configured", lambda: True)
    monkeypatch.setattr(enrich, "chat_json", _fake_chat)
    deep_match._CACHE.clear()

    # First call spends 1 chat + persists to DuckDB.
    r1 = compute_deep_match(job, profile, store)
    assert r1["score"] == 88 and calls["n"] == 1
    # Clear the in-process cache → the persisted row must serve with NO 2nd chat.
    deep_match._CACHE.clear()
    r2 = compute_deep_match(job, profile, store)
    assert r2["score"] == 88 and r2["cached"] is True and calls["n"] == 1

    # Change the resume → new fingerprint → stored row is stale → NOT served.
    fp_old = deep_match_fingerprint(job, profile)
    profile.resume_text = "Totally different resume now. " * 20
    assert deep_match_fingerprint(job, profile) != fp_old
    assert store.get_deep_match("jx", profile.id, deep_match_fingerprint(job, profile)) is None


def test_deep_results_endpoint_only_returns_current_fingerprint(client):  # noqa: ANN001
    from jobscout.deep_match import deep_match_fingerprint
    from jobscout.models import UserProfile

    store = client.app.state.relational_store
    weav_job = client.app.state.weaviate_store.get_by_id("j1")  # conftest fake job
    profile = UserProfile(label="p", resume_text="Data engineering resume. " * 20)
    store.upsert_profile(profile)
    fp = deep_match_fingerprint(weav_job, profile)
    store.put_deep_match("j1", profile.id, fp, {"verdict": "apply", "score": 77, "cached": False})

    got = client.post(
        f"/api/profiles/{profile.id}/deep-results", json={"job_ids": ["j1", "j2"]}
    ).json()["results"]
    assert got["j1"]["score"] == 77
    assert "j2" not in got  # never computed → excluded


def test_tailored_up_to_date_flips_when_resume_changes(client):  # noqa: ANN001
    from jobscout.deep_match import deep_match_fingerprint
    from jobscout.models import TailoredResumeRecord, UserProfile

    store = client.app.state.relational_store
    job = client.app.state.weaviate_store.get_by_id("j1")  # conftest fake job
    profile = UserProfile(label="p", resume_text="Original resume. " * 20)
    store.upsert_profile(profile)
    store.upsert_tailored(TailoredResumeRecord(
        profile_id=profile.id, job_id="j1", company="Acme", title="DE",
        filename="Owner_Acme_DE_Resume.docx",
        fingerprint=deep_match_fingerprint(job, profile),
    ))

    row = client.get(f"/api/profiles/{profile.id}/tailored").json()["tailored"][0]
    assert row["filename"] == "Owner_Acme_DE_Resume.docx"
    assert row["up_to_date"] is True

    # Change the resume → fingerprint flips → the tailored DOCX is now stale.
    profile.resume_text = "A completely rewritten resume now. " * 20
    store.upsert_profile(profile)
    row2 = client.get(f"/api/profiles/{profile.id}/tailored").json()["tailored"][0]
    assert row2["up_to_date"] is False


def test_tailored_catalog_lists_builds(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    # Insert directly (a full build needs the private toolkit); the endpoint is
    # what we verify here.
    client.app.state.relational_store.upsert_tailored(TailoredResumeRecord(
        profile_id=pid, job_id="j1", company="Acme", title="Data Engineer",
        recommendation="build",
    ))
    listed = client.get(f"/api/profiles/{pid}/tailored").json()["tailored"]
    assert len(listed) == 1
    assert listed[0]["company"] == "Acme"
    assert listed[0]["download_url"].endswith("/tailored/j1")


def _write_tailored_file(pid: str) -> None:
    from jobscout.tailor import tailored_resume_path

    path = tailored_resume_path(pid, "j1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"DOCX")


def test_rename_tailored_persists_and_drives_download(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    store = client.app.state.relational_store
    store.upsert_tailored(TailoredResumeRecord(
        profile_id=pid, job_id="j1", company="Acme", title="DE",
        filename="auto_name.docx",
    ))
    _write_tailored_file(pid)

    # Rename without a .docx suffix → server forces it.
    renamed = client.patch(f"/api/profiles/{pid}/tailored/j1", json={"filename": "Acme DE"})
    assert renamed.status_code == 200
    assert renamed.json()["filename"] == "Acme DE.docx"

    # List reflects it AND the download Content-Disposition uses the stored name.
    row = client.get(f"/api/profiles/{pid}/tailored").json()["tailored"][0]
    assert row["filename"] == "Acme DE.docx"
    got = client.get(f"/api/profiles/{pid}/tailored/j1")
    assert got.status_code == 200
    assert "Acme%20DE.docx" in got.headers.get("content-disposition", "")


def test_rename_tailored_collision_is_suffixed(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    store = client.app.state.relational_store
    store.upsert_tailored(TailoredResumeRecord(profile_id=pid, job_id="j1", filename="taken.docx"))
    store.upsert_tailored(TailoredResumeRecord(profile_id=pid, job_id="j2", filename="other.docx"))

    renamed = client.patch(f"/api/profiles/{pid}/tailored/j2", json={"filename": "taken.docx"})
    assert renamed.json()["filename"] == "taken (2).docx"


def test_profile_fits_ranks_all_profiles(client):  # noqa: ANN001
    p1 = client.post("/api/profiles", json={
        "label": "de", "resume_text": "Data engineering, SQL, pandas. " * 20,
    }).json()["id"]
    p2 = client.post("/api/profiles", json={
        "label": "swe", "resume_text": "Backend software engineering, Go. " * 20,
    }).json()["id"]

    fits = client.get("/api/jobs/j1/profile-fits").json()["fits"]
    ids = {f["profile_id"] for f in fits}
    assert ids == {p1, p2}
    # Sorted by score descending.
    assert fits == sorted(fits, key=lambda f: f["score"], reverse=True)


def test_profile_fits_unknown_job_404(client):  # noqa: ANN001
    assert client.get("/api/jobs/nope/profile-fits").status_code == 404


def test_profile_fits_empty_without_profiles(client):  # noqa: ANN001
    assert client.get("/api/jobs/j1/profile-fits").json()["fits"] == []
