"""Auth0 access-token verification for the JobScout backend.

Users authenticate via Auth0 Universal Login (SPA). Their access tokens are
RS256 JWTs signed by the Auth0 tenant. We verify them against the tenant's JWKS
(cached by PyJWT's ``PyJWKClient``), checking issuer + audience, then the deps
layer resolves/auto-provisions the matching ``users`` row by ``sub``→``email``.

Mirrors the Leelaa backend's ``auth0_verifier`` pattern, adapted to PyJWT. All of
this is inert until ``settings.auth0_domain`` is set — see ``api/deps.py``.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWKClient

from jobscout.config import settings

logger = logging.getLogger(__name__)

_UNAUTHORIZED = {"WWW-Authenticate": "Bearer"}


class Auth0NotConfigured(RuntimeError):
    """Raised when token verification is attempted with no AUTH0_DOMAIN set."""


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    """A JWKS client for the configured tenant (caches signing keys internally)."""
    if not settings.auth0_domain:
        raise Auth0NotConfigured("AUTH0_DOMAIN is not set")
    return PyJWKClient(f"https://{settings.auth0_domain}/.well-known/jwks.json")


def bearer_token(request: Request) -> str | None:
    """Extract the ``Authorization: Bearer <token>`` value, or None."""
    header = request.headers.get("Authorization") or request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        return None
    token = header.split(" ", 1)[1].strip()
    return token or None


def verify_token(token: str) -> dict[str, Any]:
    """Verify an Auth0 access token and return its claims.

    Raises ``HTTPException(401)`` on any verification failure (bad signature,
    wrong issuer/audience, expired). ``Auth0NotConfigured`` if the tenant is unset.
    """
    issuer = f"https://{settings.auth0_domain}/"
    audience = settings.auth0_audience or None
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={"verify_aud": bool(audience)},
        )
    except Auth0NotConfigured:
        raise
    except jwt.PyJWTError as exc:
        logger.warning("Auth0 token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers=_UNAUTHORIZED,
        ) from exc


def claim_email(claims: dict[str, Any]) -> str | None:
    """Best-effort email from an access token: the standard claim or a namespaced one.

    Auth0 access tokens omit ``email`` unless an Action adds it; a common pattern is
    a namespaced custom claim like ``https://<app>/email``. We accept either.
    """
    email = claims.get("email")
    if email:
        return str(email)
    for key, value in claims.items():
        if key.endswith("/email") and value:
            return str(value)
    return None


def claim_name(claims: dict[str, Any]) -> str | None:
    """Best-effort display name from an access token."""
    for key in ("name", "nickname"):
        if claims.get(key):
            return str(claims[key])
    for key, value in claims.items():
        if key.endswith("/name") and value:
            return str(value)
    return None
