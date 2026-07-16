"""Operator/admin API + the metering-vs-enforcement split."""

from __future__ import annotations

from jobscout.config import settings
from jobscout.entitlements import record_usage, resolve_limits
from jobscout.relational import DuckDBRelationalStore


def test_admin_users_open_to_local_operator(client):  # noqa: ANN001
    users = client.get("/api/admin/users").json()["users"]
    ids = {u["id"] for u in users}
    assert settings.local_user_id in ids
    me = next(u for u in users if u["id"] == settings.local_user_id)
    assert me["is_admin"] is True
    assert "storage_bytes" in me and "usage_30d" in me


def test_admin_routes_403_for_non_admin_when_hosting(client, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(settings, "single_user_mode", False)
    # local user IS admin → still allowed
    assert client.get("/api/admin/users").status_code == 200
    # demote the local user → now 403
    client.app.state.relational_store.update_user(settings.local_user_id, is_admin=False)
    assert client.get("/api/admin/users").status_code == 403


def test_grant_premium_changes_resolved_limits(client):  # noqa: ANN001
    store = client.app.state.relational_store
    # Cap tailor at 1 globally; a normal account resolves to that.
    store.update_user(settings.local_user_id, plan="unlimited")
    assert resolve_limits(settings.local_user_id, store).per_day("tailor") is None  # uncapped

    # A limits_json override wins for the named metric.
    store.update_user(settings.local_user_id, plan="pro", limits_json='{"tailor_per_day": 5}')
    assert resolve_limits(settings.local_user_id, store).per_day("tailor") == 5


def test_malformed_limits_json_falls_back_to_defaults():
    store = DuckDBRelationalStore(":memory:")
    store.update_user(settings.local_user_id, limits_json="{not valid json")
    lim = resolve_limits(settings.local_user_id, store)  # must not raise
    assert lim.rate_limit_per_min == settings.rate_limit_per_min
    store.close()


def test_metering_records_without_enforcing(monkeypatch):  # noqa: ANN001
    store = DuckDBRelationalStore(":memory:")
    monkeypatch.setattr(settings, "usage_metering_enabled", True)
    monkeypatch.setattr(settings, "quota_enforced", False)  # monitor, don't cap
    for _ in range(3):
        record_usage(store, "u", "tailor")
    assert store.usage_rollup("u", "2000-01-01") == {"tailor": 3}  # recorded, never capped
    store.close()


def test_patch_unknown_user_404(client):  # noqa: ANN001
    assert client.patch("/api/admin/users/nope", json={"plan": "pro"}).status_code == 404


def test_whoami_reports_local_admin(client):  # noqa: ANN001
    me = client.get("/api/users/me").json()
    assert me["user_id"] == settings.local_user_id
    assert me["is_admin"] is True
