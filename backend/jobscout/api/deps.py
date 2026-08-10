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

from uuid import uuid4

from fastapi import HTTPException, Request, status

from jobscout.auth.auth0 import bearer_token, claim_email, claim_name, verify_token
from jobscout.config import settings
from jobscout.models import UserProfile
from jobscout.relational import RelationalStore


def current_user_id(request: Request) -> str:
    """Who is calling — THE ENTIRE AUTH INTEGRATION POINT.

    - No Auth0 configured → the single local user (dev/local behavior, unchanged).
    - Auth0 configured + a valid ``Bearer`` token → the matching account's id
      (resolved/auto-provisioned from the token's ``sub``/``email``).
    - Auth0 configured, no/invalid token → the local user, unless
      ``settings.require_auth`` is on (then 401).

    Every private route routes ownership through here, so wiring auth changes
    nothing else. See docs/auth-and-hosting.md.
    """
    if not settings.auth0_configured:
        return settings.local_user_id

    token = bearer_token(request)
    if token is None:
        if settings.require_auth:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return settings.local_user_id

    claims = verify_token(token)  # raises 401 if the token is invalid/expired
    return _resolve_user_id(request, claims)


def _resolve_user_id(request: Request, claims: dict) -> str:
    """Map verified Auth0 claims to a JobScout account id, provisioning on first login.

    Order: (1) existing ``auth_subject`` match, (2) email match → link the subject,
    (3) create a new account. Keeps one account per Auth0 identity.
    """
    relational: RelationalStore = request.app.state.relational_store
    subject = str(claims.get("sub") or "")
    email = claim_email(claims)

    existing = relational.get_user_by_subject("auth0", subject)
    if existing:
        return str(existing["id"])
    if email:
        by_email = relational.get_user_by_email(email)
        if by_email:
            relational.link_user_subject(str(by_email["id"]), "auth0", subject)
            return str(by_email["id"])
    new_id = str(uuid4())
    display = claim_name(claims) or (email.split("@")[0] if email else "User")
    relational.create_auth_user(
        user_id=new_id, email=email, display_name=display,
        auth_provider="auth0", auth_subject=subject,
    )
    return new_id


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
