"""Unit tests for the deep-match LLM second-opinion (no real API calls)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jobscout import deep_match, enrich
from jobscout.models import Job, UserProfile


def _job() -> Job:
    return Job(
        job_id="abc123def456",
        source="ashby",
        title="Data Scientist",
        url="http://example.test/abc123def456",
        company="Acme",
        description="We are hiring a data scientist with Python and ML experience.",
    )


def _profile() -> UserProfile:
    return UserProfile(
        label="primary",
        target_titles=["data scientist", "ml engineer"],
        skills=["python", "ml"],
        needs_sponsorship=True,
    )


def _fake_client(payload: dict) -> SimpleNamespace:
    msg = SimpleNamespace(content=json.dumps(payload))
    choice = SimpleNamespace(message=msg)
    completions = SimpleNamespace(create=lambda **_: SimpleNamespace(choices=[choice]))
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_validate_coerces_bad_data() -> None:
    out = deep_match._validate(
        {"verdict": "MAYBE", "score": "999", "strengths": ["a", "b", "c", "d"], "gaps": []}
    )
    assert out["verdict"] == "borderline"  # invalid → borderline
    assert out["score"] == 100  # clamped to 0..100
    assert len(out["strengths"]) == 3  # capped at 3
    assert out["cached"] is False


def test_fallback_shape() -> None:
    out = deep_match._fallback("boom")
    assert out["verdict"] == "borderline"
    assert out["score"] == 50
    assert "boom" in out["summary"]


def test_bounded_context_preserves_beginning_and_end() -> None:
    value = "BEGIN-" + ("middle " * 100) + "-END"
    bounded = deep_match._bounded_context(value, 120)

    assert bounded.startswith("BEGIN-")
    assert bounded.endswith("-END")
    assert "omitted" in bounded
    assert len(bounded) <= 120


def test_missing_api_key_returns_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    deep_match._CACHE.clear()
    monkeypatch.setattr(enrich.settings, "llm_provider", "deepseek")
    monkeypatch.setattr(enrich.settings, "deepseek_api_key", "", raising=False)
    # Missing-key fallback now switches providers when the other key exists,
    # so a true "not configured" state requires both to be empty.
    monkeypatch.setattr(enrich.settings, "nvidia_api_key", "", raising=False)
    out = deep_match.compute_deep_match(_job(), _profile())
    assert out["verdict"] == "borderline"
    assert "not configured" in out["summary"].lower()


def test_compute_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    deep_match._CACHE.clear()
    monkeypatch.setattr(enrich.settings, "llm_provider", "deepseek")
    monkeypatch.setattr(enrich.settings, "deepseek_api_key", "test-key", raising=False)
    calls = {"n": 0}

    def fake_get_client():  # noqa: ANN202
        calls["n"] += 1
        return _fake_client(
            {"verdict": "apply", "score": 88, "strengths": ["python"], "gaps": [], "summary": "great"}
        )

    # _get_client is imported lazily from jobscout.enrich inside compute_deep_match
    monkeypatch.setattr("jobscout.enrich._get_client", fake_get_client, raising=False)

    job, profile = _job(), _profile()
    first = deep_match.compute_deep_match(job, profile)
    assert first["verdict"] == "apply"
    assert first["score"] == 88
    assert first["cached"] is False

    # Second identical call must hit the cache — no new client call.
    second = deep_match.compute_deep_match(job, profile)
    assert second["cached"] is True
    assert second["verdict"] == "apply"
    assert calls["n"] == 1  # client built only once

    # Editing profile evidence must invalidate the prior judgment.
    profile.interests = ["computer vision"]
    third = deep_match.compute_deep_match(job, profile)
    assert third["cached"] is False
    assert calls["n"] == 2


# ── Profile-driven avoid rules (nothing user-specific baked into the prompt) ──


def test_prompt_renders_profile_avoid_lists(monkeypatch):
    from types import SimpleNamespace

    from jobscout import enrich
    from jobscout.models import Job, UserProfile

    deep_match._CACHE.clear()
    monkeypatch.setattr(enrich.settings, "llm_provider", "deepseek")
    monkeypatch.setattr(enrich.settings, "deepseek_api_key", "test-key", raising=False)
    captured: dict = {}

    def _capturing_client():
        def create(**kw):
            captured["prompt"] = kw["messages"][1]["content"]
            msg = SimpleNamespace(content=json.dumps(
                {"verdict": "borderline", "score": 50, "strengths": [], "gaps": [], "summary": "x"}
            ))
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr("jobscout.enrich._get_client", _capturing_client, raising=False)
    job = Job(job_id="j1", source="greenhouse", title="Data Scientist",
              url="https://e.com/1", description="desc")

    steered = UserProfile(
        label="steered", target_titles=["data scientist"],
        avoid_role_types=["pure BI/reporting work"],
        avoid_domains=["Shopify", "trade lifecycle"],
    )
    deep_match.compute_deep_match(job, steered)
    text = captured["prompt"]
    assert "pure BI/reporting work" in text
    assert "Shopify" in text and "trade lifecycle" in text

    plain = UserProfile(label="plain", target_titles=["bi analyst"])
    captured.clear()
    deep_match.compute_deep_match(job, plain)
    text = captured["prompt"]
    # An empty-profile prompt must carry NO leftover owner-specific skip rules.
    assert "Shopify" not in text
    assert "BI / reporting / dashboards" not in text
    assert "trade lifecycle" not in text
