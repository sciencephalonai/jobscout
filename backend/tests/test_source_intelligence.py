"""Source provenance must agree across deduplication and UI responses."""

from jobscout.models import Job
from jobscout.source_intelligence import source_authority, source_kind, source_label


def test_primary_ats_is_classified_as_direct() -> None:
    assert source_kind("greenhouse") == "primary"
    assert source_authority("greenhouse") < source_authority("adzuna")
    assert source_label("greenhouse") == "Direct employer ATS"


def test_job_exposes_provenance_and_greenhouse_date_semantics() -> None:
    job = Job(
        job_id="source-test",
        source="greenhouse",
        title="Data Engineer",
        company="Example University",
        url="https://boards.greenhouse.io/example/jobs/1",
    )
    assert job.source_kind == "primary"
    assert job.freshness_kind == "updated"


def test_estimated_date_is_never_presented_as_posted() -> None:
    job = Job(
        job_id="estimated-test",
        source="lever",
        title="Data Engineer",
        url="https://jobs.lever.co/example/1",
        posted_date_est=True,
    )
    assert job.freshness_kind == "estimated"
