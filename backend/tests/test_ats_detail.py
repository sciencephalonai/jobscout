"""Offline tests for the allow-listed ATS description resolver."""

from __future__ import annotations

from jobscout.adapters.ats_detail import fetch_ats_description


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _Http:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self._status = status_code
        self.urls: list[str] = []

    def get(self, url, params=None, *, api_source=False, headers=None):
        self.urls.append(url)
        return _Resp(self._payload, self._status)


def test_greenhouse_url_resolves_content():
    http = _Http({"content": "&lt;p&gt;Build models&lt;/p&gt;"})
    desc = fetch_ats_description("https://boards.greenhouse.io/databricks/jobs/12345", http)
    assert desc == "<p>Build models</p>"
    assert http.urls == ["https://boards-api.greenhouse.io/v1/boards/databricks/jobs/12345"]


def test_lever_url_resolves_description():
    http = _Http({"description": "<p>Ship code</p>"})
    desc = fetch_ats_description(
        "https://jobs.lever.co/acme/1b2f3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d", http
    )
    assert desc == "<p>Ship code</p>"
    assert http.urls[0].startswith("https://api.lever.co/v0/postings/acme/")


def test_workday_url_resolves_cxs_detail():
    http = _Http({"jobPostingInfo": {"jobDescription": "<p>Train networks</p>"}})
    desc = fetch_ats_description(
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/US-CA/ML_JR1", http
    )
    assert desc == "<p>Train networks</p>"
    assert http.urls == [
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA/ML_JR1"
    ]


def test_unknown_host_returns_none_without_calls():
    http = _Http({})
    assert fetch_ats_description("https://careers.example.com/jobs/1", http) is None
    assert http.urls == []


def test_failures_degrade_to_none():
    assert fetch_ats_description("https://boards.greenhouse.io/x/jobs/1", _Http({}, 404)) is None
    assert fetch_ats_description("https://boards.greenhouse.io/weird-path", _Http({})) is None
    assert fetch_ats_description("", _Http({})) is None


def test_workday_url_without_locale_segment_resolves():
    """Simplify-feed Workday URLs skip the /en-US/ locale segment."""
    http = _Http({"jobPostingInfo": {"jobDescription": "<p>Analyze data</p>"}})
    desc = fetch_ats_description(
        "https://homedepot.wd5.myworkdayjobs.com/careerdepot/job/ATLANTA/Associate-DS_R1", http
    )
    assert desc == "<p>Analyze data</p>"
    assert http.urls == [
        "https://homedepot.wd5.myworkdayjobs.com/wday/cxs/homedepot/careerdepot/job/ATLANTA/Associate-DS_R1"
    ]
