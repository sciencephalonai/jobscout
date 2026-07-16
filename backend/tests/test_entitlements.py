"""Per-account entitlements + usage-quota seam (dormant by default)."""

from __future__ import annotations

import pytest

from jobscout.config import settings
from jobscout.entitlements import (
    QuotaExceeded,
    check_quota,
    record_usage,
    resolve_limits,
)
from jobscout.relational import DuckDBRelationalStore


def test_resolve_limits_returns_global_defaults():
    lim = resolve_limits(settings.local_user_id)
    assert lim.rate_limit_per_min == settings.rate_limit_per_min
    assert lim.per_day("tailor") == settings.tailor_per_day  # None = unlimited


def test_quota_is_dormant_by_default():
    store = DuckDBRelationalStore(":memory:")
    # quota_enforced is False → no counting, no raising, ever.
    for _ in range(1000):
        check_quota(store, "u", "tailor")
        record_usage(store, "u", "tailor")
    assert store.get_usage("u", "tailor", "2026-07-15") == 0  # nothing recorded
    store.close()


def test_quota_enforces_per_account_limit_when_on(monkeypatch):  # noqa: ANN001
    store = DuckDBRelationalStore(":memory:")
    monkeypatch.setattr(settings, "quota_enforced", True)
    monkeypatch.setattr(settings, "tailor_per_day", 2)  # global default limit = 2

    for _ in range(2):
        check_quota(store, "u", "tailor")   # under limit → OK
        record_usage(store, "u", "tailor")
    with pytest.raises(QuotaExceeded):
        check_quota(store, "u", "tailor")   # 3rd → over limit

    # An unlimited account (None) is never capped, even with enforcement on.
    monkeypatch.setattr(settings, "tailor_per_day", None)
    for _ in range(50):
        check_quota(store, "vip", "tailor")
    store.close()
