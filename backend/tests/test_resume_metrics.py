"""Lightweight AI-detection metric suite (`resume_metrics`)."""

from __future__ import annotations

from jobscout import resume_metrics as rm

# An "AI-like" resume paragraph: buzzword-heavy, uniform sentence rhythm, repetitive.
AI_LIKE = (
    "I leverage cutting-edge solutions to streamline robust workflows. "
    "I leverage scalable systems to streamline seamless processes. "
    "I utilize holistic frameworks to empower transformative outcomes. "
    "I utilize actionable insights to facilitate impactful results."
)
# A more "human" version: varied length, concrete, no buzzwords.
HUMAN_LIKE = (
    "Cut model training time from nine hours to forty minutes. "
    "I rebuilt the feature pipeline in Rust after profiling showed pandas was the bottleneck, "
    "which also dropped memory use by half. "
    "Shipped it. "
    "Two teams now depend on the nightly job, and it has not paged anyone in six months."
)


class TestComputeMetrics:
    def test_empty_text_returns_empty(self) -> None:
        assert rm.compute_metrics("") == {}
        assert rm.compute_metrics("   \n ") == {}

    def test_bundle_has_all_light_families(self) -> None:
        b = rm.compute_metrics(HUMAN_LIKE)
        for fam in ("readability", "lexical", "character", "structure",
                    "function_content", "repetition", "buzzword", "composite"):
            assert fam in b, fam

    def test_no_crash_on_tiny_text(self) -> None:
        # Should degrade, not raise.
        b = rm.compute_metrics("Hello world.")
        assert "composite" in b


class TestBuzzwords:
    def test_density_detects_buzzwords(self) -> None:
        m = rm.buzzword_metrics(AI_LIKE)
        assert m["buzzword_count"] >= 8
        assert m["ai_buzzword_density"] > 0
        assert rm.buzzword_metrics(HUMAN_LIKE)["buzzword_count"] == 0


class TestAiRisk:
    def test_score_bounds_and_inverse(self) -> None:
        risk = rm.ai_risk(rm.compute_metrics(HUMAN_LIKE))
        assert 0 <= risk["ai_risk_score"] <= 100
        assert risk["humanization_score"] == round(100 - risk["ai_risk_score"], 2)
        assert risk["band"] in ("good", "warning", "serious")
        assert len(risk["drivers"]) <= 3

    def test_ai_like_scores_riskier_than_human(self) -> None:
        ai = rm.ai_risk(rm.compute_metrics(AI_LIKE))["ai_risk_score"]
        human = rm.ai_risk(rm.compute_metrics(HUMAN_LIKE))["ai_risk_score"]
        assert ai > human

    def test_missing_family_renormalizes(self) -> None:
        # A bundle with only buzzword present still yields a bounded score.
        risk = rm.ai_risk({"buzzword": {"ai_buzzword_density": 4.0}})
        assert 0 <= risk["ai_risk_score"] <= 100


class TestDelta:
    def test_humanization_improvement_is_better(self) -> None:
        before = rm.compute_metrics(AI_LIKE)
        after = rm.compute_metrics(HUMAN_LIKE)
        rows = rm.delta(before, after)
        by_metric = {r["metric"]: r for r in rows}
        # Tailoring AI-like -> human-like should improve humanization and cut buzzwords.
        assert by_metric["humanization_score"]["direction"] == "better"
        assert by_metric["ai_buzzword_density"]["direction"] == "better"

    def test_rows_are_numeric_and_shaped(self) -> None:
        rows = rm.delta(rm.compute_metrics(HUMAN_LIKE), rm.compute_metrics(AI_LIKE))
        assert rows
        for r in rows:
            assert set(r) == {"family", "metric", "before", "after", "delta", "direction"}
            assert r["direction"] in ("better", "worse", "neutral")

    def test_identical_text_is_neutral(self) -> None:
        b = rm.compute_metrics(HUMAN_LIKE)
        rows = rm.delta(b, b)
        assert all(r["direction"] == "neutral" for r in rows)
