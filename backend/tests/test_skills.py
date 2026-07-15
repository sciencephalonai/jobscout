"""Tests for the fuzzy skill matcher (jobscout.skills)."""

from __future__ import annotations

from jobscout.skills import (
    canonicalize,
    profile_skills_mentioned_in_text,
    skills_overlap,
)


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


def test_profile_skill_mentions_are_evidence_backed_and_alias_aware():
    found = profile_skills_mentioned_in_text(
        "Build Python services with PostgreSQL, Kubernetes, and CI/CD pipelines.",
        ["python", "postgres", "k8s", "cicd", "tableau"],
    )
    assert found == ["python", "postgres", "k8s", "cicd"]


def test_profile_skill_mentions_skip_ambiguous_short_names():
    found = profile_skills_mentioned_in_text(
        "You will go to customer sites and communicate with R and D teams.",
        ["go", "r"],
    )
    assert found == []


# ── Umbrella-skill implication (concrete tools satisfy umbrella JD terms) ─────


def test_umbrella_ml_satisfied_by_concrete_tools():
    from jobscout.skills import skills_overlap

    matched, gaps = skills_overlap(["machine learning"], ["pytorch"])
    assert matched == ["machine learning"] and gaps == []


def test_umbrella_not_satisfied_by_unrelated_skills():
    from jobscout.skills import skills_overlap

    matched, gaps = skills_overlap(["machine learning"], ["excel", "tableau"])
    assert matched == [] and gaps == ["machine learning"]


def test_concrete_tool_gaps_stay_honest():
    from jobscout.skills import skills_overlap

    # Umbrella implication is one-directional: pytorch implies ML, but a
    # concrete-tool requirement is not satisfied by adjacent tools.
    matched, gaps = skills_overlap(["kubernetes"], ["pytorch", "machine learning"])
    assert matched == [] and gaps == ["kubernetes"]


def test_applied_materials_ml_engineer_case_has_no_gaps():
    from jobscout.skills import skills_overlap

    jd = ["generative ai", "large language models", "fine-tuning", "machine learning"]
    profile = [
        "python", "scikit-learn", "tensorflow", "keras", "pytorch", "xgboost",
        "langchain", "claude api", "openai api", "llm", "prompt engineering",
        "rag pipelines", "fine-tuning",
    ]
    matched, gaps = skills_overlap(jd, profile)
    assert gaps == []
    assert sorted(matched) == sorted(jd)


def test_new_umbrella_implications_and_aliases():
    from jobscout.skills import canonicalize, skills_overlap

    # aliases
    assert canonicalize("PowerBI") == "power bi"
    assert canonicalize("SB3") == "stable-baselines3"
    assert canonicalize("RAG") == "rag pipelines"

    # concrete profile skills satisfy umbrella JD requirements
    profile = ["opencv", "cnn", "stable-baselines3", "pytorch", "pandas", "clickhouse"]
    matched, gaps = skills_overlap(
        ["computer vision", "reinforcement learning", "transfer learning", "etl"], profile
    )
    assert gaps == [], gaps
    assert len(matched) == 4

    # unrelated umbrella terms are still honest gaps
    matched, gaps = skills_overlap(["kubernetes", "mlops"], ["pandas"])
    assert "kubernetes" in gaps
