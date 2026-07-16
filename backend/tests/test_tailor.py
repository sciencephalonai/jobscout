"""Tests for the local, audited resume-writing integration (no model/network calls)."""

from __future__ import annotations

import pytest

from jobscout import tailor
from jobscout.models import Job, UserProfile


def test_planner_routes_through_shared_failover() -> None:
    # The resume planner must use enrich.chat_json (NVIDIA→DeepSeek 429 failover +
    # circuit breaker), NOT a direct single-provider client call. Toolkit-free guard.
    names = tailor.build_tailored_resume.__code__.co_names
    assert "chat_json" in names
    assert "_get_client" not in names


def _profile() -> UserProfile:
    return UserProfile(label="Jordan Rivers", needs_sponsorship=True)


def _job(**updates: object) -> Job:
    values: dict[str, object] = {
        "job_id": "job-1",
        "source": "greenhouse",
        "title": "Data Engineer",
        "url": "https://example.test/job-1",
        "country": "US",
    }
    values.update(updates)
    return Job(**values)


def test_jd_gate_rejects_explicit_work_authorization_wall() -> None:
    with pytest.raises(tailor.EligibilityError, match="citizenship"):
        tailor.resume_tailoring_gate(_job(citizenship_required=True), _profile())


def test_jd_gate_rejects_senior_role() -> None:
    with pytest.raises(tailor.EligibilityError, match="5\\+ years"):
        tailor.resume_tailoring_gate(_job(yoe_min=5), _profile())


def test_jd_gate_keeps_reach_and_cap_exempt_warnings() -> None:
    warnings = tailor.resume_tailoring_gate(
        _job(yoe_min=3, employer_type="university", cap_exempt="likely"), _profile()
    )
    assert any("Reach role" in warning for warning in warnings)
    assert any("Cap-exempt" in warning for warning in warnings)


def test_tailored_resume_name_includes_candidate_company_role_and_resume() -> None:
    job = _job(company="NVIDIA", title="Machine Learning Intern - 2026")
    name = tailor._safe_output_name(job, {"header": {"name": "Jordan Rivers"}})
    assert name == "Jordan_Rivers_NVIDIA_Machine_Learning_Intern_2026_Resume.docx"


def test_config_validator_permits_only_catalog_references() -> None:
    canonical = {
        "experience": {"acme": {"bullets": {"dashboard": "fact"}}, "globex": {"bullets": {"cloud": "fact"}}},
        "projects": {
            "pipeline": {"variants": {"de": ["fact"]}, "stacks": {"default": "Python"}},
            "vision": {"variants": {"mle": ["fact"]}, "stacks": {"default": "Python"}},
        },
    }
    raw = {
        "preset": "de",
        "experience": [{"id": "acme", "bullets": ["dashboard", "invented"]}, {"id": "globex", "bullets": ["cloud"]}],
        "projects": [{"id": "pipeline", "variant": "de", "stack": "default"}, {"id": "vision", "variant": "mle", "stack": "default"}],
        "notes": ["Data pipeline evidence first"],
    }
    config, notes = tailor._validate_config(raw, canonical, {"de": {}}, "resume.docx")
    assert config["output"] == "resume.docx"
    assert config["experience"][0]["bullets"] == ["dashboard"]
    assert notes == ["Data pipeline evidence first"]


def test_tailoring_prompt_includes_full_profile_job_and_compiled_policy() -> None:
    profile = UserProfile(
        label="Jordan Rivers",
        skills=["python"],
        resume_text="Education\nExample University\nProjects\nBuilt an ETL pipeline.",
    )
    prompt = tailor._tailoring_prompt(
        _job(description="Need Python data pipelines."),
        profile,
        {"presets": ["de"]},
        "PRIMARY V4 SKILL: truthfulness first",
    )
    assert "Need Python data pipelines." in prompt
    assert "Example University" in prompt
    assert "PRIMARY V4 SKILL" in prompt
    assert "ALLOWED CATALOG" in prompt
