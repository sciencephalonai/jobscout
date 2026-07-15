"""Offline tests for the SimplifyJobs new-grad feed adapter (no network)."""

from __future__ import annotations

from jobscout.adapters.simplify import SimplifyAdapter
from jobscout.normalize import raw_to_job


class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    def get(self, url, params=None, *, api_source=False, headers=None):
        self.calls += 1
        return _FakeResp(self._payload)


def _listing(**over):
    base = {
        "id": "abc-1",
        "company_name": "Databricks",
        "title": "Software Engineer, New Grad",
        "locations": ["Mountain View, CA"],
        "url": "https://boards.greenhouse.io/databricks/jobs/1",
        "active": True,
        "is_visible": True,
        "sponsorship": "Other",
        "date_posted": 1783165593,
    }
    base.update(over)
    return base


_PAYLOAD = [
    _listing(),
    _listing(id="cit", title="Software Engineer I",
             sponsorship="U.S. Citizenship is Required"),
    _listing(id="nospon", title="Software Engineer II",
             sponsorship="Does Not Offer Sponsorship"),
    _listing(id="dead", title="Software Engineer III", active=False),
    _listing(id="hidden", title="Software Engineer IV", is_visible=False),
    _listing(id="offtopic", title="Field Marketing Technician"),
    _listing(id="multi", title="Data Engineer, New Grad",
             locations=["Seattle, WA", "Bellevue, WA"], date_posted=1783165000),
]


def test_filters_and_normalizes():
    out = list(SimplifyAdapter().search(["software engineer", "data engineer"],
                                        None, 10, None, _FakeHttp(_PAYLOAD)))
    ids = {j["source_job_id"] for j in out}
    # citizenship-required, no-sponsorship, inactive, hidden, off-topic all dropped
    assert ids == {"abc-1", "multi"}
    j = next(x for x in out if x["source_job_id"] == "abc-1")
    assert j["company"] == "Databricks"
    assert j["location"] == "Mountain View, CA"
    assert j["new_grad_program"] is True
    assert j["posted_date"].startswith("20")
    m = next(x for x in out if x["source_job_id"] == "multi")
    assert m["location"] == "Seattle, WA; Bellevue, WA"


def test_new_grad_flag_survives_raw_to_job():
    raw = next(iter(SimplifyAdapter().search(["software engineer"], None, 5, None,
                                             _FakeHttp(_PAYLOAD))))
    job = raw_to_job(raw, source="simplify")
    assert job.new_grad_program is True
    assert job.source == "simplify"


def test_results_wanted_and_newest_first():
    out = list(SimplifyAdapter().search(["engineer"], None, 1, None, _FakeHttp(_PAYLOAD)))
    assert len(out) == 1
    # date_posted 1783165593 (abc-1) is newer than 1783165000 (multi)
    assert out[0]["source_job_id"] == "abc-1"


def test_bad_payload_yields_nothing():
    assert list(SimplifyAdapter().search(["x"], None, 5, None, _FakeHttp({"not": "a list"}))) == []
