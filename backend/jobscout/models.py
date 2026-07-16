"""Pydantic schemas shared across the whole app.

The canonical shapes: ``Job`` (one posting, enriched + provenance-labeled),
``UserProfile`` (who the candidate is / what they target), ``Verdict`` (the
deterministic apply/flag/reject decision with reasons), plus API request/
response envelopes. Stores and services all speak these types — no layer
invents its own dicts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field

from jobscout.source_intelligence import source_kind, source_label
from jobscout.sponsors import derive_sponsorship_likelihood

# Ghost/stale-posting thresholds (advisory; ~1 in 3 2026 listings are stale/fake).
GHOST_STALE_DAYS = 45    # posted longer ago than this & still listed → high ghost risk
GHOST_AGING_DAYS = 30    # aging window → medium ghost risk

# Title hints that a role is pitched as entry-level / new-grad.
_ENTRY_TITLE_HINTS = ("junior", "entry level", "entry-level", "new grad", "new-grad", "associate")


def _posting_age_days(posted: datetime | None) -> int | None:
    """Whole days since *posted* (assumes UTC for naive datetimes); None if unknown."""
    if posted is None:
        return None
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - posted).days)


# ─── Core domain models ───────────────────────────────────────────────────────

class Job(BaseModel):
    """Canonical job record stored in both DuckDB and Weaviate."""

    job_id: str                             # 16-char hex dedup hash
    source: str                             # adapter name (e.g. "adzuna", "rss")
    source_job_id: str | None = None        # original ID from the upstream source
    title: str
    company: str | None = None
    location_raw: str | None = None         # verbatim location string from source
    country: str | None = None              # ISO 3166-1 alpha-2
    city: str | None = None
    remote_mode: Literal["remote", "onsite", "hybrid", "unknown"] = "unknown"
    description: str | None = None
    url: str
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None      # ISO 4217 (e.g. "USD")
    posted_date: datetime | None = None
    posted_date_est: bool = False           # True when the date was estimated/guessed
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Direct-ATS lifecycle. Legacy records without these properties are treated
    # as active until a completed board snapshot proves otherwise.
    is_active: bool = True
    last_seen_at: datetime | None = None
    closed_at: datetime | None = None
    yoe_min: int | None = None              # years-of-experience lower bound
    yoe_max: int | None = None              # years-of-experience upper bound
    visa_sponsorship: Literal["yes", "no", "unclear", "not_mentioned"] = "not_mentioned"
    work_auth_required: str | None = None   # e.g. "US citizen or GC only"
    restrictions: str | None = None         # other work-auth / legal restrictions
    company_size_bucket: str | None = None  # e.g. "201-500"; from config or LLM enrichment
    skills: list[str] = []
    seniority: Literal[
        "intern", "junior", "mid", "senior", "staff", "principal", "lead",
        "manager", "director", "vp", "c_level", "unclear"
    ] = "unclear"
    # ── Sponsorship / eligibility signals (LLM-enriched, see enrich.py) ──
    security_clearance: Literal["required", "preferred", "none", "unclear"] = "unclear"
    citizenship_required: bool = False      # US-citizen / GC-only / US-Person / ITAR / EAR
    # Exact snippets that caused deterministic eligibility extraction. They are
    # displayed to the user so a hard filter is explainable and reviewable.
    eligibility_evidence: list[str] = []
    employer_type: Literal[
        "university", "hospital", "nonprofit", "government", "for_profit", "unclear"
    ] = "unclear"
    cap_exempt: Literal["yes", "likely", "no", "unknown"] = "unknown"  # H-1B cap exemption
    known_h1b_sponsor: bool = False         # company appears in public DoL H-1B filer list
    known_everify: bool = False             # company is a known E-Verify participant (STEM OPT gate)
    is_recruiter_post: bool = False         # recruiter/aggregator wrapper vs. direct employer
    new_grad_program: bool = False          # explicit new-grad/university/early-career/rotational program
    category: Literal[
        "software_eng", "data_ml_ai", "devops_infra", "security",
        "product_mgmt", "design_ux", "management", "other"
    ] = "other"
    employment_type: Literal[
        "full_time", "part_time", "contract", "internship", "temporary"
    ] = "full_time"
    enrichment_status: Literal["pending", "done", "failed"] = "pending"
    raw_payload: str | None = None          # JSON-serialised original API response
    # Response-only (computed at query time, never stored): near-duplicate repost collapse.
    duplicate_count: int = 0                 # how many other postings collapsed into this one
    also_on: list[str] = []                  # the other sources the same role appeared on

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sponsorship_likelihood(self) -> Literal["likely", "unknown", "no"]:
        """Advisory sponsorship signal folded from visa/cap-exempt/citizenship/H-1B.

        Serialised into every API response so the UI can badge each job without
        re-deriving the logic. Best-effort from public signals — not a guarantee.
        """
        return derive_sponsorship_likelihood(
            self.visa_sponsorship,
            self.cap_exempt,
            self.citizenship_required,
            self.known_h1b_sponsor,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_kind(self) -> Literal["primary", "government", "curated", "aggregator", "scraper"]:
        """Whether this is an employer ATS, official board, or discovery layer."""
        return source_kind(self.source)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_label(self) -> str:
        """Human-readable provenance without exposing adapter implementation details."""
        return source_label(self.source)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def freshness_kind(self) -> Literal["posted", "updated", "estimated"]:
        """Explain how the displayed date should be interpreted.

        Greenhouse's public feed exposes ``updated_at``, not an original publish
        timestamp.  Treating it as "posted" falsely advertises freshness, so the
        API marks it explicitly.  Other direct APIs provide publish/create dates.
        """
        if self.posted_date_est:
            return "estimated"
        if self.source == "greenhouse":
            return "updated"
        return "posted"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def posting_age_days(self) -> int | None:
        """Whole days since the role was posted (None if no date). Drives ghost_risk."""
        return _posting_age_days(self.posted_date)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ghost_risk(self) -> Literal["low", "medium", "high"]:
        """Advisory 'likely-stale / ghost posting' signal, computed at query time.

        Fresh grads burn time on dead listings (~1 in 3 are stale/fake in 2026).
        Age-based: a posting still listed long after its post date is the reliable
        staleness signal. An unknown/estimated date is NOT treated as stale (we
        can't claim it), so it stays "low" to avoid flooding the badge.
        """
        age = self.posting_age_days
        if age is None:
            return "low"
        if age > GHOST_STALE_DAYS:
            return "high"
        if age > GHOST_AGING_DAYS:
            return "medium"
        return "low"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mislabeled_entry(self) -> bool:
        """True when pitched as entry-level/new-grad but the JD demands >= 3 yrs.

        Surfaces the 'fake entry-level' paradox so early-career users see the gap.
        """
        if self.yoe_min is None or self.yoe_min < 3:
            return False
        title = (self.title or "").lower()
        return self.seniority in ("intern", "junior") or any(
            h in title for h in _ENTRY_TITLE_HINTS
        )


class JobSource(BaseModel):
    """Lightweight record linking a canonical job to one of its upstream sources."""

    job_id: str
    source: str
    url: str
    posted_date: datetime | None = None


class Company(BaseModel):
    """A watched employer in the company registry.

    The durable entity behind the job board: which ATS the company uses, its
    board slug, careers URL, and the metadata that drives filtering. Reachable
    companies (Greenhouse/Lever/Ashby/Workday/Workable/Rippling) can be refreshed
    for new jobs; ``direct_apply_only`` companies (e.g. FAANG on Workday/Taleo)
    are surfaced with a link only — never scraped.
    """

    slug: str
    ats: Literal[
        "greenhouse", "lever", "ashby", "workday", "workable", "rippling",
        "recruitee", "smartrecruiters", "none",
    ] = "none"
    name: str
    careers_url: str | None = None
    tier: str = "unknown"                    # cleaned label (FAANG / Mid-Size Tech / Startups / …)
    employer_type: str = "for_profit"
    size_bucket: str | None = None
    known_h1b_sponsor: bool = False
    cap_exempt_hint: str = "unknown"         # yes | likely | no | unknown
    open_roles: int = 0                      # last observed open-role count
    last_checked: datetime | None = None
    enabled: bool = True                     # part of the refresh watchlist
    direct_apply_only: bool = False          # unreachable ATS → link out, don't scrape
    region: str | None = None                # Workday datacenter (wd1/wd5/…) — null for slug ATS
    site: str | None = None                  # Workday career-site path — null for slug ATS


class RunLog(BaseModel):
    """Audit log entry for a single ingest/enrichment run."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    source: str
    started_at: datetime
    finished_at: datetime | None = None
    count_ingested: int = 0
    count_failed: int = 0
    count_seen: int = 0
    count_filtered: int = 0
    count_closed: int = 0
    error: str | None = None
    status: Literal["running", "done", "failed"] = "running"


# ─── User profile & verdict models ───────────────────────────────────────────

# Structured resume — JSON-Resume-standard-aligned typed sections so the UI can
# render/edit real fields (school, role, dates, tech) instead of a text dump.
# ``resume_text`` stays the canonical flat source that matching embeds; edits
# to the structured form recompose it (see resume.compose_resume_text_from_structured).

class EducationEntry(BaseModel):
    """One school/degree (JSON Resume `education`)."""

    institution: str
    degree: str | None = None
    field_of_study: str | None = None
    gpa: str | None = None
    start_date: str | None = None   # freeform ("May 2024") — resumes vary too much for strict dates
    end_date: str | None = None
    location: str | None = None
    honors: list[str] = []


class ExperienceEntry(BaseModel):
    """One position (JSON Resume `work`)."""

    company: str
    title: str
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    current: bool = False
    summary: str | None = None
    bullets: list[str] = []


class ProjectEntry(BaseModel):
    """One project (JSON Resume `projects`)."""

    name: str
    technologies: list[str] = []
    url: str | None = None
    github_url: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    bullets: list[str] = []


class CertificationEntry(BaseModel):
    """One certification/publication (JSON Resume `certificates`)."""

    name: str
    issuer: str | None = None
    date: str | None = None
    credential_id: str | None = None
    url: str | None = None


class PublicationEntry(BaseModel):
    """One paper/article (conference, journal, preprint) — DOI/URL preserved."""

    title: str
    venue: str | None = None        # conference/journal, e.g. "ICSTE-23"
    date: str | None = None
    url: str | None = None          # DOI or landing page
    authors: list[str] = []
    description: str | None = None


class AchievementEntry(BaseModel):
    """One award/honor/scholarship/competition result."""

    title: str
    issuer: str | None = None
    date: str | None = None
    description: str | None = None


class SkillCategory(BaseModel):
    """A named group of skills ("Programming Languages" → [...])."""

    name: str
    skills: list[str] = []


class CustomSection(BaseModel):
    """User-defined extra section rendered as a bullet list."""

    title: str
    bullets: list[str] = []


class StructuredResume(BaseModel):
    """Typed view of a resume; extraction-only (the parser never invents facts)."""

    summary: str | None = None
    education: list[EducationEntry] = []
    experience: list[ExperienceEntry] = []
    projects: list[ProjectEntry] = []
    certifications: list[CertificationEntry] = []
    publications: list[PublicationEntry] = []
    achievements: list[AchievementEntry] = []
    skill_categories: list[SkillCategory] = []
    custom_sections: list[CustomSection] = []



class ResumeSection(BaseModel):
    """One faithfully retained section from an uploaded resume.

    ``resume_text`` remains the lossless source for matching and editing. These
    sections make education, experience, projects, publications, achievements,
    and any custom heading visible/editable without silently dropping content.
    """

    heading: str
    content: str

class ResumeRecord(BaseModel):
    """One uploaded resume in a profile's library.

    A profile keeps many resumes but matches with exactly one — the *active*
    resume, whose text/sections/structured view are projected onto the profile's
    ``resume_*`` fields. Keeping that projection means every matching code path
    (embedding, verdict, deep match, tailoring) is unchanged by this feature.

    ``file_path`` is stored relative to ``settings.resume_storage_dir`` so that
    pre-library uploads (``{profile_id}.pdf``) and library uploads
    (``{profile_id}/{resume_id}.pdf``) coexist without a file migration.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    profile_id: str
    filename: str                              # display name; deduped within a profile
    content_type: str | None = None
    size_bytes: int = 0
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    file_path: str                             # relative to settings.resume_storage_dir
    resume_text: str = ""
    resume_sections: list[ResumeSection] = []
    structured_resume: StructuredResume | None = None
    # Resume-derived matching facts, parsed once at upload so switching the active
    # resume costs no LLM call. User *preferences* (sponsorship, clearance, remote)
    # stay on the profile — they describe the person, not the document.
    skills: list[str] = []
    target_titles: list[str] = []
    seniority_max: str | None = None
    yoe_max: int | None = None


class TailoredResumeRecord(BaseModel):
    """A tailored DOCX built for one (profile, job) pair.

    The file already lived on disk; this record is what makes it findable and
    re-downloadable later instead of being lost with the response that built it.
    """

    profile_id: str
    job_id: str
    company: str = ""
    title: str = ""
    filename: str = ""                          # the built DOCX's human name
    recommendation: str | None = None          # gate verdict at build time
    # Fingerprint of the profile+resume+job at build time — lets the UI tell
    # "up to date" from "your resume changed since, re-tailor" (empty = legacy).
    fingerprint: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class UserProfile(BaseModel):
    """Per-user job-search preferences that drive the verdict/scoring layer.

    This is the generic, multi-user replacement for hardcoding one candidate's
    rules into the search logic. Every field is a knob the verdict engine reads.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
    # Owner of this profile. Empty on legacy rows → treated as the local user
    # (see api/deps.effective_owner). Stamped on upsert so every profile is owned.
    # This is the whole tenancy key: a route that checks ownership can't leak.
    user_id: str = ""
    label: str
    target_titles: list[str] = []           # e.g. ["data scientist", "ml engineer"]
    seniority_max: Literal[
        "intern", "junior", "mid", "senior", "staff", "principal", "lead",
        "manager", "director", "vp", "c_level",
    ] = "mid"
    yoe_max: int = 3                         # hard reject well above this (with a borderline band)
    needs_sponsorship: bool = False          # drives the sponsorship disqualifier
    reject_clearance: bool = True            # reject roles requiring a security clearance
    reject_citizenship_only: bool = True     # reject US-citizen / GC / ITAR-only roles
    remote_preference: Literal["remote", "hybrid", "onsite", "any"] = "any"
    countries: list[str] = ["us"]            # generic — not hardcoded to the US
    prefer_cap_exempt: bool = False          # sort weight, not a hard filter
    skills: list[str] = []                   # from resume; powers fit score + keyword-gap
    interests: list[str] = []
    # Personal deep-match steering (rendered into the deep-match LLM prompt;
    # empty lists add no rules — nothing user-specific is baked into code).
    avoid_role_types: list[str] = []     # e.g. "pure BI/reporting/dashboards work"
    avoid_domains: list[str] = []        # e.g. "healthcare billing systems"                # user-backed domains/topics; soft retrieval signal
    excluded_companies: list[str] = []
    # The full extracted source text is the editable, canonical matching record.
    # It is deliberately not a derived summary, so no resume evidence is lost
    # between upload, profile edits, semantic matching, and deep matching.
    resume_text: str | None = None
    # Derived from the full text, preserving every detected and custom section
    # in source order. An "Additional information" section captures text that
    # appears before a recognizable heading.
    resume_sections: list[ResumeSection] = []
    # Typed sections (additive; None until parsed/migrated). See StructuredResume.
    structured_resume: StructuredResume | None = None
    # The exact uploaded file is also retained locally for download/preview.
    # Only metadata is stored here; bytes live under settings.resume_storage_dir.
    resume_filename: str | None = None
    resume_content_type: str | None = None
    resume_uploaded_at: datetime | None = None
    # The resume library: many uploads, one active. The active resume's text /
    # sections / structured view are projected onto the resume_* fields above, so
    # all matching code keeps reading the profile and needs no change. None on
    # pre-library profiles (their single upload is adopted lazily as record 0).
    active_resume_id: str | None = None
    # True after a raw-text edit changed resume_text while a typed structured_resume
    # exists: the typed cards now lag the text (rebuilding them needs an LLM parse,
    # so it stays manual). Matching is unaffected — it reads the current resume_text.
    # Cleared by a structured edit or Rebuild/Structure. Drives the Rebuild-button dot.
    structured_stale: bool = False


class SavedSearch(BaseModel):
    """A pinned query + filters. Powers "new since last visit" (pull → push):
    ``new_count`` = matches ingested after ``last_checked_at``."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str = ""                         # owner; empty legacy rows → local user
    label: str
    filters: dict[str, Any] = {}              # serialized JobFilters (q, exp, remote, toggles, …)
    profile_id: str | None = None             # optional: attach to a profile
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    new_count: int = 0                        # computed at read time, not stored


class Verdict(BaseModel):
    """Per-job decision produced by the verdict engine for a given profile."""

    job_id: str
    verdict: Literal["apply", "flag", "reject"]
    score: float                             # 0..1 fit score
    # True when every profile-fit gate passes.  A role may remain a ``flag``
    # solely because sponsorship is not stated while still being worth showing
    # in the recommendation feed with that caveat.
    recommendable: bool = False
    reasons: list[str] = []
    red_flags: list[str] = []
    matched: list[str] = []                  # JD skills the resume/profile supports (truthful matches)
    gaps: list[str] = []                     # JD skills the profile doesn't list
    cap_exempt: str = "unknown"              # echoed for priority sorting / UI


# ─── API request / response models ───────────────────────────────────────────

class JobFilter(BaseModel):
    """Query parameters for the GET /api/jobs endpoint."""

    q: str | None = Field(None, description="Full-text / semantic search query")
    source: str | None = Field(None, description="Filter by source adapter name")
    company: str | None = Field(None, description="Filter by company name (case-insensitive prefix)")
    country: str | None = Field(None, description="ISO 3166-1 alpha-2 country code")
    city: str | None = Field(None, description="City name (case-insensitive substring)")
    remote_mode: Literal["remote", "onsite", "hybrid", "unknown"] | None = Field(
        None, description="Work mode filter"
    )
    seniority: str | None = Field(None, description="Seniority level")
    visa_sponsorship: Literal["yes", "no", "unclear", "not_mentioned"] | None = Field(
        None, description="Visa sponsorship availability"
    )
    skills: list[str] | None = Field(
        None, description="Required skills (job must mention all listed skills)"
    )
    salary_min: float | None = Field(None, description="Minimum salary (any currency)")
    salary_max: float | None = Field(None, description="Maximum salary (any currency)")
    posted_after: datetime | None = Field(None, description="Only jobs posted on/after this date")
    posted_before: datetime | None = Field(None, description="Only jobs posted before this date")
    enrichment_status: Literal["pending", "done", "failed"] | None = Field(
        None, description="Enrichment pipeline status"
    )
    page: Annotated[int, Field(ge=1)] = 1
    page_size: Annotated[int, Field(ge=1, le=200)] = 25


class JobsResponse(BaseModel):
    """Paginated job listing response."""

    jobs: list[Job]
    total: int
    page: int
    page_size: int
    facets: dict[str, dict[str, int]] = {}
    # Present only when the query supplied a profile_id; keyed by job_id.
    verdicts: dict[str, Verdict] = {}
    # The freshness window actually used (set when progressive lookback ran).
    lookback_window: str | None = None
    # True while a sparse/stale For You feed is automatically searching enabled
    # sources for newer profile-targeted candidates.
    recommendation_refreshing: bool = False


# ─── Operational / monitoring models ─────────────────────────────────────────

class SourceStatus(BaseModel):
    """Live status snapshot for a single ingestion source."""

    source: str
    enabled: bool
    last_run_at: datetime | None = None
    last_run_status: Literal["running", "done", "failed"] | None = None
    last_run_ingested: int | None = None
    last_run_failed: int | None = None
    last_run_seen: int | None = None
    last_run_filtered: int | None = None
    last_run_closed: int | None = None
    last_error: str | None = None
    total_jobs_stored: int = 0
