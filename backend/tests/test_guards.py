"""Dormant Tier-2 guard rails: off by default, functional when the flag flips on."""

from __future__ import annotations

from jobscout.config import settings


def test_security_headers_off_by_default_then_on(client, monkeypatch):  # noqa: ANN001
    assert "x-frame-options" not in {k.lower() for k in client.get("/api/health").headers}
    monkeypatch.setattr(settings, "security_headers_enabled", True)
    headers = {k.lower() for k in client.get("/api/health").headers}
    assert "x-frame-options" in headers
    assert "x-content-type-options" in headers


def test_require_auth_gate(client, monkeypatch):  # noqa: ANN001
    assert client.get("/api/stats").status_code != 401           # off → allowed
    monkeypatch.setattr(settings, "require_auth", True)
    assert client.get("/api/stats").status_code == 401           # on → 401
    assert client.get("/api/health").status_code == 200          # exempt path still open


def test_upload_limits_gate(client, monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(settings, "upload_limits_enabled", True)
    monkeypatch.setattr(settings, "max_upload_mb", 0.0000001)     # ~0 bytes → any upload too large
    r = client.post(
        "/api/match/upload",
        files={"file": ("cv.txt", b"hello world", "text/plain")},
        data={"limit": "1"},
    )
    assert r.status_code == 413


def test_rate_limit_gate(client, monkeypatch):  # noqa: ANN001
    from jobscout import security

    security._RATE_BUCKETS.clear()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_per_min", 1)
    assert client.get("/api/health").status_code == 200          # 1st in window OK
    assert client.get("/api/health").status_code == 429          # 2nd exceeds
