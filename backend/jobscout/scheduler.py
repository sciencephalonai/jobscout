"""Optional daily auto-refresh scheduler (APScheduler).

OFF by default (``settings.scheduler_enabled``). When enabled, runs the watchlist
refresh once a day at ``settings.scheduler_hour``, budget-capped by
``settings.embed_daily_budget`` so it cannot exhaust the Gemini free embedding tier
(1,000/day). The manual "Get latest jobs" button is the safe default; this is for
users who want hands-off daily updates (best with a paid tier / local embeddings).

The scheduler reuses the app's already-open Weaviate + DuckDB stores (DuckDB is
single-writer, so we must NOT open a second connection). The refresh callable is
injected to avoid an import cycle with api.main.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from jobscout.config import settings

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_JOB_ID = "daily_watchlist_refresh"


def _ensure_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(daemon=True)
        _scheduler.start()
    return _scheduler


def start_scheduler(refresh_fn: Callable[[], Any]) -> None:
    """Register the daily refresh job (only if enabled). Idempotent."""
    if not settings.scheduler_enabled:
        log.info("scheduler disabled (settings.scheduler_enabled=False) — not scheduling")
        return
    enable(refresh_fn)


def enable(refresh_fn: Callable[[], Any]) -> None:
    """Turn the daily job ON at runtime (e.g. from POST /api/scheduler)."""
    sched = _ensure_scheduler()
    sched.add_job(
        refresh_fn,
        trigger="cron",
        hour=settings.scheduler_hour,
        id=_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
    )
    log.info("daily watchlist refresh scheduled for %02d:00", settings.scheduler_hour)


def disable() -> None:
    """Turn the daily job OFF at runtime. Safe if not scheduled."""
    if _scheduler is not None:
        try:
            _scheduler.remove_job(_JOB_ID)
            log.info("daily watchlist refresh unscheduled")
        except Exception:  # noqa: BLE001 — job may not exist
            pass


def status() -> dict[str, Any]:
    """Return the current scheduler status for GET /api/scheduler."""
    running = _scheduler is not None and _scheduler.get_job(_JOB_ID) is not None
    next_run = None
    if running and _scheduler is not None:
        job = _scheduler.get_job(_JOB_ID)
        next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
    return {
        "enabled": running,
        "hour": settings.scheduler_hour,
        "embed_daily_budget": settings.embed_daily_budget,
        "next_run": next_run,
    }
