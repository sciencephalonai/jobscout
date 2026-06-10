"""Ingestion + enrichment + watchlist-refresh background jobs.

Extracted from api/main.py. Stateless functions taking the open stores as
parameters; the API schedules them via BackgroundTasks / the scheduler.
"""
from __future__ import annotations

import logging
import traceback
from typing import Any

from jobscout.adapters import (
    AshbyAdapter,
    GreenhouseAdapter,
    LeverAdapter,
    WorkableAdapter,
    WorkdayAdapter,
)
from jobscout.adapters.base import CompliantHttpClient
from jobscout.config import settings
from jobscout.embed import EmbeddingQuotaError, embed_job
from jobscout.enrich import (
    EnrichmentError,
    derive_cap_exempt,
    detect_recruiter_post,
    extract_enrichment,
)
from jobscout.models import Job, RunLog
from jobscout.normalize import is_us_job, raw_to_job
from jobscout.relational import RelationalStore
from jobscout.services.source_config import (
    _DEFAULT_AUTHORITY,
    _SOURCE_AUTHORITY,
    _build_adapters,
    _company_size_map,
    _load_sources_cfg,
)
from jobscout.sponsors import is_everify_employer, is_known_h1b_sponsor
from jobscout.store import COLLECTION_NAME, WeaviateStore

log = logging.getLogger(__name__)


# Keywords currently being auto-fetched, to avoid duplicate background runs.
_autofetch_inflight: set[str] = set()
AUTOFETCH_MIN_RESULTS = 3
# Cap on concurrent in-flight auto-fetches: each spawns a heavy multi-source
# ingest, so a burst of sparse searches must not be able to fan out unbounded.
AUTOFETCH_MAX_INFLIGHT = 2


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

    # Per-RUN embedding budget so a single "Get latest jobs" can't exhaust the
    # day's Gemini free-tier quota (1,000/day). Shared across adapters; when it's
    # reached, or the provider 429s, we stop cleanly and record why.
    budget = settings.embed_daily_budget
    embeds_used = 0
    stop_reason: str | None = None

    http = CompliantHttpClient()
    try:
        for adapter in adapters:
            if stop_reason:
                break  # budget reached / quota hit on an earlier adapter
            run_log: RunLog = relational_store.start_run(adapter.name)
            count_ingested = 0
            count_failed = 0
            count_skipped = 0  # already-indexed jobs skipped (no embed spent)
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

                        # Company size: exact value from config (Greenhouse/Lever
                        # company list) takes precedence over the LLM estimate.
                        company_key = job.company.lower() if job.company else None
                        if company_key:
                            cfg_size = size_map.get(company_key)
                            if cfg_size:
                                job.company_size_bucket = cfg_size

                        # Already in Weaviate and fully enriched/embedded? SKIP it —
                        # re-embedding produces an identical vector and would just burn
                        # the daily Gemini quota on a job we already have. Spending the
                        # embed budget only on genuinely-new (or not-yet-completed) jobs
                        # is what lets a run actually grow the index. (Jobs that exist
                        # but aren't "done" — failed/pending — fall through to retry.)
                        existing = weaviate_store.get_by_id(job.job_id)
                        if existing is not None and existing.enrichment_status == "done":
                            count_skipped += 1
                            continue

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
                                job.security_clearance = enr.get(
                                    "security_clearance", "unclear"
                                )
                                job.citizenship_required = enr.get(
                                    "citizenship_required", False
                                )
                                # A curated adapter may have already stamped
                                # employer_type from config — that wins over the
                                # LLM's guess. Only fall back to the LLM when the
                                # adapter left it "unclear".
                                if job.employer_type == "unclear":
                                    job.employer_type = enr.get(
                                        "employer_type", "unclear"
                                    )
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
                                job.enrichment_status = "done"

                        # cap_exempt is always derived from the final
                        # employer_type (whether stamped by the adapter, reused,
                        # or inferred by the LLM) — single source of truth.
                        job.cap_exempt = derive_cap_exempt(job.employer_type)

                        # Known H-1B sponsor — public DoL filer-list lookup (free).
                        job.known_h1b_sponsor = is_known_h1b_sponsor(job.company)
                        job.known_everify = is_everify_employer(job.company)

                        # Recruiter/aggregator flag — cheap heuristic, set on
                        # every job regardless of LLM enrichment status.
                        job.is_recruiter_post = detect_recruiter_post(
                            job.company, job.source, job.description
                        )

                        # Record the upstream source/url for the dedup side table
                        # BEFORE the authority override below, so job_sources keeps
                        # every portal that listed this job.
                        ingest_source = job.source
                        ingest_url = job.url

                        # Source-authority tiebreak: if this job already exists from
                        # a more authoritative source (direct ATS/employer), keep
                        # that canonical apply link rather than overwriting it with a
                        # lower-authority aggregator URL.
                        if existing is not None and existing.url and (
                            _SOURCE_AUTHORITY.get(existing.source, _DEFAULT_AUTHORITY)
                            < _SOURCE_AUTHORITY.get(job.source, _DEFAULT_AUTHORITY)
                        ):
                            job.url = existing.url
                            job.source = existing.source

                        # Stop before embedding if this run already spent its
                        # embed budget — preserves the rest of the daily quota.
                        if embeds_used >= budget:
                            stop_reason = (
                                f"Embed budget ({budget}) reached — stopped to preserve "
                                f"the daily Gemini quota. Re-run later to continue."
                            )
                            break
                        try:
                            vector = embed_job(
                                title=job.title,
                                company=job.company,
                                skills=job.skills,
                                description=job.description,
                            )
                        except EmbeddingQuotaError as exc:
                            # Provider 429 — quota is gone for the day. Stop the whole
                            # run cleanly and surface why (instead of silently dropping
                            # every remaining job as a generic failure).
                            stop_reason = str(exc)
                            log.warning("ingest_stopped_embed_quota embeds_used=%s", embeds_used)
                            break
                        embeds_used += 1
                        weaviate_store.upsert(job, vector)

                        from jobscout.models import JobSource

                        js = JobSource(
                            job_id=job.job_id,
                            source=ingest_source,
                            url=ingest_url,
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

            log.info(
                "adapter_done adapter=%s new=%s skipped(existing)=%s failed=%s",
                adapter.name, count_ingested, count_skipped, count_failed,
            )
            relational_store.finish_run(
                run_id=run_log.id,
                count_ingested=count_ingested,
                count_failed=count_failed,
                error=error_msg or stop_reason,
            )
    finally:
        http.close()

    # Opt-in: refresh the local Weaviate backup after ingest (off by default).
    # The export is a pure $0 download (no embedding); data only changes on ingest.
    if settings.export_after_ingest:
        try:
            from jobscout.backup import export_index
            header = export_index(weaviate_store)
            log.info("post_ingest_backup_exported count=%s", header.get("count"))
        except Exception:
            log.warning("post_ingest_backup_failed", exc_info=True)


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

        # A curated adapter may have stamped employer_type at ingest; keep it
        # rather than overwriting with the LLM guess. cap_exempt is derived from
        # whichever employer_type wins.
        stored_employer_type = p.get("employer_type") or "unclear"
        employer_type = (
            stored_employer_type
            if stored_employer_type != "unclear"
            else enr.get("employer_type", "unclear")
        )
        fields: dict[str, Any] = {
            "yoe_min": enr.get("yoe_min"),
            "yoe_max": enr.get("yoe_max"),
            "visa_sponsorship": enr.get("visa_sponsorship", "not_mentioned"),
            "skills": enr.get("skills", []),
            "seniority": enr.get("seniority", "unclear"),
            "security_clearance": enr.get("security_clearance", "unclear"),
            "citizenship_required": enr.get("citizenship_required", False),
            "employer_type": employer_type,
            "cap_exempt": derive_cap_exempt(employer_type),
            "is_recruiter_post": detect_recruiter_post(
                p.get("company") or None, p.get("source", ""), description
            ),
            "enrichment_status": "done",
        }
        # Only set company size from the LLM if not already known (config wins).
        if not (p.get("company_size_bucket") or "") and enr.get("company_size_bucket"):
            fields["company_size_bucket"] = enr["company_size_bucket"]
        weaviate_store.update_fields(job_id, fields)
        enriched += 1

    log.info("enrich_run complete enriched=%d failed=%d scanned=%d", enriched, failed, len(targets))


_REFRESH_ADAPTER: dict[str, Any] = {
    "greenhouse": lambda slugs: GreenhouseAdapter(companies=slugs),
    "lever": lambda slugs: LeverAdapter(companies=slugs),
    "ashby": lambda slugs: AshbyAdapter(companies=slugs),
    "workable": lambda accounts: WorkableAdapter(accounts=accounts),
    # Workday items are {tenant, region, site, name, type} dicts (cap-exempt
    # universities/AMCs); the others are {token, type}.
    "workday": lambda tenants: WorkdayAdapter(tenants=tenants, fetch_descriptions=True),
}


def _refresh_watchlist(
    weaviate_store: WeaviateStore,
    relational_store: RelationalStore,
    budget: int,
    keywords: list[str],
) -> dict[str, Any]:
    """Re-check enabled companies and ingest only NEW jobs, capped by *budget* embeds.

    Skips any job already in Weaviate (dedup by job_id) so a refresh after the
    first pull is cheap; only previously-unseen jobs are enriched + embedded,
    each counting against the embedding budget (Gemini free tier = 1,000/day).
    """
    companies = [c for c in relational_store.enabled_companies() if c.ats in _REFRESH_ADAPTER]
    # Group entries by ATS so each adapter is built once. Workday needs the full
    # tenant connection ({tenant, region, site, name, type}); slug ATS use {token, type}.
    by_ats: dict[str, list[dict]] = {}
    for c in companies:
        if c.ats == "workday":
            by_ats.setdefault("workday", []).append({
                "tenant": c.slug, "region": c.region or "wd1", "site": c.site or "",
                "name": c.name, "type": c.employer_type,
            })
        else:
            by_ats.setdefault(c.ats, []).append({"token": c.slug, "type": c.employer_type})

    http = CompliantHttpClient()
    embeds_used = new_jobs = 0
    refreshed: set[str] = set()
    stopped_early = False
    try:
        for ats, slugs in by_ats.items():
            if embeds_used >= budget or stopped_early:
                stopped_early = True
                break
            adapter = _REFRESH_ADAPTER[ats](slugs)
            per_company: dict[str, int] = {}
            for raw in adapter.search(
                keywords=keywords, location=None, results_wanted=10_000,
                since=None, http=http,
            ):
                slug = str(raw.get("company") or "")
                per_company[slug] = per_company.get(slug, 0) + 1
                if embeds_used >= budget:
                    stopped_early = True
                    break
                job = raw_to_job(raw, source=ats)
                if weaviate_store.get_by_id(job.job_id) is not None:
                    continue  # already have it — no embed spent
                if not is_us_job(job.country, job.location_raw, job.remote_mode):
                    continue
                try:
                    enr = extract_enrichment(job.title, job.company, job.description)
                    job.yoe_min = enr.get("yoe_min")
                    job.yoe_max = enr.get("yoe_max")
                    job.visa_sponsorship = enr.get("visa_sponsorship", "not_mentioned")
                    job.skills = enr.get("skills", [])
                    job.seniority = enr.get("seniority", "unclear")
                    if job.employer_type == "unclear":
                        job.employer_type = enr.get("employer_type", "unclear")
                    job.enrichment_status = "done"
                except EnrichmentError:
                    job.enrichment_status = "failed"
                job.cap_exempt = derive_cap_exempt(job.employer_type)
                job.known_h1b_sponsor = is_known_h1b_sponsor(job.company)
                job.known_everify = is_everify_employer(job.company)
                job.is_recruiter_post = detect_recruiter_post(
                    job.company, job.source, job.description
                )
                try:
                    vector = embed_job(
                        title=job.title, company=job.company,
                        skills=job.skills, description=job.description,
                    )
                except EmbeddingQuotaError:
                    # Same quota ceiling as "Get latest jobs" — stop cleanly so the
                    # refresh doesn't crash; the app-level flag (embed.py) drives
                    # the UI banner.
                    stopped_early = True
                    log.warning("refresh_stopped_embed_quota embeds_used=%s", embeds_used)
                    break
                embeds_used += 1
                weaviate_store.upsert(job, vector)
                from jobscout.models import JobSource
                relational_store.upsert_job_source(JobSource(
                    job_id=job.job_id, source=job.source, url=job.url,
                    posted_date=job.posted_date,
                ))
                new_jobs += 1
            # Record open-role counts per refreshed company.
            for slug, n in per_company.items():
                norm = slug.lower()
                # Match registry slug (companies were keyed by slug; adapters echo it).
                relational_store.touch_company(ats, norm, n)
                refreshed.add(norm)
    finally:
        http.close()

    return {
        "companies_refreshed": len(refreshed),
        "new_jobs": new_jobs,
        "embeds_used": embeds_used,
        "budget": budget,
        "stopped_early": stopped_early,
    }
