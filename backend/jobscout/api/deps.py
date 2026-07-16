"""Multi-tenancy seam — the ONE place authentication plugs in later.

Today JobScout runs as a single local user who owns every profile and saved
search. There is no auth yet, but every private route already goes through
``owned_profile`` here, so a profile that belongs to a different ``user_id`` is
invisible (404, never 403 — so ids can't be enumerated). That makes the whole
IDOR class impossible *by construction*: adding real login is just swapping the
body of ``current_user_id`` for a session/JWT lookup — nothing else changes.

See docs/multi-tenancy.md for the global-vs-private data split and the hosting
notes (per-user quota, Postgres over embedded DuckDB).
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from jobscout.config import settings
from jobscout.models import UserProfile
from jobscout.relational import RelationalStore


def current_user_id(request: Request) -> str:  # noqa: ARG001
    """Who is calling. TODAY: always the single local user.

    THIS IS THE ENTIRE AUTH INTEGRATION POINT. To host multiple users, replace
    this body with a real identity lookup (e.g. read a signed session cookie or
    verify a Bearer JWT off ``request`` and return that user's id). Every private
    route already routes ownership through here, so nothing else needs to change.
    """
    return settings.local_user_id


def effective_owner(profile: UserProfile) -> str:
    """Owner of a profile, treating legacy empty-owner rows as the local user.

    Path-based ``/api/profiles/{id}/…`` routes are guarded by the
    ``enforce_profile_ownership`` middleware in api/main.py; routes that take a
    ``profile_id`` via query/body call :func:`owned_profile` instead. Both — and the
    list/saved-search filters — share this predicate.
    """
    return profile.user_id or settings.local_user_id


def owned_profile(profile_id: str, request: Request) -> UserProfile:
    """Fetch a profile the caller owns, else 404 (never 403 → no id enumeration).

    The authorization primitive for routes that receive ``profile_id`` via QUERY or
    BODY (``/api/jobs/by-state``, ``/api/match/deep``, ``/api/jobs``, ``/api/match``),
    which the path-only ownership middleware cannot see. Changing authorization later
    means editing this one function. Under the single local user it never rejects.
    """
    relational: RelationalStore = request.app.state.relational_store
    profile = relational.get_profile(profile_id)
    if profile is None or effective_owner(profile) != current_user_id(request):
        raise HTTPException(status_code=404, detail="Profile not found.")
    return profile


def require_admin(request: Request) -> None:
    """Guard operator-only routes (settings/.env, maintenance, scheduler, /api/admin/*).

    Open while ``single_user_mode`` (the local operator owns the box); once hosting,
    only accounts with ``users.is_admin`` pass. 403 otherwise.
    """
    if settings.single_user_mode:
        return
    relational: RelationalStore = request.app.state.relational_store
    user = relational.get_user(current_user_id(request))
    if not (user and user.get("is_admin")):
        raise HTTPException(status_code=403, detail="Admin-only operation.")
