"""Auth0 token verification + user resolution at the current_user_id seam."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

import jobscout.api.deps as deps
import jobscout.auth.auth0 as auth0
from jobscout.config import settings
from jobscout.relational import DuckDBRelationalStore

DOMAIN = "test-tenant.us.auth0.com"
AUDIENCE = "https://api.jobscout.test"
ISSUER = f"https://{DOMAIN}/"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_PUBLIC_KEY = _KEY.public_key()


def _token(**overrides) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": "auth0|abc123",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=1),
        "email": "jane@example.com",
        "name": "Jane Public",
    }
    payload.update(overrides)
    return jwt.encode(payload, _PRIVATE_PEM, algorithm="RS256")


@pytest.fixture(autouse=True)
def _auth0_config(monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(settings, "auth0_domain", DOMAIN)
    monkeypatch.setattr(settings, "auth0_audience", AUDIENCE)
    # Bypass the network JWKS fetch: return our public key for any token.
    fake_client = SimpleNamespace(
        get_signing_key_from_jwt=lambda _t: SimpleNamespace(key=_PUBLIC_KEY)
    )
    monkeypatch.setattr(auth0, "_jwks_client", lambda: fake_client)


class TestVerifyToken:
    def test_valid_token_returns_claims(self) -> None:
        claims = auth0.verify_token(_token())
        assert claims["sub"] == "auth0|abc123"
        assert claims["email"] == "jane@example.com"

    def test_wrong_audience_401(self) -> None:
        with pytest.raises(HTTPException) as exc:
            auth0.verify_token(_token(aud="https://someone-else"))
        assert exc.value.status_code == 401

    def test_wrong_issuer_401(self) -> None:
        with pytest.raises(HTTPException) as exc:
            auth0.verify_token(_token(iss="https://evil.example/"))
        assert exc.value.status_code == 401

    def test_expired_401(self) -> None:
        past = datetime.now(UTC) - timedelta(hours=2)
        with pytest.raises(HTTPException) as exc:
            auth0.verify_token(_token(iat=past, exp=past + timedelta(minutes=1)))
        assert exc.value.status_code == 401


class TestClaims:
    def test_namespaced_email_fallback(self) -> None:
        assert auth0.claim_email({"https://jobscout.app/email": "x@y.com"}) == "x@y.com"

    def test_standard_email_wins(self) -> None:
        assert auth0.claim_email({"email": "a@b.com", "https://x/email": "c@d.com"}) == "a@b.com"

    def test_no_email(self) -> None:
        assert auth0.claim_email({"sub": "auth0|1"}) is None


def _request(store, token=None):  # noqa: ANN001
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return SimpleNamespace(
        headers=headers,
        app=SimpleNamespace(state=SimpleNamespace(relational_store=store)),
    )


class TestCurrentUserId:
    def test_no_auth0_returns_local(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(settings, "auth0_domain", "")  # not configured
        store = DuckDBRelationalStore(":memory:")
        assert deps.current_user_id(_request(store)) == settings.local_user_id

    def test_no_token_requires_auth_401(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(settings, "require_auth", True)
        store = DuckDBRelationalStore(":memory:")
        with pytest.raises(HTTPException) as exc:
            deps.current_user_id(_request(store))
        assert exc.value.status_code == 401

    def test_no_token_optional_auth_returns_local(self, monkeypatch) -> None:  # noqa: ANN001
        monkeypatch.setattr(settings, "require_auth", False)
        store = DuckDBRelationalStore(":memory:")
        assert deps.current_user_id(_request(store)) == settings.local_user_id

    def test_valid_token_provisions_then_reuses(self) -> None:
        store = DuckDBRelationalStore(":memory:")
        uid1 = deps.current_user_id(_request(store, _token()))
        # A new account was created and is findable by subject.
        assert store.get_user_by_subject("auth0", "auth0|abc123")["id"] == uid1
        # Second call with the same identity returns the SAME id (no duplicate).
        uid2 = deps.current_user_id(_request(store, _token()))
        assert uid2 == uid1
        auth0_users = [u for u in store.list_users() if u["auth_provider"] == "auth0"]
        assert len(auth0_users) == 1

    def test_email_links_existing_account(self) -> None:
        store = DuckDBRelationalStore(":memory:")
        # Pre-existing account with the same email but no auth subject yet.
        store.create_auth_user(user_id="pre-existing", email="jane@example.com",
                               display_name="Jane", auth_provider="local", auth_subject="")
        uid = deps.current_user_id(_request(store, _token()))
        assert uid == "pre-existing"  # linked, not duplicated
        linked = store.get_user("pre-existing")
        assert linked["auth_provider"] == "auth0"
