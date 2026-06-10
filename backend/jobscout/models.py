from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

# ─── Core domain models ───────────────────────────────────────────────────────

class Job(BaseModel):
    """Canonical job record stored in both DuckDB and Weaviate."""

    job_id: str                             # 16-char hex dedup hash
    source: str                             # adapter name (e.g. "adzuna", "rss")
    source_job_id: str | None = None        # original ID from the upstream source
    title: str
    company: str | None = None
    location_raw: str | None = None         # verbatim location string from source
    locations: list[str] = []               # all locations for this collapsed role
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
    employment_type: Literal[
        "full_time", "part_time", "contract", "internship", "temporary", "unknown"
    ] = "unknown"
    category: Literal[
        "software_eng", "data_ml_ai", "devops_infra", "security",
        "product_mgmt", "design_ux", "management", "other"
    ] = "other"
    skills: list[str] = []
    seniority: Literal[
        "intern", "junior", "mid", "senior", "staff", "principal", "lead",
        "manager", "director", "vp", "c_level", "unclear"
    ] = "unclear"
    enrichment_status: Literal["pending", "done", "failed"] = "pending"
    raw_payload: str | None = None          # JSON-serialised original API response


class JobSource(BaseModel):
    """Lightweight record linking a canonical job to one of its upstream sources."""

    job_id: str
    source: str
    url: str
    posted_date: datetime | None = None


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
