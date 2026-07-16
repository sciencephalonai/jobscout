"""Multi-tenancy seam: the IDOR class is impossible by construction.

No auth is wired yet (single local user owns everything), but a profile stamped
with a DIFFERENT user_id must be completely invisible — every profile-scoped route
404s (not 403, so ids can't be enumerated), list endpoints exclude it, and the
dangerous global routes admin-gate once single_user_mode is off.
"""

from __future__ import annotations

import pytest

from jobscout.config import settings
from jobscout.models import SavedSearch, UserProfile


def _foreign_profile(client) -> str:  # noqa: ANN001
    """A profile owned by someone OTHER than the local user, written directly."""
    store = client.app.state.relational_store
    p = UserProfile(label="not-yours", user_id="someone-else", resume_text="x " * 20)
    store.upsert_profile(p)
    return p.id


# Every profile-scoped route, keyed by the HTTP method used to reach it.
_FOREIGN_ROUTES = [
    ("get", "/api/profiles/{pid}"),
    ("put", "/api/profiles/{pid}"),
    ("delete", "/api/profiles/{pid}"),
    ("get", "/api/profiles/{pid}/resumes"),
    ("get", "/api/profiles/{pid}/tailored"),
    ("post", "/api/profiles/{pid}/deep-results"),
    ("get", "/api/profiles/{pid}/pipeline"),
    ("post", "/api/profiles/{pid}/reparse"),
    ("get", "/api/profiles/{pid}/tailored/some-job"),
    ("get", "/api/profiles/{pid}/resumes/some-rid/file"),
]


@pytest.mark.parametrize(("method", "path"), _FOREIGN_ROUTES)
def test_foreign_profile_routes_404(client, method, path):  # noqa: ANN001
    pid = _foreign_profile(client)
    kwargs = {"json": {}} if method in ("post", "put", "patch") else {}
    resp = getattr(client, method)(path.format(pid=pid), **kwargs)
    # 404 — never 403 (no id enumeration) and never 200 (no leak).
    assert resp.status_code == 404


def test_query_and_body_profile_routes_reject_foreign_owner(client):  # noqa: ANN001
    # These routes take profile_id via QUERY/BODY (not the /api/profiles/{id} path),
    # so the path-only middleware can't see them — owned_profile guards each. A
    # foreign-owned profile_id must 404, never leak another user's data/quota.
    pid = _foreign_profile(client)
    assert client.get(f"/api/jobs/by-state?profile_id={pid}&status=saved").status_code == 404
    assert client.get(f"/api/jobs?profile_id={pid}&recommendation_only=true").status_code == 404
    assert client.post("/api/match/deep/j1", json={"profile_id": pid}).status_code == 404
    assert client.post("/api/match", json={"resume_text": "x", "profile_id": pid}).status_code == 404


def test_attach_resume_rejects_foreign_source(client):  # noqa: ANN001
    # Destination is mine; the SOURCE profile belongs to someone else → 404 (no PII copy).
    mine = client.post("/api/profiles", json={"label": "mine"}).json()["id"]
    foreign = _foreign_profile(client)
    resp = client.post(f"/api/profiles/{mine}/attach-resume/{foreign}")
    assert resp.status_code == 404


def test_search_run_rejects_foreign_profile(client):  # noqa: ANN001
    foreign = _foreign_profile(client)
    resp = client.post("/api/search/run", json={"keywords": ["x"], "profile_id": foreign})
    assert resp.status_code == 404


def test_list_profiles_excludes_other_users(client):  # noqa: ANN001
    mine = client.post("/api/profiles", json={"label": "mine"}).json()["id"]
    foreign = _foreign_profile(client)
    ids = {p["id"] for p in client.get("/api/profiles").json()}
    assert mine in ids
    assert foreign not in ids


def test_created_profile_is_owned_by_caller(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
    stored = client.app.state.relational_store.get_profile(pid)
    assert stored is not None and stored.user_id == settings.local_user_id


def test_saved_search_scoping_and_ownership(client):  # noqa: ANN001
    store = client.app.state.relational_store
    mine = client.post("/api/saved-searches", json={"label": "mine", "filters": {}}).json()
    assert mine["user_id"] == settings.local_user_id
    foreign = SavedSearch(label="theirs", user_id="someone-else")
    store.create_saved_search(foreign)

    listed = {s["id"] for s in client.get("/api/saved-searches").json()}
    assert mine["id"] in listed
    assert foreign.id not in listed
    # A foreign saved search can't be touched either (404, not 403).
    assert client.delete(f"/api/saved-searches/{foreign.id}").status_code == 404
    assert client.post(f"/api/saved-searches/{foreign.id}/seen").status_code == 404


def test_export_and_delete_my_data(client):  # noqa: ANN001
    pid = client.post("/api/profiles", json={"label": "mine"}).json()["id"]
    client.post("/api/saved-searches", json={"label": "s", "filters": {}})

    bundle = client.get("/api/users/me/export").json()
    assert [p["profile"]["id"] for p in bundle["profiles"]] == [pid]
    assert len(bundle["saved_searches"]) == 1

    wiped = client.delete("/api/users/me/data").json()
    assert wiped["profiles_deleted"] == 1
    assert client.get("/api/profiles").json() == []
    assert client.get("/api/saved-searches").json() == []


def test_make_relational_store_returns_duckdb_impl():
    from jobscout.relational import DuckDBRelationalStore, make_relational_store

    store = make_relational_store(":memory:")
    assert isinstance(store, DuckDBRelationalStore)
    store.close()


def test_ensure_local_user_seeds_exactly_one():
    from jobscout.config import settings
    from jobscout.relational import DuckDBRelationalStore

    store = DuckDBRelationalStore(":memory:")  # _create_tables calls ensure_local_user
    rows = store._conn.execute("SELECT id FROM users").fetchall()
    assert [r[0] for r in rows] == [settings.local_user_id]
    store.ensure_local_user()  # idempotent — no duplicate
    assert store._conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
    store.close()


def test_store_reopens_persisted_db_without_error(tmp_path):  # noqa: ANN001
    # Reopening a persisted DB re-runs _create_tables; the idempotent ALTER … ADD
    # COLUMN IF NOT EXISTS must not error (a failed dup-column ALTER would abort the txn).
    from jobscout.relational import DuckDBRelationalStore

    path = str(tmp_path / "reopen.duckdb")
    s1 = DuckDBRelationalStore(path)
    s1.upsert_profile(UserProfile(label="p"))
    s1.close()
    s2 = DuckDBRelationalStore(path)  # second _create_tables over existing columns
    assert len(s2.list_profiles()) == 1
    s2.close()


def test_reap_stale_runs_flips_orphaned_running():
    from jobscout.relational import DuckDBRelationalStore

    store = DuckDBRelationalStore(":memory:")
    store.start_run("ashby")  # left 'running' (a crash before finish_run)
    assert store.reap_stale_runs() == 1
    status = store._conn.execute("SELECT status FROM runs").fetchone()[0]
    assert status == "interrupted"
    assert store.reap_stale_runs() == 0  # nothing left to reap
    store.close()


def test_admin_routes_open_locally_but_gate_when_hosting(client, monkeypatch):  # noqa: ANN001
    # single_user_mode (default True) → local admin allowed (not a 403).
    assert client.post("/api/sources/overrides", json={"jobspy": False}).status_code != 403
    # Flip to hosted mode + demote the caller → the dangerous globals 403.
    monkeypatch.setattr(settings, "single_user_mode", False)
    client.app.state.relational_store.update_user(settings.local_user_id, is_admin=False)
    assert client.post("/api/sources/overrides", json={"jobspy": True}).status_code == 403
    assert client.post("/api/scheduler", json={"enabled": False}).status_code == 403
    assert client.post("/api/maintenance/purge", json={"days": 30}).status_code == 403
