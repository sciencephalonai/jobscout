"""Dormant security guard rails (Tier 2).

Every guard here is OFF by default via a ``settings`` flag, so single/small-group
behavior is byte-identical to today. Flip the flags before exposing the app to
untrusted users — see docs/pre-deployment-checklist.md. Registered in api/main.py.

These are deliberately tiny and dependency-free (ponytail: a few lines beat a new
lib). The rate limiter is an in-memory per-instance fixed-window counter —
# ponytail: in-memory, per-instance; move to Redis when running multiple instances.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from jobscout.config import settings
from jobscout.entitlements import resolve_limits

# Public paths that stay reachable even when require_auth is on (health/docs/openapi).
_AUTH_EXEMPT = ("/api/health", "/docs", "/openapi.json", "/redoc")

# Fixed-window rate-limit state: (client, minute_bucket) -> count.
_RATE_BUCKETS: dict[tuple[str, int], int] = defaultdict(int)


async def rate_limit_middleware(request: Request, call_next):  # noqa: ANN001, ANN201
    """Per-client request throttle (dormant unless ``settings.rate_limit_enabled``).

    Limit comes from ``resolve_limits`` (per-account once accounts exist). ``None`` =
    unlimited. In-memory fixed 60s window; 429 when the window count exceeds the limit.
    """
    if settings.rate_limit_enabled:
        limit = resolve_limits(settings.local_user_id).rate_limit_per_min
        if limit is not None:
            client = request.client.host if request.client else "unknown"
            bucket = (client, int(time.time() // 60))
            _RATE_BUCKETS[bucket] += 1
            if _RATE_BUCKETS[bucket] > limit:
                return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded."})
    return await call_next(request)


async def request_size_middleware(request: Request, call_next):  # noqa: ANN001, ANN201
    """Reject oversized request bodies via Content-Length (dormant unless
    ``settings.max_request_mb`` is set)."""
    cap = settings.max_request_mb
    if cap is not None:
        length = request.headers.get("content-length")
        if length and int(length) > cap * 1024 * 1024:
            return JSONResponse(status_code=413, content={"detail": "Request body too large."})
    return await call_next(request)


async def security_headers_middleware(request: Request, call_next):  # noqa: ANN001, ANN201
    """Add standard security headers (dormant unless
    ``settings.security_headers_enabled``; safe to enable anytime)."""
    response: Response = await call_next(request)
    if settings.security_headers_enabled:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if settings.hsts_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
    return response


async def require_auth_middleware(request: Request, call_next):  # noqa: ANN001, ANN201
    """401 without a valid session (dormant unless ``settings.require_auth``).

    The GATE is wired now; the session/JWT *provider* is Tier 3. With it on and no
    auth provider yet, everything but the exempt paths 401s — which is the honest
    behavior until real login lands (then this reads the verified session).
    """
    if settings.require_auth and not request.url.path.startswith(_AUTH_EXEMPT):
        # ponytail: no session mechanism yet → 'authenticated' is always False here.
        # Real auth replaces this check with a verified session/JWT lookup.
        authenticated = False
        if not authenticated:
            return JSONResponse(status_code=401, content={"detail": "Authentication required."})
    return await call_next(request)


def enforce_upload_limits(file: UploadFile, data: bytes) -> None:
    """Reject oversized / disallowed-type uploads (dormant unless
    ``settings.upload_limits_enabled``). Limits from ``resolve_limits``.

    Called from the resume upload routes. ``max_upload_mb=None`` = unlimited size;
    an empty allowlist = any type.
    """
    if not settings.upload_limits_enabled:
        return
    limits = resolve_limits(settings.local_user_id)
    if limits.max_upload_mb is not None and len(data) > limits.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Uploaded file is too large.")
    if limits.upload_allowed_types and file.content_type not in limits.upload_allowed_types:
        raise HTTPException(status_code=415, detail=f"Unsupported file type: {file.content_type}.")
