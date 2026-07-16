"""Ingestion + enrichment + watchlist-refresh background jobs.

Extracted from api/main.py. Stateless functions taking the open stores as
parameters; the API schedules them via BackgroundTasks / the scheduler.
"""
from __future__ import annotations

import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

from jobscout.adapters import (
    AshbyAdapter,
    GreenhouseAdapter,
    LeverAdapter,
    RecruiteeAdapter,
    RipplingAdapter,
    SmartRecruitersAdapter,
    WorkableAdapter,
    WorkdayAdapter,
)
from jobscout.adapters.base import CompliantHttpClient
from jobscout.config import settings
from jobscout.eligibility import extract_work_authorization_evidence
from jobscout.embed import EmbeddingQuotaError, embed_job
from jobscout.enrich import (
    EnrichmentError,
    derive_cap_exempt,
    detect_recruiter_post,
    extract_enrichment,
    llm_is_configured,
)
from jobscout.models import Job, RunLog, UserProfile
from jobscout.normalize import is_us_job, raw_to_job
from jobscout.relational import RelationalStore
from jobscout.services import scoring_cache
from jobscout.services.source_config import (
    _DEFAULT_AUTHORITY,
    _SOURCE_AUTHORITY,
    _build_adapters,
    _company_size_map,
    _load_sources_cfg,
)
from jobscout.source_intelligence import (
    CURATED_SOURCES,
    GOVERNMENT_SOURCES,
    PRIMARY_SOURCES,
)
from jobscout.sponsors import is_everify_employer, is_known_h1b_sponsor
from jobscout.store import COLLECTION_NAME, WeaviateStore
from jobscout.verdict import (
    _OBVIOUS_UNRELATED_TITLES,
    _SENIORITY_RANK,
    _role_families,
    _title_seniority,
)

log = logging.getLogger(__name__)


# Keywords currently being auto-fetched, to avoid duplicate background runs.
_autofetch_inflight: set[str] = set()
AUTOFETCH_MIN_RESULTS = 3
# Cap on concurrent in-flight auto-fetches: each spawns a heavy multi-source
# ingest, so a burst of sparse searches must not be able to fan out unbounded.
AUTOFETCH_MAX_INFLIGHT = 2


def _apply_deterministic_eligibility(job: Job) -> None:
    """Apply explicit work-authorization wording before any LLM can infer it."""
    signals = extract_work_authorization_evidence(job.description)
    job.eligibility_evidence = signals["evidence"]
    if signals["visa_sponsorship"] == "no":
        job.visa_sponsorship = "no"
    if signals["citizenship_required"]:
        job.citizenship_required = True
    if signals["security_clearance"] == "required":
        job.security_clearance = "required"


def _is_profile_candidate(job: Job, profile: UserProfile) -> bool:
    """Cheap, conservative pre-LLM gate for a manual profile refresh.

    It drops only clear impossibilities. Semantic and skill judgment is deferred
    to query-time verdict/ranking so unfamiliar but plausible roles are retained.
    """
    if profile.needs_sponsorship and job.visa_sponsorship == "no":
        return False
    if profile.reject_citizenship_only and job.citizenship_required:
        return False
    if profile.reject_clearance and job.security_clearance == "required":
        return False
    # Role-type gate uses the SAME taxonomy as the query-time verdict
    # (_role_families), fail-open on both sides: a drop requires the profile
    # AND the job title to each map to known families with zero overlap.
    # (The old derive_category gate silently dropped titles the verdict would
    # recommend — e.g. "Business Intelligence Analyst" for a data-analyst
    # profile — before they were ever stored.)
    profile_families = set()
    for title in profile.target_titles:
        if title.strip():
            profile_families |= _role_families(title)
    job_families = _role_families(job.title)
    if profile_families and job_families and not (profile_families & job_families):
        return False
    # Licensed/direct-care occupations sit outside the family taxonomy on
    # purpose; don't let fail-open spend enrichment budget on them unless the
    # profile explicitly targets one.
    if (
        profile_families
        and not job_families
        and _OBVIOUS_UNRELATED_TITLES.search(job.title or "")
        and not any(
            _OBVIOUS_UNRELATED_TITLES.search(t or "") for t in profile.target_titles
        )
    ):
        return False
    # A title can advertise a level before the LLM finds a precise YoE value.
    # Fail-open on an unknown profile ceiling, matching the verdict's behavior.
    title_level = _title_seniority(job.title)
    title_rank = _SENIORITY_RANK.get(title_level) if title_level else None
    max_rank = _SENIORITY_RANK.get(profile.seniority_max)
    if title_rank is None or max_rank is None:
        return True
    return title_rank <= max_rank + 1


# ---------------------------------------------------------------------------
# Retention + first-run seed
# ---------------------------------------------------------------------------

# Keyless, fast sources used to make a fresh deployment non-empty. Simplify's
# curated new-grad feed leads (one cheap GET, highest entry-level density), then
# a few no-key aggregators / boards. No API keys required beyond Gemini embeddings.
SEED_SOURCES: frozenset[str] = frozenset(
    {"simplify", "remotive", "remoteok", "greenhouse"}
)
SEED_KEYWORDS = ["software engineer", "data engineer", "data scientist", "machine learning"]
_SEEDED_MARKER = "seeded_at"


def prune_stale_jobs(weaviate_store: WeaviateStore) -> int:
    """Drop jobs older than ``settings.retention_days`` (0 disables). Returns count.

    Reuses :meth:`WeaviateStore.purge_older_than` (posting age, falling back to
    ingest time). Kept deliberately past the ghost-risk window so a job is flagged
    before it is ever removed.
    """
    days = settings.retention_days
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    try:
        removed = weaviate_store.purge_older_than(cutoff)
        if removed:
            log.info("retention_pruned count=%s older_than_days=%s", removed, days)
        return removed
    except Exception:  # noqa: BLE001 — retention must never break an ingest
        log.warning("retention_prune_failed", exc_info=True)
        return 0


def seed_first_run(
    weaviate_store: WeaviateStore, relational_store: RelationalStore
) -> None:
    """One bounded ingest so a fresh deployment isn't empty, then stamp it done.

    Caller guarantees the preconditions (empty index, embedding key present, not
    already seeded). Stamps the marker only on success so a crash mid-seed retries
    on the next boot instead of leaving the index permanently empty.
    """
    log.info("first_run_seed_start count=%s sources=%s", settings.seed_job_count, sorted(SEED_SOURCES))
    try:
        _run_ingestion(
            keywords=SEED_KEYWORDS,
            location=None,
            results_wanted=settings.seed_job_count,
            weaviate_store=weaviate_store,
            relational_store=relational_store,
            source_names=SEED_SOURCES,
        )
    except Exception:  # noqa: BLE001
        log.warning("first_run_seed_failed", exc_info=True)
        return
    relational_store.set_meta(_SEEDED_MARKER, datetime.now(UTC).isoformat())
    log.info("first_run_seed_done")


# ---------------------------------------------------------------------------
# Background ingestion task (runs in a thread pool via BackgroundTasks)
# ---------------------------------------------------------------------------

def _run_ingestion(
    keywords: list[str],
    location: str | None,
    results_wanted: int,
    weaviate_store: WeaviateStore,
    relational_store: RelationalStore,
    profile: UserProfile | None = None,
    source_names: set[str] | frozenset[str] | None = None,
) -> None:
    """Iterate enabled adapters and ingest jobs into Weaviate + DuckDB.

    With a selected profile, an explicit eligibility/title-category precheck
    runs before LLM enrichment. Every candidate that passes that conservative
    gate is stored in the shared index; profile verdicts are query-time concerns.
    This preserves plausible jobs when enrichment is incomplete and lets future
    profile/ranking improvements reconsider the same canonical record.
    """
    cfg = _load_sources_cfg()
    sources_cfg: dict[str, Any] = cfg.get("sources", {})

    adapters = _build_adapters(sources_cfg)
    if source_names is not None:
        adapters = [adapter for adapter in adapters if adapter.name in source_names]
        # Profile refills should surface likely tech matches quickly. Workday is
        # valuable for cap-exempt roles but its multi-tenant/detail crawl is much
        # slower, so run the focused tech ATS sources first and Workday last.
        refill_order = {
            name: index for index, name in enumerate((
                # simplify first: one cheap GET, highest entry-level density.
                "simplify", "greenhouse", "lever", "ashby", "workable",
                "rippling", "recruitee", "smartrecruiters", "workday",
            ))
        }
        adapters.sort(key=lambda adapter: refill_order.get(adapter.name, 99))
        for adapter in adapters:
            if isinstance(adapter, WorkdayAdapter) and profile is not None:
                def _tenant_rank(tenant: dict[str, Any]) -> int:
                    employer_type = tenant.get("type", "unclear")
                    if employer_type == "hospital":
                        return 3
                    if profile.prefer_cap_exempt:
                        return 0 if employer_type in {
                            "university", "nonprofit", "government",
                        } else 1
                    return 0 if employer_type == "for_profit" else 1

                # Avoid spending the first minutes of a technical profile refill
                # on hospital clinical boards. Relevant tech employers lead,
                # cap-exempt universities/nonprofits follow, hospitals remain a
                # last-resort source for explicitly matching technical titles.
                adapter.tenants.sort(key=_tenant_rank)
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

    def _fetch(adapter: Any) -> tuple[Any, list[dict], str | None]:
        """Network phase only — one CompliantHttpClient per worker thread.

        Adapters own disjoint domains, so per-domain rate limiting/robots stay
        intact with a client per worker. Everything stateful (enrichment, embed
        budget, DuckDB/Weaviate writes, run logs) remains on the main thread.
        """
        client = CompliantHttpClient()
        try:
            raws = list(adapter.search(
                keywords=keywords,
                location=location,
                results_wanted=results_wanted,
                since=None,
                http=client,
            ))
            return adapter, raws, None
        except Exception:
            log.error("adapter_fetch_failed adapter=%s", adapter.name, exc_info=True)
            return adapter, [], traceback.format_exc()
        finally:
            client.close()

    fetch_workers = max(1, int(settings.ingest_fetch_workers))
    with ThreadPoolExecutor(max_workers=fetch_workers) as pool:
        futures = [pool.submit(_fetch, adapter) for adapter in adapters]
        for future in as_completed(futures):
            adapter, raws, fetch_error = future.result()
            if stop_reason:
                continue  # budget reached / quota hit — skip processing the rest
            run_log: RunLog = relational_store.start_run(adapter.name)
            count_ingested = 0
            count_failed = 0
            count_seen = 0
            count_filtered = 0
            count_skipped = 0  # already-indexed jobs skipped (no embed spent)
            error_msg: str | None = fetch_error

            try:
                for raw in raws:
                    try:
                        job: Job = raw_to_job(raw, source=adapter.name)
                        count_seen += 1

                        # JobScout is US-only: drop non-US jobs before the
                        # expensive enrichment/embedding steps.
                        if not is_us_job(
                            job.country, job.location_raw, job.remote_mode,
                            title=job.title,
                        ):
                            continue

                        _apply_deterministic_eligibility(job)
                        if profile is not None and not _is_profile_candidate(job, profile):
                            count_filtered += 1
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
                        elif llm_is_configured() and job.description:
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
                                if job.visa_sponsorship != "no":
                                    job.visa_sponsorship = enr.get(
                                        "visa_sponsorship", "not_mentioned"
                                    )
                                job.skills = enr.get("skills", [])
                                job.seniority = enr.get("seniority", "unclear")
                                if job.security_clearance != "required":
                                    job.security_clearance = enr.get(
                                        "security_clearance", "unclear"
                                    )
                                job.citizenship_required = (
                                    job.citizenship_required
                                    or enr.get("citizenship_required", False)
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
                count_seen=count_seen,
                count_filtered=count_filtered,
                error=error_msg or stop_reason,
            )
    # Job records changed → memoized verdicts are stale.
    scoring_cache.clear()

    # Keep the index a rolling recent window (freshness stays live, not frozen).
    prune_stale_jobs(weaviate_store)

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


def _profile_autofetch_and_clear(
    keywords: list[str],
    weaviate_store: WeaviateStore,
    relational_store: RelationalStore,
    profile: UserProfile,
    key: str,
    results_wanted: int = 50,
) -> None:
    """Run a profile-targeted refill and release its de-duplication key.

    This powers the For You self-refill path. It checks direct employer ATS
    sources first/only, and the conservative pre-enrichment profile gate limits
    wasted work. Every plausible candidate is retained for query-time scoring
    instead of requiring a perfect enrichment result.
    """
    try:
        _run_ingestion(
            keywords,
            None,
            results_wanted,
            weaviate_store,
            relational_store,
            profile,
            # Direct ATS + government boards + curated feeds (SimplifyJobs)
            # — every "direct" class; curated feeds are exactly the entry-level
            # supply this refill exists to find.
            PRIMARY_SOURCES | GOVERNMENT_SOURCES | CURATED_SOURCES,
        )
        # Self-heal: retry rows stuck in pending/failed (e.g. a DeepSeek outage
        # during an earlier ingest) — bounded, and a no-op without an LLM key.
        # Unenriched rows are blocked from recommendations, so leaving them
        # broken starves the For You feed permanently.
        if llm_is_configured():
            try:
                _run_enrichment(weaviate_store, limit=50)
            except Exception:
                log.warning("refill_enrichment_sweep_failed", exc_info=True)
    finally:
        _autofetch_inflight.discard(key)


def _run_enrichment(weaviate_store: WeaviateStore, limit: int) -> None:
    """Enrich jobs whose status is ``pending`` or ``failed`` (bounded by *limit*).

    Lets enrichment be retried/decoupled from ingestion — e.g. to recover jobs
    that failed during a DeepSeek outage. Preserves a company-size value that was
    already set (e.g. from config) and only fills it from the LLM when empty.
    """
    if not llm_is_configured():
        log.warning("enrich_run skipped: selected LLM provider is not configured")
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
        eligibility = extract_work_authorization_evidence(description)
        deterministic_visa = eligibility["visa_sponsorship"] == "no"
        deterministic_clearance = eligibility["security_clearance"] == "required"
        employer_type = (
            stored_employer_type
            if stored_employer_type != "unclear"
            else enr.get("employer_type", "unclear")
        )
        fields: dict[str, Any] = {
            "yoe_min": enr.get("yoe_min"),
            "yoe_max": enr.get("yoe_max"),
            "visa_sponsorship": "no" if deterministic_visa else enr.get("visa_sponsorship", "not_mentioned"),
            "skills": enr.get("skills", []),
            "seniority": enr.get("seniority", "unclear"),
            "security_clearance": "required" if deterministic_clearance else enr.get("security_clearance", "unclear"),
            "citizenship_required": bool(eligibility["citizenship_required"] or enr.get("citizenship_required", False)),
            "eligibility_evidence": eligibility["evidence"],
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

    if enriched:
        scoring_cache.clear()  # enrichment changed job fields → verdicts are stale
    log.info("enrich_run complete enriched=%d failed=%d scanned=%d", enriched, failed, len(targets))


_REFRESH_ADAPTER: dict[str, Any] = {
    "greenhouse": lambda slugs: GreenhouseAdapter(companies=slugs),
    "lever": lambda slugs: LeverAdapter(companies=slugs),
    "ashby": lambda slugs: AshbyAdapter(companies=slugs),
    "workable": lambda accounts: WorkableAdapter(accounts=accounts),
    "rippling": lambda slugs: RipplingAdapter(companies=slugs, fetch_descriptions=True),
    "recruitee": lambda slugs: RecruiteeAdapter(companies=slugs),
    "smartrecruiters": lambda slugs: SmartRecruitersAdapter(
        companies=slugs, fetch_descriptions=True
    ),
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
    closed_jobs = 0
    refreshed: set[str] = set()
    stopped_early = False
    try:
        for ats, slugs in by_ats.items():
            if embeds_used >= budget or stopped_early:
                stopped_early = True
                break
            adapter = _REFRESH_ADAPTER[ats](slugs)
            per_company: dict[str, int] = {}
            seen_by_board: dict[str, set[str]] = {}
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
                board_slug = str(raw.get("_board_slug") or "")
                if ats == "greenhouse" and board_slug:
                    seen_by_board.setdefault(board_slug, set()).add(job.job_id)
                    relational_store.mark_board_job_seen(ats, board_slug, job.job_id)
                    job.last_seen_at = datetime.now(UTC)

                existing = weaviate_store.get_by_id(job.job_id)
                if existing is not None:
                    # A re-listed job is immediately visible again. This keeps
                    # lifecycle state honest without re-embedding its vector.
                    if existing.is_active is False:
                        weaviate_store.update_fields(job.job_id, {
                            "is_active": True,
                            "closed_at": None,
                            "last_seen_at": datetime.now(UTC).isoformat(),
                        })
                    continue  # already have it — no embed spent
                if not is_us_job(job.country, job.location_raw, job.remote_mode, title=job.title):
                    continue
                _apply_deterministic_eligibility(job)
                try:
                    if llm_is_configured():
                        enr = extract_enrichment(job.title, job.company, job.description)
                        job.yoe_min = enr.get("yoe_min")
                        job.yoe_max = enr.get("yoe_max")
                        if job.visa_sponsorship != "no":
                            job.visa_sponsorship = enr.get("visa_sponsorship", "not_mentioned")
                        job.skills = enr.get("skills", [])
                        job.seniority = enr.get("seniority", "unclear")
                        if job.security_clearance != "required":
                            job.security_clearance = enr.get("security_clearance", "unclear")
                        job.citizenship_required = job.citizenship_required or enr.get("citizenship_required", False)
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
            # Closure detection is intentionally limited to a full Greenhouse
            # board snapshot. Other adapters paginate differently and will be
            # added only after they expose the same completion guarantee.
            if ats == "greenhouse" and not keywords and not stopped_early:
                for board_slug in getattr(adapter, "completed_boards", set()):
                    missing = relational_store.close_missing_board_jobs(
                        ats, board_slug, seen_by_board.get(board_slug, set())
                    )
                    for job_id in missing:
                        if not relational_store.job_has_active_board_presence(job_id):
                            try:
                                weaviate_store.update_fields(job_id, {
                                    "is_active": False,
                                    "closed_at": datetime.now(UTC).isoformat(),
                                })
                            except Exception:
                                # The vector object might have been explicitly
                                # purged since its checkpoint; DuckDB still has
                                # the correct closure state, so do not fail a
                                # whole board refresh over that stale object.
                                log.warning("watchlist_close_update_failed job_id=%s", job_id, exc_info=True)
                            else:
                                closed_jobs += 1
    finally:
        http.close()

    scoring_cache.clear()

    # Self-heal stuck enrichment on the scheduled path too (see refill note).
    if llm_is_configured():
        try:
            _run_enrichment(weaviate_store, limit=50)
        except Exception:
            log.warning("watchlist_enrichment_sweep_failed", exc_info=True)

    return {
        "companies_refreshed": len(refreshed),
        "new_jobs": new_jobs,
        "embeds_used": embeds_used,
        "budget": budget,
        "stopped_early": stopped_early,
        "closed_jobs": closed_jobs,
    }
