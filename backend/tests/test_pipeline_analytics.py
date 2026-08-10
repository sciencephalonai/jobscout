"""Funnel rollup over a profile's application pipeline (`PipelineAnalytics`)."""

from __future__ import annotations

from jobscout.models import PipelineAnalytics


def _entry(status: str, source: str = "greenhouse", kind: str = "primary") -> dict:
    return {"status": status, "source": source, "source_kind": kind}


class TestFromEntries:
    def test_empty_is_all_zero(self) -> None:
        a = PipelineAnalytics.from_entries([])
        assert a.total_applications == 0
        assert a.response_rate == 0.0
        assert a.by_stage == {
            "applied": 0, "oa": 0, "interview": 0, "offer": 0, "rejected": 0
        }
        assert a.by_source == []

    def test_stage_counts_and_rates(self) -> None:
        entries = [
            _entry("applied"), _entry("applied"),   # 2 no-response
            _entry("oa"),                            # screening only
            _entry("interview"),                     # interview
            _entry("offer"),                         # offer
            _entry("rejected"),                      # responded (rejection)
        ]
        a = PipelineAnalytics.from_entries(entries)
        assert a.total_applications == 6
        assert a.by_stage == {
            "applied": 2, "oa": 1, "interview": 1, "offer": 1, "rejected": 1
        }
        # Responded = everything not still 'applied' = 4/6.
        assert a.responded == 4
        assert a.response_rate == round(4 / 6, 4)
        # Screening = currently at oa/interview/offer = 3/6.
        assert a.screening_rate == round(3 / 6, 4)
        # Interview = currently at interview/offer = 2/6.
        assert a.interview_rate == round(2 / 6, 4)
        assert a.offer_rate == round(1 / 6, 4)

    def test_unknown_status_ignored(self) -> None:
        # A stray triage row ('saved') must not inflate the funnel.
        a = PipelineAnalytics.from_entries([_entry("applied"), _entry("saved")])
        assert a.total_applications == 1

    def test_per_source_breakdown_sorted_and_correct(self) -> None:
        entries = [
            _entry("offer", source="lever", kind="primary"),
            _entry("applied", source="lever", kind="primary"),
            _entry("interview", source="adzuna", kind="aggregator"),
            _entry("applied", source="adzuna", kind="aggregator"),
            _entry("applied", source="adzuna", kind="aggregator"),
        ]
        a = PipelineAnalytics.from_entries(entries)
        # adzuna has 3 applications, lever 2 → adzuna first (desc by applications).
        assert [s.source for s in a.by_source] == ["adzuna", "lever"]
        adzuna = a.by_source[0]
        assert adzuna.applications == 3 and adzuna.responded == 1 and adzuna.offers == 0
        lever = a.by_source[1]
        assert lever.applications == 2 and lever.responded == 1 and lever.offers == 1
        assert lever.source_kind == "primary"

    def test_missing_source_falls_back(self) -> None:
        a = PipelineAnalytics.from_entries([{"status": "applied"}])
        assert a.by_source[0].source == "unknown"
        assert a.by_source[0].source_kind == "aggregator"


class TestPipelineEndpoint:
    """The /pipeline route embeds the funnel rollup computed over real state."""

    def test_analytics_in_pipeline_response(self, client) -> None:  # noqa: ANN001
        pid = client.post("/api/profiles", json={"label": "p"}).json()["id"]
        # j1/j2 come from the fake store (source="ashby" → primary).
        client.post(f"/api/profiles/{pid}/job-state",
                    json={"job_id": "j1", "status": "interview"})
        client.post(f"/api/profiles/{pid}/job-state",
                    json={"job_id": "j2", "status": "applied"})

        body = client.get(f"/api/profiles/{pid}/pipeline").json()
        a = body["analytics"]
        assert a["total_applications"] == 2
        assert a["by_stage"]["interview"] == 1
        assert a["by_stage"]["applied"] == 1
        assert a["responded"] == 1                 # j1 moved past 'applied'
        assert a["response_rate"] == 0.5
        assert a["interview_rate"] == 0.5
        # Both jobs are from the same primary source.
        assert a["by_source"] == [{
            "source": "ashby", "source_kind": "primary",
            "applications": 2, "responded": 1, "offers": 0,
        }]

    def test_empty_pipeline_has_zeroed_analytics(self, client) -> None:  # noqa: ANN001
        pid = client.post("/api/profiles", json={"label": "empty"}).json()["id"]
        a = client.get(f"/api/profiles/{pid}/pipeline").json()["analytics"]
        assert a["total_applications"] == 0
        assert a["response_rate"] == 0.0
