"""Query-side helpers: repost dedup, date-range parsing, resume matching, and
saved-search counting. Stateless functions that take the open stores as params.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from jobscout.embed import embed_query
from jobscout.models import Job, JobsResponse, UserProfile
from jobscout.normalize import normalize_text, normalize_title
from jobscout.relational import RelationalStore
from jobscout.search import build_filters, execute_search
from jobscout.services.source_config import _DEFAULT_AUTHORITY, _SOURCE_AUTHORITY
from jobscout.store import WeaviateStore
from jobscout.verdict import priority_key
from jobscout.verdict import score as score_verdict

log = logging.getLogger(__name__)

# "Best Match" sort scores this many candidates then paginates in-memory.
MATCH_WINDOW = 500

# Cache of resume embeddings keyed by (profile_id, resume hash) — bounded, best-effort.
_resume_vec_cache: dict[str, list[float]] = {}


def _dedupe_jobs(jobs: list[Job]) -> list[Job]:
    """Collapse near-duplicate reposts (same normalized company + title) within a
    page, keeping the most authoritative source. The kept job carries
    ``duplicate_count`` + ``also_on`` (the other sources). Page-scoped (MVP)."""
    groups: dict[tuple[str, str], list[Job]] = {}
    order: list[tuple[str, str]] = []
    for j in jobs:
        key = (normalize_text(j.company or ""), normalize_title(j.title or ""))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(j)

    out: list[Job] = []
    for key in order:
        members = groups[key]
        if len(members) == 1:
            out.append(members[0])
            continue
        members.sort(key=lambda j: _SOURCE_AUTHORITY.get(j.source, _DEFAULT_AUTHORITY))
        kept = members[0]
        kept.duplicate_count = len(members) - 1
        kept.also_on = sorted({m.source for m in members[1:]})
        out.append(kept)
    return out


def _date_range_to_dates(
    date_range: str | None,
    date_from: str | None,
    date_to: str | None,
) -> tuple[date | None, date | None, str | None]:
    """Return (from_date, to_date, preset_key) to pass to build_filters."""
    _PRESETS = {"6h", "12h", "18h", "24h", "7d", "14d", "21d", "1m"}
    if date_range in _PRESETS:
        return None, None, date_range
    if date_range == "custom":
        from_d = date.fromisoformat(date_from) if date_from else None
        to_d = date.fromisoformat(date_to) if date_to else None
        return from_d, to_d, None
    return None, None, None


def _semantic_scores(profile: UserProfile, store: WeaviateStore) -> dict[str, float]:
    """Return {job_id: similarity} of jobs to the profile's resume, or {} if the
    profile has no resume text or embedding fails (→ deterministic-only scoring)."""
    text = (getattr(profile, "resume_text", None) or "").strip()
    if not text:
        return {}
    key = f"{profile.id}:{hash(text)}"
    try:
        vec = _resume_vec_cache.get(key)
        if vec is None:
            vec = embed_query(text)
            if len(_resume_vec_cache) > 64:
                _resume_vec_cache.clear()
            _resume_vec_cache[key] = vec
        return store.near_vector_scores(vec, limit=MATCH_WINDOW)
    except Exception as exc:  # noqa: BLE001 — semantic is optional, never fatal
        log.warning("semantic scoring unavailable (%s) — deterministic only", exc)
        return {}


def _match_resume_to_jobs(
    resume_text: str,
    profile: UserProfile | None,
    limit: int,
    store: WeaviateStore,
    relational: RelationalStore,
) -> JobsResponse:
    """Embed the resume, find nearest jobs, and (if a profile is given) verdict-score
    them with matched/gap keywords. Shared by /api/match and /api/match/upload."""
    filters = None
    if profile is not None:
        filters = build_filters(
            exclude_citizenship_required=profile.reject_citizenship_only,
        )

    vector = embed_query(resume_text)
    jobs = store.search_near_vector(vector, filters=filters, limit=limit)

    verdicts: dict[str, Any] = {}
    if profile is not None:
        excluded = relational.get_excluded_job_ids(profile.id)
        jobs = [j for j in jobs if j.job_id not in excluded]
        scored = [(j, score_verdict(j, profile)) for j in jobs]
        scored.sort(key=lambda pair: priority_key(pair[1]))
        jobs = [j for j, _ in scored]
        verdicts = {v.job_id: v for _, v in scored}

    return JobsResponse(
        jobs=jobs, total=len(jobs), page=1, page_size=limit, verdicts=verdicts,
    )


def _count_matches(
    store: WeaviateStore, filters: dict[str, Any], ingested_after: datetime | None
) -> int:
    """Count jobs matching a saved search's filters (optionally only those ingested
    after a cutoff). Reuses build_filters + execute_search; page_size=1 for the total."""
    f = build_filters(
        remote=filters.get("remote"),
        visa=filters.get("visa"),
        source=filters.get("source"),
        company_size=filters.get("company_size"),
        exp=filters.get("exp"),
        employer_type=filters.get("employer_type"),
        cap_exempt=filters.get("cap_exempt"),
        security_clearance=filters.get("security_clearance"),
        exclude_no_sponsorship=bool(filters.get("exclude_no_sponsorship")),
        h1b_sponsor=bool(filters.get("h1b_sponsor")),
        everify=bool(filters.get("everify")),
        date_range=filters.get("date_range"),
        ingested_after=ingested_after,
    )
    res = execute_search(
        store=store, q=filters.get("q"), alpha=float(filters.get("alpha", 0.5)),
        filters=f, sort="relevance", page=1, page_size=1,
    )
    return res.total
