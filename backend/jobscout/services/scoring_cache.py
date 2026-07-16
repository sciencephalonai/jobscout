"""Bounded memo cache for verdict scoring.

Every personalized request re-scores a full candidate window (``MATCH_WINDOW``)
against the profile. ``verdict.score`` is a pure function of (job, profile,
semantic), so its result is safe to memoize: the same job+profile pair yields
the same verdict until the *job* changes (ingest / enrichment) or the *profile*
changes (a new fingerprint key). Ingestion clears the cache; profile edits
produce a different fingerprint, so stale entries can never be served.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

# (job_id, profile_fingerprint, semantic_bucket) -> Verdict
_CACHE: OrderedDict[tuple[str, str, float], Any] = OrderedDict()
_MAX_ENTRIES = 20_000


def get(key: tuple[str, str, float]) -> Any | None:
    """Return the cached verdict for *key* (and mark it recently used)."""
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
    return hit


def put(key: tuple[str, str, float], verdict: Any) -> None:
    """Store *verdict*, evicting the least-recently-used entry when full."""
    _CACHE[key] = verdict
    _CACHE.move_to_end(key)
    if len(_CACHE) > _MAX_ENTRIES:
        _CACHE.popitem(last=False)


def clear() -> None:
    """Drop every entry — call whenever job records may have changed."""
    _CACHE.clear()


def size() -> int:
    """Current entry count (diagnostics/tests)."""
    return len(_CACHE)
