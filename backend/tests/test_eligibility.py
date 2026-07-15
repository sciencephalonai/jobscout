"""Deterministic work-authorisation evidence and direct-ATS lifecycle tests."""

from __future__ import annotations

from jobscout.eligibility import extract_work_authorization_evidence
from jobscout.models import Job, UserProfile
from jobscout.relational import DuckDBRelationalStore
from jobscout.services.ingestion_service import _is_profile_candidate


def test_explicit_no_sponsorship_is_evidence_backed() -> None:
    result = extract_work_authorization_evidence(
        "Applicants must be authorized to work permanently in the United States; "
        "we cannot provide visa sponsorship now or in the future."
    )
    assert result["visa_sponsorship"] == "no"
    assert result["evidence"]


def test_export_control_and_active_clearance_are_hard_signals() -> None:
    result = extract_work_authorization_evidence(
        "This ITAR-controlled role requires a U.S. Person and an active Secret clearance."
    )
    assert result["citizenship_required"] is True
    assert result["security_clearance"] == "required"
    assert len(result["evidence"]) == 1


def test_sponsorship_question_is_not_mistaken_for_a_refusal() -> None:
    result = extract_work_authorization_evidence(
        "Will you now or in the future require sponsorship to work in the United States?"
    )
    assert result["visa_sponsorship"] is None


def test_board_checkpoint_closes_only_missing_roles() -> None:
    store = DuckDBRelationalStore(":memory:")
    try:
        store.mark_board_job_seen("greenhouse", "acme", "open")
        store.mark_board_job_seen("greenhouse", "acme", "removed")
        assert store.close_missing_board_jobs("greenhouse", "acme", {"open"}) == ["removed"]
        assert store.job_has_active_board_presence("open") is True
        assert store.job_has_active_board_presence("removed") is False
        # A later snapshot can re-open a listing without a destructive reset.
        store.mark_board_job_seen("greenhouse", "acme", "removed")
        assert store.job_has_active_board_presence("removed") is True
    finally:
        store.close()


def test_profile_intake_gate_drops_only_clear_incompatibilities() -> None:
    profile = UserProfile(
        label="Data roles", target_titles=["data engineer"], needs_sponsorship=True
    )
    relevant = Job(job_id="1", source="greenhouse", title="Analytics Engineer", url="https://x")
    unrelated = Job(job_id="2", source="greenhouse", title="Product Designer", url="https://x")
    blocked = Job(
        job_id="3", source="greenhouse", title="Data Engineer", url="https://x",
        visa_sponsorship="no",
    )
    senior = Job(
        job_id="4", source="greenhouse", title="Sr. Staff Data Engineer", url="https://x",
    )
    assert _is_profile_candidate(relevant, profile) is True
    assert _is_profile_candidate(unrelated, profile) is False
    assert _is_profile_candidate(blocked, profile) is False
    assert _is_profile_candidate(senior, profile) is False
