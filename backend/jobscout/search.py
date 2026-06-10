"""Weaviate hybrid query builder and search executor.

This module is the single place where filter parameters (date range, YoE,
visa, remote mode, source) are translated into Weaviate ``Filter`` objects
and a hybrid (BM25 + vector) query is assembled and executed.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from weaviate.classes.query import Filter, MetadataQuery, Sort
from weaviate.exceptions import WeaviateQueryError

from jobscout.embed import embed_query
from jobscout.models import Job, JobsResponse
from jobscout.store import COLLECTION_NAME, WeaviateStore, _props_to_job

# ---------------------------------------------------------------------------
# Date preset → timedelta mapping
# ---------------------------------------------------------------------------

DATE_PRESETS: dict[str, dt.timedelta] = {
    "24h": dt.timedelta(hours=24),
    "7d": dt.timedelta(days=7),
    "14d": dt.timedelta(days=14),
    "21d": dt.timedelta(days=21),
    "1m": dt.timedelta(days=30),
}


# ---------------------------------------------------------------------------
# Filter builder
# ---------------------------------------------------------------------------

def build_filters(
    remote: list[str] | None = None,
    visa: list[str] | None = None,
    source: list[str] | None = None,
    company_size: list[str] | None = None,
    employment_type: list[str] | None = None,
    category: list[str] | None = None,
    exp: list[str] | None = None,
    date_range: str | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
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

    # Categorical multi-select filters: OR within each, AND across them.
    for prop, vals in (
        ("remote_mode", remote),
        ("visa_sponsorship", visa),
        ("source", source),
        ("company_size_bucket", company_size),
        ("employment_type", employment_type),
        ("category", category),
    ):
        f = _any_equal(prop, vals)
        if f is not None:
            clauses.append(f)

    # Experience bands (multi-select), matched against the role's required
    # years (yoe_min). Selected bands are OR'd together.
    if exp:
        yoe = Filter.by_property("yoe_min")
        band_filters = {
            "entry": yoe.is_none(True) | yoe.less_or_equal(2),
            "mid": yoe.greater_or_equal(3) & yoe.less_or_equal(5),
            "senior": yoe.greater_or_equal(6) & yoe.less_or_equal(10),
            "lead": yoe.greater_or_equal(11),
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

    for prop in ("visa_sponsorship", "remote_mode", "source", "company_size_bucket", "employment_type", "category"):
        try:
            result = collection.aggregate.over_all(
                filters=filters,
                group_by=prop,
                total_count=True,
            )
            facets[prop] = {
                str(group.grouped_by.value): group.total_count
                for group in (result.groups or [])
                if group.grouped_by is not None
            }
        except Exception:
            # Aggregation is best-effort; do not fail the whole request
            facets[prop] = {}

    return facets


# ---------------------------------------------------------------------------
# Sort helper
# ---------------------------------------------------------------------------

def _build_sort(sort: str) -> Any | None:
    """Translate sort enum to a Weaviate _Sorting object for fetch_objects.

    - ``posted_desc``  → sort by ``posted_date`` descending
    - ``salary_desc``  → sort by ``salary_max`` descending
    - ``relevance``    → no explicit sort (hybrid scoring)

    Note: fetch_objects accepts a _Sorting object directly, not a list.
    BM25/hybrid queries don't support sort at all; sort is handled in Python
    for those paths.
    """
    if sort == "posted_desc":
        return Sort.by_property("posted_date", ascending=False)
    if sort == "salary_desc":
        return Sort.by_property("salary_max", ascending=False)
    return None


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
    sort_spec = _build_sort(sort)

    def _fallback_fetch(f: Any, sp: Any) -> Any:
        """fetch_objects fallback used when BM25/hybrid fails (e.g. stopwords)."""
        fk: dict[str, Any] = dict(filters=f, limit=page_size, offset=offset)
        if sp is not None:
            fk["sort"] = sp
        return collection.query.fetch_objects(**fk)

    if q and q.strip():
        if sort_spec is not None:
            # Weaviate BM25/hybrid queries don't accept an explicit sort
            # parameter.  Fetch all keyword-matched objects and sort in Python.
            try:
                bm25_resp = collection.query.bm25(
                    query=q.strip(),
                    filters=filters,
                    limit=10_000,
                )
            except WeaviateQueryError:
                # Query is all stopwords — fall back to filter-only fetch.
                bm25_resp = _fallback_fetch(filters, None)

            all_jobs: list[Job] = [
                _props_to_job(dict(o.properties), job_id=str(o.properties.get("job_id", "")))
                for o in bm25_resp.objects
            ]
            sort_attr = "posted_date" if sort == "posted_desc" else "salary_max"

            def _sort_key(j: Job) -> Any:
                v = getattr(j, sort_attr, None)
                if v is None:
                    return dt.datetime.min.replace(tzinfo=dt.timezone.utc) if sort_attr == "posted_date" else -1
                return v

            all_jobs.sort(key=_sort_key, reverse=True)
            total = len(all_jobs)
            jobs = all_jobs[offset : offset + page_size]
            facets = _fetch_facets(store, filters)
            return JobsResponse(total=total, page=page, page_size=page_size, jobs=jobs, facets=facets)

        # Relevance sort: full hybrid search.  Fall back to BM25-only if
        # the embedding API is unavailable (rate-limit, quota, network).
        vector: list[float] | None = None
        effective_alpha = alpha
        try:
            vector = embed_query(q.strip())
        except Exception:
            effective_alpha = 0.0

        kwargs: dict[str, Any] = dict(
            query=q.strip(),
            alpha=effective_alpha,
            filters=filters,
            limit=page_size,
            offset=offset,
            return_metadata=MetadataQuery(score=True, distance=True),
        )
        if vector is not None:
            kwargs["vector"] = vector

        try:
            response = collection.query.hybrid(**kwargs)
        except WeaviateQueryError:
            # Query is all stopwords — fall back to filter-only fetch.
            response = _fallback_fetch(filters, None)
    else:
        # Keyword-free: plain fetch with metadata filters and optional sort
        fetch_kwargs: dict[str, Any] = dict(
            filters=filters,
            limit=page_size,
            offset=offset,
        )
        if sort_spec is not None:
            fetch_kwargs["sort"] = sort_spec

        response = collection.query.fetch_objects(**fetch_kwargs)

    jobs: list[Job] = []
    for obj in response.objects:
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

    facets = _fetch_facets(store, filters)

    return JobsResponse(
        total=total,
        page=page,
        page_size=page_size,
        jobs=jobs,
        facets=facets,
    )
