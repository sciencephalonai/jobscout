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

import html
import re
from functools import lru_cache
from typing import Literal

from jobscout.eligibility import detect_defense_domain, extract_work_authorization_evidence
from jobscout.models import Job, UserProfile, Verdict
from jobscout.normalize import is_us_job
from jobscout.skills import profile_skills_mentioned_in_text, skills_overlap

# Seniority ordering — lower index is more junior. Used for proximity scoring
# and the seniority ceiling.
_SENIORITY_RANK: dict[str, int] = {
    "intern": 0, "junior": 1, "mid": 2, "senior": 3, "staff": 4, "lead": 4,
    "principal": 5, "manager": 5, "director": 6, "vp": 7, "c_level": 8,
}

# Title text is the most dependable seniority signal for legacy records whose
# enrichment is missing or stale.  The patterns are deliberately narrow: they
# only inspect role-title words, never arbitrary JD prose.
_TITLE_SENIORITY: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("c_level", re.compile(r"\b(?:chief|ceo|cto|cio|cdo|cfo|cpo)\b", re.I)),
    ("vp", re.compile(r"\b(?:vice[ -]president|vp)\b", re.I)),
    ("director", re.compile(r"\b(?:director|head of)\b", re.I)),
    ("principal", re.compile(r"\bprincipal\b", re.I)),
    ("staff", re.compile(
        r"\b(?:staff|distinguished)\b|"
        r"\b(?:engineer|scientist|analyst|developer)\s+(?:iv|4)\b|\blevel\s+(?:iv|4)\b",
        re.I,
    )),
    ("manager", re.compile(r"\bmanager\b", re.I)),
    ("lead", re.compile(r"\b(?:lead|tech lead|technical lead)\b", re.I)),
    ("senior", re.compile(
        r"\b(?:senior|sr\.?)\b|"
        r"\b(?:engineer|scientist|analyst|developer)\s+(?:iii|3)\b|\blevel\s+(?:iii|3)\b",
        re.I,
    )),
    ("mid", re.compile(
        r"\bmid[ -]level\b|"
        r"\b(?:engineer|scientist|analyst|developer)\s+(?:ii|2)\b|\blevel\s+(?:ii|2)\b",
        re.I,
    )),
    ("junior", re.compile(
        r"\b(?:junior|jr\.?|entry[ -]level|new[ -]grad|associate)\b|"
        r"\b(?:engineer|scientist|analyst|developer)\s+(?:i|1)\b|\blevel\s+(?:i|1)\b",
        re.I,
    )),
    ("intern", re.compile(r"\b(?:intern|internship|co-op)\b", re.I)),
)

# Granular role families keep a broad vector/skill similarity from turning an
# unrelated job into an Apply verdict. A title can belong to multiple families
# (for example, "ML Research Engineer"), so overlap remains nuanced.
_ROLE_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ml_engineering", re.compile(
        r"\b(?:machine learning|ml|ai|artificial intelligence|deep learning|nlp|llm|"
        r"computer vision)\s+(?:engineer|developer)\b", re.I,
    )),
    ("data_science", re.compile(
        r"\b(?:data scientist|data science|applied scientist)\b", re.I,
    )),
    ("data_engineering", re.compile(
        r"\b(?:data engineer|analytics engineer|etl engineer|data platform engineer)\b", re.I,
    )),
    ("research", re.compile(
        r"\b(?:research engineer|research scientist|quantitative researcher)\b", re.I,
    )),
    ("analytics", re.compile(
        r"\b(?:data analyst|business intelligence|bi analyst|reporting analyst)\b", re.I,
    )),
    ("infrastructure", re.compile(
        r"\b(?:infrastructure|platform|site reliability|sre|devops|cloud|systems?)\s+"
        r"(?:engineer|developer|administrator|architect)\b", re.I,
    )),
    ("security", re.compile(
        r"\b(?:security|cybersecurity|infosec|soc)\s+(?:engineer|analyst|researcher|architect)\b",
        re.I,
    )),
    ("software", re.compile(
        r"\b(?:software|backend|front[ -]?end|full[ -]?stack|web|mobile|ios|android)\s+"
        r"(?:engineer|developer)\b", re.I,
    )),
    ("product", re.compile(r"\b(?:product manager|product owner)\b", re.I)),
    ("business_ops", re.compile(
        r"\b(?:business analyst|operations analyst|strategy analyst|consultant)\b", re.I,
    )),
    # Families below widen coverage so profiles targeting these get the same
    # role-type gate (an unknown-family profile weakens precision to title-score).
    ("qa", re.compile(
        r"\b(?:qa|quality assurance|test(?:ing)?|sdet|test automation)\s+"
        r"(?:engineer|analyst|developer)\b|\bsdet\b", re.I,
    )),
    ("embedded", re.compile(
        r"\b(?:embedded|firmware|fpga|rtl)\s+(?:engineer|developer)\b", re.I,
    )),
    ("solutions", re.compile(
        r"\b(?:solutions?|sales)\s+(?:architect|engineer)\b", re.I,
    )),
    ("design", re.compile(
        r"\b(?:product|ux|ui|visual|graphic|brand)\s+designer\b|\bux researcher\b", re.I,
    )),
)

# High-confidence licensed/direct-care occupations.  These titles deliberately
# sit outside the technical role-family taxonomy, so a fail-open "unknown
# family" must not let them ride shared words or embedding similarity into a
# data/software recommendation feed.  The title-fit check below still permits a
# profile that explicitly targets one of these occupations.
_OBVIOUS_UNRELATED_TITLES = re.compile(
    r"\b(?:"
    r"registered nurse|licensed practical nurse|licensed vocational nurse|"
    r"nurse practitioner|nurse manager|nursing assistant|graduate practical nurse|"
    r"medical assistant|physician assistant|pharmacist|pharmacy technician|"
    r"care partner|patient care technician|mental health technician|"
    r"radiologic technologist|respiratory therapist|physical therapist|"
    r"occupational therapist|clinical research coordinator"
    r")\b",
    re.I,
)

_HTML_TAG = re.compile(r"<[^>]+>")
_REQUIREMENT_CONTEXT = re.compile(
    r"\b(?:minimum qualifications?|required qualifications?|requirements?|qualifications?|"
    r"must have|required|candidate|you (?:have|bring|need)|proven track record|degree)\b",
    re.I,
)
_DEGREE_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (3, re.compile(r"\b(?:ph\.?\s*d\.?|doctorate|doctoral)\b", re.I)),
    (2, re.compile(r"\b(?:master(?:'s)?|m\.?\s*s\.?|m\.?\s*eng\.?|mba)\b", re.I)),
    (1, re.compile(r"\b(?:bachelor(?:'s)?|b\.?\s*s\.?|b\.?\s*eng\.?|b\.?tech\.?)\b", re.I)),
)
_YOE_BEFORE_EXPERIENCE = re.compile(
    r"(?P<prefix>\bat least\s+|\bminimum(?:\s+of)?\s+|\bmin\.?\s+)?"
    r"(?P<years>\d{1,2})\s*(?P<qualifier>\+|[-\u2013\u2014]\s*\d{1,2})?\s*"
    r"years?\s+(?:of\s+)?(?:[a-z][a-z0-9+/#.\-]*\s+){0,5}?experience\b",
    re.I,
)
_YOE_AFTER_EXPERIENCE = re.compile(
    r"\bexperience(?:\s+(?:of|level))?\s*:?\s*(?P<years>\d{1,2})\s*"
    r"(?P<qualifier>\+|[-\u2013\u2014]\s*\d{1,2})?\s*years?\b",
    re.I,
)

# Only central specialisations in the *title* trigger this guard. Missing
# evidence caps a role at Flag rather than rejecting it, which avoids penalising
# adjacent, learnable domains while keeping them out of the high-confidence
# For You shortlist.
_SPECIALTY_EVIDENCE: tuple[tuple[str, re.Pattern[str], tuple[str, ...]], ...] = (
    ("embedded/firmware", re.compile(r"\b(?:embedded|firmware|rtos)\b", re.I),
     ("embedded", "firmware", "rtos", "microcontroller")),
    ("genomics/bioinformatics", re.compile(
        r"\b(?:genomics?|bioinformatics|computational biology|rna-seq|ngs)\b", re.I,
    ), ("genomics", "bioinformatics", "computational biology", "rna-seq", "ngs")),
    ("quantitative finance", re.compile(
        r"\b(?:quantitative (?:trading|finance)|algorithmic trading|trade lifecycle)\b", re.I,
    ), ("quantitative finance", "quantitative trading", "algorithmic trading", "trade lifecycle")),
    ("cybersecurity", re.compile(r"\b(?:cybersecurity|security|penetration test)\b", re.I),
     ("cybersecurity", "security operations", "penetration testing", "infosec")),
    ("mobile", re.compile(r"\b(?:ios|android|mobile)\s+(?:engineer|developer)\b", re.I),
     ("ios", "android", "swift", "kotlin", "react native", "flutter")),
    # Enterprise-platform specialist roles (Oracle HCM/Fusion, SAP, Workday
    # config, ServiceNow, Salesforce) demand platform-specific experience a
    # generalist resume cannot honestly claim.
    ("enterprise platform (ERP/HCM/CRM)", re.compile(
        r"\b(?:oracle\s+(?:cloud\s+)?(?:hcm|fusion|erp)|sap(?:\s+\w+)?|peoplesoft|"
        r"workday\s+(?:hcm|integration|configuration)|servicenow|salesforce)\b", re.I,
    ), ("oracle hcm", "oracle fusion", "sap", "peoplesoft", "workday", "servicenow", "salesforce")),
)

# Years-of-experience at or above which a role is rejected outright regardless
# of the profile's own ceiling (matches "reject 5+ YoE with no exception").
_HARD_YOE_CAP = 5

# Score weights (sum to 1.0). When semantic similarity is unavailable (no resume
# embedding) its weight is redistributed pro-rata over the rest — see `score`.
_W_SKILLS = 0.35
_W_TITLE = 0.25
_W_SEMANTIC = 0.20
_W_SENIORITY = 0.10
_W_INTEREST = 0.05
_W_REMOTE = 0.05

# Minimum fit score for an "apply" verdict (absent any forcing flag).
_APPLY_THRESHOLD = 0.6

_US_COUNTRY_ALIASES = {
    "us", "usa", "u.s.", "u.s.a.", "united states",
    "united states of america", "america",
}


def _norm(s: str) -> str:
    return s.strip().lower()


def _title_seniority(title: str | None) -> str | None:
    """Return the highest explicit level advertised in *title*, if any."""
    matches = [
        label for label, pattern in _TITLE_SENIORITY if pattern.search(title or "")
    ]
    if not matches:
        return None
    return max(matches, key=lambda label: _SENIORITY_RANK[label])


def _effective_seniority(job: Job) -> str:
    """Use the stricter of structured enrichment and the visible job title."""
    title_level = _title_seniority(job.title)
    structured = job.seniority if job.seniority in _SENIORITY_RANK else None
    if title_level is None:
        return structured or "unclear"
    if structured is None:
        return title_level
    return max((title_level, structured), key=lambda label: _SENIORITY_RANK[label])


def _role_families(title: str | None) -> set[str]:
    return {
        family for family, pattern in _ROLE_FAMILIES if pattern.search(title or "")
    }


@lru_cache(maxsize=4_096)
def _plain_text(value: str | None) -> str:
    """Collapse HTML-ish job text into a regex-friendly plain string (memoized:
    the same resume/description strings are re-scanned for every candidate)."""
    return re.sub(r"\s+", " ", _HTML_TAG.sub(" ", html.unescape(value or ""))).strip()


@lru_cache(maxsize=64)
def _profile_degree_rank(resume_text: str | None) -> int | None:
    """Highest degree level evidenced by the resume (memoized per resume text)."""
    text = _plain_text(resume_text)
    ranks = [rank for rank, pattern in _DEGREE_PATTERNS if pattern.search(text)]
    return max(ranks) if ranks else None


def _description_yoe_min(description: str | None, resume_text: str | None) -> int | None:
    """Best-effort minimum YoE from explicit requirement language.

    This is intentionally a fallback, not a general NLP extractor.  It catches
    strong forms such as ``7+ years``, ``7-10 years of experience``, and degree
    alternatives such as ``MS + 3 years / BS + 5 years``.  A bare company-history
    sentence ("over 40 years of experience") lacks requirement context and is
    ignored.
    """
    text = _plain_text(description)
    if not text:
        return None

    # Degree alternatives need candidate context: a master's candidate can use
    # the master's (or bachelor's) branch, while an unknown degree gets the
    # smallest positive requirement rather than an unsafe doctorate-zero branch.
    degree_requirements: dict[int, list[int]] = {}
    for rank, pattern in _DEGREE_PATTERNS:
        for degree_match in pattern.finditer(text):
            block = text[degree_match.end() : degree_match.end() + 180]
            # Stop before the next degree alternative so one branch cannot steal
            # the following branch's number.
            next_degree = min(
                (
                    match.start()
                    for _other_rank, other in _DEGREE_PATTERNS
                    for match in [other.search(block)]
                    if match is not None
                ),
                default=len(block),
            )
            block = block[:next_degree]
            number = re.search(r"\b(\d{1,2})\s*(?:\+)?\s*years?\b", block, re.I)
            if number:
                value = int(number.group(1))
                if value <= 30:
                    degree_requirements.setdefault(rank, []).append(value)

    if degree_requirements:
        candidate_rank = _profile_degree_rank(resume_text)
        if candidate_rank is not None:
            eligible = [
                value
                for requirement_rank, values in degree_requirements.items()
                if requirement_rank <= candidate_rank
                for value in values
            ]
            if eligible:
                return min(eligible)
        all_values = [value for values in degree_requirements.values() for value in values]
        positive = [value for value in all_values if value > 0]
        return min(positive or all_values) if all_values else None

    values: list[int] = []
    for pattern in (_YOE_BEFORE_EXPERIENCE, _YOE_AFTER_EXPERIENCE):
        for match in pattern.finditer(text):
            value = int(match.group("years"))
            if value > 30:
                continue
            qualifier = match.groupdict().get("qualifier")
            prefix = match.groupdict().get("prefix")
            context = text[max(0, match.start() - 240) : min(len(text), match.end() + 100)]
            # + / ranges / "at least" are intrinsically requirement-like.  A
            # plain number needs a nearby qualifications/candidate cue.
            if qualifier or prefix or _REQUIREMENT_CONTEXT.search(context):
                values.append(value)
    return max(values) if values else None


def _obvious_role_mismatch(job: Job, profile: UserProfile, title_score: float) -> bool:
    return bool(
        profile.target_titles
        and title_score < 0.75
        and _OBVIOUS_UNRELATED_TITLES.search(job.title or "")
    )


def _unsupported_specialties(job: Job, profile: UserProfile) -> list[str]:
    corpus = " ".join([
        *(profile.target_titles or []),
        *(profile.interests or []),
        *(profile.skills or []),
        profile.resume_text or "",
    ]).lower()
    return [
        label
        for label, trigger, evidence in _SPECIALTY_EVIDENCE
        if trigger.search(job.title or "") and not any(term in corpus for term in evidence)
    ]


def _title_score(job: Job, profile: UserProfile) -> float:
    """Graded title fit: 1.0 for an exact/substring target match, else the best
    proportional word overlap (shared words / target words) across targets."""
    if not profile.target_titles:
        return 0.5  # no preference expressed → neutral
    title = _norm(job.title or "")
    if not title:
        return 0.0
    title_words = set(re.findall(r"[a-z0-9+#]+", title))
    job_families = _role_families(title)
    best = 0.0
    for target in profile.target_titles:
        t = _norm(target)
        if not t:
            continue
        if t in title or title in t:
            return 1.0
        if job_families & _role_families(t):
            best = max(best, 0.85)
        t_words = set(re.findall(r"[a-z0-9+#]+", t))
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

    # Structured profile skills are intentionally bounded.  A structured JD
    # skill that is explicitly present in the canonical full resume remains
    # truthful evidence even when the parser's top-N list omitted it.
    if job_skills and profile.resume_text:
        resume_backed = profile_skills_mentioned_in_text(profile.resume_text, job_skills)
        profile_skills = list(dict.fromkeys([*profile_skills, *resume_backed]))
    if not job_skills and profile_skills:
        job_skills = profile_skills_mentioned_in_text(job.description, profile_skills)
    if not job_skills or not profile_skills:
        return 0.5, [], sorted({s.strip() for s in job_skills})[:8]  # nothing to compare → neutral

    matched, gaps = skills_overlap(job_skills, profile_skills)
    coverage = len(matched) / len(job_skills)
    # Damp confidence when the job lists very few skills (noisy denominator).
    if len(job_skills) < 3:
        coverage = 0.5 + (coverage - 0.5) * (len(job_skills) / 3)
    return coverage, matched[:12], gaps[:8]


def _interest_score(job: Job, profile: UserProfile) -> float:
    """Soft topic/domain affinity backed by user-entered or parsed interests.

    Interests help retrieve and order adjacent roles, but never bypass title,
    evidence, seniority, country, or authorization gates.
    """
    if not profile.interests:
        return 0.5
    corpus = _norm(f"{job.title or ''} {(job.description or '')[:6_000]}")
    corpus_words = set(re.findall(r"[a-z0-9+#]+", corpus))
    best = 0.0
    for raw_interest in profile.interests:
        interest = _norm(raw_interest)
        if not interest:
            continue
        if interest in corpus:
            return 1.0
        words = set(re.findall(r"[a-z0-9+#]+", interest))
        if words:
            best = max(best, len(words & corpus_words) / len(words))
    return best


def _seniority_score(job: Job, profile: UserProfile) -> float:
    """1.0 at/under the ceiling, 0.5 one rank above, 0.0 further above."""
    effective = _effective_seniority(job)
    if effective == "unclear":
        return 0.6
    job_rank = _SENIORITY_RANK.get(effective)
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

    # Title/profession alignment is evaluated before any broad skill or vector
    # signal. Shared tools such as Excel, SQL, or Python cannot turn a nurse,
    # physician liaison, pharmacy technician, or other unrelated occupation into
    # a recommendation for a technical profile.
    title_s = _title_score(job, profile)
    if profile.target_titles and title_s < 0.5:
        hard_rejects.append("Job title is outside the profile's target roles")
    elif _obvious_role_mismatch(job, profile, title_s):
        hard_rejects.append("Clinical occupation is outside the profile's target roles")

    eligibility_text = " ".join(filter(None, (
        job.work_auth_required, job.restrictions, job.description,
    )))
    eligibility = extract_work_authorization_evidence(eligibility_text)
    explicit_no_sponsorship = (
        job.visa_sponsorship == "no" or eligibility["visa_sponsorship"] == "no"
    )
    citizenship_required = job.citizenship_required or eligibility["citizenship_required"]
    clearance_required = (
        job.security_clearance == "required" or eligibility["security_clearance"] == "required"
    )

    if profile.needs_sponsorship and explicit_no_sponsorship:
        hard_rejects.append("Explicitly no visa sponsorship")
    if profile.reject_clearance and clearance_required:
        hard_rejects.append("Requires a security clearance")
    if profile.reject_citizenship_only and citizenship_required:
        hard_rejects.append("Requires US citizenship / GC / ITAR eligibility")
    allowed_countries = {_norm(country) for country in profile.countries if country}
    if allowed_countries and allowed_countries <= _US_COUNTRY_ALIASES:
        # Re-check the complete per-job location at query time.  This protects
        # the recommendation feed from legacy records created before Workday
        # detail-country extraction, including global tenants whose board was
        # incorrectly stamped "us" while the role itself was in Vietnam.
        if not is_us_job(
            job.country, job.location_raw, job.remote_mode, title=job.title,
        ):
            location = job.location_raw or job.country or "unknown location"
            hard_rejects.append(f"Outside target countries ({location})")
    elif job.country and allowed_countries and _norm(job.country) not in allowed_countries:
        hard_rejects.append(f"Outside target countries ({job.country.upper()})")
    description_yoe = _description_yoe_min(job.description, profile.resume_text)
    stated_yoe_values = [
        value for value in (job.yoe_min, description_yoe) if value is not None
    ]
    effective_yoe_min = max(stated_yoe_values) if stated_yoe_values else None
    if effective_yoe_min is not None and effective_yoe_min >= _HARD_YOE_CAP:
        hard_rejects.append(f"Requires {effective_yoe_min}+ years of experience")
    # Four years is normally past a new graduate's realistic bar.  Keep only
    # explicit early-career programs whose numeric requirement is visibly loose.
    if (
        effective_yoe_min is not None
        and effective_yoe_min >= 4
        and not job.new_grad_program
        and job.seniority not in ("intern", "junior")
    ):
        hard_rejects.append(
            f"Requires {effective_yoe_min}+ years for a non-entry-level role"
        )
    effective_seniority = _effective_seniority(job)
    if effective_seniority == "intern":
        hard_rejects.append("Internship-only role")
    if job.company and _norm(job.company) in {_norm(c) for c in profile.excluded_companies}:
        hard_rejects.append(f"Excluded company ({job.company})")
    # Defense/weapons-domain postings are near-certain US-person/ITAR walls for
    # a sponsorship-needing candidate even when the JD omits the boilerplate
    # (verified pattern: UAS/radar/"weapon systems" SMEs). Deterministic, with
    # the matched keyword as evidence. Non-sponsorship profiles only get a flag.
    defense_evidence = (
        detect_defense_domain(job.title, job.description, job.company)
        if not job.citizenship_required  # already hard-rejected above when set
        else None
    )
    if defense_evidence and profile.needs_sponsorship:
        hard_rejects.append(
            f"Defense/weapons domain (\"{defense_evidence}\") — near-certain "
            "US-person/ITAR requirement"
        )

    # YoE borderline band: above the profile ceiling but under the hard cap.
    borderline_yoe = (
        effective_yoe_min is not None
        and not hard_rejects
        and effective_yoe_min > profile.yoe_max
    )
    if (
        borderline_yoe
        and effective_yoe_min is not None
        and effective_yoe_min > profile.yoe_max + 1
    ):
        hard_rejects.append(
            f"Requires {effective_yoe_min}+ years (well above target {profile.yoe_max})"
        )

    max_rank = _SENIORITY_RANK.get(profile.seniority_max)
    job_rank = _SENIORITY_RANK.get(effective_seniority)
    above_seniority = (
        max_rank is not None and job_rank is not None and job_rank > max_rank
    )
    if (
        above_seniority
        and job_rank is not None
        and max_rank is not None
        and job_rank > max_rank + 1
    ):
        hard_rejects.append(
            f"{effective_seniority.title()} role is above the "
            f"{profile.seniority_max} target"
        )

    profile_families = set().union(*(
        _role_families(target) for target in profile.target_titles if target.strip()
    )) if profile.target_titles else set()
    job_families = _role_families(job.title)
    if profile_families and job_families and not (profile_families & job_families):
        hard_rejects.append("Role family is outside the profile's target roles")

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
    skill_s, matched, gaps = _skill_score(job, profile)
    interest_s = _interest_score(job, profile)
    sen_s = _seniority_score(job, profile)
    rem_s = _remote_score(job, profile)

    # Deterministic terms always present; semantic optional.
    terms: list[tuple[float, float]] = [
        (_W_SKILLS, skill_s),
        (_W_TITLE, title_s),
        (_W_INTEREST, interest_s),
        (_W_SENIORITY, sen_s),
        (_W_REMOTE, rem_s),
    ]
    if semantic is not None:
        terms.append((_W_SEMANTIC, max(0.0, min(1.0, semantic))))
    total_w = sum(w for w, _ in terms)  # 1.0 with semantic, 0.8 without
    fit = sum(w * v for w, v in terms) / total_w  # renormalize so it sums to 1.0

    if title_s >= 1.0:
        reasons.append("Title matches a target role")
    if matched and skill_s >= 0.5:
        reasons.append("Strong skill overlap with profile")
    if effective_yoe_min is not None and effective_yoe_min <= profile.yoe_max:
        reasons.append("Experience requirement is within profile range")
    if semantic is not None and semantic >= 0.65:
        reasons.append("Overall resume background is semantically aligned")
    if profile.interests and interest_s >= 0.75:
        reasons.append("Aligns with stated interests")
    if sen_s >= 1.0:
        reasons.append("Seniority within target range")
    if profile.remote_preference != "any" and rem_s >= 1.0:
        reasons.append("Matches work-mode preference")
    if job.cap_exempt in ("yes", "likely"):
        reasons.append(f"Likely H-1B cap-exempt ({job.employer_type})")

    # ── Forcing flags (surface, but do not auto-apply) ─────────────────────
    force_flag = False
    recommendation_blocked = not profile.target_titles or title_s < 0.75
    if above_seniority:
        # Exactly one level above the ceiling is a stretch, never an automatic
        # recommendation. Two+ levels were rejected above.
        red_flags.append(
            f"Seniority above target ({effective_seniority} vs {profile.seniority_max})"
        )
        force_flag = True
        recommendation_blocked = True

    # A high semantic score cannot override stated interests or evidence. These
    # gates define the precision contract for the For You feed.
    if profile.target_titles and title_s < 0.75:
        red_flags.append("Title is not a close match to a target role")
        force_flag = True
        recommendation_blocked = True

    evidence_count = len(matched) + len(gaps)
    skill_coverage = len(matched) / evidence_count if evidence_count else 0.0
    if not profile.skills:
        red_flags.append("Profile has no verified skills for matching")
        force_flag = True
        recommendation_blocked = True
    elif not job.skills and len(matched) < 2:
        if job.new_grad_program and title_s >= 0.75:
            # Curated new-grad feeds (e.g. SimplifyJobs) carry no description,
            # so skill text can't exist. An explicit new-grad role whose title
            # matches a target is exactly what an entry candidate should see —
            # keep the caveat visible without burying the job.
            red_flags.append("Feed carries no skill text — verify requirements on the posting")
            force_flag = True
        else:
            red_flags.append("Job has insufficient extracted skill evidence")
            force_flag = True
            recommendation_blocked = True
    elif not job.skills:
        reasons.append("Verified profile skills are explicitly named in the job description")
    elif not matched:
        # Explicit requirements with zero resume support are a real mismatch,
        # not merely a low vector score.
        return Verdict(
            job_id=job.job_id,
            verdict="reject",
            score=round(fit, 3),
            reasons=reasons,
            red_flags=["No verified overlap with the job's technical skills"],
            matched=[],
            gaps=gaps,
            cap_exempt=job.cap_exempt,
        )
    elif skill_coverage < 0.5 or (evidence_count >= 4 and len(matched) < 2):
        red_flags.append("Verified skill coverage is below the recommendation bar")
        force_flag = True
        recommendation_blocked = True

    unsupported_specialties = _unsupported_specialties(job, profile)
    if unsupported_specialties:
        red_flags.append(
            "No resume evidence for central specialty: " + ", ".join(unsupported_specialties)
        )
        force_flag = True
        recommendation_blocked = True
    if profile.needs_sponsorship and job.visa_sponsorship in ("not_mentioned", "unclear"):
        # A cap-exempt institution or known H-1B filer is a strong enough
        # sponsorship signal to remain application-ready.  Keep the caveat, but
        # do not bury the exact employers this candidate should prioritise.
        if job.cap_exempt in ("yes", "likely") or job.known_h1b_sponsor:
            reasons.append("Employer has a positive sponsorship signal")
            red_flags.append("Confirm sponsorship policy during application")
        else:
            red_flags.append("Sponsorship not stated - verify before applying")
            force_flag = True
    if borderline_yoe and effective_yoe_min is not None:
        red_flags.append(f"Slightly above target YoE ({effective_yoe_min})")
        force_flag = True
        recommendation_blocked = True

    explicit_entry_signal = bool(
        job.new_grad_program
        or _title_seniority(job.title) in {"intern", "junior"}
        or job.seniority in {"intern", "junior"}
    )
    if effective_yoe_min is None and not explicit_entry_signal:
        if job.enrichment_status in {"pending", "failed"}:
            red_flags.append(
                "Experience requirements are unverified because enrichment is incomplete"
            )
            force_flag = True
            recommendation_blocked = True
        elif max_rank is not None and max_rank <= _SENIORITY_RANK["junior"]:
            # A completed extraction can truthfully conclude that the posting
            # never states a level. Ordinary postings (no YoE number, no "new
            # grad" in the title) are the single most common shape, so blocking
            # them all starves a junior profile's feed. Compromise: with STRONG
            # verified evidence (close title + real skill overlap + not
            # senior-leaning), surface it with the caveat; otherwise keep it
            # out of For You as before.
            strong_evidence = (
                title_s >= 0.75
                and len(matched) >= 2
                and effective_seniority in {"junior", "mid", "unclear"}
            )
            red_flags.append(
                "Experience level is not stated for this junior-targeted profile"
            )
            force_flag = True
            if not strong_evidence:
                recommendation_blocked = True

    if (
        profile.remote_preference != "any"
        and job.remote_mode != "unknown"
        and job.remote_mode != profile.remote_preference
    ):
        red_flags.append(
            f"Work mode does not match preference ({job.remote_mode} vs "
            f"{profile.remote_preference})"
        )
        force_flag = True
        recommendation_blocked = True
    if job.is_recruiter_post:
        red_flags.append("Recruiter/aggregator post — prefer the direct employer")
    if defense_evidence and not profile.needs_sponsorship:
        red_flags.append(
            f"Defense/weapons domain ({defense_evidence}) — check for US-person/clearance requirements"
        )

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
        recommendable=fit >= _APPLY_THRESHOLD and not recommendation_blocked,
    )


# Ranking keys for the prompt's output ordering: cap-exempt first, then by
# verdict (apply > flag > reject), then by descending fit score.
_CAP_EXEMPT_RANK = {"yes": 0, "likely": 0, "unknown": 1, "no": 2}
_VERDICT_RANK = {"apply": 0, "flag": 1, "reject": 2}


def priority_key(
    verdict: Verdict, prefer_cap_exempt: bool = False,
) -> tuple[int, int, float | int, float | int]:
    """Rank fit/verdict before sponsorship preference.

    A nurse at a cap-exempt hospital must never outrank a relevant technical
    role. When the profile explicitly prefers cap-exempt employers, that signal
    is applied only within the same verdict/recommendability tier.
    """
    prefix = (
        _VERDICT_RANK.get(verdict.verdict, 1),
        0 if verdict.recommendable else 1,
    )
    cap_rank = _CAP_EXEMPT_RANK.get(verdict.cap_exempt, 1)
    if prefer_cap_exempt:
        return (*prefix, cap_rank, -verdict.score)
    return (*prefix, -verdict.score, cap_rank)


def match_key(
    verdict: Verdict, prefer_cap_exempt: bool = False,
) -> tuple[int, int, int, int]:
    """Sort key for Best Match with hard quality tiers before score.

    Match % is the rounded fit score the UI shows, so ordering matches what the user
    sees. Cap-exempt breaks ties only when two jobs round to the same percentage (a
    small visa-aware nudge), then apply > flag > reject.
    """
    prefix = (
        _VERDICT_RANK.get(verdict.verdict, 1),
        0 if verdict.recommendable else 1,
    )
    fit_rank = -round(verdict.score * 100)
    cap_rank = _CAP_EXEMPT_RANK.get(verdict.cap_exempt, 1)
    if prefer_cap_exempt:
        return (*prefix, cap_rank, fit_rank)
    return (*prefix, fit_rank, cap_rank)
