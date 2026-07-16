"""Operator/admin API — monitor accounts and grant/revoke premium.

Every route is behind :func:`require_admin` (open to the local operator while
``single_user_mode``; ``users.is_admin`` once hosting). It reads the data already
collected by the entitlements/usage layer (``users``, ``usage_counters``) plus
on-disk file sizes, so the host can watch per-user LLM usage, tailors/deep-matches,
storage, and traffic, and flip anyone's plan/limits. See docs/multi-tenancy.md and
docs/pre-deployment-checklist.md (flip ``usage_metering_enabled`` to start collecting).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from jobscout.api.deps import require_admin
from jobscout.config import settings
from jobscout.relational import RelationalStore

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _since(days: int) -> str:
    """The 'YYYY-MM-DD' period *days* ago (usage_counters keys by day)."""
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")


def _user_storage_bytes(profile_ids: list[str]) -> int:
    """Total on-disk bytes of a user's resume + tailored files.

    # ponytail: local-filesystem walk; an S3 BlobStore would sum object sizes instead.
    """
    total = 0
    roots = (Path(settings.resume_storage_dir), Path(settings.tailored_resume_storage_dir))
    for pid in profile_ids:
        for root in roots:
            d = root / pid
            if d.is_dir():
                total += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
    return total


def _user_summary(relational: RelationalStore, user: dict[str, Any]) -> dict[str, Any]:
    """One row for the operator table: account + profile count + storage + 30-day usage."""
    profiles = relational.list_profiles(user["id"])
    profile_ids = [p.id for p in profiles]
    return {
        **user,
        "profile_count": len(profiles),
        "storage_bytes": _user_storage_bytes(profile_ids),
        "usage_30d": relational.usage_rollup(user["id"], _since(30)),
    }


@router.get("/users")
async def list_users(request: Request) -> dict[str, Any]:
    """Every account with plan, admin flag, profile count, storage, and 30-day usage."""
    require_admin(request)
    relational: RelationalStore = request.app.state.relational_store
    return {"users": [_user_summary(relational, u) for u in relational.list_users()]}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, request: Request, body: dict = Body(...)) -> dict[str, Any]:
    """Grant/revoke premium + limits: set ``plan`` / ``limits_json`` / ``is_admin``.

    Body may include any of ``plan`` (str), ``limits_json`` (JSON string or null to clear),
    ``is_admin`` (bool). ``resolve_limits`` reflects the new plan/limits immediately.
    """
    require_admin(request)
    relational: RelationalStore = request.app.state.relational_store
    if relational.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found.")
    updated = relational.update_user(
        user_id,
        plan=body.get("plan"),
        limits_json=body.get("limits_json"),
        is_admin=body.get("is_admin"),
    )
    return {"user": updated}


@router.get("/users/{user_id}/usage")
async def user_usage(user_id: str, request: Request) -> dict[str, Any]:
    """Per-metric usage rollups for one account (today / 7-day / 30-day)."""
    require_admin(request)
    relational: RelationalStore = request.app.state.relational_store
    if relational.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "user_id": user_id,
        "today": relational.usage_rollup(user_id, _since(0)),
        "last_7d": relational.usage_rollup(user_id, _since(7)),
        "last_30d": relational.usage_rollup(user_id, _since(30)),
    }


@router.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    """Deployment-wide aggregates: users, total per-metric usage, storage, traffic."""
    require_admin(request)
    relational: RelationalStore = request.app.state.relational_store
    users = relational.list_users()
    totals: dict[str, int] = {}
    storage = 0
    for u in users:
        for metric, count in relational.usage_rollup(u["id"], _since(30)).items():
            totals[metric] = totals.get(metric, 0) + count
        storage += _user_storage_bytes([p.id for p in relational.list_profiles(u["id"])])
    return {
        "user_count": len(users),
        "usage_30d": totals,          # includes 'requests' (traffic), 'llm_call', tailor, deep_match
        "storage_bytes": storage,
        "metering_enabled": settings.usage_metering_enabled,
        "quota_enforced": settings.quota_enforced,
    }
