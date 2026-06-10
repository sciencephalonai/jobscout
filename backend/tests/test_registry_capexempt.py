"""Tests for projecting cap-exempt employers into the company registry, so
"Get companies" (the watchlist refresh) covers them — including Workday."""

from __future__ import annotations

from jobscout.models import Company
from jobscout.relational import RelationalStore
from jobscout.services.ingestion_service import _REFRESH_ADAPTER
from jobscout.services.registry import register_cap_exempt_companies

_CFG = {
    "sources": {
        "greenhouse": {"companies": [
            {"token": "mozilla", "type": "nonprofit"},
            {"token": "stripe"},  # for-profit → excluded
        ]},
        "workable": {"accounts": [{"token": "braven", "type": "nonprofit"}]},
        "workday": {"tenants": [
            {"tenant": "umd", "region": "wd1", "site": "UMCP",
             "type": "university", "name": "University of Maryland"},
        ]},
    }
}


def test_sync_creates_capexempt_rows_only():
    r = RelationalStore(":memory:")
    try:
        n = register_cap_exempt_companies(r, _CFG)
        assert n == 3  # mozilla, braven, umd (NOT stripe)
        rows = {(c.ats, c.slug): c for c in r.list_companies()}
        assert ("greenhouse", "mozilla") in rows
        assert ("workable", "braven") in rows
        assert ("workday", "umd") in rows
        assert ("greenhouse", "stripe") not in rows  # for-profit not synced
        assert rows[("greenhouse", "mozilla")].employer_type == "nonprofit"
        assert rows[("greenhouse", "mozilla")].cap_exempt_hint == "likely"
    finally:
        r.close()


def test_workday_row_keeps_region_site_name():
    r = RelationalStore(":memory:")
    try:
        register_cap_exempt_companies(r, _CFG)
        umd = r.get_company("workday", "umd")
        assert umd is not None
        assert umd.region == "wd1" and umd.site == "UMCP"
        assert umd.name == "University of Maryland"
        assert umd.employer_type == "university"
    finally:
        r.close()


def test_idempotent():
    r = RelationalStore(":memory:")
    try:
        register_cap_exempt_companies(r, _CFG)
        register_cap_exempt_companies(r, _CFG)  # again
        umd = [c for c in r.list_companies() if c.slug == "umd"]
        assert len(umd) == 1  # keyed by (ats, slug) — no duplicate
    finally:
        r.close()


def test_refresh_adapter_covers_workday_and_workable():
    # "Get companies" can now build these adapters for cap-exempt refresh.
    for ats in ("greenhouse", "lever", "ashby", "workable", "workday"):
        assert ats in _REFRESH_ADAPTER


def test_region_site_roundtrip_upsert():
    r = RelationalStore(":memory:")
    try:
        r.upsert_company(Company(ats="workday", slug="cornell", name="Cornell University",
                                 employer_type="university", region="wd1", site="cornellCareerPage"))
        c = r.get_company("workday", "cornell")
        assert c is not None and c.region == "wd1" and c.site == "cornellCareerPage"
    finally:
        r.close()
