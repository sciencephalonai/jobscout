"""Tests for the company registry (DuckDB companies table + CRUD)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from jobscout.models import Company
from jobscout.relational import RelationalStore


@pytest.fixture()
def store():
    path = Path(tempfile.mktemp(suffix=".duckdb"))
    s = RelationalStore(db_path=str(path))
    yield s
    s.close()
    path.unlink(missing_ok=True)


def test_upsert_and_get(store):
    store.upsert_company(Company(slug="stripe", ats="greenhouse", name="Stripe",
                                 tier="Mid-Size Tech", open_roles=483, known_h1b_sponsor=True))
    c = store.get_company("greenhouse", "stripe")
    assert c is not None
    assert c.name == "Stripe" and c.open_roles == 483 and c.known_h1b_sponsor is True


def test_upsert_is_idempotent_update(store):
    store.upsert_company(Company(slug="ramp", ats="ashby", name="Ramp", open_roles=100))
    store.upsert_company(Company(slug="ramp", ats="ashby", name="Ramp", open_roles=111))
    assert store.get_company("ashby", "ramp").open_roles == 111
    assert len(store.list_companies()) == 1  # not duplicated


def test_filters(store):
    store.upsert_company(Company(slug="stripe", ats="greenhouse", name="Stripe",
                                 tier="Mid-Size Tech", known_h1b_sponsor=True, enabled=True))
    store.upsert_company(Company(slug="amazon", ats="none", name="Amazon",
                                 tier="FAANG + Top Tech", direct_apply_only=True, enabled=False))
    assert [c.name for c in store.list_companies(tier="FAANG + Top Tech")] == ["Amazon"]
    assert [c.name for c in store.list_companies(h1b_sponsor=True)] == ["Stripe"]
    assert [c.name for c in store.list_companies(direct_apply_only=True)] == ["Amazon"]
    # enabled_companies = reachable watchlist only
    assert [c.name for c in store.enabled_companies()] == ["Stripe"]


def test_touch_updates_open_roles_and_last_checked(store):
    store.upsert_company(Company(slug="x", ats="lever", name="X", open_roles=0))
    assert store.get_company("lever", "x").last_checked is None
    store.touch_company("lever", "x", 42)
    c = store.get_company("lever", "x")
    assert c.open_roles == 42 and c.last_checked is not None
