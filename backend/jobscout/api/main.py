"""FastAPI application — JobScout REST API."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from dotenv import set_key
from fastapi import (
    BackgroundTasks,
    Body,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from jobscout import security as security_guards
from jobscout.api.admin import router as admin_router
from jobscout.api.deps import current_user_id, effective_owner, owned_profile, require_admin
from jobscout.blob import blob_store
from jobscout.config import settings
from jobscout.deep_match import compute_deep_match, deep_match_fingerprint
from jobscout.embed import embedding_quota_hit
from jobscout.enrich import (
    SUPPORTED_LLM_PROVIDERS,
    EnrichmentError,
    active_llm_configuration,
    llm_is_configured,
)
from jobscout.entitlements import QuotaExceeded, check_quota, record_usage
from jobscout.logging_config import configure_logging
from jobscout.models import (
    GHOST_STALE_DAYS,
    Company,
    Job,
    JobsResponse,
    PipelineAnalytics,
    ResumeRecord,
    RunLog,
    SavedSearch,
    TailoredResumeRecord,
    UserProfile,
)
from jobscout.normalize import compute_job_id
from jobscout.relational import RelationalStore, make_relational_store
from jobscout.resume import (
    compose_resume_text,
    compose_resume_text_from_structured,
    dedupe_name,
    extract_resume_sections,
    extract_resume_text,
    needs_section_rebuild,
    parse_resume_to_profile,
    parse_structured_resume,
    resume_file_path,
    store_library_resume,
    store_original_resume,
)
from jobscout.search import (
    PROGRESSIVE_LADDER,
    RECOMMENDATION_LADDER,
    build_filters,
    execute_search,
)
from jobscout.services import scoring_cache
from jobscout.services.ingestion_service import (
    _REFRESH_ADAPTER,
    AUTOFETCH_MAX_INFLIGHT,
    AUTOFETCH_MIN_RESULTS,
    _autofetch_and_clear,
    _autofetch_inflight,
    _profile_autofetch_and_clear,
    _refresh_watchlist,
    _run_enrichment,
    _run_ingestion,
    seed_first_run,
)
from jobscout.services.query_service import (
    MATCH_WINDOW,
    _count_matches,
    _date_range_to_dates,
    _dedupe_jobs,
    _match_resume_to_jobs,
    _profile_search_query,
    _semantic_scores,
)
from jobscout.services.source_config import (
    _RUNTIME_SOURCE_OVERRIDES,
    _TOGGLABLE_SOURCES,
    _enabled_source_names,
    _load_sources_cfg,
)
from jobscout.source_intelligence import source_kind
from jobscout.store import COLLECTION_NAME, WeaviateStore
from jobscout.tailor import (
    EligibilityError,
    TailoringError,
    build_tailored_resume,
    tailored_resume_filename,
    tailored_resume_path,
)
from jobscout.verdict import match_key, priority_key
from jobscout.verdict import score as score_verdict

log = logging.getLogger(__name__)


def _serve_file(path: Path, *, media_type: str, filename: str) -> Response:
    """Download a stored file through the blob seam.

    Local backend → zero-copy ``FileResponse`` from disk; a remote backend
    (Supabase Storage) → streamed bytes with a download filename. Works for both
    without the routes knowing which backend is active.
    """
    local = blob_store.local_path(path)
    if local is not None:
        return FileResponse(local, media_type=media_type, filename=filename)
    return Response(
        content=blob_store.read(path),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# A sparse/stale For You request may automatically refill the shared index for
# that profile. Bound it to one run per profile-evidence fingerprint every six
# hours so page refreshes cannot repeatedly spend API/embedding quota.
_PROFILE_REFILL_COOLDOWN = timedelta(hours=6)
_profile_refill_last_started: dict[str, datetime] = {}


# ---------------------------------------------------------------------------
# Application lifespan — open/close stores once per process
# ---------------------------------------------------------------------------

def _index_is_empty(store: WeaviateStore) -> bool:
    """True when the vector index holds no jobs (best-effort; False on error)."""
    try:
        collection = store._client.collections.get(COLLECTION_NAME)
        return (collection.aggregate.over_all(total_count=True).total_count or 0) == 0
    except Exception:  # noqa: BLE001
        return False


def _should_seed_first_run(
    store: WeaviateStore, relational: RelationalStore
) -> bool:
    """Seed only once, only when it can actually help and won't loop.

    Requires: the feature on, an embedding key (every job needs a Gemini vector),
    no prior seed marker, and a genuinely empty index.
    """
    if not settings.seed_on_first_run or not settings.google_api_key:
        return False
    if relational.get_meta("seeded_at") is not None:
        return False
    return _index_is_empty(store)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open the Weaviate + DuckDB stores for the app's lifetime, then close them."""
    configure_logging()
    # Both stores connect synchronously in their constructors.
    weaviate_store = WeaviateStore()
    relational_store = make_relational_store()
    app.state.weaviate_store = weaviate_store
    app.state.relational_store = relational_store
    # A crash mid-ingest leaves run rows stuck 'running' — reconcile them at startup.
    reaped = relational_store.reap_stale_runs()
    log.info("stores_open reaped_stale_runs=%s", reaped)
    # Project every configured ATS board plus curated direct-apply target into
    # the company registry. Only verified public boards are refreshable;
    # bespoke/Oracle/company-hosted targets remain safe outbound links.
    try:
        from jobscout.services.registry import register_configured_companies
        n = register_configured_companies(relational_store, _load_sources_cfg())
        log.info("configured_company_registry_synced count=%s", n)
    except Exception as exc:  # noqa: BLE001 — never block startup on the sync
        log.warning("configured company registry sync skipped (%s)", exc)
    # Optional daily auto-refresh (OFF by default; see jobscout.scheduler).
    from jobscout import scheduler as _sched
    _sched.start_scheduler(
        lambda: _refresh_watchlist(
            weaviate_store, relational_store, settings.embed_daily_budget, []
        )
    )
    # First-run seed: make a fresh deployment non-empty with ONE bounded, live
    # ingest (never a committed snapshot — jobs go stale). Runs in a background
    # thread so startup isn't blocked; guarded so it happens at most once.
    app.state.seeding = False
    if _should_seed_first_run(weaviate_store, relational_store):
        import threading
        app.state.seeding = True

        def _seed_worker() -> None:
            try:
                seed_first_run(weaviate_store, relational_store)
            finally:
                app.state.seeding = False

        threading.Thread(target=_seed_worker, name="first-run-seed", daemon=True).start()
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

# CORS origins are config-driven. Wildcard + credentials is invalid per the CORS
# spec (browsers reject it), so credentials are only allowed with a concrete
# allowlist. Lock `cors_allow_origins` down before deployment.
_cors_origins = settings.cors_allow_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dormant guard rails (Tier 2) — each is a no-op unless its settings flag is on.
# Registered as HTTP middleware; see jobscout/security.py + pre-deployment-checklist.md.
app.middleware("http")(security_guards.security_headers_middleware)
app.middleware("http")(security_guards.require_auth_middleware)
app.middleware("http")(security_guards.request_size_middleware)
app.middleware("http")(security_guards.rate_limit_middleware)

# Operator/admin API (monitor accounts, grant/revoke premium). Routes self-guard
# with require_admin; see jobscout/api/admin.py.
app.include_router(admin_router)


@app.middleware("http")
async def _traffic_meter(request: Request, call_next):  # noqa: ANN001, ANN201
    """Count API requests per user (dormant unless usage_metering_enabled).

    Feeds the admin dashboard's traffic metric. record_usage self-gates, so this is a
    no-op today. # ponytail: per-instance; a hosted deploy aggregates in the store.
    """
    # Skip /api/health — it's polled every few seconds and would dominate the metric.
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        with contextlib.suppress(Exception):  # metering must never break a request
            record_usage(request.app.state.relational_store, current_user_id(request), "requests")
    return await call_next(request)

# Multi-tenancy guard: every /api/profiles/{id}/… route is scoped to ONE profile,
# and every such profile has an owner. Enforcing ownership here — in one place all
# 24 profile-scoped routes pass through — makes the whole IDOR class impossible:
# a caller can never reach another user's resumes/tailored DOCX/deep-results/etc.
# 404 (not 403) so profile ids can't be enumerated. The bare collection routes
# (GET/POST /api/profiles) have no id segment and are filtered inside the handler.
# See api/deps.py — replacing current_user_id's body is the entire auth drop-in.
_PROFILE_SCOPED = re.compile(r"^/api/profiles/([^/]+)(?:/|$)")


@app.exception_handler(QuotaExceeded)
async def _quota_exceeded_handler(request: Request, exc: QuotaExceeded) -> JSONResponse:
    """Map an over-quota account to HTTP 429 (dormant unless quota_enforced)."""
    return JSONResponse(status_code=429, content={"detail": str(exc), "metric": exc.metric})


@app.middleware("http")
async def enforce_profile_ownership(request: Request, call_next):  # noqa: ANN001, ANN201
    match = _PROFILE_SCOPED.match(request.url.path)
    if match:
        profile = request.app.state.relational_store.get_profile(match.group(1))
        if profile is None or effective_owner(profile) != current_user_id(request):
            return JSONResponse(status_code=404, content={"detail": "Profile not found."})
    return await call_next(request)


# ---------------------------------------------------------------------------
# Source config + adapter construction live in services/source_config.py
# (imported above). What remains here is request orchestration only.
# ---------------------------------------------------------------------------





def _rank_scored_pairs(scored, sort: str, prefer_cap_exempt: bool) -> None:
    """Order (job, verdict) pairs in place for a personalized result set.

    ``match`` → global best-fit; ``posted_desc`` → newest first (None dates
    last) so "latest" works inside For You; anything else → the default
    verdict/priority ranking. Qualification never changes here — only order.
    """
    if sort == "posted_desc":
        scored.sort(
            key=lambda pair: (
                pair[0].posted_date is None,
                -(pair[0].posted_date.timestamp() if pair[0].posted_date else 0.0),
            )
        )
    elif sort == "match":
        scored.sort(key=lambda pair: match_key(pair[1], prefer_cap_exempt))
    else:
        scored.sort(key=lambda pair: priority_key(pair[1], prefer_cap_exempt))


def _profile_fingerprint(profile: UserProfile) -> str:
    """Stable short hash of everything in the profile that can change a verdict."""
    return sha256(profile.model_dump_json().encode("utf-8")).hexdigest()[:16]


def _score_cached(
    job: Job, profile: UserProfile, fingerprint: str, semantic: float | None = None
) -> Any:
    """``score_verdict`` with memoization (see services/scoring_cache).

    Re-scoring a 500-job candidate window on every poll dominated For You's
    latency; verdicts are pure, so the same (job, profile, semantic) triple is
    served from cache until ingestion changes the jobs.
    """
    key = (job.job_id, fingerprint, round(semantic, 4) if semantic is not None else -1.0)
    hit = scoring_cache.get(key)
    if hit is not None:
        return hit
    verdict = score_verdict(job, profile, semantic=semantic)
    scoring_cache.put(key, verdict)
    return verdict


# For You keeps widening its freshness ladder until it holds this many qualified
# recommendations (or the ladder is exhausted), and this is also the hard result
# ceiling. A ceiling exists because every recommendation is re-scored live from a
# 500-candidate window per rung; 200 ≈ two weeks of applications, and clearing
# jobs (apply/hide) continuously backfills the feed.
_RECOMMEND_MAX_RESULTS = 500


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health", tags=["system"])
async def health(request: Request) -> dict[str, Any]:
    """Deploy-time readiness with actionable fix hints for anything critical.

    ``embeddings_ok`` (Gemini key — REQUIRED, no fallback exists),
    ``llm_ok`` + the effective provider (NVIDIA optional; DeepSeek fallback),
    ``weaviate_ok`` (vector store reachable).
    """
    from jobscout.enrich import active_llm_configuration

    provider, llm_key, _model = active_llm_configuration()
    store: WeaviateStore = request.app.state.weaviate_store
    try:
        weaviate_ok = bool(store._client.is_ready())
    except Exception:  # noqa: BLE001
        weaviate_ok = False

    problems: list[dict[str, str]] = []
    if not settings.google_api_key:
        problems.append({
            "key": "google",
            "message": "GOOGLE_API_KEY is not set — embeddings power ALL search and matching.",
            "fix": "Get a free key at aistudio.google.com and add GOOGLE_API_KEY=... to .env, then restart.",
        })
    if not llm_key:
        problems.append({
            "key": "llm",
            "message": "No LLM key configured — enrichment, deep match, resume parsing and polish are disabled.",
            "fix": "Add DEEPSEEK_API_KEY=... to .env (NVIDIA_API_KEY also works and is optional), then restart.",
        })
    if not weaviate_ok:
        problems.append({
            "key": "weaviate",
            "message": "Vector database unreachable — jobs cannot be stored or searched.",
            "fix": "Run `docker compose up -d weaviate` from the repo root (Docker Desktop must be running).",
        })
    return {
        "embeddings_ok": bool(settings.google_api_key),
        "llm_ok": bool(llm_key),
        "llm_provider_effective": provider if llm_key else None,
        "weaviate_ok": weaviate_ok,
        "seeding": bool(getattr(request.app.state, "seeding", False)),
        "problems": problems,
    }


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
    exclude_ghost: bool = Query(
        False,
        description=f"Hide likely-stale 'ghost' postings (posted_date older than "
        f"{GHOST_STALE_DAYS} days). Each job also carries an advisory ghost_risk badge.",
    ),
    true_entry_only: bool = Query(
        False,
        description="True entry-level only: high-confidence entry roles (yoe_min <= 2, or "
        "junior/intern when YoE is unknown, or explicit new-grad programs). Excludes senior "
        "roles and the loose 'unclear'-seniority bucket.",
    ),
    new_grad_only: bool = Query(
        False,
        description="Only explicit new-grad / university / early-career / rotational programs "
        "(the best-fit, least-contested roles for fresh graduates).",
    ),
    h1b_sponsor: bool = Query(
        False, description="Only companies in the public DoL H-1B filer list (proven sponsors)"
    ),
    everify: bool = Query(
        False, description="Only known E-Verify employers (required for the STEM OPT extension)"
    ),
    direct_sources_only: bool = Query(
        False, description="Only official employer ATS and government job boards"
    ),
    apply_only: bool = Query(
        False,
        description="Only application-ready verdicts. Requires profile_id and excludes flags/rejects.",
    ),
    recommendation_only: bool = Query(
        False,
        description="Only profile-fit recommendations. Includes strong matches with a non-fit "
        "caveat such as unstated sponsorship, but excludes role, skill, experience, seniority, "
        "location, and authorization mismatches. Requires profile_id.",
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
        description="Progressive lookback until this many results are found. Generic/apply-only "
        "queries stop at 24h; recommendation_only can widen through 1m without unrelated filler "
        "(ignored if date_range is set).",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    background_tasks: BackgroundTasks = None,  # type: ignore[assignment]
) -> JobsResponse:
    """Search/browse jobs with filters; with a profile, attach verdicts, apply recommendation gating, and self-refill a sparse For You feed."""
    from_d, to_d, preset = _date_range_to_dates(date_range, date_from, date_to)
    store: WeaviateStore = request.app.state.weaviate_store

    def _search(
        window: str | None,
        *,
        query_override: str | None = None,
        sort_override: str | None = None,
        page_override: int | None = None,
        page_size_override: int | None = None,
        include_facets: bool = True,
    ) -> JobsResponse:
        """Run one Weaviate search for the given freshness window/overrides."""
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
            exclude_ghost=exclude_ghost,
            true_entry_only=true_entry_only,
            new_grad_only=new_grad_only,
            h1b_sponsor=h1b_sponsor,
            everify=everify,
            direct_sources_only=direct_sources_only,
            date_range=window,
            date_from=from_d,
            date_to=to_d,
            include_active=True,
        )
        return execute_search(
            store=store,
            q=query_override if query_override is not None else q,
            alpha=alpha,
            filters=filters,
            sort=sort_override if sort_override is not None else sort,
            page=page_override if page_override is not None else page,
            page_size=page_size_override if page_size_override is not None else page_size,
            include_facets=include_facets,
        )

    profile: UserProfile | None = None
    profile_fp = ""
    excluded: set[str] = set()
    relational: RelationalStore = request.app.state.relational_store
    if profile_id:
        profile = owned_profile(profile_id, request)  # 404 unless the caller owns it
        excluded = relational.get_excluded_job_ids(profile_id)
        profile_fp = _profile_fingerprint(profile)
    elif apply_only or recommendation_only:
        mode = "apply_only" if apply_only else "recommendation_only"
        raise HTTPException(status_code=422, detail=f"{mode} requires profile_id.")

    # The Fresh Apply shortlist must widen based on qualified, unique Apply
    # roles, not the raw number of postings in a window. Otherwise a busy six
    # hours full of senior or ineligible jobs can incorrectly stop the ladder.
    profile_result_ready = False
    personalized_only = apply_only or recommendation_only
    if target_min and date_range is None and personalized_only and profile is not None:
        semantic = _semantic_scores(profile, store)
        recommendation_query = _profile_search_query(profile) if not q else None
        qualified: list[tuple[Job, Any]] = []
        candidates: JobsResponse | None = None
        # Recommendations: ONE search of the widest window. The old 3-rung walk
        # almost always reached "1m" anyway (fill target ≫ typical qualified
        # count) and each rung repeated a 500-object hybrid fetch — 3× the cost
        # for identical results, since a wider window is a superset and the
        # final ordering is fit/date-based, not rung-based.
        # The Fresh Apply shortlist keeps the ladder: it is deliberately
        # fresh-first and stops at the first rung with target_min hits.
        ladder = [RECOMMENDATION_LADDER[-1]] if recommendation_only else PROGRESSIVE_LADDER
        used = ladder[0]
        for window in ladder:
            try:
                candidates = _search(
                    window,
                    query_override=recommendation_query,
                    sort_override="relevance" if sort == "match" else sort,
                    page_override=1,
                    page_size_override=MATCH_WINDOW,
                    include_facets=False,  # 8 aggregate round-trips, unused here
                )
            except Exception:  # noqa: BLE001 — retrieval falls back; verdicts stay strict
                if recommendation_query is None:
                    raise
                log.warning("profile recommendation retrieval failed; using recent candidates", exc_info=True)
                recommendation_query = None
                candidates = _search(
                    window,
                    sort_override="relevance" if sort == "match" else sort,
                    page_override=1,
                    page_size_override=MATCH_WINDOW,
                    include_facets=False,
                )
            scored = [
                (job, _score_cached(job, profile, profile_fp, semantic.get(job.job_id)))
                for job in candidates.jobs
                if job.job_id not in excluded
            ]
            if apply_only:
                scored = [pair for pair in scored if pair[1].verdict == "apply"]
            else:
                scored = [pair for pair in scored if pair[1].recommendable]
            _rank_scored_pairs(scored, sort, profile.prefer_cap_exempt)

            verdict_by_id = {verdict.job_id: verdict for _, verdict in scored}
            unique_jobs = _dedupe_jobs([job for job, _ in scored]) if dedupe else [
                job for job, _ in scored
            ]
            qualified = [
                (job, verdict_by_id[job.job_id])
                for job in unique_jobs
                if job.job_id in verdict_by_id
            ]
            used = window
            if len(qualified) >= target_min:
                break

        assert candidates is not None
        qualified = qualified[:_RECOMMEND_MAX_RESULTS]
        effective_page_size = min(page_size, _RECOMMEND_MAX_RESULTS)
        start = (page - 1) * effective_page_size
        page_slice = qualified[start : start + effective_page_size]
        result = candidates
        result.total = len(qualified)
        result.page = page
        result.page_size = effective_page_size
        result.jobs = [job for job, _ in page_slice]
        result.verdicts = {verdict.job_id: verdict for _, verdict in page_slice}
        result.lookback_window = used
        profile_result_ready = True
    elif target_min and date_range is None:
        # Generic progressive search still widens on raw results because there
        # is no profile verdict to evaluate.
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
        # Keep the UI's window tag alive when the user picks an explicit
        # window (the ladder paths set this; this path must too).
        result.lookback_window = preset

    # Profile-driven verdict layer. Two orderings:
    #   - sort="match": a GLOBAL "Best Match" sort. Score a bounded candidate
    #     window (MATCH_WINDOW), order by match_key (highest fit% first), and
    #     paginate in-memory — so the top of page 1 is the best match overall,
    #     not just the best on whatever page Weaviate returned.
    #   - otherwise: the default cap-exempt-first priority sort on the page.
    # When no profile is supplied the un-profiled behaviour above is returned
    # unchanged (and sort="match" degrades to relevance, since fit needs a profile).
    if profile is not None and not profile_result_ready:
        # Apply-only needs a larger candidate set before pagination. Filtering a
        # single Weaviate page would create arbitrary empty pages and hide better
        # jobs just beyond the raw page boundary.
        if sort == "match" or personalized_only:
            candidate_window = result.lookback_window or preset
            recommendation_query = (
                _profile_search_query(profile) if personalized_only and not q else None
            )
            try:
                candidates = _search(
                    candidate_window,
                    query_override=recommendation_query,
                    sort_override="relevance" if sort == "match" else sort,
                    page_override=1,
                    page_size_override=MATCH_WINDOW,
                    include_facets=False,
                )
            except Exception:  # noqa: BLE001 — semantic retrieval is optional
                if recommendation_query is None:
                    raise
                log.warning("profile recommendation retrieval failed; using recent candidates", exc_info=True)
                candidates = _search(
                    candidate_window,
                    sort_override="relevance" if sort == "match" else sort,
                    page_override=1,
                    page_size_override=MATCH_WINDOW,
                    include_facets=False,
                )
            # Semantic blend: resume↔job cosine similarity, if the profile has a
            # resume. Cached per profile; degrades to deterministic-only on failure.
            sem = _semantic_scores(profile, store)
            scored = [
                (job, _score_cached(job, profile, profile_fp, sem.get(job.job_id)))
                for job in candidates.jobs
                if job.job_id not in excluded
            ]
            if apply_only:
                scored = [pair for pair in scored if pair[1].verdict == "apply"]
            elif recommendation_only:
                scored = [pair for pair in scored if pair[1].recommendable]
            _rank_scored_pairs(scored, sort, profile.prefer_cap_exempt)
            # Personalized totals and pagination must describe the cards the
            # user can actually see.  Deduplicating only after slicing made a
            # response report (for example) five matches while rendering four.
            # Collapse the complete scored window first so duplicates neither
            # inflate ``total`` nor consume a page slot.
            if dedupe:
                verdict_by_id = {verdict.job_id: verdict for _, verdict in scored}
                unique_jobs = _dedupe_jobs([job for job, _ in scored])
                scored = [
                    (job, verdict_by_id[job.job_id])
                    for job in unique_jobs
                    if job.job_id in verdict_by_id
                ]
            start = (page - 1) * page_size
            page_slice = scored[start : start + page_size]
            result = candidates
            result.total = len(scored)
            result.jobs = [job for job, _ in page_slice]
            result.verdicts = {v.job_id: v for _, v in page_slice}
        else:
            scored = [
                (job, _score_cached(job, profile, profile_fp))
                for job in result.jobs
                if job.job_id not in excluded
            ]
            scored.sort(
                key=lambda pair: priority_key(pair[1], profile.prefer_cap_exempt)
            )
            result.jobs = [job for job, _ in scored]
            result.verdicts = {v.job_id: v for _, v in scored}
    # Collapse near-duplicate reposts on this page (same company+title across boards),
    # keeping the most authoritative source. Page-scoped (MVP). Toggle with dedupe=false.
    if dedupe and not profile_result_ready:
        result.jobs = _dedupe_jobs(result.jobs)

    # Auto-refill For You when the qualified feed is sparse or had to widen past
    # one day. This runs the same compliant, budget-capped ingestion pipeline as
    # the explicit "Find profile matches" action; unrelated jobs still cannot
    # enter the response because every read is gated by ``recommendable``.
    if (
        recommendation_only
        and profile is not None
        and background_tasks is not None
        and target_min is not None
        and date_range is None
        and profile.target_titles
    ):
        fingerprint = sha256(
            profile.model_dump_json().encode("utf-8")
        ).hexdigest()[:16]
        refill_key = f"profile:{profile.id}:{fingerprint}"
        stale_window = result.lookback_window in {"7d", "14d", "21d", "1m"}
        needs_refill = result.total < (target_min or 5) or stale_window
        now = datetime.now(UTC)
        last_started = _profile_refill_last_started.get(refill_key)
        cooldown_elapsed = (
            last_started is None or now - last_started >= _PROFILE_REFILL_COOLDOWN
        )
        if refill_key in _autofetch_inflight:
            result.recommendation_refreshing = True
        elif (
            needs_refill
            and cooldown_elapsed
            and len(_autofetch_inflight) < AUTOFETCH_MAX_INFLIGHT
        ):
            if len(_profile_refill_last_started) > 64:
                # Evict only the stalest entry — a bulk clear would reset every
                # profile's cooldown at once (refill stampede).
                oldest = min(_profile_refill_last_started, key=_profile_refill_last_started.get)  # type: ignore[arg-type]
                _profile_refill_last_started.pop(oldest, None)
            _profile_refill_last_started[refill_key] = now
            _autofetch_inflight.add(refill_key)
            result.recommendation_refreshing = True
            background_tasks.add_task(
                _profile_autofetch_and_clear,
                # Raw target titles skew senior on most boards; entry terms also
                # capture "New Grad SWE 2026"-style titles that the verdict
                # gates actually let through for a junior profile.
                [*profile.target_titles, "new grad", "early career", "2026 graduate"],
                request.app.state.weaviate_store,
                request.app.state.relational_store,
                profile,
                refill_key,
                50,
            )

    # Auto-fetch: a sparse keyword search quietly pulls fresh jobs in the
    # background (LinkedIn/Indeed style), deduped so we never double-trigger.
    if (
        q
        and q.strip()
        and not personalized_only
        and result.total < AUTOFETCH_MIN_RESULTS
        and background_tasks is not None
    ):
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
    profile = owned_profile(profile_id, request)  # 404 unless the caller owns it
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
    """Fetch a single job by its dedup id (404 when absent)."""
    store: WeaviateStore = request.app.state.weaviate_store
    job = store.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    return job


@app.post("/api/match/deep/{job_id}", tags=["jobs"])
async def deep_match_job(
    job_id: str, body: dict[str, Any], request: Request
) -> dict[str, Any]:
    """LLM "second opinion" on a single job vs. a profile.

    Body: ``{"profile_id": "..."}``. Returns
    ``{verdict, score, strengths, gaps, summary, cached}``. The DeepSeek call is
    run off the event loop; results are cached per (job_id, profile_id). Never
    raises on LLM failure — falls back to a neutral ``borderline`` verdict.
    """
    store: WeaviateStore = request.app.state.weaviate_store
    job = store.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    profile_id = (body.get("profile_id") or "").strip()
    if not profile_id:
        raise HTTPException(status_code=422, detail="profile_id is required.")
    relational: RelationalStore = request.app.state.relational_store
    profile = owned_profile(profile_id, request)  # 404 unless the caller owns it
    uid = current_user_id(request)
    check_quota(relational, uid, "deep_match")  # dormant unless quota_enforced
    result = await run_in_threadpool(compute_deep_match, job, profile, relational)
    record_usage(relational, uid, "deep_match")
    return result


@app.post("/api/profiles/{profile_id}/deep-results", tags=["jobs"])
async def profile_deep_results(
    profile_id: str, request: Request, body: dict = Body(...)
) -> dict[str, Any]:
    """Return already-computed deep-match results for the given jobs (no LLM spend).

    Only a result whose stored fingerprint still matches the CURRENT
    (profile + resume + job) fingerprint is returned — so a changed profile/resume
    surfaces nothing stale. Used to rehydrate card badges + the detail pane on load.
    """
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    store: WeaviateStore = request.app.state.weaviate_store
    job_ids = [str(j) for j in (body.get("job_ids") or [])][:500]
    results: dict[str, Any] = {}
    for jid in job_ids:
        job = store.get_by_id(jid)
        if job is None:
            continue
        hit = relational.get_deep_match(jid, profile_id, deep_match_fingerprint(job, profile))
        if hit is not None:
            results[jid] = {**hit, "cached": True}
    return {"results": results}


@app.get("/api/jobs/{job_id}/profile-fits", tags=["jobs"])
async def job_profile_fits(job_id: str, request: Request) -> dict[str, Any]:
    """Score ONE job against EVERY saved profile — deterministic, no LLM, no embedding.

    Answers "which of my profiles should I tailor this job with?": the UI defaults
    the tailor action to the best-fitting profile and shows each profile's fit %.
    """
    store: WeaviateStore = request.app.state.weaviate_store
    job = store.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    relational: RelationalStore = request.app.state.relational_store
    fits: list[dict[str, Any]] = []
    for profile in relational.list_profiles(current_user_id(request)):
        verdict = score_verdict(job, profile)  # semantic omitted → weights renormalize
        fits.append({
            "profile_id": profile.id,
            "label": profile.label,
            "score": round(verdict.score, 4),
            "verdict": verdict.verdict,
            "recommendable": verdict.recommendable,
        })
    fits.sort(key=lambda f: float(f["score"]), reverse=True)
    return {"fits": fits}


# ── Settings: switch vector-store / key backends from the UI ──────────────────
# Maps editable settings fields → their .env keys. Keys are written server-side to
# the gitignored .env (never returned to or stored in the browser).
_SETTING_ENV = {
    "storage_mode": "STORAGE_MODE",
    "google_api_key": "GOOGLE_API_KEY",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_model": "DEEPSEEK_MODEL",
    "llm_provider": "LLM_PROVIDER",
    "nvidia_api_key": "NVIDIA_API_KEY",
    "nvidia_model": "NVIDIA_MODEL",
    "weaviate_cluster_url": "WEAVIATE_CLUSTER_URL",
    "weaviate_api_key": "WEAVIATE_API_KEY",
}
_STORAGE_MODES = {"both", "cloud", "local"}
_STORAGE_RECONNECT_FIELDS = {
    "storage_mode", "weaviate_cluster_url", "weaviate_api_key",
}


@app.get("/api/settings", tags=["settings"])
async def get_settings(request: Request) -> dict[str, Any]:
    """Current backend wiring for the Settings panel. Reports key *presence* only —
    never the secret values."""
    store: WeaviateStore = request.app.state.weaviate_store
    provider, _api_key, model = active_llm_configuration()
    return {
        "storage_mode": settings.storage_mode,
        "backend": store.backend_status(),  # {primary, mirror, dual_write}
        "keys_present": {
            "google": bool(settings.google_api_key),
            "deepseek": bool(settings.deepseek_api_key),
            "nvidia": bool(settings.nvidia_api_key),
            "weaviate_cloud": bool(settings.weaviate_cluster_url and settings.weaviate_api_key),
        },
        "llm": {
            "provider": provider,
            "model": model,
            "configured": llm_is_configured(),
        },
    }


@app.put("/api/settings", tags=["settings"])
async def update_settings(body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Persist storage_mode / API keys to .env (+ runtime) and reconnect the vector
    store so the change takes effect immediately. Auto-degrades (e.g. cloud→local)
    are reflected in the returned ``backend``. Secrets are never echoed back."""
    require_admin(request)  # writes the server .env — never a per-user action
    applied: list[str] = []
    for field, env_key in _SETTING_ENV.items():
        if field not in body or body[field] is None:
            continue
        val = str(body[field])
        if field == "storage_mode" and val not in _STORAGE_MODES:
            raise HTTPException(
                status_code=422, detail=f"storage_mode must be one of {sorted(_STORAGE_MODES)}"
            )
        if field == "llm_provider" and val not in SUPPORTED_LLM_PROVIDERS:
            raise HTTPException(
                status_code=422,
                detail=f"llm_provider must be one of {sorted(SUPPORTED_LLM_PROVIDERS)}",
            )
        setattr(settings, field, val)  # runtime effect
        try:
            set_key(".env", env_key, val)  # persist to gitignored .env
        except Exception as exc:  # noqa: BLE001
            log.warning("could not persist %s to .env: %s", env_key, exc)
        applied.append(field)

    # Only storage changes need a reconnect. Switching the chat provider/model
    # takes effect lazily on the next LLM request and should not interrupt the
    # job index merely because a user saved an NVIDIA model choice.
    if set(applied) & _STORAGE_RECONNECT_FIELDS:
        old = request.app.state.weaviate_store
        try:
            new_store = await run_in_threadpool(WeaviateStore)
        except Exception as exc:  # noqa: BLE001
            log.error("settings reconnect failed, keeping previous store: %s", exc)
            raise HTTPException(status_code=503, detail=f"Could not apply settings: {exc}") from exc
        request.app.state.weaviate_store = new_store
        await run_in_threadpool(old.close)

    store: WeaviateStore = request.app.state.weaviate_store
    return {
        "applied": applied,
        "storage_mode": settings.storage_mode,
        "backend": store.backend_status(),
        "llm": {
            "provider": active_llm_configuration()[0],
            "model": active_llm_configuration()[2],
            "configured": llm_is_configured(),
        },
    }


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
        profile = owned_profile(body["profile_id"], request)  # 404 unless the caller owns it
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
    security_guards.enforce_upload_limits(file, data)  # dormant unless upload_limits_enabled
    text = extract_resume_text(file.filename or "resume.txt", data)
    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail="Could not extract any text from the file (is it a scanned image?).",
        )
    relational: RelationalStore = request.app.state.relational_store
    # Re-uploading the same file must not create indistinguishable duplicate
    # profiles — auto-suffix the label ("name (2)") when it's already taken.
    uid = current_user_id(request)
    label = dedupe_name(
        (file.filename or "resume").rsplit(".", 1)[0][:60],
        {p.label for p in relational.list_profiles(uid)},
    )
    try:
        profile = parse_resume_to_profile(text, label=label)
    except EnrichmentError as exc:
        raise HTTPException(status_code=502, detail=f"Resume parse failed: {exc}") from exc
    profile.user_id = uid  # the uploader owns the new profile

    try:
        profile.resume_filename = store_original_resume(
            profile.id, file.filename or "resume", data
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save original resume locally: {exc}") from exc
    profile.resume_content_type = file.content_type
    profile.resume_uploaded_at = datetime.now(UTC)

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
    profile.user_id = current_user_id(request)  # the caller owns what they create
    return relational.upsert_profile(profile)


@app.put("/api/profiles/{profile_id}", response_model=UserProfile, tags=["profiles"])
async def update_profile(
    profile_id: str, profile: UserProfile, request: Request
) -> UserProfile:
    """Replace an existing profile with its edited canonical matching record."""
    relational: RelationalStore = request.app.state.relational_store
    existing = relational.get_profile(profile_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found.")

    # The URL owns identity. Retain document metadata if an editor only changes
    # the extracted text and matching preferences.
    profile.id = profile_id
    profile.user_id = existing.user_id or current_user_id(request)  # owner is not editable via the body
    # Label collisions make profiles indistinguishable (and broke renames once) —
    # auto-suffix "name (2)" against every OTHER profile's label (same owner).
    if profile.label != existing.label:
        profile.label = dedupe_name(
            profile.label,
            {p.label for p in relational.list_profiles(profile.user_id) if p.id != profile_id},
        )
    if profile.resume_text is None:
        profile.resume_text = existing.resume_text
    structured_edited = (
        profile.structured_resume is not None
        and (
            existing.structured_resume is None
            or profile.structured_resume.model_dump() != existing.structured_resume.model_dump()
        )
    )
    if profile.structured_resume is None:
        profile.structured_resume = existing.structured_resume
    # GET responses may re-derive resume_sections without saving them, so a plain
    # GET→PUT round trip (e.g. a rename) can carry sections that differ from the
    # STORED ones without the user editing anything. Only treat sections as edited
    # when they differ from the stored sections AND from what the current text
    # derives to — otherwise the recompose below would silently rewrite the
    # lossless canonical resume_text.
    incoming_sections = [s.model_dump() for s in profile.resume_sections]
    sections_edited = (
        bool(profile.resume_sections)
        and incoming_sections != [s.model_dump() for s in (existing.resume_sections or [])]
        and incoming_sections
        != [s.model_dump() for s in extract_resume_sections(existing.resume_text or "")]
    )
    if structured_edited:
        # Typed sections are the source of truth when edited: recompose the
        # canonical flat text (what matching embeds) and the flat sections.
        assert profile.structured_resume is not None  # guarded by structured_edited
        profile.resume_text = compose_resume_text_from_structured(profile.structured_resume)
        profile.resume_sections = extract_resume_sections(profile.resume_text)
        profile.structured_stale = False  # structured is now the source → in sync
    elif sections_edited and profile.resume_text == existing.resume_text:
        # Section-level edit from the Profile UI: the sections are the source of
        # truth — recompose the flat text so semantic matching (and the
        # hash-keyed resume-embedding cache) pick the edits up automatically.
        profile.resume_text = compose_resume_text(profile.resume_sections)
    elif profile.resume_text != existing.resume_text:
        profile.resume_sections = extract_resume_sections(profile.resume_text or "")
        # Raw-text edit: flat sections re-derive for free, but the TYPED structured
        # cards can't without an LLM parse — flag them stale so the UI can prompt a Rebuild.
        if profile.structured_resume is not None:
            profile.structured_stale = True
    elif not profile.resume_sections:
        profile.resume_sections = existing.resume_sections or extract_resume_sections(profile.resume_text or "")
    if profile.resume_filename is None:
        profile.resume_filename = existing.resume_filename
    if profile.resume_content_type is None:
        profile.resume_content_type = existing.resume_content_type
    if profile.resume_uploaded_at is None:
        profile.resume_uploaded_at = existing.resume_uploaded_at
    return relational.upsert_profile(profile)


@app.get("/api/profiles", response_model=list[UserProfile], tags=["profiles"])
async def list_profiles(request: Request) -> list[UserProfile]:
    """Return the calling user's saved profiles."""
    relational: RelationalStore = request.app.state.relational_store
    profiles = relational.list_profiles(current_user_id(request))
    for profile in profiles:
        if (
            (not profile.resume_sections or needs_section_rebuild(profile.resume_sections, profile.resume_text))
            and (profile.resume_text or "").strip()
        ):
            profile.resume_sections = extract_resume_sections(profile.resume_text or "")
    return profiles


@app.get("/api/profiles/{profile_id}", response_model=UserProfile, tags=["profiles"])
async def get_profile(profile_id: str, request: Request) -> UserProfile:
    """Return one profile by id (404 when absent)."""
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found.")
    if (
        (not profile.resume_sections or needs_section_rebuild(profile.resume_sections, profile.resume_text))
        and (profile.resume_text or "").strip()
    ):
        profile.resume_sections = extract_resume_sections(profile.resume_text or "")
    return profile


@app.get("/api/profiles/{profile_id}/resume", tags=["profiles"])
async def download_original_resume(profile_id: str, request: Request) -> Response:
    """Return the stored original upload, separate from editable text."""
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found.")
    if not profile.resume_filename:
        raise HTTPException(status_code=404, detail="This profile has no original resume file.")
    path = resume_file_path(profile.id, profile.resume_filename)
    if not blob_store.exists(path):
        raise HTTPException(status_code=404, detail="The original resume file is unavailable.")
    return _serve_file(
        path,
        media_type=profile.resume_content_type or "application/octet-stream",
        filename=profile.resume_filename,
    )


# ---------------------------------------------------------------------------
# Resume library — many uploads per profile, one active for matching
# ---------------------------------------------------------------------------

def _project_active_resume(profile: UserProfile, record: ResumeRecord) -> None:
    """Make *record* the profile's active resume (mutates *profile* in place).

    Copies the resume's canonical text/sections/structured view AND its derived
    matching facts onto the profile, so every downstream path (embedding cache,
    verdict, deep match, tailoring) keeps reading the profile unchanged. User
    *preferences* (sponsorship, clearance, remote, interests, excluded companies)
    are NOT touched — they describe the person, not the document.
    """
    profile.active_resume_id = record.id
    profile.resume_text = record.resume_text
    profile.resume_sections = record.resume_sections
    profile.structured_resume = record.structured_resume
    profile.resume_filename = record.filename
    profile.resume_content_type = record.content_type
    profile.resume_uploaded_at = record.uploaded_at
    if record.skills:
        profile.skills = record.skills
    if record.target_titles:
        profile.target_titles = record.target_titles
    if record.seniority_max:  # already validated at parse time
        profile.seniority_max = record.seniority_max  # type: ignore[assignment]
    if record.yoe_max is not None:
        profile.yoe_max = record.yoe_max


def _resume_row(record: ResumeRecord) -> dict[str, Any]:
    """Trim a ResumeRecord to what the library UI needs (never ship the file)."""
    return {
        "id": record.id,
        "filename": record.filename,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "uploaded_at": record.uploaded_at.isoformat(),
    }


@app.get("/api/profiles/{profile_id}/resumes", tags=["profiles"])
async def list_profile_resumes(profile_id: str, request: Request) -> dict[str, Any]:
    """List a profile's resume library (metadata only), newest first.

    Lazily adopts a pre-library single upload as record 0 so the library is never
    empty for a profile that already has a resume.
    """
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")

    records = relational.list_resumes(profile_id)
    if not records and (profile.resume_text or "").strip():
        adopted = _adopt_legacy_resume(profile, relational)
        if adopted is not None:
            records = [adopted]
    return {
        "active_resume_id": profile.active_resume_id,
        "resumes": [_resume_row(r) for r in records],
    }


def _adopt_legacy_resume(
    profile: UserProfile, relational: RelationalStore
) -> ResumeRecord | None:
    """Backfill a library row for a profile whose only resume predates the library."""
    legacy = resume_file_path(profile.id, profile.resume_filename)
    record = ResumeRecord(
        profile_id=profile.id,
        filename=profile.resume_filename or "resume",
        content_type=profile.resume_content_type,
        size_bytes=legacy.stat().st_size if legacy.is_file() else 0,
        uploaded_at=profile.resume_uploaded_at or datetime.now(UTC),
        file_path=legacy.name,  # legacy files live flat under resume_storage_dir
        resume_text=profile.resume_text or "",
        resume_sections=profile.resume_sections,
        structured_resume=profile.structured_resume,
        skills=profile.skills,
        target_titles=profile.target_titles,
        seniority_max=profile.seniority_max,
        yoe_max=profile.yoe_max,
    )
    relational.add_resume(record)
    profile.active_resume_id = record.id
    relational.upsert_profile(profile)
    return record


@app.post("/api/profiles/{profile_id}/resumes", tags=["profiles"])
async def upload_profile_resume(
    profile_id: str, request: Request, file: UploadFile = File(...)
) -> dict[str, Any]:
    """Add a resume to a profile's library. First upload becomes active."""
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Empty file.")
    security_guards.enforce_upload_limits(file, data)  # dormant unless upload_limits_enabled
    text = extract_resume_text(file.filename or "resume.txt", data)
    if not text.strip():
        raise HTTPException(
            status_code=422,
            detail="Could not extract any text from the file (is it a scanned image?).",
        )
    label = (file.filename or "resume").rsplit(".", 1)[0][:60]
    try:
        parsed = await run_in_threadpool(parse_resume_to_profile, text, label)
    except EnrichmentError as exc:
        raise HTTPException(status_code=502, detail=f"Resume parse failed: {exc}") from exc

    record = ResumeRecord(
        profile_id=profile_id,
        # Same-name re-uploads stay distinguishable: "resume (2).docx".
        filename=dedupe_name(
            file.filename or "resume",
            {r.filename for r in relational.list_resumes(profile_id)},
        ),
        content_type=file.content_type,
        size_bytes=len(data),
        file_path="",  # set below once the id exists
        resume_text=parsed.resume_text or text,
        resume_sections=parsed.resume_sections,
        structured_resume=parsed.structured_resume,
        skills=parsed.skills,
        target_titles=parsed.target_titles,
        seniority_max=parsed.seniority_max,
        yoe_max=parsed.yoe_max,
    )
    try:
        stored = store_library_resume(profile_id, record.id, record.filename, data)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not save resume locally: {exc}") from exc
    record.file_path = str(stored.relative_to(settings.resume_storage_dir))
    relational.add_resume(record)

    existing = relational.list_resumes(profile_id)
    if profile.active_resume_id is None or len(existing) == 1:
        _project_active_resume(profile, record)
        relational.upsert_profile(profile)

    return {
        "active_resume_id": profile.active_resume_id,
        "resumes": [_resume_row(r) for r in relational.list_resumes(profile_id)],
    }


@app.post("/api/profiles/{profile_id}/resumes/{resume_id}/activate", tags=["profiles"])
async def activate_profile_resume(
    profile_id: str, resume_id: str, request: Request
) -> UserProfile:
    """Switch which resume drives matching. Re-scoring picks it up automatically."""
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    record = relational.get_resume(resume_id)
    if record is None or record.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Resume not found for this profile.")
    _project_active_resume(profile, record)
    scoring_cache.clear()  # active resume changed → memoized verdicts are stale
    return relational.upsert_profile(profile)


@app.patch("/api/profiles/{profile_id}/resumes/{resume_id}", tags=["profiles"])
async def rename_profile_resume(
    profile_id: str, resume_id: str, request: Request, body: dict = Body(...)
) -> dict[str, Any]:
    """Rename a resume's display label (the file on disk is untouched)."""
    relational: RelationalStore = request.app.state.relational_store
    record = relational.get_resume(resume_id)
    if record is None or record.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Resume not found for this profile.")
    new_name = str(body.get("filename") or "").strip()
    if not new_name:
        raise HTTPException(status_code=422, detail="A resume name is required.")
    siblings = {
        r.filename for r in relational.list_resumes(profile_id) if r.id != resume_id
    }
    record.filename = dedupe_name(new_name[:120], siblings)
    relational.add_resume(record)
    # Keep the projected label in sync when renaming the active resume.
    profile = relational.get_profile(profile_id)
    if profile is not None and profile.active_resume_id == resume_id:
        profile.resume_filename = record.filename
        relational.upsert_profile(profile)
    return _resume_row(record)


@app.get("/api/profiles/{profile_id}/resumes/{resume_id}/file", tags=["profiles"])
async def download_profile_resume(
    profile_id: str, resume_id: str, request: Request
) -> Response:
    """Download one library resume's original file."""
    relational: RelationalStore = request.app.state.relational_store
    record = relational.get_resume(resume_id)
    if record is None or record.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Resume not found for this profile.")
    path = Path(settings.resume_storage_dir) / record.file_path
    if not blob_store.exists(path):
        raise HTTPException(status_code=404, detail="The resume file is unavailable.")
    # A display rename may have dropped the extension ("Marriott SWE") — restore
    # the real suffix from the stored file so the download opens correctly.
    download_name = record.filename
    if not Path(download_name).suffix and path.suffix:
        download_name = f"{download_name}{path.suffix}"
    return _serve_file(
        path,
        media_type=record.content_type or "application/octet-stream",
        filename=download_name,
    )


@app.delete("/api/profiles/{profile_id}/resumes/{resume_id}", tags=["profiles"])
async def delete_profile_resume(
    profile_id: str, resume_id: str, request: Request
) -> dict[str, Any]:
    """Delete a resume. If it was active, the next-newest becomes active."""
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    record = relational.get_resume(resume_id)
    if record is None or record.profile_id != profile_id:
        raise HTTPException(status_code=404, detail="Resume not found for this profile.")

    with contextlib.suppress(OSError):
        blob_store.delete(Path(settings.resume_storage_dir) / record.file_path)
    relational.delete_resume(resume_id)

    if profile.active_resume_id == resume_id:
        remaining = relational.list_resumes(profile_id)
        if remaining:
            _project_active_resume(profile, remaining[0])
        else:
            profile.active_resume_id = None  # keep resume_text as a lossless fallback
        scoring_cache.clear()
        relational.upsert_profile(profile)

    return {
        "active_resume_id": profile.active_resume_id,
        "resumes": [_resume_row(r) for r in relational.list_resumes(profile_id)],
    }


@app.post("/api/profiles/{profile_id}/reparse", response_model=UserProfile, tags=["profiles"])
async def reparse_profile_resume(profile_id: str, request: Request) -> UserProfile:
    """Rebuild structured matching fields from the saved, edited resume text."""
    relational: RelationalStore = request.app.state.relational_store
    existing = relational.get_profile(profile_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found.")
    if not (existing.resume_text or "").strip():
        raise HTTPException(status_code=422, detail="Add resume text before rebuilding this profile.")
    try:
        rebuilt = parse_resume_to_profile(existing.resume_text or "", label=existing.label)
    except EnrichmentError as exc:
        raise HTTPException(status_code=502, detail=f"Resume reparse failed: {exc}") from exc

    # Keep deliberate search preferences and the original-document reference;
    # only extracted skills, targets, experience and sponsorship are refreshed.
    rebuilt.id = existing.id
    rebuilt.resume_text = existing.resume_text
    rebuilt.resume_filename = existing.resume_filename
    rebuilt.resume_content_type = existing.resume_content_type
    rebuilt.resume_uploaded_at = existing.resume_uploaded_at
    rebuilt.reject_clearance = existing.reject_clearance
    rebuilt.reject_citizenship_only = existing.reject_citizenship_only
    rebuilt.remote_preference = existing.remote_preference
    rebuilt.countries = existing.countries
    rebuilt.prefer_cap_exempt = existing.prefer_cap_exempt
    rebuilt.excluded_companies = existing.excluded_companies
    return relational.upsert_profile(rebuilt)


@app.post(
    "/api/profiles/{profile_id}/attach-resume/{source_profile_id}",
    response_model=UserProfile,
    tags=["profiles"],
)
async def attach_saved_resume_to_profile(
    profile_id: str, source_profile_id: str, request: Request
) -> UserProfile:
    """Copy one locally saved resume into a metadata-only matching profile.

    This is explicit rather than automatic: local users may keep several candidates
    or resume variants. Matching preferences stay on the destination profile while
    the lossless source text and its extracted sections move with the selected
    resume record.
    """
    if profile_id == source_profile_id:
        raise HTTPException(status_code=422, detail="Choose a different saved resume.")

    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    # The destination is path-guarded by the ownership middleware; the SOURCE arrives
    # as a second path segment the middleware doesn't see, so guard it here — else a
    # caller could copy another user's resume (PII) by guessing a source id (IDOR).
    source = owned_profile(source_profile_id, request)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    if not (source.resume_text or "").strip():
        raise HTTPException(status_code=422, detail="The selected profile has no saved resume text.")

    profile.resume_text = source.resume_text
    profile.resume_sections = source.resume_sections or extract_resume_sections(source.resume_text or "")
    # Full resume extraction is more complete than a quick matcher profile. Keep
    # a user's explicit target roles, then append any source-derived targets.
    profile.skills = list(dict.fromkeys([*source.skills, *profile.skills]))
    profile.target_titles = list(dict.fromkeys([*profile.target_titles, *source.target_titles]))

    # Preserve the original source file separately for each profile when one was
    # uploaded. The text copy above is still enough when the source was pasted.
    if source.resume_filename:
        source_path = resume_file_path(source.id, source.resume_filename)
        if blob_store.exists(source_path):
            try:
                profile.resume_filename = store_original_resume(
                    profile.id, source.resume_filename, blob_store.read(source_path)
                )
            except OSError as exc:
                raise HTTPException(
                    status_code=500, detail=f"Could not copy the original resume locally: {exc}"
                ) from exc
            profile.resume_content_type = source.resume_content_type
            profile.resume_uploaded_at = datetime.now(UTC)

    return relational.upsert_profile(profile)


@app.post("/api/profiles/{profile_id}/import-applied", tags=["profiles"])
async def import_applied_jobs(
    profile_id: str, request: Request, body: dict = Body(...)
) -> dict[str, Any]:
    """Mark already-applied roles from a pasted markdown table (no LLM, no network).

    Accepts the common tracker shape ``| Date | Company | Role | Link | Notes |``
    (header + separator rows ignored). Each row is matched against the index by
    URL first, then by normalized company+title; matches are marked ``applied``
    so they drop out of the feed. Rows that match nothing are reported back so
    the user can see what was not found.
    """
    relational: RelationalStore = request.app.state.relational_store
    if relational.get_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    store: WeaviateStore = request.app.state.weaviate_store

    text = str(body.get("text") or "")
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3 or set("".join(cells)) <= set("-: "):
            continue  # separator row
        if cells[0].lower() in {"date", "applied"} or cells[1].lower() == "company":
            continue  # header row
        url = ""
        for cell in cells:
            m = re.search(r"https?://\S+?(?=[)\s\]]|$)", cell)
            if m:
                url = m.group(0)
                break
        rows.append({"company": cells[1], "title": cells[2] if len(cells) > 2 else "", "url": url})

    matched, unmatched = 0, []
    for row in rows:
        job_id: str | None = None
        if row["url"]:
            found = store.find_by_url(row["url"])
            if found:
                job_id = found.job_id
        if job_id is None and row["company"] and row["title"]:
            job_id = compute_job_id(row["company"], row["title"], None)
            if store.get_by_id(job_id) is None:
                job_id = None
        if job_id:
            relational.set_job_state(profile_id, job_id, "applied")
            matched += 1
        else:
            unmatched.append(f"{row['company']} — {row['title']}")

    return {"rows": len(rows), "marked_applied": matched, "unmatched": unmatched[:20]}


@app.post("/api/profiles/{profile_id}/structure", response_model=UserProfile, tags=["profiles"])
async def structure_profile_resume(profile_id: str, request: Request) -> UserProfile:
    """Parse the stored resume text into typed sections (one LLM call).

    One-time migration for profiles created before structured parsing; new
    uploads and Rebuild populate ``structured_resume`` automatically.
    """
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    if not (profile.resume_text or "").strip():
        raise HTTPException(status_code=422, detail="Profile has no resume text to structure.")
    try:
        profile.structured_resume = await run_in_threadpool(
            parse_structured_resume, profile.resume_text or ""
        )
    except EnrichmentError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    profile.structured_stale = False  # freshly parsed from the current text
    return relational.upsert_profile(profile)


_POLISH_SYSTEM_PROMPT = (
    "You improve resume bullet points. STRICT truthfulness: keep every fact, tool, and number "
    "exactly as stated; NEVER add metrics, technologies, or achievements that are not present. "
    "Make each bullet concise and impact-first (strong verb, what, how, outcome). If a bullet is "
    "already strong, return it unchanged. Reply as JSON: {\"bullets\": [str, ...]} — same count, "
    "same order as the input."
)


@app.post("/api/profiles/{profile_id}/polish", tags=["profiles"])
async def polish_entry_bullets(
    profile_id: str, request: Request, body: dict = Body(...)
) -> dict[str, Any]:
    """Suggest truthful rewrites for one structured entry's bullets (one LLM call).

    Returns ``{"bullets": [{"original", "suggested"}]}`` — read-only; the UI
    saves accepted suggestions through the normal profile PUT.
    """
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None or profile.structured_resume is None:
        raise HTTPException(status_code=404, detail="Structured profile not found.")
    section = str(body.get("section") or "")
    index = int(body.get("index") or 0)
    entries = getattr(profile.structured_resume, section, None)
    if not isinstance(entries, list) or not (0 <= index < len(entries)):
        raise HTTPException(status_code=422, detail="Unknown section or index.")
    bullets = list(getattr(entries[index], "bullets", []) or [])
    if not bullets:
        raise HTTPException(status_code=422, detail="Entry has no bullets to improve.")

    from jobscout.enrich import chat_json, llm_is_configured

    if not llm_is_configured():
        raise HTTPException(status_code=503, detail="LLM provider is not configured.")

    def _run() -> list[str]:
        content = chat_json(_POLISH_SYSTEM_PROMPT, json.dumps({"bullets": bullets}))
        data = json.loads(content or "{}")
        out = [str(b) for b in (data.get("bullets") or [])]
        # Count mismatch → trust the originals for the tail (never drop bullets).
        while len(out) < len(bullets):
            out.append(bullets[len(out)])
        return out[: len(bullets)]

    try:
        suggested = await run_in_threadpool(_run)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Polish failed: {exc}") from exc
    return {
        "bullets": [
            {"original": orig, "suggested": sug}
            for orig, sug in zip(bullets, suggested, strict=True)
        ]
    }


# Per-field guidance for the suggest endpoint. Suggestions are ADD-ONLY: the UI
# lets the user accept each one; existing values are never edited or removed.
_SUGGEST_FIELDS: dict[str, str] = {
    "interests": "domains/topics this candidate would plausibly enjoy working in, grounded in their resume",
    "avoid_role_types": (
        "role types this candidate likely wants to AVOID (e.g. pure BI/reporting work), inferred "
        "from what their resume emphasizes — clearly speculative preferences, phrased briefly"
    ),
    "avoid_domains": (
        "industry domains this candidate likely wants to AVOID, inferred from their resume focus — "
        "clearly speculative preferences, phrased briefly"
    ),
    "target_titles": "job titles this resume realistically targets",
    "skills": "real skills evidenced in the resume text that are missing from the current list",
}

_SUGGEST_SYSTEM_PROMPT = (
    "You suggest ADDITIONS to one list field of a job-search profile, grounded in the candidate's "
    "resume. Never invent facts, never repeat items already present, keep each item short (1-4 "
    "words, lowercase). Reply as JSON: {\"suggestions\": [str, ...]} with 3 to 8 items."
)


@app.post("/api/profiles/{profile_id}/suggest", tags=["profiles"])
async def suggest_profile_field(
    profile_id: str, request: Request, body: dict = Body(...)
) -> dict[str, Any]:
    """Suggest add-only values for one empty/thin profile list field (one LLM call).

    Read-only: returns ``{"suggestions": [...]}`` filtered against what's already
    on the profile; the UI adds accepted items through the normal profile PUT.
    """
    field = str(body.get("field") or "")
    if field not in _SUGGEST_FIELDS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown field. Suggestible: {', '.join(sorted(_SUGGEST_FIELDS))}.",
        )
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    if not (profile.resume_text or "").strip():
        raise HTTPException(status_code=422, detail="Add a resume before requesting suggestions.")

    from jobscout.enrich import chat_json, llm_is_configured

    if not llm_is_configured():
        raise HTTPException(status_code=503, detail="LLM provider is not configured.")

    existing = [str(v) for v in (getattr(profile, field, None) or [])]
    existing_lower = {v.strip().lower() for v in existing}

    def _run() -> list[str]:
        content = chat_json(_SUGGEST_SYSTEM_PROMPT, json.dumps({
            "field": field,
            "guidance": _SUGGEST_FIELDS[field],
            "already_present": existing,
            "resume": (profile.resume_text or "")[:6000],
        }))
        data = json.loads(content or "{}")
        out: list[str] = []
        for item in data.get("suggestions") or []:
            s = str(item).strip()
            if s and s.lower() not in existing_lower and s.lower() not in {o.lower() for o in out}:
                out.append(s)
        return out[:8]

    try:
        suggestions = await run_in_threadpool(_run)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"Suggest failed: {exc}") from exc
    return {"field": field, "suggestions": suggestions}


@app.post("/api/profiles/{profile_id}/tailor/{job_id}", tags=["profiles"])
async def tailor_profile_resume(
    profile_id: str, job_id: str, request: Request, body: dict | None = Body(None)
) -> dict[str, Any]:
    """Generate and audit a truthful, job-specific DOCX for one saved role.

    PRE-FLIGHT GATE: before spending an LLM call + build on a role the
    candidate should skip (explicit sponsorship wall, defense/ITAR domain,
    seniority/role mismatch), the rule verdict + deep-match second opinion run
    first. A "skip" conclusion returns ``built: false`` with the reasons; the
    caller may override with ``{"force": true}`` ("tailor anyway").

    The configured private resume-writing skill owns facts, formatting, and the
    audit rules. This endpoint only permits its verified selector IDs and runs
    the JD eligibility gate before an LLM request or document build.
    """
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    store: WeaviateStore = request.app.state.weaviate_store
    job = store.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    force = bool((body or {}).get("force"))
    rule_verdict = score_verdict(job, profile)
    deep = await run_in_threadpool(compute_deep_match, job, profile, relational)
    should_skip = rule_verdict.verdict == "reject" or deep.get("verdict") == "skip"
    gate = {
        "recommendation": "skip" if should_skip else "build",
        "rule_verdict": rule_verdict.verdict,
        "rule_red_flags": rule_verdict.red_flags,
        "deep_verdict": deep.get("verdict"),
        "deep_score": deep.get("score"),
        "deep_gaps": deep.get("gaps"),
        "deep_summary": deep.get("summary"),
    }
    if should_skip and not force:
        return {"built": False, "gate": gate}

    check_quota(relational, current_user_id(request), "tailor")  # dormant unless quota_enforced
    try:
        result = await run_in_threadpool(build_tailored_resume, job, profile)
    except EligibilityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TailoringError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    metrics = result.metrics or {}
    # Catalog the build so it stays findable/downloadable after this response.
    relational.upsert_tailored(TailoredResumeRecord(
        profile_id=profile_id,
        job_id=job_id,
        company=job.company or "",
        title=job.title or "",
        filename=result.filename,
        recommendation="skip" if should_skip else "build",
        fingerprint=deep_match_fingerprint(job, profile),
        engine=result.engine,
        pdf_filename=result.pdf_path.name if result.pdf_path else "",
        metrics_json=json.dumps(metrics) if metrics else "",
        ai_risk_after=metrics.get("ai_risk_after"),
    ))
    record_usage(relational, current_user_id(request), "tailor")
    has_pdf = result.pdf_path is not None
    return {
        "built": True,
        "gate": gate,
        "filename": result.filename,
        "notes": result.notes,
        "warnings": result.warnings,
        "provider": result.provider,
        "model": result.model,
        "engine": result.engine,
        "metrics": metrics,
        "download_url": f"/api/profiles/{profile_id}/tailored/{job_id}",
        "pdf_download_url": f"/api/profiles/{profile_id}/tailored/{job_id}/pdf" if has_pdf else None,
    }


@app.get("/api/profiles/{profile_id}/tailored", tags=["profiles"])
async def list_tailored_resumes(profile_id: str, request: Request) -> dict[str, Any]:
    """List a profile's built tailored resumes, newest first.

    Each row carries ``up_to_date``: whether the tailored DOCX was built for the
    profile's CURRENT resume (fingerprint match). A changed resume flips it to
    false so the UI can prompt a re-tailor. Legacy rows (no stored fingerprint)
    and purged jobs default to ``up_to_date=True`` (no false nag).
    """
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    store: WeaviateStore = request.app.state.weaviate_store
    out: list[dict[str, Any]] = []
    for r in relational.list_tailored(profile_id):
        up_to_date = True
        job = store.get_by_id(r.job_id) if (r.fingerprint or not r.filename) else None
        if r.fingerprint and job is not None:
            up_to_date = deep_match_fingerprint(job, profile) == r.fingerprint
        # Legacy rows stored no filename — show the name the download would serve.
        display_name = r.filename or (tailored_resume_filename(job) if job is not None else "")
        out.append({
            "job_id": r.job_id,
            "company": r.company,
            "title": r.title,
            "filename": display_name,
            "recommendation": r.recommendation,
            "up_to_date": up_to_date,
            "created_at": r.created_at.isoformat(),
            "download_url": f"/api/profiles/{profile_id}/tailored/{r.job_id}",
        })
    return {"tailored": out}


@app.patch("/api/profiles/{profile_id}/tailored/{job_id}", tags=["profiles"])
async def rename_tailored_resume(
    profile_id: str, job_id: str, request: Request, body: dict = Body(...)
) -> dict[str, Any]:
    """Rename a tailored resume's download filename (the DOCX on disk is untouched)."""
    relational: RelationalStore = request.app.state.relational_store
    rows = relational.list_tailored(profile_id)
    record = next((r for r in rows if r.job_id == job_id), None)
    if record is None:
        raise HTTPException(status_code=404, detail="No tailored resume for this job.")
    new_name = str(body.get("filename") or "").strip()[:120]
    if not new_name:
        raise HTTPException(status_code=422, detail="A filename is required.")
    if not new_name.lower().endswith(".docx"):
        new_name = f"{new_name}.docx"
    # Keep names distinct within the profile so downloads never collide.
    siblings = {r.filename for r in rows if r.job_id != job_id and r.filename}
    record.filename = dedupe_name(new_name, siblings)
    relational.upsert_tailored(record)
    return {"job_id": job_id, "filename": record.filename}


@app.get("/api/profiles/{profile_id}/tailored/{job_id}", tags=["profiles"])
async def download_tailored_resume(
    profile_id: str, job_id: str, request: Request
) -> Response:
    """Download a locally retained tailored DOCX only when its profile still exists."""
    relational: RelationalStore = request.app.state.relational_store
    if relational.get_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    path = tailored_resume_path(profile_id, job_id)
    if not blob_store.exists(path):
        raise HTTPException(status_code=404, detail="No tailored resume exists for this job yet.")
    record = next((r for r in relational.list_tailored(profile_id) if r.job_id == job_id), None)
    # The STORED name is the source of truth (the user can rename it). Only fall
    # back to a computed name for legacy rows that never stored one.
    if record is not None and record.filename:
        base = record.filename
    else:
        job = request.app.state.weaviate_store.get_by_id(job_id)
        base = tailored_resume_filename(job) if job is not None else path.name
        # A dated fallback so re-downloads of different jobs don't collide in ~/Downloads.
        if record is not None and base.lower().endswith(".docx"):
            base = f"{base[:-5]}_{record.created_at:%Y-%m-%d}.docx"
    return _serve_file(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=base,
    )


@app.get("/api/profiles/{profile_id}/tailored/{job_id}/pdf", tags=["profiles"])
async def download_tailored_pdf(
    profile_id: str, job_id: str, request: Request
) -> Response:
    """Download the LaTeX-engine PDF for a tailored resume (if one was built)."""
    relational: RelationalStore = request.app.state.relational_store
    if relational.get_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    pdf_path = tailored_resume_path(profile_id, job_id).with_suffix(".pdf")
    if not blob_store.exists(pdf_path):
        raise HTTPException(status_code=404, detail="No tailored PDF exists for this job.")
    record = relational.get_tailored(profile_id, job_id)
    base = (record.pdf_filename if record and record.pdf_filename
            else (record.filename[:-5] + ".pdf" if record and record.filename.endswith(".docx")
                  else pdf_path.name))
    return _serve_file(pdf_path, media_type="application/pdf", filename=base)


@app.get("/api/profiles/{profile_id}/tailored/{job_id}/metrics", tags=["profiles"])
async def get_tailored_metrics(
    profile_id: str, job_id: str, request: Request
) -> dict[str, Any]:
    """Return the AI-reduction metric bundle for one tailored resume (per-job dashboard)."""
    relational: RelationalStore = request.app.state.relational_store
    if relational.get_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    record = relational.get_tailored(profile_id, job_id)
    if record is None or not record.metrics_json:
        raise HTTPException(status_code=404, detail="No metrics for this tailored resume.")
    return {
        "job_id": job_id,
        "engine": record.engine,
        "filename": record.filename,
        "warnings": [],
        "metrics": json.loads(record.metrics_json),
    }


@app.get("/api/profiles/{profile_id}/dashboard", tags=["profiles"])
async def candidate_dashboard(profile_id: str, request: Request) -> dict[str, Any]:
    """Per-candidate dashboard: profile summary + tailored resumes + pipeline funnel."""
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    store: WeaviateStore = request.app.state.weaviate_store

    tailored: list[dict[str, Any]] = []
    for r in relational.list_tailored(profile_id):
        up_to_date = True
        job = store.get_by_id(r.job_id) if r.fingerprint else None
        if r.fingerprint and job is not None:
            up_to_date = deep_match_fingerprint(job, profile) == r.fingerprint
        has_pdf = tailored_resume_path(profile_id, r.job_id).with_suffix(".pdf").is_file()
        tailored.append({
            "job_id": r.job_id,
            "company": r.company,
            "title": r.title,
            "filename": r.filename,
            "engine": r.engine,
            "ai_risk_after": r.ai_risk_after,
            "recommendation": r.recommendation,
            "up_to_date": up_to_date,
            "created_at": r.created_at.isoformat(),
            "download_url": f"/api/profiles/{profile_id}/tailored/{r.job_id}",
            "pdf_download_url": (f"/api/profiles/{profile_id}/tailored/{r.job_id}/pdf"
                                 if has_pdf else None),
            "has_metrics": bool(r.metrics_json),
        })

    entries: list[dict[str, Any]] = []
    for row in relational.list_pipeline(profile_id):
        job = store.get_by_id(row["job_id"])
        source = job.source if job is not None else None
        entries.append({"status": row["status"], "source": source,
                        "source_kind": source_kind(source)})

    return {
        "profile": {
            "id": profile.id,
            "label": profile.label,
            "target_titles": profile.target_titles,
            "has_resume": bool((profile.resume_text or "").strip()),
        },
        "tailored": tailored,
        "pipeline": PipelineAnalytics.from_entries(entries).model_dump(),
    }


def _delete_profile_and_files(relational: RelationalStore, profile_id: str) -> None:
    """Delete a profile's rows (job-state/resumes/tailored) AND its files on disk."""
    profile = relational.get_profile(profile_id)
    relational.delete_profile(profile_id)
    if profile and profile.resume_filename:
        with contextlib.suppress(OSError):
            blob_store.delete(resume_file_path(profile.id, profile.resume_filename))
    # Resume-library + tailored files live under per-profile directories — remove them.
    with contextlib.suppress(OSError):
        blob_store.delete_tree(Path(settings.resume_storage_dir) / profile_id)
    with contextlib.suppress(OSError):
        blob_store.delete_tree(tailored_resume_path(profile_id, "placeholder").parent)


@app.delete("/api/profiles/{profile_id}", tags=["profiles"])
async def delete_profile(profile_id: str, request: Request) -> dict[str, str]:
    """Delete a profile and its saved per-job states."""
    relational: RelationalStore = request.app.state.relational_store
    _delete_profile_and_files(relational, profile_id)
    return {"status": "deleted", "profile_id": profile_id}


@app.get("/api/users/me", tags=["users"])
async def whoami(request: Request) -> dict[str, Any]:
    """The calling account's basics (drives the frontend Admin-tab visibility)."""
    relational: RelationalStore = request.app.state.relational_store
    uid = current_user_id(request)
    user = relational.get_user(uid) or {}
    return {"user_id": uid, "is_admin": bool(user.get("is_admin")), "plan": user.get("plan")}


@app.get("/api/users/me/export", tags=["users"])
async def export_my_data(request: Request) -> dict[str, Any]:
    """Export the calling user's data (profiles + their resumes/tailored/saved searches).

    Right-to-access, useful now as a local backup. Metadata + editable resume text
    (the canonical matching record); original binary files are downloadable via the
    per-resume routes. Scoped to ``current_user_id`` — never another user's data.
    """
    relational: RelationalStore = request.app.state.relational_store
    uid = current_user_id(request)
    profiles = []
    for p in relational.list_profiles(uid):
        profiles.append({
            "profile": p.model_dump(),
            "resumes": [r.model_dump() for r in relational.list_resumes(p.id)],
            "tailored": [t.model_dump() for t in relational.list_tailored(p.id)],
            "pipeline": relational.list_pipeline(p.id),
        })
    return {
        "user_id": uid,
        "exported_at": datetime.now(UTC).isoformat(),
        "profiles": profiles,
        "saved_searches": [s.model_dump() for s in relational.list_saved_searches(uid)],
    }


@app.delete("/api/users/me/data", tags=["users"])
async def delete_my_data(request: Request) -> dict[str, Any]:
    """Delete ALL of the calling user's data (right-to-erasure / a clean reset).

    Removes every profile the user owns (and its resumes, tailored files, job-state)
    plus their saved searches. Scoped to ``current_user_id``; other users are untouched.
    """
    relational: RelationalStore = request.app.state.relational_store
    uid = current_user_id(request)
    profile_ids = [p.id for p in relational.list_profiles(uid)]
    for pid in profile_ids:
        _delete_profile_and_files(relational, pid)
    for s in relational.list_saved_searches(uid):
        relational.delete_saved_search(s.id)
    return {"status": "deleted", "profiles_deleted": len(profile_ids)}


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

    Shape: ``{jobs: [...], stages: {job_id: {stage, note, updated_at}}, analytics}``.
    The frontend groups jobs by stage and renders the funnel rollup. Newest
    activity first. ``analytics`` is computed over the pipeline rows even for jobs
    that have since dropped out of the index, so the funnel stays complete."""
    relational: RelationalStore = request.app.state.relational_store
    if relational.get_profile(profile_id) is None:
        raise HTTPException(status_code=404, detail=f"Profile '{profile_id}' not found.")
    store: WeaviateStore = request.app.state.weaviate_store
    jobs: list[dict[str, Any]] = []
    stages: dict[str, Any] = {}
    entries: list[dict[str, Any]] = []
    for row in relational.list_pipeline(profile_id):
        job = store.get_by_id(row["job_id"])
        # The job may have aged out of the index; still count it in the funnel.
        source = job.source if job is not None else None
        entries.append({
            "status": row["status"],
            "source": source,
            "source_kind": source_kind(source),
        })
        if job is None:
            continue
        jobs.append(job.model_dump())
        stages[job.job_id] = {"stage": row["status"], "note": row["note"],
                              "updated_at": str(row["updated_at"])}
    analytics = PipelineAnalytics.from_entries(entries)
    return {"jobs": jobs, "stages": stages, "analytics": analytics.model_dump()}


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
            "results_wanted":  50,
            "profile_id":      "optional profile UUID"
        }

    Returns one ``RunLog`` stub per enabled source (status ``running``).
    The actual ingestion happens in the background.
    """
    keywords: list[str] = body.get("keywords", ["software engineer"])
    location: str | None = body.get("location")
    results_wanted: int = int(body.get("results_wanted", 50))

    weaviate_store: WeaviateStore = request.app.state.weaviate_store
    relational_store: RelationalStore = request.app.state.relational_store
    profile_id = str(body.get("profile_id") or "").strip()
    # owned_profile → 404 unless the caller owns it (no personalizing a shared ingest
    # under another user's profile). None when no profile_id was supplied.
    profile = owned_profile(profile_id, request) if profile_id else None

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
        profile,
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

        if ats == "rippling":
            url = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
            r = _req.get(url, timeout=timeout,
                         headers={"Accept": "application/json", "User-Agent": "JobScout/1.0"})
            if r.status_code == 200:
                data = r.json()
                jobs = data if isinstance(data, list) else data.get("items", [])
                return {"valid": True, "job_count": len(jobs),
                        "sample_title": jobs[0].get("name") if jobs else None}
            return {"valid": False, "error": f"HTTP {r.status_code}"}

        if ats == "smartrecruiters":
            url = (
                f"https://api.smartrecruiters.com/v1/companies/{slug}"
                "/postings?limit=5"
            )
            r = _req.get(url, timeout=timeout,
                         headers={"Accept": "application/json", "User-Agent": "JobScout/1.0"})
            if r.status_code == 200:
                data = r.json()
                jobs = data.get("content", [])
                return {"valid": True, "job_count": data.get("totalFound", len(jobs)),
                        "sample_title": jobs[0].get("name") if jobs else None}
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
    if not result["valid"] and ats not in (
        "greenhouse", "lever", "ashby", "workday", "rippling", "smartrecruiters"
    ):
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
        """Live-probe one ATS board slug and report whether it serves jobs."""
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
    require_admin(request)  # shared-quota daily ingest — global, not per-user
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
    require_admin(request)  # purges the GLOBAL shared job index
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
async def set_source_overrides(body: dict[str, Any], request: Request) -> dict[str, bool]:
    """Enable/disable a source at runtime. Body e.g. ``{"jobspy": true}``.

    Only known high-risk sources are togglable here; default off. This is how the
    UI's "high-risk scraper" switch turns JobSpy on without editing sources.yaml.
    """
    require_admin(request)  # toggles a shared scraper for the whole deployment
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
    for s in relational.list_saved_searches(current_user_id(request)):
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
        user_id=current_user_id(request),
    )
    relational: RelationalStore = request.app.state.relational_store
    return relational.create_saved_search(s)


@app.post("/api/saved-searches/{search_id}/seen", tags=["operations"])
async def mark_saved_search_seen(search_id: str, request: Request) -> dict[str, str]:
    """Mark a saved search as seen (resets its new_count to 0)."""
    relational: RelationalStore = request.app.state.relational_store
    _owned_search_or_404(relational, search_id, request)
    if relational.mark_saved_search_seen(search_id) is None:
        raise HTTPException(status_code=404, detail="Saved search not found.")
    return {"status": "ok", "id": search_id}


@app.delete("/api/saved-searches/{search_id}", tags=["operations"])
async def delete_saved_search(search_id: str, request: Request) -> dict[str, str]:
    """Delete a saved search."""
    relational: RelationalStore = request.app.state.relational_store
    _owned_search_or_404(relational, search_id, request)
    relational.delete_saved_search(search_id)
    return {"status": "deleted", "id": search_id}


def _owned_search_or_404(relational: RelationalStore, search_id: str, request: Request) -> None:
    """404 (not 403) unless the caller owns this saved search — no id enumeration."""
    s = relational.get_saved_search(search_id)
    if s is None or (s.user_id or settings.local_user_id) != current_user_id(request):
        raise HTTPException(status_code=404, detail="Saved search not found.")


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


@app.post("/api/maintenance/backfill-lever-descriptions", tags=["operations"])
async def backfill_lever_descriptions(request: Request, background_tasks: BackgroundTasks) -> dict:
    """Patch full Lever descriptions into existing Weaviate jobs without re-embedding.

    Lever's API splits the JD across descriptionPlain + lists (bullets) + additionalPlain.
    Previously only descriptionPlain was stored. This endpoint fetches all configured Lever
    companies, assembles the full description, and patches only the description field.
    """
    require_admin(request)  # bulk-patches the GLOBAL shared job index
    import html as _html
    import re

    import httpx
    from weaviate.classes.query import Filter as _Filter

    from jobscout.services.source_config import _load_sources_cfg
    from jobscout.store import COLLECTION_NAME as _CN

    store: WeaviateStore = request.app.state.weaviate_store

    _TAG_RE = re.compile(r"<[^>]+>")

    def _strip(t: str) -> str:
        """Collapse an HTML fragment to plain text."""
        return _TAG_RE.sub("", _html.unescape(t)).strip()

    def _full_desc(p: dict) -> str | None:
        """Join a posting's description sections into one text blob."""
        parts: list[str] = []
        intro = (p.get("descriptionPlain") or "").strip()
        if intro:
            parts.append(intro)
        for sec in p.get("lists") or []:
            h = (sec.get("text") or "").strip()
            c = _strip(sec.get("content") or "")
            if h:
                parts.append(f"\n{h}")
            if c:
                parts.append(c)
        footer = (p.get("additionalPlain") or "").strip()
        if footer:
            parts.append(f"\n{footer}")
        return "\n".join(parts) or None

    def _get_slugs() -> list[str]:
        """Collect the configured company slugs for one ATS from sources.yaml."""
        cfg = _load_sources_cfg()
        lever_cfg = cfg.get("sources", {}).get("lever", {})
        slugs = []
        for entry in lever_cfg.get("companies", []):
            if isinstance(entry, str):
                slugs.append(entry)
            elif isinstance(entry, dict):
                t = entry.get("token") or entry.get("slug") or entry.get("name")
                if t:
                    slugs.append(t)
        return slugs

    def _do_backfill() -> None:
        """Background task: refetch full descriptions for jobs missing them."""
        col = store._client.collections.get(_CN)
        slugs = _get_slugs()
        updated = skipped = 0
        for slug in slugs:
            try:
                r = httpx.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", timeout=15)
                postings = r.json()
            except Exception as e:
                log.error("Lever fetch failed for %s: %s", slug, e)
                continue
            for p in postings:
                title = (p.get("text") or "").strip()
                if not title:
                    continue
                full = _full_desc(p)
                if not full:
                    skipped += 1
                    continue
                # Match by source + company + title (avoids city normalization mismatch)
                try:
                    result = col.query.fetch_objects(
                        filters=(
                            _Filter.by_property("source").equal("lever") &
                            _Filter.by_property("company").equal(slug) &
                            _Filter.by_property("title").equal(title)
                        ),
                        limit=1,
                    )
                except Exception as e:
                    log.warning("Query failed %s/%s: %s", slug, title[:40], e)
                    skipped += 1
                    continue
                if not result.objects:
                    skipped += 1
                    continue
                obj = result.objects[0]
                existing = len(str(obj.properties.get("description") or ""))
                if len(full) <= existing:
                    skipped += 1
                    continue
                try:
                    col.data.update(uuid=obj.uuid, properties={"description": full})
                    updated += 1
                except Exception as e:
                    log.warning("Update failed %s/%s: %s", slug, title[:40], e)
                    skipped += 1
        log.info("backfill_lever done updated=%d skipped=%d", updated, skipped)

    background_tasks.add_task(_do_backfill)
    return {"status": "started", "message": "Lever description backfill running in background. Check server logs for progress."}
