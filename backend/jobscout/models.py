from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field

from jobscout.sponsors import derive_sponsorship_likelihood

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
    employer_type: Literal[
        "university", "hospital", "nonprofit", "government", "for_profit", "unclear"
    ] = "unclear"
    cap_exempt: Literal["yes", "likely", "no", "unknown"] = "unknown"  # H-1B cap exemption
    known_h1b_sponsor: bool = False         # company appears in public DoL H-1B filer list
    known_everify: bool = False             # company is a known E-Verify participant (STEM OPT gate)
    is_recruiter_post: bool = False         # recruiter/aggregator wrapper vs. direct employer
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
        "greenhouse", "lever", "ashby", "workday", "workable", "rippling", "none"
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
    error: str | None = None
    status: Literal["running", "done", "failed"] = "running"


# ─── User profile & verdict models ───────────────────────────────────────────

class UserProfile(BaseModel):
    """Per-user job-search preferences that drive the verdict/scoring layer.

    This is the generic, multi-user replacement for hardcoding one candidate's
    rules into the search logic. Every field is a knob the verdict engine reads.
    """

    id: str = Field(default_factory=lambda: str(uuid4()))
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
    excluded_companies: list[str] = []
    resume_text: str | None = None           # source resume text (re-derivable; stored in the JSON blob)


class SavedSearch(BaseModel):
    """A pinned query + filters. Powers "new since last visit" (pull → push):
    ``new_count`` = matches ingested after ``last_checked_at``."""

    id: str = Field(default_factory=lambda: str(uuid4()))
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


# ─── Operational / monitoring models ─────────────────────────────────────────

class SourceStatus(BaseModel):
    """Live status snapshot for a single ingestion source."""

    source: str
    enabled: bool
    last_run_at: datetime | None = None
    last_run_status: Literal["running", "done", "failed"] | None = None
    last_run_ingested: int | None = None
    last_run_failed: int | None = None
    last_error: str | None = None
    total_jobs_stored: int = 0
