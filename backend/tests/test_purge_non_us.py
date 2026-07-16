"""Focused tests for the safe legacy Workday revalidation command."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parents[2] / "scripts" / "purge_non_us.py"
_SPEC = importlib.util.spec_from_file_location("purge_non_us", _SCRIPT)
assert _SPEC and _SPEC.loader
purge_non_us = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(purge_non_us)


def _row(**overrides):
    row = {
        "source": "workday",
        "title": "System Software Engineer",
        "location_raw": "2 Locations",
        "country": "us",
        "remote_mode": "onsite",
    }
    row.update(overrides)
    return row


def test_revalidates_placeholder_and_ambiguous_global_board_rows():
    assert purge_non_us._needs_workday_revalidation(_row()) is True
    assert purge_non_us._needs_workday_revalidation(
        _row(location_raw="Vietnam, Hanoi")
    ) is True
    assert purge_non_us._needs_workday_revalidation(
        _row(location_raw="LVD 1st Floor")
    ) is True


def test_does_not_reprobe_workday_row_with_self_contained_us_location():
    assert purge_non_us._needs_workday_revalidation(
        _row(location_raw="Santa Clara, CA")
    ) is False


def test_does_not_reprobe_other_sources():
    assert purge_non_us._needs_workday_revalidation(
        _row(source="greenhouse", location_raw="Unknown")
    ) is False


def test_probe_uses_url_path_when_detail_request_fails(monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(purge_non_us.urllib.request, "urlopen", fail)
    location, country = purge_non_us._probe_workday_detail(
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/"
        "job/Vietnam-Ho-Chi-Minh-City/System-Software-Engineer_JR1"
    )
    assert location == "Vietnam Ho Chi Minh City"
    assert country is None
