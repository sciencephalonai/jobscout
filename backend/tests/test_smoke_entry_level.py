"""Tests for the smoke-adapter entry-level title heuristic."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_adapters.py"
_spec = importlib.util.spec_from_file_location("smoke_adapters", _SCRIPT)
assert _spec and _spec.loader
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)


@pytest.mark.parametrize("title", [
    "Research Engineer",
    "Machine Learning Engineer",
    "Applied Scientist",
    "AI Engineer",
    "Data Scientist I",
    "Analytics Engineer I",
])
def test_unsenior_technical_titles_count_as_entry_level(title: str) -> None:
    assert smoke.looks_entry_level(title) is True


@pytest.mark.parametrize("title", [
    "Senior Data Scientist",
    "Staff Machine Learning Engineer",
    "Principal AI Engineer",
    "Engineering Manager",
    "Software Engineer II",
])
def test_senior_titles_are_not_entry_level(title: str) -> None:
    assert smoke.looks_entry_level(title) is False
