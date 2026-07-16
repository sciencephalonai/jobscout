"""Configuration-only tests for selectable OpenAI-compatible LLM providers."""

from __future__ import annotations

import jobscout.enrich as enrich


def test_nvidia_configuration_uses_the_openai_compatible_endpoint(monkeypatch):
    monkeypatch.setattr(enrich.settings, "llm_provider", "nvidia")
    monkeypatch.setattr(enrich.settings, "nvidia_api_key", "test-nvidia-key")
    monkeypatch.setattr(enrich.settings, "nvidia_base_url", "https://example.nvidia/v1")
    monkeypatch.setattr(enrich.settings, "nvidia_model", "z-ai/glm-5.2")
    monkeypatch.setattr(enrich, "_client", None)
    monkeypatch.setattr(enrich, "_client_signature", None)

    seen: dict[str, object] = {}

    class FakeClient:
        def __init__(
            self, *, api_key: str, base_url: str, timeout: float, max_retries: int,
        ) -> None:
            seen.update({
                "api_key": api_key,
                "base_url": base_url,
                "timeout": timeout,
                "max_retries": max_retries,
            })

    monkeypatch.setattr(enrich, "OpenAI", FakeClient)
    client = enrich._get_client()

    assert isinstance(client, FakeClient)
    assert enrich.active_llm_configuration() == ("nvidia", "test-nvidia-key", "z-ai/glm-5.2")
    assert enrich.llm_is_configured() is True
    assert seen == {
        "api_key": "test-nvidia-key",
        "base_url": "https://example.nvidia/v1",
        "timeout": 45.0,
        "max_retries": 4,
    }


def test_429_on_primary_falls_back_to_alternate_provider(monkeypatch):
    """NVIDIA free-tier exhaustion must not park jobs as failed when DeepSeek is configured."""
    import jobscout.enrich as enrich
    from jobscout.config import settings

    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    monkeypatch.setattr(settings, "nvidia_api_key", "nv-key")
    monkeypatch.setattr(settings, "deepseek_api_key", "ds-key")
    monkeypatch.setattr(enrich, "_client", None)
    monkeypatch.setattr(enrich, "_client_signature", None)

    calls = []

    class _Msg:
        content = '{"yoe_min": 0, "visa_sponsorship": "yes", "skills": [], "seniority": "junior"}'

    class _Choice:
        message = _Msg()

    class _Completion:
        choices = [_Choice()]

    class _Chat:
        def __init__(self, fail):
            self._fail = fail

        def create(self, **kw):
            calls.append(kw["model"])
            if self._fail:
                raise RuntimeError("Error code: 429 - Too Many Requests")
            return _Completion()

    class _FakeOpenAI:
        def __init__(self, api_key=None, base_url=None, **kw):
            self.chat = type("C", (), {})()
            # primary (nvidia key) fails; fallback (deepseek key) succeeds
            self.chat.completions = _Chat(fail=(api_key == "nv-key"))

    monkeypatch.setattr(enrich, "OpenAI", _FakeOpenAI)
    result = enrich.extract_enrichment("Data Scientist", "TestCo", "Python, SQL. 0-2 years.")
    assert result["seniority"] == "junior"
    assert len(calls) == 2  # primary attempt + fallback attempt


def test_selected_provider_without_key_falls_back_to_configured_one(monkeypatch):
    from jobscout.config import settings
    from jobscout.enrich import active_llm_configuration

    monkeypatch.setattr(settings, "llm_provider", "nvidia")
    monkeypatch.setattr(settings, "nvidia_api_key", "")
    monkeypatch.setattr(settings, "deepseek_api_key", "ds-key", raising=False)
    provider, key, _model = active_llm_configuration()
    assert provider == "deepseek" and key == "ds-key"

    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_api_key", "", raising=False)
    monkeypatch.setattr(settings, "nvidia_api_key", "nv-key")
    provider, key, _model = active_llm_configuration()
    assert provider == "nvidia" and key == "nv-key"
