"""Tests for ghost-risk + mislabeled-entry computed fields and their filters."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jobscout.models import GHOST_STALE_DAYS, Job
from jobscout.search import build_filters


def _job(**kw) -> Job:
    base = dict(job_id="x" * 16, source="ashby", title="Data Scientist", url="http://x/y")
    base.update(kw)
    return Job(**base)  # type: ignore[arg-type]


def _days_ago(n: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


# ── ghost_risk / posting_age_days ───────────────────────────────────────────
def test_ghost_low_for_fresh_direct_post() -> None:
    j = _job(posted_date=_days_ago(3), is_recruiter_post=False, posted_date_est=False)
    assert j.posting_age_days == 3
    assert j.ghost_risk == "low"


def test_ghost_high_for_very_stale() -> None:
    j = _job(posted_date=_days_ago(GHOST_STALE_DAYS + 10))
    assert j.ghost_risk == "high"


def test_ghost_medium_for_aging_posting() -> None:
    j = _job(posted_date=_days_ago(35))  # between 30 and 45 days
    assert j.ghost_risk == "medium"


def test_ghost_low_for_unknown_or_estimated_date() -> None:
    # Unknown/estimated dates are NOT treated as stale (avoid flooding the badge).
    assert _job(posted_date=None).ghost_risk == "low"
    assert _job(posted_date=None).posting_age_days is None
    assert _job(posted_date=_days_ago(2), posted_date_est=True).ghost_risk == "low"
    assert _job(posted_date=_days_ago(2), is_recruiter_post=True).ghost_risk == "low"


# ── mislabeled_entry ────────────────────────────────────────────────────────
def test_mislabeled_when_junior_title_demands_experience() -> None:
    assert _job(title="Junior Data Engineer", yoe_min=4).mislabeled_entry is True
    assert _job(title="Software Engineer", seniority="junior", yoe_min=3).mislabeled_entry is True
    assert _job(title="New Grad Analyst", yoe_min=5).mislabeled_entry is True


def test_not_mislabeled_when_entry_or_senior_consistent() -> None:
    assert _job(title="Junior Data Engineer", yoe_min=0).mislabeled_entry is False
    assert _job(title="Junior Data Engineer", yoe_min=None).mislabeled_entry is False
    # Honestly-leveled senior role is not "mislabeled entry"
    assert _job(title="Senior Data Engineer", seniority="senior", yoe_min=6).mislabeled_entry is False


# ── filters compile to a Weaviate clause ────────────────────────────────────
def test_exclude_ghost_and_true_entry_build_filters() -> None:
    assert build_filters(exclude_ghost=True) is not None
    assert build_filters(true_entry_only=True) is not None
    assert build_filters() is None  # no filters → None
