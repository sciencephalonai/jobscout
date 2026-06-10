"""Tests for keyword_title_match — the fix for the multi-keyword 'single-phrase trap'
that made per-company ATS adapters match nothing on multi-keyword searches."""

from __future__ import annotations

from jobscout.adapters.base import keyword_title_match


def test_any_keyword_matches():
    kws = ["data engineer", "software engineer", "data"]
    assert keyword_title_match("Senior Software Engineer", kws) is True
    assert keyword_title_match("Staff Data Scientist", kws) is True   # "data"
    assert keyword_title_match("Registered Nurse", kws) is False


def test_joined_phrase_would_have_failed_but_any_match_works():
    # The old behaviour joined keywords → "data engineer software engineer", which is
    # never a substring of a real title. ANY-match fixes this.
    kws = ["data engineer", "software engineer"]
    assert keyword_title_match("Software Engineer II", kws) is True


def test_empty_keywords_keeps_everything():
    assert keyword_title_match("Anything At All", []) is True
    assert keyword_title_match("x", ["  "]) is True


def test_case_insensitive():
    assert keyword_title_match("MACHINE LEARNING ENGINEER", ["machine learning"]) is True
