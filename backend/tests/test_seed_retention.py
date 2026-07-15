"""First-run seed gating + retention prune (no network, no keys)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jobscout.api.main as main
import jobscout.services.ingestion_service as ing
from jobscout.config import settings
from jobscout.relational import DuckDBRelationalStore


class _EmptyStore:
    def __init__(self, count: int = 0) -> None:
        self._count = count
        self.purged_cutoff: datetime | None = None

    # main._index_is_empty reaches through _client.collections.get(...).aggregate
    class _Agg:
        def __init__(self, count: int) -> None:
            self._count = count

        def over_all(self, **_):  # noqa: ANN001, ANN003
            return type("R", (), {"total_count": self._count})()

    class _Coll:
        def __init__(self, count: int) -> None:
            self.aggregate = _EmptyStore._Agg(count)

    class _Colls:
        def __init__(self, count: int) -> None:
            self._count = count

        def get(self, _name: str):  # noqa: ANN202
            return _EmptyStore._Coll(self._count)

    @property
    def _client(self):  # noqa: ANN202
        return type("C", (), {"collections": _EmptyStore._Colls(self._count)})()

    def purge_older_than(self, cutoff: datetime) -> int:
        self.purged_cutoff = cutoff
        return 3


def _mem() -> DuckDBRelationalStore:
    return DuckDBRelationalStore(":memory:")


def test_should_seed_when_empty_and_keyed(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(settings, "seed_on_first_run", True)
    monkeypatch.setattr(settings, "google_api_key", "x")
    rel = _mem()
    assert main._should_seed_first_run(_EmptyStore(0), rel) is True


def test_no_seed_without_embedding_key(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(settings, "seed_on_first_run", True)
    monkeypatch.setattr(settings, "google_api_key", "")
    assert main._should_seed_first_run(_EmptyStore(0), _mem()) is False


def test_no_seed_when_index_has_jobs(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(settings, "seed_on_first_run", True)
    monkeypatch.setattr(settings, "google_api_key", "x")
    assert main._should_seed_first_run(_EmptyStore(42), _mem()) is False


def test_no_seed_once_marker_set(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(settings, "seed_on_first_run", True)
    monkeypatch.setattr(settings, "google_api_key", "x")
    rel = _mem()
    rel.set_meta("seeded_at", datetime.now(UTC).isoformat())
    assert main._should_seed_first_run(_EmptyStore(0), rel) is False


def test_seed_stamps_marker_on_success(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(ing, "_run_ingestion", lambda **_: None)
    rel = _mem()
    ing.seed_first_run(_EmptyStore(0), rel)
    assert rel.get_meta("seeded_at") is not None


def test_seed_does_not_stamp_on_failure(monkeypatch):  # noqa: ANN001
    def _boom(**_):  # noqa: ANN003, ANN202
        raise RuntimeError("network down")

    monkeypatch.setattr(ing, "_run_ingestion", _boom)
    rel = _mem()
    ing.seed_first_run(_EmptyStore(0), rel)
    assert rel.get_meta("seeded_at") is None  # retries next boot


def test_prune_uses_retention_window(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(settings, "retention_days", 60)
    store = _EmptyStore()
    removed = ing.prune_stale_jobs(store)
    assert removed == 3
    assert store.purged_cutoff is not None
    # cutoff ≈ now - 60 days (allow scheduling slack)
    delta = datetime.now(UTC) - store.purged_cutoff
    assert timedelta(days=59) < delta < timedelta(days=61)


def test_prune_disabled_when_zero(monkeypatch):  # noqa: ANN001
    monkeypatch.setattr(settings, "retention_days", 0)
    store = _EmptyStore()
    assert ing.prune_stale_jobs(store) == 0
    assert store.purged_cutoff is None
