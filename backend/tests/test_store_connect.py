"""Boot-resilience tests for WeaviateStore.__init__ — a transient/slow Weaviate
must not kill startup; a cloud-primary failure degrades to local; only a local
failure raises the friendly error."""

from __future__ import annotations

import pytest

import jobscout.store as store


class _DummyCollections:
    def exists(self, *_a, **_k) -> bool:
        return True


class _DummyClient:
    def __init__(self) -> None:
        self.collections = _DummyCollections()

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _cloud_creds_and_no_sleep(monkeypatch):
    # Cloud creds present + cloud-primary by default; don't sleep between retries.
    monkeypatch.setattr(store.settings, "weaviate_cluster_url", "test.weaviate.cloud")
    monkeypatch.setattr(store.settings, "weaviate_api_key", "k")
    monkeypatch.setattr(store.settings, "storage_mode", "cloud")
    monkeypatch.setattr(store.time, "sleep", lambda *_a, **_k: None)
    # Isolate connect retry from schema bootstrap.
    monkeypatch.setattr(store.WeaviateStore, "_ensure_collection", lambda self, client: None)


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
    assert s.primary_target == "cloud"


def test_persistent_cloud_failure_degrades_to_local(monkeypatch):
    # New behavior: a dead cloud primary degrades to local instead of being fatal.
    local_client = _DummyClient()
    monkeypatch.setattr(
        store.weaviate, "connect_to_weaviate_cloud",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("cloud down")),
    )
    monkeypatch.setattr(store.weaviate, "connect_to_local", lambda *a, **k: local_client)
    s = store.WeaviateStore()
    assert s.primary_target == "local"
    assert s._client is local_client


def test_local_failure_raises_friendly_error(monkeypatch):
    monkeypatch.setattr(store.settings, "storage_mode", "local")  # local primary

    def always_fail(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(store.weaviate, "connect_to_local", always_fail)
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
