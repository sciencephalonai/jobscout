"""Weaviate hybrid query builder and search executor.

This module is the single place where filter parameters (date range, YoE,
visa, remote mode, source) are translated into Weaviate ``Filter`` objects
and a hybrid (BM25 + vector) query is assembled and executed.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from weaviate.classes.query import Filter, MetadataQuery

from jobscout.embed import embed_query
from jobscout.models import GHOST_STALE_DAYS, Job, JobsResponse
from jobscout.source_intelligence import CURATED_SOURCES, GOVERNMENT_SOURCES, PRIMARY_SOURCES
from jobscout.store import COLLECTION_NAME, WeaviateStore, _props_to_job

# ---------------------------------------------------------------------------
# Date preset → timedelta mapping
# ---------------------------------------------------------------------------

DATE_PRESETS: dict[str, dt.timedelta] = {
    "6h": dt.timedelta(hours=6),
    "12h": dt.timedelta(hours=12),
    "18h": dt.timedelta(hours=18),
    "24h": dt.timedelta(hours=24),
    "7d": dt.timedelta(days=7),
    "14d": dt.timedelta(days=14),
    "21d": dt.timedelta(days=21),
    "1m": dt.timedelta(days=30),
}

# Default freshness ladder for generic browsing: keep the feed tightly focused
# on newly posted roles.
PROGRESSIVE_LADDER: list[str] = ["6h", "12h", "18h", "24h"]

# Personalized recommendations optimize for qualified matches before recency.
# A sparse day must not produce an empty feed when a still-active, profile-fit
# role exists in the index. The API stops as soon as it has enough qualified
# matches, so fresh roles still win without allowing unrelated filler.
# Three rungs, not five: entry-level supply is scarce, so the 6h/14d rungs
# almost never fill the target and each rung re-scores a full candidate window
# (~20s apiece against the live profile).
RECOMMENDATION_LADDER: list[str] = ["24h", "7d", "1m"]


# ---------------------------------------------------------------------------
# Filter builder
# ---------------------------------------------------------------------------

def build_filters(
    remote: list[str] | None = None,
    visa: list[str] | None = None,
    source: list[str] | None = None,
    company_size: list[str] | None = None,
    exp: list[str] | None = None,
    employer_type: list[str] | None = None,
    cap_exempt: list[str] | None = None,
    security_clearance: list[str] | None = None,
    category: list[str] | None = None,
    employment_type: list[str] | None = None,
    exclude_citizenship_required: bool = False,
    exclude_recruiter: bool = False,
    exclude_no_sponsorship: bool = False,
    exclude_ghost: bool = False,
    true_entry_only: bool = False,
    new_grad_only: bool = False,
    h1b_sponsor: bool = False,
    everify: bool = False,
    direct_sources_only: bool = False,
    date_range: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    ingested_after: dt.datetime | None = None,
    include_active: bool = False,
) -> Any | None:
    """Compose Weaviate ``Filter`` objects from search parameters.

    Each parameter is optional.  When multiple filters are provided they are
    combined with logical AND.  Returns ``None`` if no filters are specified
    (Weaviate treats ``None`` as "no filter").

    Args:
        remote:     One of ``remote|onsite|hybrid|unknown`` or ``None``.
        yoe_max:    Upper bound on ``yoe_min`` stored in the job
                    (show jobs that require *at most* this many years).
        yoe_min:    Lower bound on ``yoe_max`` stored in the job
                    (show jobs that are relevant for candidates with at least
                    this many years).
        visa:       One of ``yes|no|unclear|not_mentioned``.
        date_range: Preset key from ``DATE_PRESETS`` (e.g. ``"7d"``).
        date_from:  Start of a custom date range (inclusive).
        date_to:    End of a custom date range (inclusive).
        source:     Adapter name (e.g. ``"adzuna"``).

    Returns:
        A Weaviate ``Filter`` (possibly a compound AND) or ``None``.
    """
    clauses: list[Any] = []

    def _or(parts: list[Any]) -> Any | None:
        """OR a list of Filter clauses (None if empty)."""
        parts = [p for p in parts if p is not None]
        if not parts:
            return None
        combined = parts[0]
        for p in parts[1:]:
            combined = combined | p
        return combined

    def _any_equal(prop: str, values: list[str] | None) -> Any | None:
        """Match a property against ANY of the given values (multi-select OR)."""
        return _or([Filter.by_property(prop).equal(v) for v in (values or []) if v])

    if include_active:
        # Only a completed direct-ATS snapshot explicitly writes ``False``. The
        # existing collection contains legacy NULLs, but Weaviate's ``is_none``
        # filter does not reliably include them when combined with an OR. A
        # negative check is both simpler and preserves every record except one
        # proven closed by lifecycle tracking.
        clauses.append(Filter.by_property("is_active").not_equal(False))

    # Categorical multi-select filters: OR within each, AND across them.
    for prop, vals in (
        ("remote_mode", remote),
        ("visa_sponsorship", visa),
        ("source", source),
        ("company_size_bucket", company_size),
        ("employer_type", employer_type),
        ("security_clearance", security_clearance),
        ("category", category),
        ("employment_type", employment_type),
    ):
        f = _any_equal(prop, vals)
        if f is not None:
            clauses.append(f)

    # Boolean exclusion filters (hard cuts).
    if exclude_citizenship_required:
        clauses.append(Filter.by_property("citizenship_required").equal(False))
    if exclude_recruiter:
        clauses.append(Filter.by_property("is_recruiter_post").equal(False))
    # Use the same source classification that deduplication and the API expose.
    # This is more dependable than a recruiter heuristic when the candidate only
    # wants a primary application URL.
    if direct_sources_only:
        direct = _any_equal(
            "source", sorted(PRIMARY_SOURCES | GOVERNMENT_SOURCES | CURATED_SOURCES)
        )
        if direct is not None:
            clauses.append(direct)
    # "Hide no-sponsorship": drop explicit refusals AND citizenship-required roles,
    # but KEEP the ~96% that say nothing (visa_sponsorship == "not_mentioned").
    if exclude_no_sponsorship:
        # "no" is a Weaviate stopword, so .not_equal("no") errors with
        # "only stopwords provided". Express the same intent positively over the
        # non-refusal enum values (keep yes/unclear/not_mentioned, drop "no").
        keep_visa = _or([
            Filter.by_property("visa_sponsorship").equal(v)
            for v in ("yes", "unclear", "not_mentioned")
        ])
        if keep_visa is not None:
            clauses.append(keep_visa)
        clauses.append(Filter.by_property("citizenship_required").equal(False))
    # "Hide likely-stale" (ghost jobs): drop postings older than GHOST_STALE_DAYS that
    # are still listed. Uses the stored posted_date so it paginates correctly. The
    # nuanced per-job badge (ghost_risk) additionally folds in recruiter/estimated-date.
    if exclude_ghost:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=GHOST_STALE_DAYS)
        clauses.append(Filter.by_property("posted_date").greater_or_equal(cutoff))
    # "True entry-level": restrict to high-confidence entry roles. yoe_max is almost
    # always unenriched, so key off yoe_min (≤2 = entry), plus junior/intern when YoE is
    # unknown, plus explicit new-grad programs. Excludes senior roles and the loose
    # "unclear"-seniority bucket that the Experience→Entry band lets through.
    if true_entry_only:
        yoe = Filter.by_property("yoe_min")
        sen = Filter.by_property("seniority")
        # Exclude explicitly senior-and-up roles even when they list a low yoe_min
        # (e.g. a "Staff ML Engineer" asking for "2+ years" is not entry-level).
        not_senior = (
            sen.not_equal("senior") & sen.not_equal("staff") & sen.not_equal("principal")
            & sen.not_equal("lead") & sen.not_equal("manager") & sen.not_equal("director")
            & sen.not_equal("vp") & sen.not_equal("c_level")
        )
        entry_yoe = _or([
            yoe.less_or_equal(2),
            yoe.is_none(True) & (sen.equal("intern") | sen.equal("junior")),
        ])
        # New-grad programs are entry-level by definition → always kept; otherwise an
        # entry YoE signal AND not a senior-leveled title.
        keep = _or([
            Filter.by_property("new_grad_program").equal(True),
            (entry_yoe & not_senior) if entry_yoe is not None else None,
        ])
        if keep is not None:
            clauses.append(keep)
    # "New-grad programs only": explicit new-grad/university/early-career/rotational roles.
    if new_grad_only:
        clauses.append(Filter.by_property("new_grad_program").equal(True))
    # Positive sponsorship signals are OR'd together (additive), then AND'd with the
    # rest. cap-exempt (university/nonprofit), proven H-1B filer, and E-Verify
    # employer rarely overlap, so AND-ing them would empty the list — a user enabling
    # several wants the UNION of "any positive work-authorization signal".
    positive = _or([
        _any_equal("cap_exempt", cap_exempt),
        Filter.by_property("known_h1b_sponsor").equal(True) if h1b_sponsor else None,
        Filter.by_property("known_everify").equal(True) if everify else None,
    ])
    if positive is not None:
        clauses.append(positive)

    # "New since last visit" — only jobs ingested after a cutoff (saved-search alerts).
    if ingested_after is not None:
        clauses.append(Filter.by_property("ingested_at").greater_than(ingested_after))

    # Experience bands (multi-select), matched against the role's required
    # years (yoe_min). Selected bands are OR'd together.
    # When yoe_min is null (unenriched job), fall back to the seniority field
    # so that Senior/Lead/Manager titles don't bleed into entry-level results.
    if exp:
        yoe = Filter.by_property("yoe_min")

        def _seniority_in(*vals: str) -> Any:
            f = Filter.by_property("seniority").equal(vals[0])
            for v in vals[1:]:
                f = f | Filter.by_property("seniority").equal(v)
            return f

        band_filters = {
            "entry": yoe.less_or_equal(2) | (
                yoe.is_none(True) & _seniority_in("intern", "junior", "unclear")
            ),
            "mid": (yoe.greater_or_equal(3) & yoe.less_or_equal(5)) | (
                yoe.is_none(True) & _seniority_in("mid")
            ),
            "senior": (yoe.greater_or_equal(6) & yoe.less_or_equal(10)) | (
                yoe.is_none(True) & _seniority_in("senior", "staff")
            ),
            "lead": yoe.greater_or_equal(11) | (
                yoe.is_none(True) & _seniority_in("lead", "principal", "manager", "director", "vp", "c_level")
            ),
        }
        bands = _or([band_filters[b] for b in exp if b in band_filters])
        if bands is not None:
            clauses.append(bands)

    # Date filters — preset takes priority over explicit from/to
    if date_range and date_range in DATE_PRESETS:
        cutoff = dt.datetime.now(dt.UTC) - DATE_PRESETS[date_range]
        clauses.append(Filter.by_property("posted_date").greater_than(cutoff))
    else:
        if date_from is not None:
            from_dt = dt.datetime(
                date_from.year, date_from.month, date_from.day, tzinfo=dt.UTC
            )
            clauses.append(
                Filter.by_property("posted_date").greater_or_equal(from_dt)
            )
        if date_to is not None:
            # Include the full day: advance to start of the *next* day
            to_dt = dt.datetime(
                date_to.year, date_to.month, date_to.day, tzinfo=dt.UTC
            ) + dt.timedelta(days=1)
            clauses.append(Filter.by_property("posted_date").less_than(to_dt))

    if not clauses:
        return None

    combined: Any = clauses[0]
    for clause in clauses[1:]:
        combined = combined & clause
    return combined


# ---------------------------------------------------------------------------
# Facet aggregation
# ---------------------------------------------------------------------------

def _fetch_facets(
    store: WeaviateStore,
    filters: Any | None,
) -> dict[str, dict[str, int]]:
    """Run aggregate queries to produce facet counts for the UI.

    Returns a dict with keys ``visa_sponsorship``, ``remote_mode``, ``source``,
    each mapping to ``{value: count}``.

    Weaviate's ``aggregate.over_all`` with ``group_by`` returns one bucket per
    distinct property value within the current filter set.
    """
    collection = store._client.collections.get(COLLECTION_NAME)
    facets: dict[str, dict[str, int]] = {}

    for prop in (
        "visa_sponsorship", "remote_mode", "source", "company_size_bucket",
        "employer_type", "cap_exempt", "security_clearance", "category",
    ):
        try:
            result = collection.aggregate.over_all(
                filters=filters,
                group_by=prop,
                total_count=True,
            )
            facets[prop] = {
                str(group.grouped_by.value): group.total_count or 0
                for group in (result.groups or [])
                if group.grouped_by is not None
            }
        except Exception:
            # Aggregation is best-effort; do not fail the whole request
            facets[prop] = {}

    return facets


# ---------------------------------------------------------------------------
# Main search executor
# ---------------------------------------------------------------------------

def execute_search(
    store: WeaviateStore,
    q: str | None,
    alpha: float,
    filters: Any | None,
    sort: str,
    page: int,
    page_size: int,
    include_facets: bool = True,
) -> JobsResponse:
    """Execute a Weaviate query and return a paginated :class:`~jobscout.models.JobsResponse`.

    Strategy:
    - If ``q`` is a non-empty string → hybrid query (BM25 + vector via
      ``embed_query``).
    - If ``q`` is empty/None → ``fetch_objects`` with filters and sort only.

    Facet counts for ``visa_sponsorship``, ``remote_mode``, and ``source`` are
    computed via separate aggregate calls scoped to the same filter.

    Args:
        store:      Open :class:`~jobscout.store.WeaviateStore` instance.
        q:          Keyword/semantic query string.
        alpha:      Hybrid blend (0 = pure BM25, 1 = pure vector).
        filters:    Weaviate Filter object (from :func:`build_filters`) or None.
        sort:       ``posted_desc | relevance | salary_desc``.
        page:       1-based page number.
        page_size:  Number of results per page.

    Returns:
        :class:`~jobscout.models.JobsResponse` with jobs, total, and facets.
    """
    collection = store._client.collections.get(COLLECTION_NAME)
    offset = (page - 1) * page_size

    if q and q.strip():
        # Hybrid: BM25 + pre-computed vector. Weaviate hybrid paging is
        # score-ranked; fetch offset+page_size and slice (native offset= is
        # unreliable under range filters — see the no-q branch).
        vector = embed_query(q.strip())

        response = collection.query.hybrid(
            query=q.strip(),
            alpha=alpha,
            vector=vector,
            filters=filters,
            limit=min(offset + page_size, 10_000),
            return_metadata=MetadataQuery(score=True, distance=True),
        )
        page_objects = list(response.objects)[offset:]
    else:
        # ponytail: Weaviate sort/offset is BROKEN under a range filter (e.g.
        # posted_date): pages overlap ~95% and results aren't even prefix-stable
        # across limits (verified on 1.27.0 and 1.27.27). So for keyword-free
        # browsing we fetch ALL matching rows as light (job_id + sort-key)
        # records, sort + paginate in Python, then hydrate just the page.
        # ~2-3k rows of 3 fields is cheap; revisit if the corpus nears 10k.
        light = collection.query.fetch_objects(
            filters=filters,
            limit=10_000,
            return_properties=["job_id", "posted_date", "salary_max"],
        )
        _far_past = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)

        def _key(o: Any) -> Any:
            p = o.properties
            if sort == "salary_desc":
                return (-(p.get("salary_max") or 0), p.get("job_id") or "")
            # posted_desc (and the no-query "relevance" fallback): newest first,
            # job_id tiebreak so pagination is deterministic.
            posted = p.get("posted_date") or _far_past
            return (-posted.timestamp(), p.get("job_id") or "")

        ordered = sorted(light.objects, key=_key)
        page_ids = [
            o.properties["job_id"] for o in ordered[offset : offset + page_size]
        ]
        by_id: dict[str, Any] = {}
        if page_ids:
            hydrated = collection.query.fetch_objects(
                filters=Filter.by_property("job_id").contains_any(page_ids),
                limit=len(page_ids),
            )
            by_id = {o.properties.get("job_id"): o for o in hydrated.objects}
        page_objects = [by_id[jid] for jid in page_ids if jid in by_id]

    jobs: list[Job] = []
    for obj in page_objects:
        props = dict(obj.properties)
        job_id = props.get("job_id", "")
        jobs.append(_props_to_job(props, job_id=job_id))

    # Total count — use a simple aggregate (Weaviate has no built-in total
    # with pagination, so we run a cheap count-only query).
    try:
        count_result = collection.aggregate.over_all(
            filters=filters,
            total_count=True,
        )
        total = count_result.total_count or 0
    except Exception:
        total = len(jobs)  # fallback: at least the current page count

    # 8 aggregate round-trips — skip for intermediate/candidate fetches (the
    # recommendation pipeline pulls candidate windows where facets are unused).
    facets = _fetch_facets(store, filters) if include_facets else {}

    return JobsResponse(
        total=total,
        page=page,
        page_size=page_size,
        jobs=jobs,
        facets=facets,
    )
