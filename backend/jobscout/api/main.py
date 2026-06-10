"""FastAPI application — JobScout REST API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware

from jobscout.config import settings
from jobscout.embed import embedding_quota_hit
from jobscout.enrich import (
    EnrichmentError,
)
from jobscout.models import Company, Job, JobsResponse, RunLog, SavedSearch, UserProfile
from jobscout.relational import RelationalStore
from jobscout.resume import extract_resume_text, parse_resume_to_profile
from jobscout.search import PROGRESSIVE_LADDER, build_filters, execute_search
from jobscout.services.ingestion_service import (
    _REFRESH_ADAPTER,
    AUTOFETCH_MAX_INFLIGHT,
    AUTOFETCH_MIN_RESULTS,
    _autofetch_and_clear,
    _autofetch_inflight,
    _refresh_watchlist,
    _run_enrichment,
    _run_ingestion,
)
from jobscout.services.query_service import (
    MATCH_WINDOW,
    _count_matches,
    _date_range_to_dates,
    _dedupe_jobs,
    _match_resume_to_jobs,
    _semantic_scores,
)
from jobscout.services.source_config import (
    _RUNTIME_SOURCE_OVERRIDES,
    _TOGGLABLE_SOURCES,
    _enabled_source_names,
    _load_sources_cfg,
)
from jobscout.store import COLLECTION_NAME, WeaviateStore
from jobscout.verdict import match_key, priority_key
from jobscout.verdict import score as score_verdict

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application lifespan — open/close stores once per process
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Both stores connect synchronously in their constructors.
    weaviate_store = WeaviateStore()
    relational_store = RelationalStore()
    app.state.weaviate_store = weaviate_store
    app.state.relational_store = relational_store
    log.info("stores_open")
    # Project curated cap-exempt employers (sources.yaml + discovered) into the
    # company registry so the Companies tab shows them and "Get companies"
    # refreshes them. Idempotent; single source of truth stays sources.yaml.
    try:
        from jobscout.services.registry import register_cap_exempt_companies
        n = register_cap_exempt_companies(relational_store, _load_sources_cfg())
        log.info("cap_exempt_registry_synced count=%s", n)
    except Exception as exc:  # noqa: BLE001 — never block startup on the sync
        log.warning("cap-exempt registry sync skipped (%s)", exc)
    # Optional daily auto-refresh (OFF by default; see jobscout.scheduler).
    from jobscout import scheduler as _sched
    _sched.start_scheduler(
        lambda: _refresh_watchlist(
            weaviate_store, relational_store, settings.embed_daily_budget, []
        )
    )
    try:
        yield
    finally:
        weaviate_store.close()
        relational_store.close()
        log.info("stores_closed")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="JobScout API",
    version="1.0.0",
    description="Multi-portal job aggregation and filtering engine.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Source config + adapter construction live in services/source_config.py
# (imported above). What remains here is request orchestration only.
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/jobs", response_model=JobsResponse, tags=["jobs"])
async def list_jobs(
    request: Request,
    q: str | None = Query(None, description="Full-text / semantic search query"),
    location: str | None = Query(None),
    remote: list[str] | None = Query(None, description="repeatable: remote|onsite|hybrid"),
    visa: list[str] | None = Query(None, description="repeatable: yes|no|unclear|not_mentioned"),
    exp: list[str] | None = Query(None, description="repeatable experience band: entry|mid|senior|lead"),
    date_range: str | None = Query(
        None, description="6h|12h|18h|24h|7d|14d|21d|1m|custom"
    ),
    date_from: str | None = Query(
        None, description="ISO date string (YYYY-MM-DD), used when date_range=custom"
    ),
    date_to: str | None = Query(
        None, description="ISO date string (YYYY-MM-DD), used when date_range=custom"
    ),
    source: list[str] | None = Query(None, description="repeatable source name"),
    company_size: list[str] | None = Query(
        None, description="repeatable size bucket: 1-50|51-200|201-500|501-1000|1001-5000|5000+"
    ),
    employer_type: list[str] | None = Query(
        None, description="repeatable: university|hospital|nonprofit|government|for_profit|unclear"
    ),
    cap_exempt: list[str] | None = Query(
        None, description="repeatable: yes|likely|no|unknown"
    ),
    security_clearance: list[str] | None = Query(
        None, description="repeatable: required|preferred|none|unclear"
    ),
    category: list[str] | None = Query(
        None, description="repeatable: software_eng|data_ml_ai|devops_infra|security|product_mgmt|design_ux|management|other"
    ),
    employment_type: list[str] | None = Query(
        None, description="repeatable: full_time|part_time|contract|internship|temporary"
    ),
    exclude_citizenship_required: bool = Query(
        False, description="Drop roles that require US citizenship / GC / ITAR eligibility"
    ),
    exclude_recruiter: bool = Query(
        False, description="Drop recruiter/aggregator postings (prefer direct employers)"
    ),
    exclude_no_sponsorship: bool = Query(
        False,
        description="Hide explicit no-sponsorship + citizenship-required roles (keeps the "
        "~96% that say nothing about visa). The sensible default for visa-needing users.",
    ),
    h1b_sponsor: bool = Query(
        False, description="Only companies in the public DoL H-1B filer list (proven sponsors)"
    ),
    everify: bool = Query(
        False, description="Only known E-Verify employers (required for the STEM OPT extension)"
    ),
    dedupe: bool = Query(
        True, description="Collapse near-duplicate reposts (same company+title) on the page"
    ),
    alpha: float = Query(
        0.5, ge=0.0, le=1.0, description="Hybrid blend: 0=keyword, 1=vector"
    ),
    sort: str = Query(
        "relevance", description="posted_desc|relevance|salary_desc"
    ),
    profile_id: str | None = Query(
        None, description="Apply a saved profile's verdict/scoring + exclusion + priority sort"
    ),
    target_min: int | None = Query(
        None,
        ge=1,
        description="Progressive lookback: widen 6h→12h→18h→24h until this many results "
        "are found (ignored if date_range is set)",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
) -> JobsResponse:
    from_d, to_d, preset = _date_range_to_dates(date_range, date_from, date_to)
    store: WeaviateStore = request.app.state.weaviate_store

    def _search(
        window: str | None,
        *,
        sort_override: str | None = None,
        page_override: int | None = None,
        page_size_override: int | None = None,
    ) -> JobsResponse:
        filters = build_filters(
            remote=remote,
            visa=visa,
            source=source,
            company_size=company_size,
            exp=exp,
            employer_type=employer_type,
            cap_exempt=cap_exempt,
            security_clearance=security_clearance,
            category=category,
            employment_type=employment_type,
            exclude_citizenship_required=exclude_citizenship_required,
            exclude_recruiter=exclude_recruiter,
            exclude_no_sponsorship=exclude_no_sponsorship,
            h1b_sponsor=h1b_sponsor,
            everify=everify,
            date_range=window,
            date_from=from_d,
            date_to=to_d,
        )
        return execute_search(
            store=store,
            q=q,
            alpha=alpha,
            filters=filters,
            sort=sort_override if sort_override is not None else sort,
            page=page_override if page_override is not None else page,
            page_size=page_size_override if page_size_override is not None else page_size,
        )

    # Progressive lookback: widen the freshness window until target_min results
    # are found (or the ladder is exhausted). Only when no explicit date_range
    # was requested.
    if target_min and date_range is None:
        result = _search(PROGRESSIVE_LADDER[0])
        used = PROGRESSIVE_LADDER[0]
        for window in PROGRESSIVE_LADDER[1:]:
            if result.total >= target_min:
                break
            result = _search(window)
            used = window
        result.lookback_window = used
    else:
        result = _search(preset)

    # Profile-driven verdict layer. Two orderings:
    #   - sort="match": a GLOBAL "Best Match" sort. Score a bounded candidate
    #     window (MATCH_WINDOW), order by match_key (highest fit% first), and
    #     paginate in-memory — so the top of page 1 is the best match overall,
    #     not just the best on whatever page Weaviate returned.
    #   - otherwise: the default cap-exempt-first priority sort on the page.
    # When no profile is supplied the un-profiled behaviour above is returned
    # unchanged (and sort="match" degrades to relevance, since fit needs a profile).
    if profile_id:
        relational: RelationalStore = request.app.state.relational_store
        profile = relational.get_profile(profile_id)
        if profile is None:
            raise HTTPException(
                status_code=404, detail=f"Profile '{profile_id}' not found."
            )
        excluded = relational.get_excluded_job_ids(profile_id)

        if sort == "match":
            candidates = _search(
                preset, sort_override="relevance", page_override=1,
                page_size_override=MATCH_WINDOW,
            )
            # Semantic blend: resume↔job cosine similarity, if the profile has a
            # resume. Cached per profile; degrades to deterministic-only on failure.
            sem = _semantic_scores(profile, store)
            scored = [
                (job, score_verdict(job, profile, semantic=sem.get(job.job_id)))
                for job in candidates.jobs
                if job.job_id not in excluded
            ]
            scored.sort(key=lambda pair: match_key(pair[1]))
            start = (page - 1) * page_size
            page_slice = scored[start : start + page_size]
            result = candidates
            result.total = len(scored)
            result.jobs = [job for job, _ in page_slice]
            result.verdicts = {v.job_id: v for _, v in page_slice}
        else:
            scored = [
                (job, score_verdict(job, profile))
                for job in result.jobs
                if job.job_id not in excluded
            ]
            scored.sort(key=lambda pair: priority_key(pair[1]))
            result.jobs = [job for job, _ in scored]
            result.verdicts = {v.job_id: v for _, v in scored}

    # Collapse near-duplicate reposts on this page (same company+title across boards),
    # keeping the most authoritative source. Page-scoped (MVP). Toggle with dedupe=false.
    if dedupe:
        result.jobs = _dedupe_jobs(result.jobs)

    # Auto-fetch: a sparse keyword search quietly pulls fresh jobs in the
    # background (LinkedIn/Indeed style), deduped so we never double-trigger.
    if q and q.strip() and result.total < AUTOFETCH_MIN_RESULTS and background_tasks is not None:
        key = q.strip().lower()
        if (
            key not in _autofetch_inflight
            and len(_autofetch_inflight) < AUTOFETCH_MAX_INFLIGHT
        ):
            _autofetch_inflight.add(key)
            background_tasks.add_task(
                _autofetch_and_clear,
                [q.strip()],
                location,
                request.app.state.weaviate_store,
                request.app.state.relational_store,
                key,
            )

    return result




@app.get("/api/jobs/by-state", response_model=JobsResponse, tags=["jobs"])
async def jobs_by_state(
    request: Request,
    profile_id: str = Query(..., description="Profile whose marked jobs to list"),
    status: str = Query("saved", description="applied|saved|seen|hidden"),
) -> JobsResponse:
    """Return the jobs a profile marked with *status* (newest first), verdict-scored.

    Powers the Shortlist (``saved``) and Applied (``applied``) views. NOTE: declared
    before ``/api/jobs/{job_id}`` so the literal path wins over the path param.
    """
    if status not in ("applied", "saved", "seen", "hidden"):
        raise HTTPException(status_code=422, detail="status must be applied|saved|seen|hidden.")
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found.")
    store: WeaviateStore = request.app.state.weaviate_store

    jobs: list[Job] = []
    verdicts: dict[str, Any] = {}
    for jid in relational.get_job_state_ids(profile_id, status):
        job = store.get_by_id(jid)
        if job is None:
            continue
        jobs.append(job)
        verdicts[jid] = score_verdict(job, profile)

    return JobsResponse(
        jobs=jobs, total=len(jobs), page=1, page_size=len(jobs) or 1, verdicts=verdicts,
    )


@app.get("/api/jobs/{job_id}", response_model=Job, tags=["jobs"])
async def get_job(job_id: str, request: Request) -> Job:
    store: WeaviateStore = request.app.state.weaviate_store
    job = store.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


# Cache of resume embeddings keyed by (profile_id, resume_text hash) so repeated
# Best-Match queries for the same profile don't re-embed. Bounded + best-effort.
@app.post("/api/match", response_model=JobsResponse, tags=["jobs"])
async def match_resume(body: dict[str, Any], request: Request) -> JobsResponse:
    """Resume TEXT → jobs match. Body: {resume_text, profile_id?, limit?}.

    Embeds the resume (same model as jobs) and runs ``near_vector`` with the
    profile's eligibility filters. With a profile, each match carries a verdict
    (fit score + matched + gap keywords).
    """
    resume_text = (body.get("resume_text") or "").strip()
    if not resume_text:
        raise HTTPException(status_code=422, detail="resume_text is required.")
    limit = int(body.get("limit", 5))
    profile = None
    if body.get("profile_id"):
        relational: RelationalStore = request.app.state.relational_store
        profile = relational.get_profile(body["profile_id"])
        if profile is None:
            raise HTTPException(status_code=404, detail="Profile not found.")
    return _match_resume_to_jobs(
        resume_text, profile, limit,
        request.app.state.weaviate_store, request.app.state.relational_store,
    )


@app.post("/api/match/upload", tags=["jobs"])
async def match_resume_upload(
    request: Request,
    file: UploadFile = File(...),
    limit: int = Form(10),
) -> dict[str, Any]:
    """Drop a resume FILE (PDF/DOCX/TXT/JSON/anything) → saved profile + matched jobs.

    Extracts text, parses it into a UserProfile via DeepSeek (truthful: only
    skills the resume supports), SAVES the profile (reusable + deletable), then
    matches. Returns ``{profile, jobs, verdicts}`` with per-job matched/gap keywords.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Empty file.")
    text = extract_resume_text(file.filename or "resume.txt", data)
    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail="Could not extract any text from the file (is it a scanned image?).",
        )
    label = (file.filename or "resume").rsplit(".", 1)[0][:60]
    try:
        profile = parse_resume_to_profile(text, label=label)
    except EnrichmentError as exc:
        raise HTTPException(status_code=502, detail=f"Resume parse failed: {exc}") from exc

    relational: RelationalStore = request.app.state.relational_store
    relational.upsert_profile(profile)  # save — reusable + deletable

    result = _match_resume_to_jobs(
        text, profile, limit, request.app.state.weaviate_store, relational,
    )
    return {
        "profile": profile.model_dump(),
        "jobs": [j.model_dump() for j in result.jobs],
        "verdicts": {k: v.model_dump() for k, v in result.verdicts.items()},
    }


@app.post("/api/profiles", response_model=UserProfile, tags=["profiles"])
async def create_or_update_profile(profile: UserProfile, request: Request) -> UserProfile:
    """Create a new profile or update an existing one (by id)."""
    relational: RelationalStore = request.app.state.relational_store
    return relational.upsert_profile(profile)


@app.get("/api/profiles", response_model=list[UserProfile], tags=["profiles"])
async def list_profiles(request: Request) -> list[UserProfile]:
    relational: RelationalStore = request.app.state.relational_store
    return relational.list_profiles()


@app.get("/api/profiles/{profile_id}", response_model=UserProfile, tags=["profiles"])
async def get_profile(profile_id: str, request: Request) -> UserProfile:
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found.")
    return profile


@app.delete("/api/profiles/{profile_id}", tags=["profiles"])
async def delete_profile(profile_id: str, request: Request) -> dict[str, str]:
    relational: RelationalStore = request.app.state.relational_store
    relational.delete_profile(profile_id)
    return {"status": "deleted", "profile_id": profile_id}


@app.post("/api/profiles/{profile_id}/job-state", tags=["profiles"])
async def set_job_state(
    profile_id: str, body: dict[str, Any], request: Request
) -> dict[str, str]:
    """Set a job's state for a profile. Body: {job_id, status, note?}.

    Triage: ``saved`` (shortlist, still shown) · ``seen`` (informational) · ``hidden``.
    Pipeline stages (excluded from the main list, shown in the tracker):
    ``applied`` · ``oa`` · ``interview`` · ``offer`` · ``rejected``. Optional ``note``.
    """
    valid = ("saved", "seen", "hidden", "applied", "oa", "interview", "offer", "rejected")
    job_id = body.get("job_id")
    status = body.get("status")
    if not job_id or status not in valid:
        raise HTTPException(
            status_code=422,
            detail=f"Body must include job_id and status in {{{', '.join(valid)}}}.",
        )
    relational: RelationalStore = request.app.state.relational_store
    if relational.get_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found.")
    relational.set_job_state(profile_id, job_id, status, note=body.get("note"))
    return {"status": "ok", "job_id": job_id, "state": status}


@app.get("/api/profiles/{profile_id}/pipeline", tags=["profiles"])
async def get_pipeline(profile_id: str, request: Request) -> dict[str, Any]:
    """Return the profile's application pipeline (applied→oa→interview→offer→rejected).

    Shape: ``{jobs: [...], stages: {job_id: {stage, note, updated_at}}}``. The
    frontend groups jobs by stage. Newest activity first."""
    relational: RelationalStore = request.app.state.relational_store
    if relational.get_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found.")
    store: WeaviateStore = request.app.state.weaviate_store
    jobs: list[dict[str, Any]] = []
    stages: dict[str, Any] = {}
    for row in relational.list_pipeline(profile_id):
        job = store.get_by_id(row["job_id"])
        if job is None:
            continue
        jobs.append(job.model_dump())
        stages[job.job_id] = {"stage": row["status"], "note": row["note"],
                              "updated_at": str(row["updated_at"])}
    return {"jobs": jobs, "stages": stages}


@app.post("/api/search/run", response_model=list[RunLog], tags=["ingestion"])
async def trigger_search_run(
    body: dict[str, Any],
    request: Request,
    background_tasks: BackgroundTasks,
) -> list[RunLog]:
    """Trigger an on-demand ingestion run (non-blocking).

    Body schema::

        {
            "keywords":        ["software engineer"],
            "location":        "remote",
            "results_wanted":  50
        }

    Returns one ``RunLog`` stub per enabled source (status ``running``).
    The actual ingestion happens in the background.
    """
    keywords: list[str] = body.get("keywords", ["software engineer"])
    location: str | None = body.get("location")
    results_wanted: int = int(body.get("results_wanted", 50))

    weaviate_store: WeaviateStore = request.app.state.weaviate_store
    relational_store: RelationalStore = request.app.state.relational_store

    # Create placeholder RunLog stubs for each enabled source so the caller
    # gets an immediate response, then start the real work in the background.
    cfg = _load_sources_cfg()
    sources_cfg: dict[str, Any] = cfg.get("sources", {})

    enabled_sources: list[str] = _enabled_source_names(sources_cfg)

    # Build the immediate response in memory only — do NOT persist stubs. The real
    # per-adapter runs are recorded (start_run + finish_run) inside _run_ingestion,
    # so /api/sources/status reflects actual finished runs instead of stuck stubs.
    now = datetime.now(UTC)
    stub_logs: list[RunLog] = [
        RunLog(source=name, started_at=now, status="running")
        for name in enabled_sources
    ]

    background_tasks.add_task(
        _run_ingestion,
        keywords,
        location,
        results_wanted,
        weaviate_store,
        relational_store,
    )

    return stub_logs


# ---------------------------------------------------------------------------
# On-demand enrichment of pending/failed jobs (decoupled from ingest)
# ---------------------------------------------------------------------------



@app.post("/api/enrich/run", tags=["enrichment"])
async def enrich_run(
    body: dict[str, Any],
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Re-run enrichment over jobs currently ``pending`` or ``failed``.

    Body: ``{"limit": 50}``. Runs in the background; returns immediately.
    """
    limit = int(body.get("limit", 50))
    weaviate_store: WeaviateStore = request.app.state.weaviate_store
    background_tasks.add_task(_run_enrichment, weaviate_store, limit)
    return {"status": "running", "limit": limit}


# ---------------------------------------------------------------------------
# Company registry + incremental watchlist refresh
# ---------------------------------------------------------------------------



@app.get("/api/companies", response_model=list[Company], tags=["companies"])
async def list_companies(
    request: Request,
    tier: str | None = Query(None),
    ats: str | None = Query(None),
    size: str | None = Query(None),
    h1b_sponsor: bool | None = Query(None),
    enabled: bool | None = Query(None),
    direct_apply_only: bool | None = Query(None),
    sort: str = Query("open_roles", description="open_roles|last_checked|name|tier"),
) -> list[Company]:
    """List/filter the company registry."""
    rel: RelationalStore = request.app.state.relational_store
    return rel.list_companies(
        tier=tier, ats=ats, size=size, h1b_sponsor=h1b_sponsor,
        enabled=enabled, direct_apply_only=direct_apply_only, order_by=sort,
    )


def _probe_ats_slug(
    ats: str,
    slug: str,
    region: str = "wd1",
    site: str = "External",
    timeout: int = 8,
) -> dict[str, Any]:
    """Probe one ATS slug and return ``{valid, job_count, sample_title, error}``.

    Pure read — no side effects. Used by both /validate and /discover.
    """
    import requests as _req  # lazy import — only needed for ATS probing

    try:
        if ats == "greenhouse":
            url = f"https://boards.greenhouse.io/v1/boards/{slug}/jobs"
            r = _req.get(url, timeout=timeout, headers={"User-Agent": "JobScout/1.0"})
            if r.status_code == 200:
                jobs = r.json().get("jobs", [])
                return {"valid": True, "job_count": len(jobs),
                        "sample_title": jobs[0]["title"] if jobs else None}
            return {"valid": False, "error": f"HTTP {r.status_code}"}

        if ats == "lever":
            url = f"https://api.lever.co/v0/postings/{slug}"
            r = _req.get(url, timeout=timeout, headers={"User-Agent": "JobScout/1.0"})
            if r.status_code == 200:
                jobs = r.json()
                return {"valid": True, "job_count": len(jobs),
                        "sample_title": jobs[0]["text"] if jobs else None}
            return {"valid": False, "error": f"HTTP {r.status_code}"}

        if ats == "ashby":
            url = (f"https://api.ashbyhq.com/posting-api/job-board/"
                   f"{slug}?includeCompensation=true")
            r = _req.get(url, timeout=timeout,
                         headers={"Accept": "application/json", "User-Agent": "JobScout/1.0"})
            if r.status_code == 200:
                jobs = [j for j in r.json().get("jobs", [])
                        if j.get("isListed") is not False]
                return {"valid": True, "job_count": len(jobs),
                        "sample_title": jobs[0]["title"] if jobs else None}
            return {"valid": False, "error": f"HTTP {r.status_code}"}

        if ats == "workday":
            url = (f"https://{slug}.{region}.myworkdayjobs.com"
                   f"/wday/cxs/{slug}/{site}/jobs")
            r = _req.post(url, json={"limit": 5, "offset": 0, "searchText": "",
                                     "appliedFacets": {}},
                          timeout=timeout + 4,
                          headers={"Content-Type": "application/json",
                                   "User-Agent": "JobScout/1.0"})
            if r.status_code == 200:
                data = r.json()
                jobs = data.get("jobPostings", [])
                total = data.get("total", len(jobs))
                return {"valid": True, "job_count": total,
                        "sample_title": jobs[0]["title"] if jobs else None}
            return {"valid": False, "error": f"HTTP {r.status_code}"}

        return {"valid": False, "error": f"Validation not supported for {ats}"}

    except _req.exceptions.Timeout:
        return {"valid": False, "error": "timed out"}
    except Exception as exc:  # noqa: BLE001
        return {"valid": False, "error": str(exc)}


@app.post("/api/companies/validate", tags=["companies"])
async def validate_company_ats(body: dict[str, Any]) -> dict[str, Any]:
    """Probe whether an ATS slug/tenant returns open jobs. No side effects."""
    ats = str(body.get("ats", ""))
    slug = str(body.get("slug", "")).strip()
    if not slug:
        return {"valid": False, "error": "slug is required"}
    result = _probe_ats_slug(
        ats=ats,
        slug=slug,
        region=str(body.get("region") or "wd1").strip(),
        site=str(body.get("site") or "External").strip(),
    )
    if not result["valid"] and ats not in ("greenhouse", "lever", "ashby", "workday"):
        result["error"] = f"Validation not supported for {ats} — add manually."
    return result


@app.post("/api/companies/discover", tags=["companies"])
async def discover_companies(body: dict[str, Any], request: Request) -> list[dict[str, Any]]:
    """Discover ATS boards for companies seen in the job index but not yet watched.

    1. Fetch recent non-recruiter jobs from Weaviate → unique company names
    2. Filter out names already in the registry
    3. Probe Greenhouse/Lever/Ashby for each (parallelised, 5 s timeout)
    4. Return verified candidates sorted by job_count desc (max 20)
    """
    import re
    from concurrent.futures import ThreadPoolExecutor, as_completed

    store: WeaviateStore = request.app.state.weaviate_store
    rel: RelationalStore = request.app.state.relational_store

    # --- Step 1: collect unique company names from recent aggregator jobs ---
    try:
        from weaviate.classes.query import Filter as _F
        coll = store._client.collections.get("Job")
        # Aggregator sources only — ATS-adapter jobs are already in the registry
        agg_sources = [
            "adzuna", "remotive", "arbeitnow", "jobicy", "remoteok",
            "workingnomads", "themuse", "jobrightai", "rss",
        ]
        source_filter = _F.any_of([
            _F.by_property("source").equal(s) for s in agg_sources
        ])
        recruiter_filter = _F.by_property("is_recruiter_post").equal(False)
        combined = _F.all_of([source_filter, recruiter_filter])

        resp = coll.query.fetch_objects(
            filters=combined,
            limit=600,
            return_properties=["company"],
        )
        from collections import Counter
        name_counts: Counter[str] = Counter()
        for obj in resp.objects:
            raw = obj.properties.get("company")
            name: str = str(raw) if raw else ""
            if name and len(name) > 1:
                name_counts[name] += 1
    except Exception:  # noqa: BLE001
        name_counts = Counter()

    if not name_counts:
        return []

    # --- Step 2: filter out already-registered companies ---
    existing_names = {c.name.lower() for c in rel.list_companies()}
    candidates = [
        (name, count) for name, count in name_counts.most_common(80)
        if name.lower() not in existing_names
    ][:60]  # probe at most 60

    if not candidates:
        return []

    # --- Step 3: probe Greenhouse/Lever/Ashby for each candidate ---
    _ATS_ORDER = ("greenhouse", "lever", "ashby")

    def _slug_variants(name: str) -> list[str]:
        """Generate likely ATS slug variants from a company display name."""
        base = re.sub(r"[^a-z0-9]", "", name.lower())
        # Also try with common suffixes stripped
        variants = [base]
        for suffix in ("ai", "inc", "corp", "hq", "io", "co"):
            if base.endswith(suffix) and len(base) > len(suffix) + 2:
                variants.append(base[: -len(suffix)])
        return variants

    def _probe_company(name: str) -> dict[str, Any] | None:
        for slug in _slug_variants(name):
            for ats in _ATS_ORDER:
                result = _probe_ats_slug(ats, slug, timeout=5)
                if result["valid"] and (result.get("job_count") or 0) > 0:
                    return {
                        "name": name,
                        "ats": ats,
                        "slug": slug,
                        "job_count": result["job_count"],
                        "sample_title": result.get("sample_title"),
                    }
        return None

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_probe_company, name): name for name, _ in candidates}
        for future in as_completed(futures):
            hit = future.result()
            if hit:
                results.append(hit)

    results.sort(key=lambda x: x.get("job_count") or 0, reverse=True)
    return results[:20]


@app.post("/api/companies", response_model=Company, tags=["companies"])
async def upsert_company(company: Company, request: Request) -> Company:
    """Add or update a company in the registry / watchlist."""
    rel: RelationalStore = request.app.state.relational_store
    return rel.upsert_company(company)


@app.post("/api/companies/refresh", tags=["companies"])
async def refresh_watchlist(
    body: dict[str, Any],
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Kick off an incremental refresh of the enabled watchlist (background).

    Body: ``{"keywords": [...], "budget": 500}``. Ingests only NEW jobs, capped at
    ``budget`` embeds (defaults to ``settings.embed_daily_budget``). Runs in the
    background; new jobs appear in ``/api/jobs`` and ``last_checked`` updates.
    """
    keywords: list[str] = body.get("keywords", [])
    budget = int(body.get("budget", settings.embed_daily_budget))
    weaviate_store: WeaviateStore = request.app.state.weaviate_store
    relational_store: RelationalStore = request.app.state.relational_store
    enabled_n = len(
        [c for c in relational_store.enabled_companies() if c.ats in _REFRESH_ADAPTER]
    )
    background_tasks.add_task(
        _refresh_watchlist, weaviate_store, relational_store, budget, keywords
    )
    return {"status": "running", "companies": enabled_n, "budget": budget}


@app.get("/api/sources/status", tags=["operations"])
async def sources_status(request: Request) -> list[dict[str, Any]]:
    """Return per-source last run info and configuration status."""
    relational_store: RelationalStore = request.app.state.relational_store
    return relational_store.get_sources_status()


@app.get("/api/scheduler", tags=["operations"])
async def get_scheduler() -> dict[str, Any]:
    """Return daily auto-refresh scheduler status (enabled, hour, next run)."""
    from jobscout import scheduler as _sched
    return _sched.status()


@app.post("/api/scheduler", tags=["operations"])
async def set_scheduler(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Enable/disable the daily auto-refresh at runtime. Body: {"enabled": true|false}."""
    from jobscout import scheduler as _sched
    enabled = bool(body.get("enabled"))
    if enabled:
        weaviate_store: WeaviateStore = request.app.state.weaviate_store
        relational_store: RelationalStore = request.app.state.relational_store
        _sched.enable(
            lambda: _refresh_watchlist(
                weaviate_store, relational_store, settings.embed_daily_budget, []
            )
        )
    else:
        _sched.disable()
    return _sched.status()


@app.post("/api/maintenance/purge", tags=["operations"])
async def purge_old_jobs(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Delete jobs older than ``days`` from the index. Explicit cleanup only.

    Body: ``{"days": 60}``. Removes jobs whose posted_date (or ingested_at when the
    date is unknown) is older than the cutoff. Returns the count deleted.
    """
    try:
        days = int(body.get("days", 60))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="days must be an integer.") from None
    if days < 1:
        raise HTTPException(status_code=422, detail="days must be >= 1.")
    store: WeaviateStore = request.app.state.weaviate_store
    cutoff = datetime.now(UTC) - timedelta(days=days)
    deleted = store.purge_older_than(cutoff)
    return {"status": "ok", "days": days, "deleted": deleted}


@app.get("/api/sources/overrides", tags=["operations"])
async def get_source_overrides() -> dict[str, bool]:
    """Runtime source enable/disable overrides (e.g. the high-risk JobSpy scraper).
    Default empty → config defaults apply. In-memory; resets to off on restart."""
    return dict(_RUNTIME_SOURCE_OVERRIDES)


@app.post("/api/sources/overrides", tags=["operations"])
async def set_source_overrides(body: dict[str, Any]) -> dict[str, bool]:
    """Enable/disable a source at runtime. Body e.g. ``{"jobspy": true}``.

    Only known high-risk sources are togglable here; default off. This is how the
    UI's "high-risk scraper" switch turns JobSpy on without editing sources.yaml.
    """
    for name, val in body.items():
        if name in _TOGGLABLE_SOURCES:
            _RUNTIME_SOURCE_OVERRIDES[name] = bool(val)
    return dict(_RUNTIME_SOURCE_OVERRIDES)


@app.get("/api/saved-searches", tags=["operations"])
async def list_saved_searches(request: Request) -> list[dict[str, Any]]:
    """List saved searches, each with a live ``new_count`` (matches ingested since
    the last time it was marked seen). Powers the pull→push "new for me" badges."""
    relational: RelationalStore = request.app.state.relational_store
    store: WeaviateStore = request.app.state.weaviate_store
    out: list[dict[str, Any]] = []
    for s in relational.list_saved_searches():
        try:
            s.new_count = _count_matches(store, s.filters, s.last_checked_at)
        except Exception:  # noqa: BLE001 — count is best-effort, never fatal
            s.new_count = 0
        out.append(s.model_dump())
    return out


@app.post("/api/saved-searches", response_model=SavedSearch, tags=["operations"])
async def create_saved_search(body: dict[str, Any], request: Request) -> SavedSearch:
    """Save the current query+filters. Body: {label, filters, profile_id?}."""
    label = (body.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=422, detail="label is required.")
    s = SavedSearch(
        label=label,
        filters=body.get("filters") or {},
        profile_id=body.get("profile_id"),
    )
    relational: RelationalStore = request.app.state.relational_store
    return relational.create_saved_search(s)


@app.post("/api/saved-searches/{search_id}/seen", tags=["operations"])
async def mark_saved_search_seen(search_id: str, request: Request) -> dict[str, str]:
    """Mark a saved search as seen (resets its new_count to 0)."""
    relational: RelationalStore = request.app.state.relational_store
    if relational.mark_saved_search_seen(search_id) is None:
        raise HTTPException(status_code=404, detail="Saved search not found.")
    return {"status": "ok", "id": search_id}


@app.delete("/api/saved-searches/{search_id}", tags=["operations"])
async def delete_saved_search(search_id: str, request: Request) -> dict[str, str]:
    """Delete a saved search."""
    relational: RelationalStore = request.app.state.relational_store
    relational.delete_saved_search(search_id)
    return {"status": "deleted", "id": search_id}


@app.get("/api/stats", tags=["operations"])
async def stats(request: Request) -> dict[str, Any]:
    """Return aggregate counts: total jobs, by source, by date bucket."""
    store: WeaviateStore = request.app.state.weaviate_store

    collection = store._client.collections.get(COLLECTION_NAME)

    # Total jobs
    try:
        total_result = collection.aggregate.over_all(total_count=True)
        total_jobs: int = total_result.total_count or 0
    except Exception:
        total_jobs = 0

    # By source — group_by aggregate
    by_source: dict[str, int] = {}
    try:
        source_result = collection.aggregate.over_all(
            group_by="source",
            total_count=True,
        )
        for group in source_result.groups or []:
            if group.grouped_by is not None:
                by_source[str(group.grouped_by.value)] = group.total_count or 0
    except Exception:
        pass

    # By date bucket — one count-since query per preset
    from weaviate.classes.query import Filter

    now = datetime.now(UTC)
    _BUCKETS: dict[str, timedelta] = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "14d": timedelta(days=14),
        "21d": timedelta(days=21),
        "1m": timedelta(days=30),
    }
    by_date_bucket: dict[str, int] = {}
    for label, delta in _BUCKETS.items():
        cutoff = now - delta
        try:
            bucket_result = collection.aggregate.over_all(
                filters=Filter.by_property("posted_date").greater_than(cutoff),
                total_count=True,
            )
            by_date_bucket[label] = bucket_result.total_count or 0
        except Exception:
            by_date_bucket[label] = 0

    return {
        "total_jobs": total_jobs,
        "by_source": by_source,
        "by_date_bucket": by_date_bucket,
        # True if the embedding provider quota is currently hit (set on a 429,
        # cleared on the next successful embed). Drives the UI quota banner for
        # both "Get latest jobs" and "Get companies".
        "embed_quota_exhausted": embedding_quota_hit(),
    }
