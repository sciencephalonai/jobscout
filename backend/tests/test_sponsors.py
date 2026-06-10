"""Tests for the sponsorship-intelligence layer (sponsors.py + Job computed field)."""

from __future__ import annotations

import pytest

from jobscout.models import Job
from jobscout.search import build_filters
from jobscout.sponsors import (
    derive_sponsorship_likelihood,
    is_everify_employer,
    is_known_h1b_sponsor,
)

# ── E-Verify lookup (mirrors the H-1B tests) ─────────────────────────────────

def test_everify_matches_normalized():
    assert is_everify_employer("Stripe") is True
    assert is_everify_employer("Stripe, Inc.") is True
    assert is_everify_employer("  google  ") is True


def test_everify_unknown_is_false():
    assert is_everify_employer("Qwizzle Labs XYZ") is False
    assert is_everify_employer(None) is False
    assert is_everify_employer("") is False


def test_everify_filter_builds():
    assert build_filters(everify=True) is not None


def test_job_known_everify_roundtrips():
    from jobscout.store import _props_to_job
    job = _props_to_job({"job_id": "x", "title": "X", "url": "http://x",
                         "source": "ashby", "known_everify": None}, job_id="x")
    assert job.known_everify is False  # None coerced to default

# ── H-1B sponsor lookup (normalized, exact) ──────────────────────────────────

def test_known_sponsor_matches_normalized():
    assert is_known_h1b_sponsor("Stripe") is True
    assert is_known_h1b_sponsor("Stripe, Inc.") is True   # legal suffix stripped
    assert is_known_h1b_sponsor("  google  ") is True


def test_unknown_company_is_not_sponsor():
    assert is_known_h1b_sponsor("Qwizzle Labs XYZ") is False
    assert is_known_h1b_sponsor(None) is False
    assert is_known_h1b_sponsor("") is False


# ── Likelihood derivation (every branch) ─────────────────────────────────────

@pytest.mark.parametrize("visa,cap,citizen,h1b,expected", [
    ("no",            "unknown", False, False, "no"),       # explicit refusal
    ("not_mentioned", "unknown", True,  False, "no"),       # citizenship required
    ("not_mentioned", "likely",  True,  True,  "no"),       # citizenship overrides everything
    ("yes",           "unknown", False, False, "likely"),   # explicit yes
    ("not_mentioned", "unknown", False, True,  "likely"),   # known H-1B filer
    ("not_mentioned", "likely",  False, False, "likely"),   # cap-exempt employer
    ("not_mentioned", "yes",     False, False, "likely"),   # cap-exempt (yes)
    ("not_mentioned", "unknown", False, False, "unknown"),  # the silent ~96%
    ("unclear",       "no",      False, False, "unknown"),  # unclear, for-profit, no h1b
])
def test_derive_likelihood(visa, cap, citizen, h1b, expected):
    assert derive_sponsorship_likelihood(visa, cap, citizen, h1b) == expected


# ── Job computed field ───────────────────────────────────────────────────────

def test_job_computed_sponsorship_likelihood_serializes():
    job = Job(job_id="x", source="ashby", title="SWE", url="http://x",
              company="Acme", cap_exempt="likely")
    assert job.sponsorship_likelihood == "likely"
    assert job.model_dump()["sponsorship_likelihood"] == "likely"


def test_job_no_sponsorship_when_citizenship_required():
    job = Job(job_id="x", source="ashby", title="SWE", url="http://x",
              citizenship_required=True, cap_exempt="likely", known_h1b_sponsor=True)
    assert job.sponsorship_likelihood == "no"


# ── Filters ──────────────────────────────────────────────────────────────────

def test_exclude_no_sponsorship_filter():
    assert build_filters(exclude_no_sponsorship=True) is not None


def test_h1b_sponsor_filter():
    assert build_filters(h1b_sponsor=True) is not None


def test_positive_sponsorship_signals_or_combine():
    # cap-exempt + proven-H-1B + E-Verify must produce a (non-None) filter and be
    # OR-combined, not AND — enabling several positive signals unions, never empties.
    assert build_filters(cap_exempt=["yes", "likely"], h1b_sponsor=True, everify=True) is not None
    # A single positive signal still yields a filter (unchanged single-toggle behaviour).
    assert build_filters(cap_exempt=["yes", "likely"]) is not None
    assert build_filters(everify=True) is not None
