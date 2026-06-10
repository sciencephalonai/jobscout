"""Unit test for near-duplicate repost collapse (api.main._dedupe_jobs)."""

from __future__ import annotations

from jobscout.api.main import _dedupe_jobs
from jobscout.models import Job


def _job(jid, source, title, company):  # noqa: ANN001, ANN202
    return Job(job_id=jid, source=source, title=title, url=f"http://x/{jid}", company=company)


def test_collapses_same_role_keeps_authoritative_source():
    jobs = [
        _job("a", "jobspy", "Data Engineer (Remote)", "Acme Inc"),
        _job("b", "greenhouse", "Data Engineer", "Acme"),
        _job("c", "lever", "ML Engineer", "Beta"),
    ]
    out = _dedupe_jobs(jobs)
    assert len(out) == 2
    de = next(j for j in out if "Data Engineer" in j.title)
    assert de.source == "greenhouse"          # most authoritative wins over jobspy
    assert de.duplicate_count == 1
    assert de.also_on == ["jobspy"]


def test_distinct_roles_untouched():
    jobs = [
        _job("a", "greenhouse", "Backend Engineer", "Acme"),
        _job("b", "greenhouse", "Frontend Engineer", "Acme"),
    ]
    out = _dedupe_jobs(jobs)
    assert len(out) == 2
    assert all(j.duplicate_count == 0 for j in out)
