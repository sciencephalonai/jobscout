"""Tests for the new enrichment fields in jobscout.enrich.

Covers the _validate coercion of the sponsorship/eligibility fields, the
deterministic cap_exempt derivation from employer_type, and the recruiter-post
heuristic. No network/LLM calls are made.
"""

from __future__ import annotations

from jobscout.enrich import _validate, detect_recruiter_post


def test_security_clearance_valid_value_kept() -> None:
    assert _validate({"security_clearance": "required"})["security_clearance"] == "required"


def test_security_clearance_invalid_defaults_to_unclear() -> None:
    assert _validate({"security_clearance": "top-secret"})["security_clearance"] == "unclear"


def test_citizenship_required_coerced_to_bool() -> None:
    assert _validate({"citizenship_required": True})["citizenship_required"] is True
    assert _validate({"citizenship_required": "yes"})["citizenship_required"] is True
    assert _validate({})["citizenship_required"] is False


def test_employer_type_valid_value_kept() -> None:
    assert _validate({"employer_type": "university"})["employer_type"] == "university"


def test_employer_type_invalid_defaults_to_unclear() -> None:
    assert _validate({"employer_type": "ngo"})["employer_type"] == "unclear"


# ── cap_exempt is derived, never taken from the model ─────────────────────────

def test_cap_exempt_likely_for_university() -> None:
    assert _validate({"employer_type": "university"})["cap_exempt"] == "likely"


def test_cap_exempt_likely_for_government_and_nonprofit_and_hospital() -> None:
    for etype in ("government", "nonprofit", "hospital"):
        assert _validate({"employer_type": etype})["cap_exempt"] == "likely"


def test_cap_exempt_no_for_for_profit() -> None:
    assert _validate({"employer_type": "for_profit"})["cap_exempt"] == "no"


def test_cap_exempt_unknown_for_unclear() -> None:
    assert _validate({"employer_type": "unclear"})["cap_exempt"] == "unknown"


def test_model_cannot_assert_cap_exempt_directly() -> None:
    # Even if the model returns cap_exempt, it is ignored — only employer_type drives it.
    result = _validate({"employer_type": "for_profit", "cap_exempt": "yes"})
    assert result["cap_exempt"] == "no"


# ── Recruiter / aggregator heuristic ──────────────────────────────────────────

def test_aggregator_source_is_recruiter() -> None:
    assert detect_recruiter_post("Acme", "jobspy", "Great role") is True


def test_missing_company_is_recruiter() -> None:
    assert detect_recruiter_post(None, "greenhouse", "desc") is True
    assert detect_recruiter_post("  ", "greenhouse", "desc") is True


def test_recruiter_phrase_in_text() -> None:
    assert detect_recruiter_post(
        "Talent Co", "greenhouse", "We are hiring on behalf of our client"
    ) is True


def test_direct_employer_is_not_recruiter() -> None:
    assert detect_recruiter_post("Stripe", "greenhouse", "Join our payments team") is False
