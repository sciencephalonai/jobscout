"""Tests for the fuzzy skill matcher (jobscout.skills)."""

from __future__ import annotations

from jobscout.skills import canonicalize, skills_overlap


def test_canonicalize_aliases():
    assert canonicalize("JS") == "javascript"
    assert canonicalize("Postgres") == "postgresql"
    assert canonicalize("sklearn") == "scikit-learn"
    assert canonicalize("K8s") == "kubernetes"


def test_overlap_matches_synonyms():
    matched, gaps = skills_overlap(
        ["JavaScript", "PostgreSQL", "scikit-learn", "Kubernetes", "Rust"],
        ["js", "postgres", "sklearn", "python", "k8s"],
    )
    assert set(matched) == {"JavaScript", "PostgreSQL", "scikit-learn", "Kubernetes"}
    assert gaps == ["Rust"]


def test_overlap_token_subset():
    # "data science" (profile) should match "data science engineer" (job).
    matched, _ = skills_overlap(["Data Science Engineer"], ["data science"])
    assert matched == ["Data Science Engineer"]


def test_overlap_empty_inputs():
    assert skills_overlap([], ["python"]) == ([], [])
    matched, gaps = skills_overlap(["python"], [])
    assert matched == [] and gaps == ["python"]


def test_no_false_match_on_short_substring():
    # "go" must NOT match "django" (substring guard requires len>=4 on both).
    matched, gaps = skills_overlap(["Django"], ["go"])
    assert matched == []
    assert gaps == ["Django"]
