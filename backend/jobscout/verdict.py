"""Deterministic verdict / fit-scoring engine.

Given a :class:`~jobscout.models.Job` (already LLM-enriched) and a
:class:`~jobscout.models.UserProfile`, decide whether the user should **apply**,
**flag** (surface but verify), or **reject** the role — and explain why.

This is a *pure function* layer. It encodes the kind of hard-disqualifier and
fit rules a personalized job-search agent would apply, but as deterministic
predicates over enriched fields rather than per-query LLM calls. That makes it
cheap, testable, and explainable (every verdict carries its ``reasons`` and
``red_flags``).

Key nuance preserved from real-world sponsorship screening: a *missing* /
*unclear* sponsorship signal is NOT a rejection. When a user needs sponsorship
and the posting simply doesn't mention it, the role is surfaced as a **flag**
("verify sponsorship"), never rejected.
"""

from __future__ import annotations

from typing import Literal

from jobscout.models import Job, UserProfile, Verdict
from jobscout.skills import skills_overlap

# Seniority ordering — lower index is more junior. Used for proximity scoring
# and the seniority ceiling.
_SENIORITY_RANK: dict[str, int] = {
    "intern": 0, "junior": 1, "mid": 2, "senior": 3, "staff": 4, "lead": 4,
    "principal": 5, "manager": 5, "director": 6, "vp": 7, "c_level": 8,
}

# Years-of-experience at or above which a role is rejected outright regardless
# of the profile's own ceiling (matches "reject 5+ YoE with no exception").
_HARD_YOE_CAP = 5

# Score weights (sum to 1.0). When semantic similarity is unavailable (no resume
# embedding) its weight is redistributed pro-rata over the rest — see `score`.
_W_SKILLS = 0.40
_W_TITLE = 0.25
_W_SEMANTIC = 0.20
_W_SENIORITY = 0.10
_W_REMOTE = 0.05

# Minimum fit score for an "apply" verdict (absent any forcing flag).
_APPLY_THRESHOLD = 0.5


def _norm(s: str) -> str:
    return s.strip().lower()


def _title_score(job: Job, profile: UserProfile) -> float:
    """Graded title fit: 1.0 for an exact/substring target match, else the best
    proportional word overlap (shared words / target words) across targets."""
    if not profile.target_titles:
        return 0.5  # no preference expressed → neutral
    title = _norm(job.title or "")
    if not title:
        return 0.0
    title_words = set(title.split())
    best = 0.0
    for target in profile.target_titles:
        t = _norm(target)
        if not t:
            continue
        if t in title or title in t:
            return 1.0
        t_words = set(t.split())
        if t_words:
            best = max(best, len(t_words & title_words) / len(t_words))
    return best


def _skill_score(job: Job, profile: UserProfile) -> tuple[float, list[str], list[str]]:
    """Return (job-coverage fraction, matched JD skills, gap JD skills).

    Job-coverage = of THIS job's required skills, the fraction the profile supports
    (fuzzy/synonym-aware via jobscout.skills). This answers "how well do I meet this
    role's needs" — full coverage → 1.0 — instead of diluting by the resume's whole
    skill inventory. Truthfulness rule preserved: a skill is matched only if the
    resume genuinely supports it; the rest are gaps. Tiny job-skill lists (<3) are
    damped toward neutral so a single lucky match can't fake a perfect score.
    """
    job_skills = [s for s in (job.skills or []) if s]
    profile_skills = [s for s in (profile.skills or []) if s]
    if not job_skills or not profile_skills:
        return 0.5, [], sorted({s.strip() for s in job_skills})[:8]  # nothing to compare → neutral

    matched, gaps = skills_overlap(job_skills, profile_skills)
    coverage = len(matched) / len(job_skills)
    # Damp confidence when the job lists very few skills (noisy denominator).
    if len(job_skills) < 3:
        coverage = 0.5 + (coverage - 0.5) * (len(job_skills) / 3)
    return coverage, matched[:12], gaps[:8]


def _seniority_score(job: Job, profile: UserProfile) -> float:
    """1.0 at/under the ceiling, 0.5 one rank above, 0.0 further above."""
    if job.seniority == "unclear":
        return 0.6
    job_rank = _SENIORITY_RANK.get(job.seniority)
    max_rank = _SENIORITY_RANK.get(profile.seniority_max)
    if job_rank is None or max_rank is None:
        return 0.6
    if job_rank <= max_rank:
        return 1.0
    if job_rank == max_rank + 1:
        return 0.5
    return 0.0


def _remote_score(job: Job, profile: UserProfile) -> float:
    """1.0 if the work mode satisfies the preference (or no preference)."""
    if profile.remote_preference == "any":
        return 1.0
    if job.remote_mode == "unknown":
        return 0.5
    return 1.0 if job.remote_mode == profile.remote_preference else 0.0


def score(job: Job, profile: UserProfile, semantic: float | None = None) -> Verdict:
    """Produce a :class:`Verdict` for *job* under *profile*.

    *semantic* is an optional resume↔job cosine similarity (0–1). When provided it
    contributes ``_W_SEMANTIC`` of the fit; when ``None`` (no resume embedding) that
    weight is redistributed pro-rata over the deterministic terms, so the score still
    sums to 1.0 and remains comparable.
    """
    reasons: list[str] = []
    red_flags: list[str] = []

    # ── Hard disqualifiers (any one → reject) ──────────────────────────────
    hard_rejects: list[str] = []

    if profile.needs_sponsorship and job.visa_sponsorship == "no":
        hard_rejects.append("Explicitly no visa sponsorship")
    if profile.reject_clearance and job.security_clearance == "required":
        hard_rejects.append("Requires a security clearance")
    if profile.reject_citizenship_only and job.citizenship_required:
        hard_rejects.append("Requires US citizenship / GC / ITAR eligibility")
    if job.yoe_min is not None and job.yoe_min >= _HARD_YOE_CAP:
        hard_rejects.append(f"Requires {job.yoe_min}+ years of experience")
    if job.seniority == "intern":
        hard_rejects.append("Internship-only role")
    if job.company and _norm(job.company) in {_norm(c) for c in profile.excluded_companies}:
        hard_rejects.append(f"Excluded company ({job.company})")

    # YoE borderline band: above the profile ceiling but under the hard cap.
    borderline_yoe = (
        job.yoe_min is not None
        and not hard_rejects
        and job.yoe_min > profile.yoe_max
    )
    if borderline_yoe and job.yoe_min is not None and job.yoe_min > profile.yoe_max + 1:
        hard_rejects.append(
            f"Requires {job.yoe_min}+ years (well above target {profile.yoe_max})"
        )

    if hard_rejects:
        return Verdict(
            job_id=job.job_id,
            verdict="reject",
            score=0.0,
            reasons=[],
            red_flags=hard_rejects,
            gaps=[],
            cap_exempt=job.cap_exempt,
        )

    # ── Soft fit score ─────────────────────────────────────────────────────
    title_s = _title_score(job, profile)
    skill_s, matched, gaps = _skill_score(job, profile)
    sen_s = _seniority_score(job, profile)
    rem_s = _remote_score(job, profile)

    # Deterministic terms always present; semantic optional.
    terms: list[tuple[float, float]] = [
        (_W_SKILLS, skill_s),
        (_W_TITLE, title_s),
        (_W_SENIORITY, sen_s),
        (_W_REMOTE, rem_s),
    ]
    if semantic is not None:
        terms.append((_W_SEMANTIC, max(0.0, min(1.0, semantic))))
    total_w = sum(w for w, _ in terms)  # 1.0 with semantic, 0.8 without
    fit = sum(w * v for w, v in terms) / total_w  # renormalize so it sums to 1.0

    if title_s >= 1.0:
        reasons.append("Title matches a target role")
    if skill_s >= 0.5:
        reasons.append("Strong skill overlap with profile")
    if sen_s >= 1.0:
        reasons.append("Seniority within target range")
    if job.cap_exempt in ("yes", "likely"):
        reasons.append(f"Likely H-1B cap-exempt ({job.employer_type})")

    # ── Forcing flags (surface, but do not auto-apply) ─────────────────────
    force_flag = False
    if profile.needs_sponsorship and job.visa_sponsorship in ("not_mentioned", "unclear"):
        red_flags.append("Sponsorship not stated — verify before applying")
        force_flag = True
    if borderline_yoe and job.yoe_min is not None:
        red_flags.append(f"Slightly above target YoE ({job.yoe_min})")
        force_flag = True
    if job.is_recruiter_post:
        red_flags.append("Recruiter/aggregator post — prefer the direct employer")

    verdict_label: Literal["apply", "flag"] = (
        "apply" if (fit >= _APPLY_THRESHOLD and not force_flag) else "flag"
    )

    return Verdict(
        job_id=job.job_id,
        verdict=verdict_label,
        score=round(fit, 3),
        reasons=reasons,
        red_flags=red_flags,
        matched=matched,
        gaps=gaps,
        cap_exempt=job.cap_exempt,
    )


# Ranking keys for the prompt's output ordering: cap-exempt first, then by
# verdict (apply > flag > reject), then by descending fit score.
_CAP_EXEMPT_RANK = {"yes": 0, "likely": 0, "unknown": 1, "no": 2}
_VERDICT_RANK = {"apply": 0, "flag": 1, "reject": 2}


def priority_key(verdict: Verdict) -> tuple[int, int, float]:
    """Sort key implementing 'cap-exempt first → apply/flag → best fit'."""
    return (
        _CAP_EXEMPT_RANK.get(verdict.cap_exempt, 1),
        _VERDICT_RANK.get(verdict.verdict, 1),
        -verdict.score,
    )


def match_key(verdict: Verdict) -> tuple[int, int, int]:
    """Sort key for 'Best Match': highest match % first, then cap-exempt, then verdict.

    Match % is the rounded fit score the UI shows, so ordering matches what the user
    sees. Cap-exempt breaks ties only when two jobs round to the same percentage (a
    small visa-aware nudge), then apply > flag > reject.
    """
    return (
        -round(verdict.score * 100),
        _CAP_EXEMPT_RANK.get(verdict.cap_exempt, 1),
        _VERDICT_RANK.get(verdict.verdict, 1),
    )
