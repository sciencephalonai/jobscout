"""Tests for new-grad / early-career program detection + filter."""

from __future__ import annotations

from jobscout.normalize import detect_new_grad_program
from jobscout.search import build_filters


def test_detect_from_title() -> None:
    assert detect_new_grad_program("Software Engineer, New Grad 2026") is True
    assert detect_new_grad_program("University Graduate - Data Scientist") is True
    assert detect_new_grad_program("Early Career Rotational Program — Analytics") is True
    assert detect_new_grad_program("Data Science Graduate Program") is True


def test_detect_from_description_when_title_silent() -> None:
    assert detect_new_grad_program(
        "Data Analyst",
        "This is our early career program for recent graduates joining in 2026.",
    ) is True


def test_not_detected_for_regular_roles() -> None:
    assert detect_new_grad_program("Senior Data Engineer", "5+ years required.") is False
    assert detect_new_grad_program("Data Scientist", None) is False
    assert detect_new_grad_program("Machine Learning Engineer II") is False
    assert detect_new_grad_program(None) is False


def test_description_scan_is_bounded() -> None:
    # A mention far past the scan window should NOT trigger.
    long_desc = ("x" * 2000) + " new grad program"
    assert detect_new_grad_program("Data Analyst", long_desc) is False


def test_structured_employee_type_is_detected_deep_in_description() -> None:
    long_desc = ("Company and benefits. " * 200) + "Employee Type: New College Grad"
    assert detect_new_grad_program("Machine Learning Engineer", long_desc) is True


def test_new_grad_only_filter_builds() -> None:
    assert build_filters(new_grad_only=True) is not None
    assert build_filters() is None
