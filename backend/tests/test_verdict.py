"""Tests for jobscout.verdict — the deterministic Apply/Flag/Reject engine.

These assert the hard-disqualifier rules, the critical "sponsorship not stated
is NOT a reject" nuance, and the cap-exempt-first priority ordering.
"""

from __future__ import annotations

from jobscout.models import Job, UserProfile, Verdict
from jobscout.verdict import match_key, priority_key, score


def _job(**overrides: object) -> Job:
    """Build a Job with sensible, benign defaults; override per test."""
    base: dict[str, object] = {
        "job_id": "abc123",
        "source": "greenhouse",
        "title": "Data Scientist",
        "url": "https://example.com/job",
        "company": "Acme",
        "skills": ["python", "sql"],
        "seniority": "mid",
        "yoe_min": 2,
        "visa_sponsorship": "yes",
        "security_clearance": "none",
        "citizenship_required": False,
        "employer_type": "for_profit",
        "cap_exempt": "no",
        "remote_mode": "remote",
    }
    base.update(overrides)
    return Job(**base)  # type: ignore[arg-type]


def _profile(**overrides: object) -> UserProfile:
    base: dict[str, object] = {
        "label": "test",
        "target_titles": ["data scientist", "ml engineer"],
        "seniority_max": "mid",
        "yoe_max": 3,
        "needs_sponsorship": True,
        "skills": ["python", "sql", "pytorch"],
    }
    base.update(overrides)
    return UserProfile(**base)  # type: ignore[arg-type]


# ── Hard disqualifiers ────────────────────────────────────────────────────────

def test_explicit_no_sponsorship_is_reject() -> None:
    v = score(_job(visa_sponsorship="no"), _profile(needs_sponsorship=True))
    assert v.verdict == "reject"
    assert any("sponsorship" in r.lower() for r in v.red_flags)


def test_clearance_required_is_reject() -> None:
    v = score(_job(security_clearance="required"), _profile(reject_clearance=True))
    assert v.verdict == "reject"


def test_citizenship_required_is_reject() -> None:
    v = score(_job(citizenship_required=True), _profile(reject_citizenship_only=True))
    assert v.verdict == "reject"


def test_high_yoe_is_reject() -> None:
    v = score(_job(yoe_min=6), _profile(yoe_max=3))
    assert v.verdict == "reject"


def test_intern_only_is_reject() -> None:
    v = score(_job(seniority="intern"), _profile())
    assert v.verdict == "reject"


def test_excluded_company_is_reject() -> None:
    v = score(_job(company="BadCorp"), _profile(excluded_companies=["badcorp"]))
    assert v.verdict == "reject"


# ── The key nuance: missing sponsorship signal is NOT a reject ────────────────

def test_sponsorship_not_mentioned_is_flag_not_reject() -> None:
    v = score(_job(visa_sponsorship="not_mentioned"), _profile(needs_sponsorship=True))
    assert v.verdict == "flag"
    assert any("verify" in r.lower() or "not stated" in r.lower() for r in v.red_flags)


def test_sponsorship_unclear_is_flag_not_reject() -> None:
    v = score(_job(visa_sponsorship="unclear"), _profile(needs_sponsorship=True))
    assert v.verdict == "flag"


def test_no_sponsorship_needed_ignores_visa() -> None:
    # User who does not need sponsorship should not be flagged on visa at all.
    v = score(_job(visa_sponsorship="no"), _profile(needs_sponsorship=False))
    assert v.verdict != "reject"


# ── Borderline + happy path ──────────────────────────────────────────────────

def test_borderline_yoe_is_flag() -> None:
    # One year above the ceiling but under the hard cap → surfaced as a flag.
    v = score(_job(yoe_min=4), _profile(yoe_max=3, needs_sponsorship=False))
    assert v.verdict == "flag"


def test_strong_match_is_apply() -> None:
    v = score(
        _job(
            title="Machine Learning Engineer",
            skills=["python", "pytorch"],
            visa_sponsorship="yes",
            seniority="mid",
        ),
        _profile(needs_sponsorship=True),
    )
    assert v.verdict == "apply"
    assert v.score > 0.5


def test_recruiter_post_adds_red_flag() -> None:
    v = score(
        _job(is_recruiter_post=True, visa_sponsorship="yes"),
        _profile(needs_sponsorship=True),
    )
    assert any("recruiter" in r.lower() for r in v.red_flags)


def test_gaps_report_unmatched_jd_skills() -> None:
    v = score(
        _job(skills=["python", "rust", "kubernetes"], visa_sponsorship="yes"),
        _profile(skills=["python"], needs_sponsorship=False),
    )
    assert "rust" in v.gaps
    assert "kubernetes" in v.gaps
    assert "python" not in v.gaps


# ── Priority ordering ────────────────────────────────────────────────────────

def test_cap_exempt_sorts_before_for_profit() -> None:
    cap_exempt = score(
        _job(employer_type="university", cap_exempt="likely", visa_sponsorship="yes"),
        _profile(needs_sponsorship=True),
    )
    for_profit = score(
        _job(employer_type="for_profit", cap_exempt="no", visa_sponsorship="yes"),
        _profile(needs_sponsorship=True),
    )
    ordered = sorted([for_profit, cap_exempt], key=priority_key)
    assert ordered[0].cap_exempt == "likely"


# ── Best-Match ordering (match_key) ──────────────────────────────────────────

def _verdict(score_val: float, cap: str = "unknown", v: str = "flag") -> Verdict:
    return Verdict(job_id="x", verdict=v, score=score_val, cap_exempt=cap)


def test_match_key_orders_higher_fit_first() -> None:
    high = _verdict(0.82)
    low = _verdict(0.31)
    assert sorted([low, high], key=match_key)[0].score == 0.82


def test_match_key_cap_exempt_breaks_ties() -> None:
    # Same rounded match % → cap-exempt wins the tie.
    plain = _verdict(0.70, cap="no")
    capx = _verdict(0.70, cap="likely")
    ordered = sorted([plain, capx], key=match_key)
    assert ordered[0].cap_exempt == "likely"


# ── Scoring fixes: job-coverage ceiling + semantic blend ─────────────────────

def test_full_coverage_scores_near_perfect() -> None:
    # Job fully covered by the profile (incl. a synonym ml↔machine learning) should
    # now reach ~100% — not the old ~70% ceiling caused by dividing by profile size.
    v = score(
        _job(title="Data Scientist", skills=["python", "sql", "machine learning"],
             seniority="mid", visa_sponsorship="yes"),
        _profile(target_titles=["data scientist"], seniority_max="mid",
                 skills=["python", "sql", "ml", "pandas", "numpy", "statistics", "aws"],
                 needs_sponsorship=False),
    )
    assert v.score >= 0.9
    assert "machine learning" in v.matched


def test_semantic_blend_is_optional_and_bounded() -> None:
    job = _job(skills=["python"], visa_sponsorship="yes")
    prof = _profile(skills=["python"], needs_sponsorship=False)
    base = score(job, prof)                       # deterministic only
    blended = score(job, prof, semantic=1.0)      # with perfect semantic
    assert 0.0 <= base.score <= 1.0
    assert 0.0 <= blended.score <= 1.0
    assert blended.score >= base.score           # semantic 1.0 can only help
