"""Tests for the discovered-sources merge in _load_sources_cfg."""

from __future__ import annotations

import yaml

from jobscout.services.source_config import _merge_discovered


def test_merge_adds_new_tokens_and_dedups(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sources.discovered.yaml").write_text(yaml.safe_dump({"sources": {
        "greenhouse": {"companies": [
            {"token": "stripe", "type": "for_profit"},   # dup → skipped
            {"token": "neon", "type": "for_profit"},      # new → added
        ]},
        "ashby": {"companies": [{"token": "ramp", "type": "for_profit"}]},
    }}))

    cfg = {"sources": {"greenhouse": {"enabled": True, "companies": [{"token": "stripe"}]}}}
    out = _merge_discovered(cfg)

    gh = {c["token"] for c in out["sources"]["greenhouse"]["companies"]}
    assert gh == {"stripe", "neon"}                      # deduped + added
    assert out["sources"]["ashby"]["companies"] == [{"token": "ramp", "type": "for_profit"}]


def test_merge_missing_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no sources.discovered.yaml here
    cfg = {"sources": {"greenhouse": {"companies": [{"token": "stripe"}]}}}
    out = _merge_discovered(cfg)
    assert out["sources"]["greenhouse"]["companies"] == [{"token": "stripe"}]
