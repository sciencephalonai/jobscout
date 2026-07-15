"""Contract for the company list imported from job_search_results.md."""

from __future__ import annotations

from pathlib import Path

import yaml

from jobscout.models import Company
from jobscout.services.source_config import _load_sources_cfg

ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "data" / "company_targets.yaml"


def _rows() -> list[dict]:
    return (yaml.safe_load(TARGETS.read_text()) or {}).get("companies") or []


def test_import_contains_61_unique_surfaced_companies() -> None:
    rows = _rows()
    assert len(rows) == 61
    assert len({row["name"].casefold() for row in rows}) == 61
    assert len({(row["ats"], row["slug"]) for row in rows}) == 61
    for row in rows:
        Company.model_validate(row)


def test_note_only_hard_skips_were_not_imported() -> None:
    names = {row["name"] for row in _rows()}
    assert {"Calendly", "Fidelity", "Pfizer", "Boeing", "Guidehouse"}.isdisjoint(names)


def test_every_refreshable_target_has_a_matching_source_entry(monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    cfg = _load_sources_cfg()["sources"]
    for row in _rows():
        ats = row["ats"]
        if ats == "none":
            assert row["enabled"] is False
            assert row["direct_apply_only"] is True
            continue
        if ats == "workday":
            assert any(
                tenant.get("tenant") == row["slug"]
                and tenant.get("site") == row["site"]
                for tenant in cfg["workday"]["tenants"]
            )
            continue
        field = "accounts" if ats == "workable" else "companies"
        assert any(
            (entry.get("token") if isinstance(entry, dict) else entry) == row["slug"]
            for entry in cfg[ats][field]
        )


def test_notion_uses_verified_ashby_board_not_dead_greenhouse_board(
    monkeypatch,
) -> None:
    monkeypatch.chdir(ROOT)
    cfg = _load_sources_cfg()["sources"]
    ashby = {entry["token"] for entry in cfg["ashby"]["companies"]}
    greenhouse = {entry["token"] for entry in cfg["greenhouse"]["companies"]}
    assert "notion" in ashby
    assert "notion" not in greenhouse
