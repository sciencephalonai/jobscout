"""Offline parsing tests for the API source adapters.

Each adapter's ``search()`` takes a ``CompliantHttpClient``-like ``http`` object;
we feed a fake one that returns a canned payload so no network is touched. We
assert the adapter yields normalized raw dicts carrying at least ``title`` and
``url`` (the fields ``normalize.raw_to_job`` requires).
"""

import pytest

from jobscout.adapters.arbeitnow import ArbeitnowAdapter
from jobscout.adapters.greenhouse import GreenhouseAdapter
from jobscout.adapters.jobicy import JobicyAdapter
from jobscout.adapters.lever import LeverAdapter
from jobscout.adapters.remoteok import RemoteOKAdapter
from jobscout.adapters.remotive import RemotiveAdapter
from jobscout.adapters.themuse import TheMuseAdapter
from jobscout.adapters.workingnomads import WorkingNomadsAdapter


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeHttp:
    """Stand-in for CompliantHttpClient that returns a canned payload."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def get(self, url, params=None, *, api_source=False):
        self.calls += 1
        # Paginated adapters call repeatedly; serve the payload only once so
        # the loop terminates (subsequent pages look "empty").
        if self.calls == 1:
            return FakeResponse(self._payload)
        empty = [] if isinstance(self._payload, list) else {}
        return FakeResponse(empty)


# (adapter factory, canned payload) — payloads match each adapter's real reads.
CASES = {
    "remotive": (
        lambda: RemotiveAdapter(),
        {"jobs": [{
            "id": 1, "url": "https://r.co/1", "title": "Backend Engineer",
            "company_name": "Acme", "publication_date": "2026-05-01T00:00:00",
            "candidate_required_location": "USA", "description": "<p>Build APIs</p>",
            "salary": "", "tags": ["python"],
        }]},
    ),
    "arbeitnow": (
        lambda: ArbeitnowAdapter(),
        {"data": [{
            "slug": "abc", "title": "Backend Engineer", "company_name": "Acme",
            "description": "<p>Build</p>", "remote": True, "url": "https://a.co/abc",
            "tags": [], "job_types": [], "location": "Remote", "created_at": 1714521600,
        }]},
    ),
    "jobicy": (
        lambda: JobicyAdapter(),
        {"jobs": [{
            "id": 2, "url": "https://j.co/2", "jobTitle": "Backend Engineer",
            "companyName": "Acme", "jobGeo": "USA", "jobLevel": "Senior",
            "jobExcerpt": "Build", "jobDescription": "<p>Build</p>",
            "pubDate": "2026-05-01 00:00:00",
        }]},
    ),
    "remoteok": (
        lambda: RemoteOKAdapter(),
        [
            {"legal": "RemoteOK API notice"},  # first element skipped
            {"id": 3, "slug": "be", "position": "Backend Engineer", "company": "Acme",
             "location": "Worldwide", "tags": ["python"], "description": "<p>Build</p>",
             "url": "https://remoteok.com/3", "date": "2026-05-01T00:00:00+00:00"},
        ],
    ),
    "workingnomads": (
        lambda: WorkingNomadsAdapter(),
        [{
            "url": "https://wn.co/123", "title": "Backend Engineer",
            "description": "<p>Build</p>", "company_name": "Acme",
            "category_name": "Development", "tags": "python", "location": "Anywhere",
            "pub_date": "2026-05-01T00:00:00",
        }],
    ),
    "themuse": (
        lambda: TheMuseAdapter(),
        {"page": 0, "page_count": 1, "results": [{
            "id": 4, "name": "Backend Engineer", "company": {"name": "Acme"},
            "locations": [{"name": "New York, NY"}], "contents": "<p>Build</p>",
            "refs": {"landing_page": "https://m.co/4"},
            "publication_date": "2026-05-01T00:00:00Z", "levels": [{"name": "Senior"}],
            "type": "external",
        }]},
    ),
    "greenhouse": (
        lambda: GreenhouseAdapter(companies=["acme"]),
        {"jobs": [{
            "id": 5, "title": "Backend Engineer", "absolute_url": "https://gh.co/5",
            "location": {"name": "San Francisco, CA"}, "content": "&lt;p&gt;Build&lt;/p&gt;",
            "updated_at": "2026-05-01T00:00:00-04:00",
        }]},
    ),
    "lever": (
        lambda: LeverAdapter(companies=["acme"]),
        [{
            "id": "6", "text": "Backend Engineer", "hostedUrl": "https://lever.co/6",
            "createdAt": 1714521600000, "descriptionPlain": "Build",
            "categories": {"location": "New York", "team": "Eng", "commitment": "Full-time"},
        }],
    ),
}


@pytest.mark.parametrize("name", list(CASES.keys()))
def test_adapter_parses_payload(name):
    factory, payload = CASES[name]
    adapter = factory()
    http = FakeHttp(payload)
    jobs = list(
        adapter.search(
            keywords=["engineer"], location=None, results_wanted=5, since=None, http=http
        )
    )
    assert jobs, f"{name}: expected at least one job parsed"
    first = jobs[0]
    assert first.get("title"), f"{name}: missing title"
    assert first.get("url"), f"{name}: missing url"


def test_remoteok_skips_legal_first_element():
    factory, payload = CASES["remoteok"]
    http = FakeHttp(payload)
    jobs = list(
        factory().search(keywords=[], location=None, results_wanted=5, since=None, http=http)
    )
    # The legal/metadata first element must not be yielded as a job.
    assert all("legal" not in j for j in jobs)
    assert jobs and jobs[0]["title"] == "Backend Engineer"
