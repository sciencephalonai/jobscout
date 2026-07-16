"""Tests for jobscout.verdict — the deterministic Apply/Flag/Reject engine.

These assert the hard-disqualifier rules, the critical "sponsorship not stated
is NOT a reject" nuance, and the cap-exempt-first priority ordering.
"""

from __future__ import annotations

import pytest

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

def test_four_year_non_entry_role_is_reject() -> None:
    # Four years is above the routine early-career bar unless the posting is
    # explicitly a junior/new-grad role with a loose numeric requirement.
    v = score(_job(yoe_min=4), _profile(yoe_max=3, needs_sponsorship=False))
    assert v.verdict == "reject"


def test_cap_exempt_sponsorship_silence_can_still_be_apply() -> None:
    v = score(
        _job(
            employer_type="university",
            cap_exempt="likely",
            visa_sponsorship="not_mentioned",
        ),
        _profile(needs_sponsorship=True),
    )
    assert v.verdict == "apply"
    assert any("positive sponsorship" in reason.lower() for reason in v.reasons)


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


def test_title_seniority_overrides_stale_unclear_enrichment() -> None:
    v = score(
        _job(
            title="Principal Data Scientist",
            seniority="unclear",
            yoe_min=None,
        ),
        _profile(seniority_max="junior", yoe_max=2, needs_sponsorship=False),
    )
    assert v.verdict == "reject"
    assert any("principal" in flag.lower() for flag in v.red_flags)


def test_senior_title_is_not_recommended_to_junior_profile() -> None:
    v = score(
        _job(
            title="Senior Machine Learning Engineer",
            seniority="unclear",
            yoe_min=None,
            skills=["python", "pytorch"],
        ),
        _profile(seniority_max="junior", yoe_max=2, needs_sponsorship=False),
        semantic=1.0,
    )
    assert v.verdict == "reject"  # semantic similarity cannot rescue seniority


def test_one_level_above_seniority_is_flag_never_apply() -> None:
    v = score(
        _job(seniority="mid", yoe_min=2),
        _profile(seniority_max="junior", yoe_max=2, needs_sponsorship=False),
    )
    assert v.verdict == "flag"
    assert any("seniority above" in flag.lower() for flag in v.red_flags)


def test_numbered_title_level_is_not_mistaken_for_junior() -> None:
    v = score(
        _job(title="Data Scientist II", seniority="unclear", yoe_min=None),
        _profile(seniority_max="junior", yoe_max=2, needs_sponsorship=False),
    )
    assert v.verdict == "flag"
    assert any("mid vs junior" in flag.lower() for flag in v.red_flags)


def test_unrelated_role_family_is_rejected_even_with_shared_skills() -> None:
    v = score(
        _job(
            title="Product Manager",
            seniority="junior",
            skills=["python", "sql"],
        ),
        _profile(
            target_titles=["data scientist"],
            seniority_max="junior",
            needs_sponsorship=False,
        ),
        semantic=1.0,
    )
    assert v.verdict == "reject"
    assert any("target roles" in flag.lower() for flag in v.red_flags)


@pytest.mark.parametrize(
    "title",
    [
        "Transplant Physician Liaison – Nashville, TN - 75% travel",
        "Medical Assistant - Pediatric Primary Care Clinic",
        "Mental Health Technician",
        "Certified Pharmacy Technician - Evening Shift",
        "Graduate Practical Nurse - Home Care Academy",
        "Registered Nurse, Women's Health Clinic",
        "Care Partner - Adult Float Pool",
        "Ophthalmic Technician PRN",
        "Scheduler",
    ],
)
def test_unrelated_clinical_titles_never_enter_technical_recommendations(
    title: str,
) -> None:
    verdict = score(
        _job(
            title=title,
            skills=[],
            description="Use Excel and Tableau to support hospital operations.",
            employer_type="hospital",
            cap_exempt="likely",
            enrichment_status="failed",
            seniority="unclear",
            yoe_min=None,
        ),
        _profile(
            target_titles=["data scientist", "machine learning engineer", "software engineer"],
            skills=["python", "excel", "tableau"],
            seniority_max="junior",
            yoe_max=2,
            needs_sponsorship=True,
        ),
        semantic=1.0,
    )

    assert verdict.verdict == "reject"
    assert verdict.recommendable is False
    assert any("target roles" in flag.lower() for flag in verdict.red_flags)


def test_zero_verified_skill_overlap_is_reject() -> None:
    v = score(
        _job(skills=["rust", "kubernetes"], seniority="junior"),
        _profile(skills=["python", "sql"], seniority_max="junior", needs_sponsorship=False),
        semantic=1.0,
    )
    assert v.verdict == "reject"
    assert v.matched == []


def test_missing_job_skill_evidence_is_flag_not_apply() -> None:
    v = score(
        _job(skills=[], seniority="junior"),
        _profile(seniority_max="junior", needs_sponsorship=False),
        semantic=1.0,
    )
    assert v.verdict == "flag"
    assert any("insufficient" in flag.lower() for flag in v.red_flags)


def test_full_resume_restores_truthful_skills_omitted_from_profile_top_n() -> None:
    verdict = score(
        _job(
            title="Junior Software Engineer",
            skills=["react", "typescript", "docker"],
            seniority="junior",
            yoe_min=1,
        ),
        _profile(
            target_titles=["software engineer"],
            skills=["python"],
            resume_text="Built a React and TypeScript application and deployed it with Docker.",
            seniority_max="junior",
            yoe_max=2,
            needs_sponsorship=False,
        ),
    )

    assert verdict.verdict == "apply"
    assert verdict.recommendable is True
    assert set(verdict.matched) == {"react", "typescript", "docker"}


def test_sponsorship_silence_is_still_recommendable_with_a_caveat() -> None:
    verdict = score(
        _job(
            title="Junior Data Scientist",
            seniority="junior",
            yoe_min=1,
            visa_sponsorship="not_mentioned",
        ),
        _profile(seniority_max="junior", yoe_max=2, needs_sponsorship=True),
    )

    assert verdict.verdict == "flag"
    assert verdict.recommendable is True
    assert any("sponsorship" in flag.lower() for flag in verdict.red_flags)


def test_failed_enrichment_cannot_hide_explicit_seven_year_requirement() -> None:
    verdict = score(
        _job(
            title="Software Engineer",
            seniority="unclear",
            yoe_min=None,
            enrichment_status="failed",
            description="Minimum qualifications: 7-10 years of software engineering experience.",
        ),
        _profile(
            target_titles=["software engineer"],
            seniority_max="junior",
            yoe_max=2,
            needs_sponsorship=False,
        ),
    )

    assert verdict.verdict == "reject"
    assert verdict.recommendable is False
    assert any("7+ years" in flag for flag in verdict.red_flags)


def test_degree_conditioned_experience_uses_candidate_degree_branch() -> None:
    verdict = score(
        _job(
            title="AI Engineer",
            seniority="unclear",
            yoe_min=None,
            enrichment_status="failed",
            description=(
                "Required qualifications: Master's degree and 3 years of experience "
                "or Bachelor's degree and 5 years of experience."
            ),
        ),
        _profile(
            target_titles=["ai engineer"],
            seniority_max="junior",
            yoe_max=2,
            needs_sponsorship=False,
            resume_text="Education: Master of Science in Data Science.",
        ),
    )

    assert verdict.verdict == "flag"
    assert verdict.recommendable is False
    assert any("3" in flag and "yoe" in flag.lower() for flag in verdict.red_flags)


def test_experience_level_field_is_checked_when_enrichment_missed_it() -> None:
    verdict = score(
        _job(
            title="Machine Learning Engineer",
            seniority="unclear",
            yoe_min=None,
            enrichment_status="done",
            description="Required qualifications — Work Experience: Experience Level: 3 years.",
        ),
        _profile(seniority_max="junior", yoe_max=2, needs_sponsorship=False),
    )

    assert verdict.verdict == "flag"
    assert verdict.recommendable is False


def test_failed_enrichment_with_unknown_experience_is_not_recommendable() -> None:
    verdict = score(
        _job(
            title="Software Engineer",
            seniority="unclear",
            yoe_min=None,
            enrichment_status="failed",
            description="Build Python and SQL services for our product.",
        ),
        _profile(
            target_titles=["software engineer"],
            seniority_max="junior",
            yoe_max=2,
            needs_sponsorship=False,
        ),
    )

    assert verdict.verdict == "flag"
    assert verdict.recommendable is False
    assert any("unverified" in flag.lower() for flag in verdict.red_flags)


def test_completed_enrichment_without_experience_but_strong_evidence_is_recommendable() -> None:
    """Unstated YoE on an enriched, strongly-matching, non-senior posting is a
    caveat, not a For You exclusion — blocking the most common posting shape
    starved junior feeds. Weak-evidence and pending/failed cases stay blocked
    (covered by the dedicated tests below)."""
    verdict = score(
        _job(
            title="Full Stack Software Engineer",
            seniority="unclear",
            yoe_min=None,
            enrichment_status="done",
            description="Build and ship full-stack consumer products in production.",
        ),
        _profile(
            target_titles=["software engineer", "full stack developer"],
            seniority_max="junior",
            yoe_max=2,
            needs_sponsorship=False,
        ),
    )

    assert verdict.verdict == "flag"
    assert verdict.recommendable is True
    assert any("experience level is not stated" in flag.lower() for flag in verdict.red_flags)


def test_engineer_one_is_an_explicit_entry_signal_when_years_are_unstated() -> None:
    verdict = score(
        _job(
            title="Software Engineer I",
            seniority="unclear",
            yoe_min=None,
            enrichment_status="done",
        ),
        _profile(
            target_titles=["software engineer"],
            seniority_max="junior",
            yoe_max=2,
            needs_sponsorship=False,
        ),
    )

    assert verdict.verdict == "apply"
    assert verdict.recommendable is True


def test_explicit_junior_role_can_survive_failed_enrichment_for_review() -> None:
    verdict = score(
        _job(
            title="Junior Data Scientist",
            seniority="junior",
            yoe_min=None,
            skills=[],
            enrichment_status="failed",
            description="Use Python and SQL to build analytical models.",
            visa_sponsorship="not_mentioned",
        ),
        _profile(
            target_titles=["data scientist"],
            skills=["python", "sql"],
            seniority_max="junior",
            yoe_max=2,
            needs_sponsorship=True,
        ),
    )

    assert verdict.verdict == "flag"
    assert verdict.recommendable is True


def test_known_work_mode_mismatch_is_not_recommendable() -> None:
    verdict = score(
        _job(remote_mode="onsite", country="us", location_raw="New York, NY"),
        _profile(remote_preference="remote", needs_sponsorship=False),
    )

    assert verdict.recommendable is False
    assert any("work mode" in flag.lower() for flag in verdict.red_flags)


def test_unsupported_central_specialty_is_flagged_not_rejected() -> None:
    v = score(
        _job(
            title="Embedded Software Engineer",
            seniority="junior",
            skills=["python", "sql"],
        ),
        _profile(
            target_titles=["software engineer"],
            seniority_max="junior",
            needs_sponsorship=False,
            resume_text="Built data pipelines and machine learning models.",
        ),
    )
    assert v.verdict == "flag"
    assert any("embedded" in flag.lower() for flag in v.red_flags)


def test_specialty_with_resume_evidence_can_apply() -> None:
    v = score(
        _job(
            title="Embedded Software Engineer",
            seniority="junior",
            skills=["python", "c++"],
        ),
        _profile(
            target_titles=["software engineer"],
            seniority_max="junior",
            needs_sponsorship=False,
            skills=["python", "c++", "embedded systems"],
            resume_text="Built embedded systems software on a microcontroller.",
        ),
    )
    assert v.verdict == "apply"


def test_security_specialty_requires_real_resume_evidence() -> None:
    verdict = score(
        _job(
            title="Software Engineer, Product Security",
            seniority="junior",
            yoe_min=1,
            skills=["python", "sql"],
        ),
        _profile(
            target_titles=["software engineer"],
            seniority_max="junior",
            yoe_max=2,
            needs_sponsorship=False,
            resume_text="Built data pipelines and full-stack analytics applications.",
        ),
    )

    assert verdict.verdict == "flag"
    assert verdict.recommendable is False
    assert any("cybersecurity" in flag.lower() for flag in verdict.red_flags)


def test_job_outside_profile_country_is_rejected() -> None:
    v = score(
        _job(country="vn", seniority="junior"),
        _profile(countries=["us"], seniority_max="junior", needs_sponsorship=False),
    )
    assert v.verdict == "reject"
    assert any("outside target countries" in flag.lower() for flag in v.red_flags)


def test_legacy_description_work_auth_is_rechecked_at_query_time() -> None:
    v = score(
        _job(
            visa_sponsorship="not_mentioned",
            description="Applicants must have permanent work authorization without sponsorship.",
        ),
        _profile(needs_sponsorship=True),
    )
    assert v.verdict == "reject"


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

def test_cap_exempt_sorts_before_for_profit_when_profile_prefers_it() -> None:
    cap_exempt = score(
        _job(employer_type="university", cap_exempt="likely", visa_sponsorship="yes"),
        _profile(needs_sponsorship=True),
    )
    for_profit = score(
        _job(employer_type="for_profit", cap_exempt="no", visa_sponsorship="yes"),
        _profile(needs_sponsorship=True),
    )
    ordered = sorted(
        [for_profit, cap_exempt], key=lambda verdict: priority_key(verdict, True)
    )
    assert ordered[0].cap_exempt == "likely"


def test_relevance_beats_cap_exempt_status_without_profile_preference() -> None:
    relevant = Verdict(
        job_id="relevant",
        verdict="flag",
        score=0.81,
        recommendable=True,
        cap_exempt="no",
    )
    unrelated = Verdict(
        job_id="nurse",
        verdict="reject",
        score=0.99,
        recommendable=False,
        cap_exempt="likely",
    )

    assert sorted([unrelated, relevant], key=priority_key)[0].job_id == "relevant"


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


def test_match_key_never_places_high_score_reject_before_apply() -> None:
    rejected = _verdict(0.99, cap="likely", v="reject")
    applied = _verdict(0.61, cap="no", v="apply")

    assert sorted([rejected, applied], key=match_key)[0].verdict == "apply"


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


# ── Curated new-grad feeds (no description / no skill text) ──────────────────


def test_curated_new_grad_without_skill_text_is_recommendable() -> None:
    """SimplifyJobs-style rows: explicit new-grad, title matches, but the feed
    carries no description — thin skill evidence must caveat, not bury."""
    job = _job(
        source="simplify",
        title="Data Scientist, New Grad",
        description=None,
        skills=[],
        seniority="unclear",
        yoe_min=None,
        visa_sponsorship="not_mentioned",
        known_h1b_sponsor=True,  # positive sponsorship signal keeps the caveat soft
        new_grad_program=True,
    )
    verdict = score(job, _profile(seniority_max="junior", yoe_max=2))
    assert verdict.verdict != "reject"
    assert verdict.recommendable is True
    assert any("verify requirements" in f.lower() for f in verdict.red_flags)


def test_no_skill_text_without_new_grad_flag_stays_blocked() -> None:
    job = _job(
        title="Data Scientist",
        description=None,
        skills=[],
        seniority="unclear",
        yoe_min=None,
        new_grad_program=False,
    )
    verdict = score(job, _profile(seniority_max="junior", yoe_max=2))
    assert verdict.recommendable is False


def test_new_grad_flag_cannot_rescue_an_off_target_title() -> None:
    job = _job(
        title="Field Marketing Technician, New Grad",
        description=None,
        skills=[],
        seniority="unclear",
        yoe_min=None,
        new_grad_program=True,
    )
    verdict = score(job, _profile(seniority_max="junior", yoe_max=2))
    assert verdict.recommendable is False


# ── Enriched, YoE-unstated postings for junior profiles (feed-starver fix) ───


def test_enriched_unstated_yoe_with_strong_evidence_is_recommendable() -> None:
    job = _job(
        title="Data Scientist",
        yoe_min=None,
        seniority="unclear",
        skills=["python", "sql", "pytorch"],
        enrichment_status="done",
        visa_sponsorship="yes",
    )
    verdict = score(job, _profile(seniority_max="junior", yoe_max=2))
    assert verdict.recommendable is True
    assert any("not stated" in f.lower() for f in verdict.red_flags)


def test_enriched_unstated_yoe_with_weak_evidence_stays_blocked() -> None:
    job = _job(
        title="Data Scientist",
        yoe_min=None,
        seniority="unclear",
        skills=["python", "haskell", "erlang", "cobol"],  # 1 match of 4 → weak
        enrichment_status="done",
        visa_sponsorship="yes",
    )
    verdict = score(job, _profile(seniority_max="junior", yoe_max=2))
    assert verdict.recommendable is False


def test_pending_enrichment_unstated_yoe_stays_blocked() -> None:
    job = _job(
        title="Data Scientist",
        yoe_min=None,
        seniority="unclear",
        skills=[],
        enrichment_status="pending",
        visa_sponsorship="yes",
    )
    verdict = score(job, _profile(seniority_max="junior", yoe_max=2))
    assert verdict.recommendable is False


# ── Defense-domain + enterprise-platform gates (V's 4-job triage escapes) ────


def test_weapons_domain_rejected_for_sponsorship_needing_profile() -> None:
    job = _job(
        title="Associate Software Engineer",
        company="Agile Development Group",
        description="Work on next-generation weapon systems, RF sensor technologies, "
                    "and unmanned air systems (UAS) radar software.",
        visa_sponsorship="not_mentioned",
        seniority="junior",
        yoe_min=0,
    )
    verdict = score(job, _profile(seniority_max="junior", yoe_max=2, needs_sponsorship=True,
                                  target_titles=["software engineer"]))
    assert verdict.verdict == "reject"
    assert any("defense/weapons" in f.lower() for f in verdict.red_flags)


def test_weapons_domain_only_flags_when_no_sponsorship_needed() -> None:
    job = _job(
        title="Software Engineer",
        description="Radar systems and electronic warfare software.",
        visa_sponsorship="not_mentioned",
        seniority="junior",
        yoe_min=0,
    )
    verdict = score(job, _profile(needs_sponsorship=False, target_titles=["software engineer"]))
    assert verdict.verdict != "reject"
    assert any("defense/weapons" in f.lower() for f in verdict.red_flags)


def test_oracle_hcm_specialist_title_blocked_without_evidence() -> None:
    job = _job(
        title="Software Engineer I - Oracle Cloud HCM - CET Services",
        description="Implement HCM Fusion Core modules and Redwood pages.",
        skills=["java", "sql"],
        seniority="junior",
        yoe_min=0,
        visa_sponsorship="yes",
    )
    verdict = score(job, _profile(seniority_max="junior", yoe_max=2,
                                  target_titles=["software engineer"]))
    assert verdict.recommendable is False
    assert any("no resume evidence" in f.lower() for f in verdict.red_flags)


def test_generic_entry_swe_unaffected_by_new_gates() -> None:
    job = _job(
        title="Product Software Engineer I",
        company="Disney",
        description="Backend APIs for sports products. Java, Git, unit testing, code reviews.",
        skills=["java", "sql"],
        seniority="junior",
        yoe_min=0,
        visa_sponsorship="yes",
    )
    verdict = score(job, _profile(seniority_max="junior", yoe_max=2, skills=["java", "sql", "python"],
                                  target_titles=["software engineer"]))
    assert verdict.verdict == "apply"


def test_defense_prime_rejected_even_with_keyword_free_description() -> None:
    """Feed-truncated JDs at defense primes omit the US-person boilerplate."""
    for company in ("L3Harris Technologies", "RTX", "Northrop Grumman", "SpaceX"):
        job = _job(
            title="Associate Software Engineer",
            company=company,
            description="Write software. Java, agile, unit tests.",
            visa_sponsorship="not_mentioned",
            seniority="junior",
            yoe_min=0,
        )
        verdict = score(job, _profile(seniority_max="junior", yoe_max=2,
                                      needs_sponsorship=True,
                                      target_titles=["software engineer"]))
        assert verdict.verdict == "reject", company
