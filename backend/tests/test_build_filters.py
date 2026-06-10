"""Tests for jobscout.search.build_filters.

These tests assert observable behaviour only (``is None`` / ``is not None``).
They never introspect the internal structure of the Weaviate ``Filter`` object,
which is an opaque implementation detail.
"""

from __future__ import annotations

import datetime as dt

from jobscout.search import build_filters


def test_no_args_returns_none() -> None:
    assert build_filters() is None


def test_empty_lists_behave_like_none() -> None:
    assert (
        build_filters(remote=[], visa=[], source=[], company_size=[], exp=[])
        is None
    )


def test_single_source_filter() -> None:
    assert build_filters(source=["adzuna"]) is not None


def test_multi_value_source_filter() -> None:
    assert build_filters(source=["adzuna", "greenhouse"]) is not None


def test_employment_type_filter() -> None:
    assert build_filters(employment_type=["contract"]) is not None
    assert build_filters(employment_type=["full_time", "contract"]) is not None
    assert build_filters(employment_type=[]) is None


def test_single_remote_filter() -> None:
    assert build_filters(remote=["remote"]) is not None


def test_single_visa_filter() -> None:
    assert build_filters(visa=["yes"]) is not None


def test_employer_type_filter() -> None:
    assert build_filters(employer_type=["university", "hospital"]) is not None


def test_cap_exempt_filter() -> None:
    assert build_filters(cap_exempt=["likely"]) is not None


def test_security_clearance_filter() -> None:
    assert build_filters(security_clearance=["none"]) is not None


def test_exclude_citizenship_required_flag() -> None:
    assert build_filters(exclude_citizenship_required=True) is not None


def test_exclude_recruiter_flag() -> None:
    assert build_filters(exclude_recruiter=True) is not None


def test_hourly_presets_apply() -> None:
    for preset in ("6h", "12h", "18h"):
        assert build_filters(date_range=preset) is not None


def test_single_company_size_filter() -> None:
    assert build_filters(company_size=["startup"]) is not None


def test_exp_valid_bands() -> None:
    assert build_filters(exp=["entry", "senior"]) is not None


def test_exp_unknown_band_returns_none() -> None:
    # No recognised bands → no valid clauses → None.
    assert build_filters(exp=["bogus"]) is None


def test_date_range_preset() -> None:
    assert build_filters(date_range="7d") is not None


def test_unknown_date_range_returns_none() -> None:
    assert build_filters(date_range="999y") is None


def test_date_from_only() -> None:
    assert build_filters(date_from=dt.date(2026, 1, 1)) is not None


def test_date_to_only() -> None:
    assert build_filters(date_to=dt.date(2026, 6, 1)) is not None


def test_combined_filters() -> None:
    result = build_filters(
        remote=["remote"],
        visa=["yes"],
        source=["adzuna", "greenhouse"],
        exp=["entry", "senior"],
        date_range="7d",
    )
    assert result is not None
