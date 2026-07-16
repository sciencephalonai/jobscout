"""Per-account entitlements + usage quotas — ONE seam for every limit (dormant).

Every guard rail (rate limit, upload size, tailoring/deep-match/LLM-spend quotas, and
any future limit) reads its value from :func:`resolve_limits`, never from ``settings``
directly. Today ``resolve_limits`` ignores ``user_id`` and returns the global defaults,
so behavior is identical for everyone. When accounts arrive, its body reads
``users.plan`` / ``users.limits_json`` → **per-account** limits, including **unlimited**
(a ``None`` value). Because callers already pass ``current_user_id``, turning limits
per-account is a **data change, not a code change**. See docs/multi-tenancy.md.

Enforcement (counting + capping) is gated by ``settings.quota_enforced`` (default off):
with it off there are zero DB writes and zero rejections — byte-identical to today.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from jobscout.config import settings

if TYPE_CHECKING:
    from jobscout.relational import RelationalStore

# Scalar limits an account can override (via plan='unlimited' or a limits_json key).
_OVERRIDABLE = (
    "rate_limit_per_min", "max_upload_mb",
    "tailor_per_day", "deep_match_per_day", "llm_spend_per_day",
)

# Usage metrics that have a per-day quota. Adding one = add a key here + a matching
# `<metric>_per_day` on Limits/settings; no schema change (usage_counters is generic).
USAGE_METRICS = ("tailor", "deep_match", "llm_spend")


@dataclass(frozen=True)
class Limits:
    """Resolved limits for one account. ``None`` means *unlimited* for that metric."""

    rate_limit_per_min: int | None
    max_upload_mb: float | None
    upload_allowed_types: tuple[str, ...]
    tailor_per_day: int | None
    deep_match_per_day: int | None
    llm_spend_per_day: int | None

    def per_day(self, metric: str) -> int | None:
        """Per-day cap for a usage *metric* (``None`` = unlimited)."""
        return getattr(self, f"{metric}_per_day", None)


def _global_defaults() -> Limits:
    """The deployment-wide default limits, from ``settings``."""
    return Limits(
        rate_limit_per_min=settings.rate_limit_per_min,
        max_upload_mb=settings.max_upload_mb,
        upload_allowed_types=tuple(settings.upload_allowed_types),
        tailor_per_day=settings.tailor_per_day,
        deep_match_per_day=settings.deep_match_per_day,
        llm_spend_per_day=settings.llm_spend_per_day,
    )


def resolve_limits(user_id: str, store: RelationalStore | None = None) -> Limits:
    """Limits for *user_id*: global defaults, overlaid with the account's overrides.

    Without a *store* (e.g. the edge rate-limit middleware) it returns the global
    defaults. With one, it reads the account's ``plan`` / ``limits_json`` from the
    ``users`` table: ``plan='unlimited'`` uncaps every scalar limit, and any
    ``limits_json`` key overrides that field (``null`` = unlimited for that metric).
    This is what makes the operator's "grant/revoke premium" take effect — a data
    change, no code change.
    """
    base = _global_defaults()
    if store is None:
        return base
    user = store.get_user(user_id)
    if not user:
        return base
    overrides: dict[str, Any] = {}
    if user.get("plan") == "unlimited":
        overrides = {field: None for field in _OVERRIDABLE}
    raw = user.get("limits_json")
    if raw:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = {}  # a malformed override never breaks limit resolution
        for key, val in parsed.items():
            if key in _OVERRIDABLE:
                overrides[key] = val
    return dataclasses.replace(base, **overrides) if overrides else base


class QuotaExceeded(Exception):
    """Raised when an account is at/over its per-day quota for a metric."""

    def __init__(self, metric: str, limit: int) -> None:
        super().__init__(f"Daily {metric} limit reached ({limit}).")
        self.metric = metric
        self.limit = limit


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def check_quota(store: RelationalStore, user_id: str, metric: str) -> None:
    """Raise :class:`QuotaExceeded` if *user_id* is at/over their daily *metric* cap.

    No-op unless ``settings.quota_enforced`` (dormant by default). ``None`` limit =
    unlimited (e.g. a ``plan='unlimited'`` account) → never raises.
    """
    if not settings.quota_enforced:
        return
    limit = resolve_limits(user_id, store).per_day(metric)  # per-account (incl. unlimited)
    if limit is None:
        return
    if store.get_usage(user_id, metric, _today()) >= limit:
        raise QuotaExceeded(metric, limit)


def record_usage(store: RelationalStore, user_id: str, metric: str, amount: int = 1) -> None:
    """Increment the per-day usage counter for *metric* (for monitoring + enforcement).

    Records when EITHER metering (monitor-only) OR enforcement (needs the counts) is on;
    a no-op when both are off, so behavior is byte-identical to today. This is what lets
    the operator *watch* usage without *capping* anyone (metering on, quota off).
    """
    if not (settings.usage_metering_enabled or settings.quota_enforced):
        return
    store.incr_usage(user_id, metric, _today(), amount)
