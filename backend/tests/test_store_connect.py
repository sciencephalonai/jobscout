"""Boot-resilience tests for WeaviateStore.__init__ — a transient/slow Weaviate
must not kill startup; only a persistent failure raises the friendly error."""

from __future__ import annotations

import pytest

import jobscout.store as store


@pytest.fixture(autouse=True)
def _cloud_creds_and_no_sleep(monkeypatch):
    # Force the cloud branch deterministically + don't actually sleep between retries.
    monkeypatch.setattr(store.settings, "weaviate_cluster_url", "test.weaviate.cloud")
    monkeypatch.setattr(store.settings, "weaviate_api_key", "k")
    monkeypatch.setattr(store.time, "sleep", lambda *_a, **_k: None)
    # Isolate the connect retry from schema bootstrap.
    monkeypatch.setattr(store.WeaviateStore, "_ensure_collection", lambda self: None)


class _DummyClient:
    def close(self) -> None:
        pass


def test_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("WeaviateGRPCUnavailableError: transient")
        return _DummyClient()

    monkeypatch.setattr(store.weaviate, "connect_to_weaviate_cloud", flaky)
    s = store.WeaviateStore()
    assert calls["n"] == 3                 # failed twice, succeeded on the 3rd
    assert isinstance(s._client, _DummyClient)


def test_persistent_failure_raises_friendly_error(monkeypatch):
    def always_fail(*a, **k):
        raise RuntimeError("WeaviateGRPCUnavailableError: down")

    monkeypatch.setattr(store.weaviate, "connect_to_weaviate_cloud", always_fail)
    with pytest.raises(RuntimeError, match="Could not connect to Weaviate"):
        store.WeaviateStore()


def test_passes_skip_init_checks(monkeypatch):
    seen = {}

    def capture(*a, **k):
        seen.update(k)
        return _DummyClient()

    monkeypatch.setattr(store.weaviate, "connect_to_weaviate_cloud", capture)
    store.WeaviateStore()
    assert seen.get("skip_init_checks") is True
    assert seen.get("additional_config") is not None
