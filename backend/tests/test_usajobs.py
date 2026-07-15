"""Offline tests for the USAJobs adapter (no network).

Covers normalization, the citizenship-only pre-filter, keyword title matching,
results_wanted honoring, and the employer_type=government stamp (→ cap_exempt
"likely" via derive_cap_exempt / raw_to_job).
"""

from __future__ import annotations

import pytest

from jobscout.adapters.usajobs import USAJobsAdapter
from jobscout.config import settings
from jobscout.enrich import derive_cap_exempt
from jobscout.normalize import raw_to_job


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeHttp:
    """Serves the payload once, then an empty result set (loop terminator)."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = 0
        self.last_headers = None

    def get(self, url, params=None, *, api_source=False, headers=None):
        self.calls += 1
        self.last_headers = headers
        if self.calls == 1:
            return _FakeResp(self._payload)
        return _FakeResp({"SearchResult": {"SearchResultItems": []}})


def _item(position_id, title, org, summary, city="Bethesda"):
    return {
        "MatchedObjectId": position_id,
        "MatchedObjectDescriptor": {
            "PositionID": position_id,
            "PositionTitle": title,
            "OrganizationName": org,
            "ApplyURI": [f"https://www.usajobs.gov/job/{position_id}"],
            "PositionURI": f"https://www.usajobs.gov/job/{position_id}",
            "PositionLocationDisplay": f"{city}, MD",
            "PositionLocation": [{"CityName": city}],
            "QualificationSummary": summary,
            "PublicationStartDate": "2026-06-15T00:00:00.0000",
            "PositionRemuneration": [{"MinimumRange": "90000", "MaximumRange": "120000"}],
            "UserArea": {"Details": {"JobSummary": "Join our research team."}},
        },
    }


_PAYLOAD = {
    "SearchResult": {
        "SearchResultItems": [
            _item("A1", "Data Scientist", "National Institutes of Health",
                  "Experience with Python and machine learning required."),
            _item("A2", "Data Scientist (Federal)", "Dept of Defense",
                  "Applicants must be United States citizens to be considered."),
            _item("A3", "Park Ranger", "National Park Service",
                  "Outdoor role, no data work."),
        ]
    }
}


@pytest.fixture
def _creds(monkeypatch):
    monkeypatch.setattr(settings, "usajobs_api_key", "test-key")
    monkeypatch.setattr(settings, "usajobs_email", "test@example.com")


def test_skips_without_creds(monkeypatch):
    monkeypatch.setattr(settings, "usajobs_api_key", "")
    monkeypatch.setattr(settings, "usajobs_email", "")
    http = _FakeHttp(_PAYLOAD)
    out = list(USAJobsAdapter().search(["data scientist"], None, 10, None, http))
    assert out == []
    assert http.calls == 0


def test_filters_and_normalizes(_creds):
    http = _FakeHttp(_PAYLOAD)
    out = list(USAJobsAdapter().search(["data scientist"], None, 10, None, http))

    # A2 dropped (citizenship-only), A3 dropped (title mismatch) → only A1 remains.
    assert len(out) == 1
    job = out[0]
    assert job["title"] == "Data Scientist"
    assert job["company"] == "National Institutes of Health"
    assert job["country"] == "us"
    assert job["city"] == "Bethesda"
    assert job["employer_type"] == "government"
    assert job["url"].startswith("https://www.usajobs.gov/job/A1")
    assert job["salary_min"] == 90000
    # Auth headers were passed through.
    assert http.last_headers["Authorization-Key"] == "test-key"
    assert http.last_headers["User-Agent"] == "test@example.com"


def test_government_stamp_is_cap_exempt_likely(_creds):
    http = _FakeHttp(_PAYLOAD)
    raw = next(iter(USAJobsAdapter().search(["data scientist"], None, 10, None, http)))
    job = raw_to_job(raw, source="usajobs")
    assert job.employer_type == "government"
    assert derive_cap_exempt(job.employer_type) == "likely"


def test_results_wanted_caps_output(_creds):
    # Three eligible DS items; results_wanted=1 stops after the first.
    payload = {
        "SearchResult": {
            "SearchResultItems": [
                _item("B1", "Data Scientist", "NIH", "Python and ML."),
                _item("B2", "Data Scientist II", "NSF", "Python and ML."),
                _item("B3", "Senior Data Scientist", "NASA", "Python and ML."),
            ]
        }
    }
    http = _FakeHttp(payload)
    out = list(USAJobsAdapter().search(["data scientist"], None, 1, None, http))
    assert len(out) == 1
