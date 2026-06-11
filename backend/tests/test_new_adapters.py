"""Offline tests for the cap-exempt source adapters and their plumbing.

Covers the Workable / Workday / RSS adapters (with fake HTTP clients so no
network is touched), the per-company employer_type stamping on Greenhouse/Lever,
the raw_to_job employer_type passthrough, the derive_cap_exempt helper, and the
CompliantHttpClient.post() compliance gate.
"""

from __future__ import annotations

import pytest

from jobscout.adapters.ashby import AshbyAdapter
from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError
from jobscout.adapters.greenhouse import GreenhouseAdapter, normalize_company_entries
from jobscout.adapters.jobrightai import JobrightAIAdapter
from jobscout.adapters.lever import LeverAdapter
from jobscout.adapters.recruitee import RecruiteeAdapter
from jobscout.adapters.rippling import RipplingAdapter
from jobscout.adapters.rss import RssAdapter
from jobscout.adapters.smartrecruiters import SmartRecruitersAdapter
from jobscout.adapters.workable import WorkableAdapter
from jobscout.adapters.workday import WorkdayAdapter
from jobscout.enrich import derive_cap_exempt
from jobscout.normalize import raw_to_job


class FakeResp:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeHttpGet:
    """Serves a JSON payload on the first get(), then empties (loop terminator)."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def get(self, url, params=None, *, api_source=False):
        self.calls += 1
        if self.calls == 1:
            return FakeResp(self._payload)
        return FakeResp([] if isinstance(self._payload, list) else {})


class FakeHttpPostGet:
    """post() serves a payload once (then empty); get() serves a detail payload."""

    def __init__(self, post_payload, get_payload):
        self.post_payload = post_payload
        self.get_payload = get_payload
        self.post_calls = 0
        self.get_calls = 0

    def post(self, url, json=None, *, api_source=False, headers=None):
        self.post_calls += 1
        if self.post_calls == 1:
            return FakeResp(self.post_payload)
        return FakeResp({"total": 0, "jobPostings": []})

    def get(self, url, params=None, *, api_source=False):
        self.get_calls += 1
        return FakeResp(self.get_payload)


class FakeHttpListThenDetail:
    """get() serves a listing payload on the first call, then a detail object.

    Models Rippling: one GET for the board listing, then one GET per job detail.
    """

    def __init__(self, listing, detail):
        self.listing = listing
        self.detail = detail
        self.calls = 0

    def get(self, url, params=None, *, api_source=False):
        self.calls += 1
        return FakeResp(self.listing if self.calls == 1 else self.detail)


class FakeHttpText:
    """Serves raw text (for RSS) once, then empty."""

    def __init__(self, text):
        self._text = text
        self.calls = 0

    def get(self, url, params=None, *, api_source=False):
        self.calls += 1
        return FakeResp(text=self._text if self.calls == 1 else "")


# ── Workable ──────────────────────────────────────────────────────────────────

def test_workable_parses_and_stamps_employer_type():
    # Shape mirrors the REAL apply.workable.com widget API: location fields are
    # top-level (country/city/state) plus a `locations` array carrying countryCode.
    payload = {
        "name": "Braven",
        "jobs": [{
            "title": "Data Scientist",
            "url": "https://apply.workable.com/j/ABC123",
            "shortcode": "ABC123",
            "telecommuting": False,
            "country": "United States",
            "city": "Chicago",
            "state": "Illinois",
            "locations": [{"country": "United States", "countryCode": "US",
                           "city": "Chicago", "region": "Illinois"}],
            "published_on": "2026-05-01",
            "description": "<p>Build models</p>",
        }],
    }
    adapter = WorkableAdapter(accounts=[{"token": "braven", "type": "nonprofit"}])
    jobs = list(adapter.search(["data"], None, 5, None, FakeHttpGet(payload)))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Scientist"
    assert j["url"].startswith("https://apply.workable.com/")
    assert j["employer_type"] == "nonprofit"
    assert j["company"] == "Braven"
    assert j["country"] == "US"
    assert "Chicago" in j["location"]


def test_workable_remote_flag_from_telecommuting():
    payload = {
        "name": "Braven",
        "jobs": [{
            "title": "Data Analyst", "url": "https://apply.workable.com/j/R1",
            "shortcode": "R1", "telecommuting": True,
            "country": "United States", "city": "Remote",
            "locations": [{"countryCode": "US"}],
        }],
    }
    adapter = WorkableAdapter(accounts=[{"token": "braven", "type": "nonprofit"}])
    jobs = list(adapter.search(["data"], None, 5, None, FakeHttpGet(payload)))
    assert jobs and jobs[0]["remote"] == "remote"


# ── Workday ───────────────────────────────────────────────────────────────────

def test_workday_parses_paginates_and_fetches_description():
    post_payload = {
        "total": 1,
        "jobPostings": [{
            "title": "Machine Learning Engineer",
            "externalPath": "/job/New-York/ML-Engineer_R1",
            "locationsText": "New York, NY",
            "postedOn": "Posted 3 Days Ago",
            "bulletFields": ["R1"],
        }],
    }
    get_payload = {"jobPostingInfo": {"jobDescription": "<p>Train models</p>"}}
    adapter = WorkdayAdapter(tenants=[
        {"tenant": "cornell", "region": "wd1", "site": "cornellCareerPage", "type": "university"}
    ])
    http = FakeHttpPostGet(post_payload, get_payload)
    jobs = list(adapter.search(["engineer"], None, 5, None, http))  # title-substring match
    assert len(jobs) == 1
    j = jobs[0]
    assert j["employer_type"] == "university"
    assert j["url"] == "https://cornell.wd1.myworkdayjobs.com/en-US/cornellCareerPage/job/New-York/ML-Engineer_R1"
    assert j["source_job_id"] == "R1"
    assert j["description"] and "Train models" in j["description"]
    assert j["posted_date"] == "3 Days Ago"  # "Posted " prefix stripped
    # Curated US tenant stamps country so bare campus-name locations aren't dropped.
    assert j["country"] == "us"


def test_workday_country_defaults_us_for_bare_campus_location():
    post_payload = {"total": 1, "jobPostings": [{
        "title": "Research Aide", "externalPath": "/job/Ithaca/Aide_R9",
        "locationsText": "Ithaca (Main Campus)",  # no US token on its own
        "postedOn": "Posted Today", "bulletFields": ["R9"],
    }]}
    adapter = WorkdayAdapter(
        tenants=[{"tenant": "cornell", "region": "wd1",
                  "site": "CornellCareerPage", "type": "university"}],
        fetch_descriptions=False,
    )
    http = FakeHttpPostGet(post_payload, {})
    jobs = list(adapter.search([], None, 5, None, http))
    assert jobs and jobs[0]["country"] == "us"


def test_workday_listings_only_when_descriptions_disabled():
    post_payload = {"total": 1, "jobPostings": [{
        "title": "Data Engineer", "externalPath": "/job/x_R2",
        "locationsText": "Remote", "postedOn": "Posted Today", "bulletFields": ["R2"],
    }]}
    adapter = WorkdayAdapter(
        tenants=[{"tenant": "nyp", "region": "wd1", "site": "nypcareers", "type": "hospital"}],
        fetch_descriptions=False,
    )
    http = FakeHttpPostGet(post_payload, {"jobPostingInfo": {"jobDescription": "x"}})
    jobs = list(adapter.search([], None, 5, None, http))
    assert jobs and jobs[0]["description"] is None
    assert http.get_calls == 0  # no detail fetch


# ── Ashby ─────────────────────────────────────────────────────────────────────

def test_ashby_parses_real_shape_and_stamps_employer_type():
    # Shape mirrors the REAL api.ashbyhq.com posting API (verified live: ramp).
    payload = {
        "jobs": [{
            "id": "34413f8d-26bf",
            "title": "Data Scientist",
            "location": "New York, NY (HQ)",
            "employmentType": "FullTime",
            "jobUrl": "https://jobs.ashbyhq.com/ramp/34413f8d-26bf",
            "applyUrl": "https://jobs.ashbyhq.com/ramp/34413f8d-26bf/application",
            "publishedAt": "2026-04-07T17:12:35.753+00:00",
            "isListed": True,
            "isRemote": False,
            "descriptionPlain": "Build models",
        }],
    }
    adapter = AshbyAdapter(companies=[{"token": "ramp", "type": "for_profit"}])
    jobs = list(adapter.search(["data"], None, 5, None, FakeHttpGet(payload)))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Scientist"
    assert j["url"].startswith("https://jobs.ashbyhq.com/ramp/")
    assert j["employer_type"] == "for_profit"
    assert j["source_job_id"] == "34413f8d-26bf"
    assert "New York" in j["location"]
    assert j["description"] == "Build models"


def test_ashby_skips_unlisted_and_filters_keyword():
    payload = {"jobs": [
        {"id": "1", "title": "Data Scientist", "jobUrl": "https://jobs.ashbyhq.com/x/1",
         "isListed": False, "publishedAt": "2026-04-01T00:00:00Z"},
        {"id": "2", "title": "Chef", "jobUrl": "https://jobs.ashbyhq.com/x/2",
         "isListed": True, "publishedAt": "2026-04-01T00:00:00Z"},
    ]}
    adapter = AshbyAdapter(companies=["x"])
    jobs = list(adapter.search(["data"], None, 5, None, FakeHttpGet(payload)))
    assert jobs == []  # first is unlisted, second fails the keyword filter


# ── Rippling ──────────────────────────────────────────────────────────────────

_RIPPLING_LISTING = [{
    "uuid": "107a4fae-87ea-4ffc-96f7-36aad67a95fc",
    "name": "Machine Learning Engineer",
    "department": {"id": "Technology", "label": "Technology"},
    "url": "https://ats.rippling.com/tavernresearch/jobs/107a4fae-87ea-4ffc-96f7-36aad67a95fc",
    "workLocation": {"label": "Chicago, IL", "id": "Chicago, IL"},
}]
_RIPPLING_DETAIL = {
    "uuid": "107a4fae-87ea-4ffc-96f7-36aad67a95fc",
    "name": "Machine Learning Engineer",
    "description": {"company": "<p>About Tavern</p>", "role": "<p>Build ML</p>"},
    "createdOn": "2026-05-12T07:38:16.902000-07:00",
    "companyName": "Tavern Research",
    "employmentType": {"label": "SALARIED_FT", "id": "Salaried, full-time"},
    "workLocations": ["Chicago, IL"],
}


def test_rippling_parses_listing_and_fetches_detail():
    adapter = RipplingAdapter(companies=[{"token": "tavernresearch", "type": "for_profit"}])
    http = FakeHttpListThenDetail(_RIPPLING_LISTING, _RIPPLING_DETAIL)
    jobs = list(adapter.search(["machine"], None, 5, None, http))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Machine Learning Engineer"
    assert j["url"].startswith("https://ats.rippling.com/tavernresearch/jobs/")
    assert j["source_job_id"] == "107a4fae-87ea-4ffc-96f7-36aad67a95fc"
    assert j["employer_type"] == "for_profit"
    assert j["company"] == "Tavern Research"
    assert j["description"] and "Build ML" in j["description"]
    assert j["posted_date"] is not None
    assert http.calls == 2  # listing + one detail


def test_rippling_listings_only_when_descriptions_disabled():
    adapter = RipplingAdapter(
        companies=[{"token": "tavernresearch", "type": "nonprofit"}],
        fetch_descriptions=False,
    )
    http = FakeHttpListThenDetail(_RIPPLING_LISTING, _RIPPLING_DETAIL)
    jobs = list(adapter.search([], None, 5, None, http))
    assert jobs and jobs[0]["description"] is None
    assert jobs[0]["company"] == "tavernresearch"  # slug fallback (no detail fetch)
    assert jobs[0]["employer_type"] == "nonprofit"
    assert http.calls == 1  # listing only, no detail


def test_rippling_keyword_filter_excludes_nonmatching():
    adapter = RipplingAdapter(companies=["tavernresearch"])
    http = FakeHttpListThenDetail(_RIPPLING_LISTING, _RIPPLING_DETAIL)
    jobs = list(adapter.search(["nurse"], None, 5, None, http))
    assert jobs == []


# ── Recruitee ─────────────────────────────────────────────────────────────────

def test_recruitee_parses_real_shape_and_stamps_employer_type():
    # Shape mirrors the real {company}.recruitee.com/api/offers/ response.
    payload = {"offers": [{
        "id": 123, "title": "Data Engineer",
        "careers_url": "https://acme.recruitee.com/o/data-engineer",
        "location": "New York, NY", "description": "<p>Build pipelines</p>",
        "requirements": "<p>SQL</p>", "created_at": "2026-05-01 12:00:00 UTC",
    }]}
    adapter = RecruiteeAdapter(companies=[{"token": "acme", "type": "nonprofit"}])
    jobs = list(adapter.search(["data"], None, 5, None, FakeHttpGet(payload)))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["url"].startswith("https://acme.recruitee.com/")
    assert j["employer_type"] == "nonprofit"
    assert j["source_job_id"] == "123"
    assert j["description"] and "Build pipelines" in j["description"] and "SQL" in j["description"]


def test_recruitee_keyword_filter_excludes():
    payload = {"offers": [{"id": 1, "title": "Chef", "careers_url": "https://x.recruitee.com/o/1",
                           "created_at": "2026-05-01 12:00:00 UTC"}]}
    adapter = RecruiteeAdapter(companies=["x"])
    assert list(adapter.search(["engineer"], None, 5, None, FakeHttpGet(payload))) == []


# ── SmartRecruiters ───────────────────────────────────────────────────────────

def test_smartrecruiters_list_plus_detail():
    list_payload = {"content": [{
        "id": "abc", "name": "Software Engineer",
        "company": {"name": "Visa"},
        "location": {"city": "Austin", "region": "TX", "country": "us"},
        "releasedDate": "2026-04-23T16:54:54.835Z",
    }]}
    detail_payload = {
        "postingUrl": "https://jobs.smartrecruiters.com/Visa/abc",
        "jobAd": {"sections": {"jobDescription": {"text": "<p>Build payments</p>"}}},
    }
    adapter = SmartRecruitersAdapter(companies=[{"token": "Visa", "type": "for_profit"}])
    http = FakeHttpListThenDetail(list_payload, detail_payload)
    jobs = list(adapter.search(["software"], None, 5, None, http))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Software Engineer"
    assert j["company"] == "Visa"
    assert j["url"] == "https://jobs.smartrecruiters.com/Visa/abc"
    assert j["employer_type"] == "for_profit"
    assert "Austin" in j["location"]
    assert j["description"] and "Build payments" in j["description"]


def test_smartrecruiters_listings_only_constructs_url_no_detail():
    list_payload = {"content": [{
        "id": "xyz", "name": "Data Analyst", "company": {"name": "Visa"},
        "location": {"city": "Remote", "country": "us"},
        "releasedDate": "2026-04-23T16:54:54.835Z",
    }]}
    adapter = SmartRecruitersAdapter(companies=["Visa"], fetch_descriptions=False)
    http = FakeHttpListThenDetail(list_payload, {})
    jobs = list(adapter.search(["data"], None, 5, None, http))
    assert jobs and jobs[0]["description"] is None
    assert jobs[0]["url"] == "https://jobs.smartrecruiters.com/Visa/xyz"  # constructed fallback
    assert http.calls == 1  # listing only — no per-posting detail fetch


# ── RSS ───────────────────────────────────────────────────────────────────────

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>HigherEdJobs - Data Science</title>
<item>
  <title>Data Scientist</title>
  <link>https://www.higheredjobs.com/details.cfm?JobCode=1</link>
  <description>Build models for research</description>
  <pubDate>Thu, 01 May 2026 00:00:00 GMT</pubDate>
  <author>Some University</author>
  <guid>job-123</guid>
</item>
</channel></rss>"""


def test_rss_parses_and_stamps_employer_type():
    adapter = RssAdapter(feeds=[
        {"url": "https://www.higheredjobs.com/rss/categoryFeed.cfm?catID=290", "type": "university"}
    ])
    jobs = list(adapter.search(["data"], None, 5, None, FakeHttpText(_RSS)))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Scientist"
    assert j["url"].startswith("https://www.higheredjobs.com/")
    assert j["employer_type"] == "university"


def test_rss_keyword_filter_excludes_nonmatching():
    adapter = RssAdapter(feeds=[{"url": "https://x/feed", "type": "university"}])
    jobs = list(adapter.search(["nurse"], None, 5, None, FakeHttpText(_RSS)))
    assert jobs == []


# ── Greenhouse / Lever employer_type stamping ────────────────────────────────

def test_greenhouse_stamps_employer_type():
    payload = {"jobs": [{
        "id": 1, "title": "Data Scientist", "absolute_url": "https://gh.co/1",
        "location": {"name": "Remote"}, "content": "Build", "updated_at": "2026-05-01T00:00:00Z",
    }]}
    adapter = GreenhouseAdapter(companies=[{"token": "givewell", "type": "nonprofit"}])
    jobs = list(adapter.search(["data"], None, 5, None, FakeHttpGet(payload)))
    assert jobs and jobs[0]["employer_type"] == "nonprofit"


def test_lever_stamps_employer_type():
    payload = [{
        "id": "1", "text": "Data Scientist", "hostedUrl": "https://lever.co/1",
        "createdAt": 1714521600000, "descriptionPlain": "Build",
        "categories": {"location": "Remote"},
    }]
    adapter = LeverAdapter(companies=[{"token": "wri", "type": "nonprofit"}])
    jobs = list(adapter.search(["data"], None, 5, None, FakeHttpGet(payload)))
    assert jobs and jobs[0]["employer_type"] == "nonprofit"


def test_plain_token_strings_still_work():
    # Backward compat: a plain token string → employer_type "unclear".
    assert normalize_company_entries(["acme"]) == [("acme", "unclear")]
    assert normalize_company_entries([{"token": "x", "type": "university"}]) == [("x", "university")]


# ── Passthrough + derivation ─────────────────────────────────────────────────

def test_raw_to_job_employer_type_passthrough():
    job = raw_to_job({"title": "X", "url": "https://x/1", "employer_type": "university"}, "rss")
    assert job.employer_type == "university"


def test_raw_to_job_invalid_employer_type_defaults_unclear():
    job = raw_to_job({"title": "X", "url": "https://x/1", "employer_type": "bogus"}, "rss")
    assert job.employer_type == "unclear"


@pytest.mark.parametrize("etype,expected", [
    ("university", "likely"),
    ("hospital", "likely"),
    ("nonprofit", "likely"),
    ("government", "likely"),
    ("for_profit", "no"),
    ("unclear", "unknown"),
])
def test_derive_cap_exempt(etype, expected):
    assert derive_cap_exempt(etype) == expected


# ── JobRightAI (staged aggregator) ────────────────────────────────────────────

def test_jobrightai_parses_next_data():
    import json as _json
    job_list = [{
        "jobResult": {
            "jobId": "J1", "jobTitle": "Data Engineer",
            "url": "https://jobright.ai/jobs/info/J1",
            "jobLocation": "New York, NY", "workModel": "Remote",
            "jobSummary": "Build pipelines", "publishTime": "2026-05-01T00:00:00",
            "h1BStatus": "H1B Sponsor Likely", "minYearsOfExperience": 1,
            "salaryDesc": "$120K/yr - $160K/yr",
        },
        "companyResult": {"companyName": "Acme", "companySize": "51-200 employees"},
    }]
    html_text = (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        + _json.dumps({"props": {"pageProps": {"jobList": job_list}}})
        + "</script></body></html>"
    )
    adapter = JobrightAIAdapter()
    jobs = list(adapter.search(["data", "engineer"], None, 3, None, FakeHttpText(html_text)))
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["url"].startswith("https://jobright.ai/")
    assert j["company"] == "Acme"
    assert j["remote"] == "remote"
    assert j["visa_sponsorship"] == "yes"   # mapped from "H1B Sponsor Likely"


def test_jobrightai_empty_keywords_yields_nothing():
    assert list(JobrightAIAdapter().search([], None, 3, None, FakeHttpText(""))) == []


# ── store: reading back migrated objects with None-valued new fields ──────────

def test_props_to_job_tolerates_none_enrichment_fields():
    """Weaviate returns objects migrated before the new fields existed with the
    key PRESENT and value None. _props_to_job must coerce those to valid Literal
    defaults instead of passing None to the Job model (which would crash)."""
    from jobscout.store import _props_to_job

    props = {
        "job_id": "abc", "title": "Data Scientist", "url": "https://x/1",
        "source": "ashby",
        # New enrichment fields present-but-None (the migration case):
        "security_clearance": None, "employer_type": None, "cap_exempt": None,
        "citizenship_required": None, "is_recruiter_post": None,
    }
    job = _props_to_job(props, job_id="abc")
    assert job.security_clearance == "unclear"
    assert job.employer_type == "unclear"
    assert job.cap_exempt == "unknown"
    assert job.citizenship_required is False
    assert job.is_recruiter_post is False


# ── post() compliance gate ───────────────────────────────────────────────────

def test_post_enforces_blocklist():
    client = CompliantHttpClient()
    try:
        client._blocked_domains.add("blocked.example.com")
        with pytest.raises(DomainBlockedError):
            client.post("https://blocked.example.com/wday/cxs/x/y/jobs", json={})
    finally:
        client.close()


def test_himalayas_parses_filters_and_unescapes_real_shape():
    """Himalayas adapter: real API field names, title keyword filter, html unescape.

    Uses a full 20-record page (the API's hard cap) so the
    ``len(jobs) < _PAGE_SIZE`` end-of-data check does NOT fire on page 1 —
    guarding the regression where _PAGE_SIZE=100 killed pagination immediately.
    """
    from jobscout.adapters.himalayas import _PAGE_SIZE, HimalayasAdapter

    assert _PAGE_SIZE == 20  # must match the API's real per-page cap

    jobs = [
        {
            "title": f"Marketing Specialist {i}",
            "companyName": "Acme",
            "applicationLink": f"https://himalayas.app/jobs/{i}",
            "description": "<p>Build &amp; ship</p>",
            "pubDate": 1700000000,
            "guid": f"g{i}",
            "currency": "USD",
        }
        for i in range(_PAGE_SIZE)
    ]
    jobs[3]["title"] = "Senior Data Engineer"
    jobs[7]["title"] = "Platform Engineer"

    ad = HimalayasAdapter()
    rows = list(
        ad.search(
            keywords=["engineer"],
            location=None,
            results_wanted=10,
            since=None,
            http=FakeHttpGet({"jobs": jobs}),
        )
    )

    assert {r["title"] for r in rows} == {"Senior Data Engineer", "Platform Engineer"}
    r = rows[0]
    assert r["url"].startswith("https://himalayas.app/jobs/")
    assert r["company"] == "Acme"
    assert r["remote"] == "remote"
    assert "&amp;" not in (r["description"] or "")  # html entities unescaped
