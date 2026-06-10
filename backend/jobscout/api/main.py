"""FastAPI application — JobScout REST API."""

from __future__ import annotations

import logging
import traceback
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from jobscout.adapters import (
    AdzunaAdapter,
    ArbeitnowAdapter,
    AshbyAdapter,
    GreenhouseAdapter,
    JobicyAdapter,
    JobrightAIAdapter,
    JobSpyAdapter,
    LeverAdapter,
    RecruiteeAdapter,
    RemoteOKAdapter,
    RemotiveAdapter,
    SmartRecruitersAdapter,
    TheMuseAdapter,
    WorkableAdapter,
    WorkingNomadsAdapter,
)
from jobscout.adapters.base import CompliantHttpClient
from jobscout.config import settings
from jobscout.embed import embed_job
from jobscout.enrich import EnrichmentError, extract_enrichment
from jobscout.models import Job, JobsResponse, RunLog
from jobscout.normalize import is_us_job, raw_to_job
from jobscout.relational import RelationalStore
from jobscout.search import build_filters, execute_search
from jobscout.store import COLLECTION_NAME, WeaviateStore

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application lifespan — open/close stores once per process
# ---------------------------------------------------------------------------

def _scheduled_ingest() -> None:
    """Runs in the APScheduler background thread on the configured cron schedule."""
    keywords = [k.strip() for k in settings.ingest_keywords.split(",") if k.strip()]
    log.info("scheduled_ingest_start keywords=%s", keywords)
    ws = WeaviateStore()
    rs = RelationalStore()
    try:
        _run_ingestion(
            keywords=keywords,
            location=None,
            results_wanted=settings.ingest_results_wanted,
            weaviate_store=ws,
            relational_store=rs,
        )
    finally:
        ws.close()
        rs.close()
    log.info("scheduled_ingest_done")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Both stores connect synchronously in their constructors.
    weaviate_store = WeaviateStore()
    relational_store = RelationalStore()
    app.state.weaviate_store = weaviate_store
    app.state.relational_store = relational_store
    log.info("stores_open")

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _scheduled_ingest,
        CronTrigger.from_crontab(settings.ingest_schedule),
        id="daily_ingest",
        replace_existing=True,
    )
    scheduler.start()
    log.info("scheduler_started schedule=%s", settings.ingest_schedule)

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
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
# Helper: load sources.yaml
# ---------------------------------------------------------------------------

def _load_sources_cfg() -> dict[str, Any]:
    """Load sources.yaml relative to the CWD (where the server is launched)."""
    with open("sources.yaml") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Source registry — instantiate enabled adapters from sources.yaml config
# ---------------------------------------------------------------------------

# Order here is also the ingestion order. Add new sources in one place.
_SOURCE_ORDER = [
    "adzuna", "remotive", "arbeitnow", "jobicy", "jobrightai", "remoteok",
    "workingnomads", "themuse", "greenhouse", "lever", "ashby", "workable",
    "smartrecruiters", "recruitee", "jobspy",
]


def _company_tokens(companies: list[Any]) -> list[str]:
    """Extract board tokens from a config list of strings or {token,size} dicts."""
    tokens: list[str] = []
    for c in companies or []:
        if isinstance(c, dict):
            tok = c.get("token") or c.get("name")
            if tok:
                tokens.append(str(tok))
        elif c:
            tokens.append(str(c))
    return tokens


def _build_adapters(sources_cfg: dict[str, Any]) -> list[Any]:
    """Instantiate every enabled source adapter from sources.yaml."""
    adapters: list[Any] = []
    for name in _SOURCE_ORDER:
        cfg = sources_cfg.get(name, {})
        if not cfg.get("enabled", False):
            continue
        if name == "adzuna":
            adapters.append(AdzunaAdapter(countries=cfg.get("countries", ["us"])))
        elif name == "remotive":
            adapters.append(RemotiveAdapter())
        elif name == "arbeitnow":
            adapters.append(ArbeitnowAdapter())
        elif name == "jobicy":
            adapters.append(JobicyAdapter())
        elif name == "jobrightai":
            adapters.append(JobrightAIAdapter())
        elif name == "remoteok":
            adapters.append(RemoteOKAdapter())
        elif name == "workingnomads":
            adapters.append(WorkingNomadsAdapter())
        elif name == "themuse":
            adapters.append(TheMuseAdapter())
        elif name == "greenhouse":
            adapters.append(GreenhouseAdapter(companies=_company_tokens(cfg.get("companies", []))))
        elif name == "lever":
            adapters.append(LeverAdapter(companies=_company_tokens(cfg.get("companies", []))))
        elif name == "ashby":
            adapters.append(AshbyAdapter(companies=_company_tokens(cfg.get("companies", []))))
        elif name == "workable":
            adapters.append(WorkableAdapter(companies=_company_tokens(cfg.get("companies", []))))
        elif name == "smartrecruiters":
            adapters.append(SmartRecruitersAdapter(companies=_company_tokens(cfg.get("companies", []))))
        elif name == "recruitee":
            adapters.append(RecruiteeAdapter(companies=_company_tokens(cfg.get("companies", []))))
        elif name == "jobspy":
            adapters.append(
                JobSpyAdapter(sites=cfg.get("sites", []), hours_old=cfg.get("hours_old", 168))
            )
    return adapters


def _company_size_map(sources_cfg: dict[str, Any]) -> dict[str, str]:
    """Map company token (lowercased) -> size bucket, from greenhouse/lever config."""
    out: dict[str, str] = {}
    for key in ("greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee"):
        for c in sources_cfg.get(key, {}).get("companies", []) or []:
            if isinstance(c, dict):
                tok = c.get("token") or c.get("name")
                size = c.get("size")
                if tok and size:
                    out[str(tok).lower()] = str(size)
    return out


def _enabled_source_names(sources_cfg: dict[str, Any]) -> list[str]:
    return [n for n in _SOURCE_ORDER if sources_cfg.get(n, {}).get("enabled", False)]


# Keywords currently being auto-fetched, to avoid duplicate background runs.
_autofetch_inflight: set[str] = set()
AUTOFETCH_MIN_RESULTS = 3

# Cap jobs kept per company per ingest run, so one company reposting the same
# role across many cities (e.g. a gig board) can't flood the index.
MAX_JOBS_PER_COMPANY_PER_RUN = 15
# Cap on concurrent in-flight auto-fetches: each spawns a heavy multi-source
# ingest, so a burst of sparse searches must not be able to fan out unbounded.
AUTOFETCH_MAX_INFLIGHT = 2


def _date_range_to_dates(
    date_range: str | None,
    date_from: str | None,
    date_to: str | None,
) -> tuple[date | None, date | None, str | None]:
    """Return (from_date, to_date, preset_key) to pass to build_filters.

    When a named preset (24h/7d/14d/21d/1m) is given, return the preset key
    directly so build_filters can apply it.  For 'custom', parse the ISO date
    strings into ``date`` objects.
    """
    _PRESETS = {"24h", "7d", "14d", "21d", "1m"}
    if date_range in _PRESETS:
        return None, None, date_range
    if date_range == "custom":
        from_d = date.fromisoformat(date_from) if date_from else None
        to_d = date.fromisoformat(date_to) if date_to else None
        return from_d, to_d, None
    # No date filter supplied — enforce a 1-month floor so old jobs never appear.
    return None, None, "1m"


# ---------------------------------------------------------------------------
# Background ingestion task (runs in a thread pool via BackgroundTasks)
# ---------------------------------------------------------------------------

def _run_ingestion(
    keywords: list[str],
    location: str | None,
    results_wanted: int,
    weaviate_store: WeaviateStore,
    relational_store: RelationalStore,
) -> None:
    """Iterate all enabled adapters and ingest jobs into Weaviate + DuckDB."""
    cfg = _load_sources_cfg()
    sources_cfg: dict[str, Any] = cfg.get("sources", {})

    adapters = _build_adapters(sources_cfg)
    size_map = _company_size_map(sources_cfg)

    # Per-run cache: normalized company name -> size bucket, populated as jobs
    # are enriched so repeated companies in the same run reuse an estimate
    # rather than each triggering a fresh LLM guess. Config (size_map) still
    # takes precedence over anything cached here.
    run_size_cache: dict[str, str] = {}
    # Per-run count of jobs kept per company (flood cap, see constant above).
    run_company_counts: dict[str, int] = {}
    # Per-run accumulated locations per collapsed job_id, so a role posted in
    # several cities ends up as one job listing all of them.
    run_locations: dict[str, set[str]] = {}

    http = CompliantHttpClient()
    try:
        for adapter in adapters:
            run_log: RunLog = relational_store.start_run(adapter.name)
            count_ingested = 0
            count_failed = 0
            error_msg: str | None = None

            try:
                for raw in adapter.search(
                    keywords=keywords,
                    location=location,
                    results_wanted=results_wanted,
                    since=None,
                    http=http,
                ):
                    try:
                        job: Job = raw_to_job(raw, source=adapter.name)

                        # JobScout is US-only: drop non-US jobs before the
                        # expensive enrichment/embedding steps.
                        if not is_us_job(
                            job.country, job.location_raw, job.remote_mode
                        ):
                            continue

                        # Drop jobs older than 30 days — only skip when we
                        # have a reliable date (posted_date_est stays).
                        if job.posted_date is not None:
                            age_cutoff = datetime.now(UTC) - timedelta(days=30)
                            if job.posted_date < age_cutoff:
                                continue

                        # Company size: exact value from config (Greenhouse/Lever
                        # company list) takes precedence over the LLM estimate.
                        company_key = job.company.lower() if job.company else None
                        if company_key:
                            # Per-company flood cap.
                            if run_company_counts.get(company_key, 0) >= MAX_JOBS_PER_COMPANY_PER_RUN:
                                continue
                            run_company_counts[company_key] = (
                                run_company_counts.get(company_key, 0) + 1
                            )
                            cfg_size = size_map.get(company_key)
                            if cfg_size:
                                job.company_size_bucket = cfg_size

                        # Idempotent enrichment: if this exact job is already in
                        # Weaviate and enriched, reuse those fields and skip the
                        # paid DeepSeek call entirely.
                        existing = weaviate_store.get_by_id(job.job_id)
                        if existing is not None and existing.enrichment_status == "done":
                            job.yoe_min = existing.yoe_min
                            job.yoe_max = existing.yoe_max
                            job.visa_sponsorship = existing.visa_sponsorship
                            job.skills = existing.skills
                            job.seniority = existing.seniority
                            if job.employment_type == "unknown":
                                job.employment_type = existing.employment_type
                            if not job.company_size_bucket:
                                job.company_size_bucket = existing.company_size_bucket
                            job.enrichment_status = "done"
                            if company_key and job.company_size_bucket:
                                run_size_cache.setdefault(
                                    company_key, job.company_size_bucket
                                )
                        # LLM enrichment (DeepSeek): YoE, visa, skills, seniority,
                        # and company-size fallback. Skipped if no key/description.
                        elif settings.deepseek_api_key and job.description:
                            try:
                                enr = extract_enrichment(
                                    job.title, job.company, job.description
                                )
                            except EnrichmentError:
                                # Hard failure (outage/rate-limit/unparseable):
                                # store the job but record the honest status so a
                                # blank-field record isn't mistaken for success.
                                log.warning(
                                    "enrichment_failed job_id=%s company=%s",
                                    job.job_id, job.company, exc_info=True,
                                )
                                job.enrichment_status = "failed"
                            else:
                                job.yoe_min = enr.get("yoe_min")
                                job.yoe_max = enr.get("yoe_max")
                                job.visa_sponsorship = enr.get(
                                    "visa_sponsorship", "not_mentioned"
                                )
                                job.skills = enr.get("skills", [])
                                job.seniority = enr.get("seniority", "unclear")
                                if not job.company_size_bucket:
                                    # Prefer a size already estimated for this
                                    # company earlier in the run; else the LLM's.
                                    cached = (
                                        run_size_cache.get(company_key)
                                        if company_key else None
                                    )
                                    job.company_size_bucket = (
                                        cached or enr.get("company_size_bucket")
                                    )
                                if company_key and job.company_size_bucket:
                                    run_size_cache.setdefault(
                                        company_key, job.company_size_bucket
                                    )
                                if job.employment_type == "unknown":
                                    job.employment_type = enr.get(
                                        "employment_type", "unknown"
                                    )
                                job.enrichment_status = "done"

                        # Aggregate locations: union this posting's location with
                        # any already collected this run and any already stored,
                        # so the collapsed job lists every location it appears in.
                        loc_set = set(run_locations.get(job.job_id, set()))
                        if existing is not None and existing.locations:
                            loc_set.update(existing.locations)
                        loc_set.update(job.locations)
                        if job.location_raw:
                            loc_set.add(job.location_raw)
                        job.locations = sorted(loc for loc in loc_set if loc)
                        run_locations[job.job_id] = loc_set

                        vector = embed_job(
                            title=job.title,
                            company=job.company,
                            skills=job.skills,
                            description=job.description,
                        )
                        weaviate_store.upsert(job, vector)

                        from jobscout.models import JobSource

                        js = JobSource(
                            job_id=job.job_id,
                            source=job.source,
                            url=job.url,
                            posted_date=job.posted_date,
                        )
                        relational_store.upsert_job_source(js)
                        count_ingested += 1
                    except Exception:
                        log.warning("ingest_item_failed", exc_info=True)
                        count_failed += 1
            except Exception:
                error_msg = traceback.format_exc()
                log.error(
                    "adapter_run_failed adapter=%s", adapter.name, exc_info=True
                )

            relational_store.finish_run(
                run_id=run_log.id,
                count_ingested=count_ingested,
                count_failed=count_failed,
                error=error_msg,
            )
    finally:
        http.close()


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
        None, description="24h|7d|14d|21d|1m|custom"
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
    employment_type: list[str] | None = Query(
        None, description="repeatable: full_time|part_time|contract|internship|temporary"
    ),
    category: list[str] | None = Query(
        None, description="repeatable: software_eng|data_ml_ai|devops_infra|security|product_mgmt|design_ux|management|other"
    ),
    alpha: float = Query(
        0.5, ge=0.0, le=1.0, description="Hybrid blend: 0=keyword, 1=vector"
    ),
    sort: str = Query(
        "relevance", description="posted_desc|relevance|salary_desc"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
) -> JobsResponse:
    from_d, to_d, preset = _date_range_to_dates(date_range, date_from, date_to)

    filters = build_filters(
        remote=remote,
        visa=visa,
        source=source,
        company_size=company_size,
        employment_type=employment_type,
        category=category,
        exp=exp,
        date_range=preset,
        date_from=from_d,
        date_to=to_d,
    )

    store: WeaviateStore = request.app.state.weaviate_store
    result = execute_search(
        store=store,
        q=q,
        alpha=alpha,
        filters=filters,
        sort=sort,
        page=page,
        page_size=page_size,
    )

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


def _autofetch_and_clear(
    keywords: list[str],
    location: str | None,
    weaviate_store: WeaviateStore,
    relational_store: RelationalStore,
    key: str,
) -> None:
    """Background ingestion triggered by a sparse search; clears the in-flight flag."""
    try:
        _run_ingestion(keywords, location, 20, weaviate_store, relational_store)
    finally:
        _autofetch_inflight.discard(key)


@app.get("/api/jobs/{job_id}", response_model=Job, tags=["jobs"])
async def get_job(job_id: str, request: Request) -> Job:
    store: WeaviateStore = request.app.state.weaviate_store
    job = store.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


@app.post("/api/search/run", response_model=list[RunLog], tags=["ingestion"])
async def trigger_search_run(
    body: dict[str, Any],
    request: Request,
    background_tasks: BackgroundTasks,
) -> list[RunLog]:
    """Trigger an on-demand ingestion run (non-blocking).

    Body schema::

        {
            "keywords":        ["data scientist"],
            "location":        "remote",
            "results_wanted":  50
        }

    Returns one ``RunLog`` stub per enabled source (status ``running``).
    The actual ingestion happens in the background.
    """
    keywords: list[str] = body.get("keywords", ["data scientist"])
    location: str | None = body.get("location")
    results_wanted: int = int(body.get("results_wanted", 50))

    weaviate_store: WeaviateStore = request.app.state.weaviate_store
    relational_store: RelationalStore = request.app.state.relational_store

    # Create placeholder RunLog stubs for each enabled source so the caller
    # gets an immediate response, then start the real work in the background.
    cfg = _load_sources_cfg()
    sources_cfg: dict[str, Any] = cfg.get("sources", {})

    enabled_sources: list[str] = _enabled_source_names(sources_cfg)

    stub_logs: list[RunLog] = []
    for source_name in enabled_sources:
        run_log = relational_store.start_run(source_name)
        stub_logs.append(run_log)

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

def _run_enrichment(weaviate_store: WeaviateStore, limit: int) -> None:
    """Enrich jobs whose status is ``pending`` or ``failed`` (bounded by *limit*).

    Lets enrichment be retried/decoupled from ingestion — e.g. to recover jobs
    that failed during a DeepSeek outage. Preserves a company-size value that was
    already set (e.g. from config) and only fills it from the LLM when empty.
    """
    if not settings.deepseek_api_key:
        log.warning("enrich_run skipped: DEEPSEEK_API_KEY not configured")
        return

    collection = weaviate_store._client.collections.get(COLLECTION_NAME)
    targets: list[Any] = []
    for obj in collection.iterator():
        p = dict(obj.properties)
        if p.get("enrichment_status") in ("pending", "failed"):
            targets.append(p)
            if len(targets) >= limit:
                break

    enriched = failed = 0
    for p in targets:
        job_id = p.get("job_id")
        description = p.get("description") or None
        if not job_id or not description:
            continue
        try:
            enr = extract_enrichment(p.get("title", ""), p.get("company") or None, description)
        except EnrichmentError:
            weaviate_store.update_fields(job_id, {"enrichment_status": "failed"})
            failed += 1
            continue

        fields: dict[str, Any] = {
            "yoe_min": enr.get("yoe_min"),
            "yoe_max": enr.get("yoe_max"),
            "visa_sponsorship": enr.get("visa_sponsorship", "not_mentioned"),
            "skills": enr.get("skills", []),
            "seniority": enr.get("seniority", "unclear"),
            "enrichment_status": "done",
        }
        # Only set company size from the LLM if not already known (config wins).
        if not (p.get("company_size_bucket") or "") and enr.get("company_size_bucket"):
            fields["company_size_bucket"] = enr["company_size_bucket"]
        # Only set employment type from the LLM if the adapter didn't determine it.
        if (p.get("employment_type") or "unknown") == "unknown" and enr.get("employment_type"):
            fields["employment_type"] = enr["employment_type"]
        weaviate_store.update_fields(job_id, fields)
        enriched += 1

    log.info("enrich_run complete enriched=%d failed=%d scanned=%d", enriched, failed, len(targets))


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


@app.post("/api/purge/old", tags=["operations"])
async def purge_old_jobs(request: Request) -> dict[str, Any]:
    """Delete all jobs older than 30 days from the Weaviate index.

    Uses ``posted_date`` as the primary signal; falls back to ``ingested_at``
    for jobs where the posted date is unknown.  Returns the count deleted.
    """
    store: WeaviateStore = request.app.state.weaviate_store
    cutoff = datetime.now(UTC) - timedelta(days=30)
    deleted = store.purge_older_than(cutoff)
    log.info("purge_old_jobs deleted=%d cutoff=%s", deleted, cutoff.isoformat())
    return {"deleted": deleted, "cutoff": cutoff.isoformat()}


@app.get("/api/sources/status", tags=["operations"])
async def sources_status(request: Request) -> list[dict[str, Any]]:
    """Return per-source last run info and configuration status."""
    relational_store: RelationalStore = request.app.state.relational_store
    return relational_store.get_sources_status()


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
    }
