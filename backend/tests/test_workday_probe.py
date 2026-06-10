"""Tests for the Workday cap-exempt prober support: URL parsing, tenant merge,
and the company-name stamping that keeps cap-exempt Workday jobs from rendering
with a blank employer."""

from __future__ import annotations

from jobscout.adapters.workday import WorkdayAdapter, parse_workday_url
from jobscout.services.source_config import _merge_discovered


class TestParseWorkdayUrl:
    def test_en_us_locale(self):
        assert parse_workday_url(
            "https://cornell.wd1.myworkdayjobs.com/en-US/cornellCareerPage"
        ) == {"tenant": "cornell", "region": "wd1", "site": "cornellCareerPage"}

    def test_no_locale(self):
        assert parse_workday_url(
            "https://nyp.wd1.myworkdayjobs.com/nypcareers"
        ) == {"tenant": "nyp", "region": "wd1", "site": "nypcareers"}

    def test_trailing_slash_and_region(self):
        assert parse_workday_url(
            "https://nyuhs.wd12.myworkdayjobs.com/en-US/nyuhscareers1/"
        ) == {"tenant": "nyuhs", "region": "wd12", "site": "nyuhscareers1"}

    def test_non_workday_returns_none(self):
        assert parse_workday_url("https://example.com/jobs") is None

    def test_garbage_returns_none(self):
        assert parse_workday_url("not a url") is None
        assert parse_workday_url("https://myworkdayjobs.com") is None  # no tenant/region


class TestMergeTenants:
    def test_merges_and_dedups_by_tenant_site(self, monkeypatch, tmp_path):
        discovered = tmp_path / "sources.discovered.yaml"
        discovered.write_text(
            "sources:\n"
            "  workday:\n"
            "    tenants:\n"
            "      - {tenant: umd, region: wd1, site: UMCP, type: university, name: UMD}\n"
            "      - {tenant: cornell, region: wd1, site: cornellCareerPage, type: university, name: Cornell}\n"
        )
        monkeypatch.chdir(tmp_path)
        cfg = {"sources": {"workday": {"enabled": True, "tenants": [
            {"tenant": "cornell", "region": "wd1", "site": "cornellCareerPage", "type": "university"},
        ]}}}
        merged = _merge_discovered(cfg)
        tenants = merged["sources"]["workday"]["tenants"]
        sites = sorted(t["site"] for t in tenants)
        assert sites == ["UMCP", "cornellCareerPage"]  # umd added, cornell not duplicated


class TestWorkdayCompanyName:
    def _adapter(self):
        return WorkdayAdapter(tenants=[], fetch_descriptions=False)

    def test_name_becomes_company(self):
        raw = self._adapter()._build_raw(
            {"title": "Data Engineer", "externalPath": "/job/x", "bulletFields": ["R1"]},
            "cornell.wd1.myworkdayjobs.com", "https://cornell.wd1.myworkdayjobs.com/wday/cxs/cornell/site",
            "site", "university", "us", "Cornell University", None,
        )
        assert raw is not None
        assert raw["company"] == "Cornell University"
        assert raw["employer_type"] == "university"

    def test_blank_name_keeps_company_none(self):
        raw = self._adapter()._build_raw(
            {"title": "Data Engineer", "externalPath": "/job/x", "bulletFields": ["R1"]},
            "h", "c", "site", "university", "us", "", None,
        )
        assert raw is not None
        assert raw["company"] is None

    def test_normalize_tenants_reads_name(self):
        a = WorkdayAdapter(tenants=[
            {"tenant": "umd", "site": "UMCP", "type": "university", "name": "University of Maryland"},
        ])
        assert a.tenants[0]["name"] == "University of Maryland"
        assert a.tenants[0]["region"] == "wd1"  # default
