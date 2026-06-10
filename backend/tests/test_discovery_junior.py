"""Test the seniority-aware title heuristic in scripts/discover_companies.py.

This is the fix for the 'Founding Engineer flagged as a 0-2yr target' problem —
discovery must not count senior titles as junior-relevant.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "discover_companies.py"
_spec = importlib.util.spec_from_file_location("discover_companies", _SCRIPT)
assert _spec and _spec.loader
discover = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(discover)


@pytest.mark.parametrize("title", [
    "Founding Full-Stack Engineer",
    "Senior Data Scientist",
    "Sr. Software Engineer",
    "Staff Machine Learning Engineer",
    "Principal Engineer",
    "Lead Data Analyst",
    "Engineering Manager",
    "Director of Data",
    "Software Engineer II",
])
def test_senior_titles_are_not_junior(title):
    assert discover.is_junior_title(title) is False


@pytest.mark.parametrize("title", [
    "Software Engineer",
    "Data Analyst",
    "Software Engineer - Product (New Grad)",
    "Associate Data Scientist",
    "Junior Backend Engineer",
    "Data Engineer, Platform",
])
def test_entry_titles_are_junior(title):
    assert discover.is_junior_title(title) is True


def test_slugify():
    assert discover.slugify("Modern Treasury") == "moderntreasury"
    assert discover.slugify("tavern-research!") == "tavernresearch"
