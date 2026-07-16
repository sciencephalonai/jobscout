"""Audited, job-specific DOCX resumes built with the local resume-writing skill.

This module deliberately delegates document construction and auditing to the
configured ``resume-writing`` toolkit. JobScout selects an evidence-backed
configuration with the active OpenAI-compatible LLM (DeepSeek or NVIDIA), then
the toolkit's *single* builder and hard auditor produce the DOCX. No resume
facts, skills, or metrics are invented by JobScout.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobscout.config import settings
from jobscout.enrich import (
    EnrichmentError,
    _strip_code_fences,
    active_llm_configuration,
    chat_json,
    llm_is_configured,
)
from jobscout.models import Job, UserProfile

log = logging.getLogger(__name__)

_MAX_RESUME_CONTEXT_CHARS = 30_000
_MAX_JOB_CONTEXT_CHARS = 24_000
_MAX_PRIMARY_SKILL_CHARS = 14_000
_MAX_LEGACY_GUARDRAILS_CHARS = 4_000
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9]+")
_BLOCKED_WORK_AUTH = re.compile(
    r"\b(?:itar|ear|export control|u\.?s\.? person|citizen(?:ship)?|"
    r"green card|required clearance|security clearance)\b",
    re.IGNORECASE,
)


class TailoringError(Exception):
    """The resume writer cannot safely generate a document."""


class EligibilityError(TailoringError):
    """The job failed the resume-writing skill's hard JD gate."""


@dataclass(frozen=True)
class TailoredResume:
    """A completed, audited resume plus transparent tailoring context."""

    path: Path
    filename: str
    notes: list[str]
    warnings: list[str]
    provider: str
    model: str


def _toolkit_root() -> Path:
    root = Path(settings.resume_writer_dir).expanduser()
    required = (root / "data" / "canonical.json", root / "scripts" / "build_resume.js", root / "scripts" / "audit.py")
    if not all(path.is_file() for path in required):
        raise TailoringError(
            "The resume-writing toolkit is unavailable. Configure RESUME_WRITER_DIR to the supplied skill folder."
        )
    return root


def _bounded_text(text: str, limit: int) -> str:
    """Keep complete normal-size inputs, retaining both ends of very long ones."""
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n\n[... truncated only for model context ...]\n\n{text[-tail:]}"


def _tailoring_policy(root: Path) -> str:
    """Compile the two local skills into a conflict-safe planner policy.

    v4 owns canonical facts, formatting, builder and audit. The earlier skill is
    narrowed to its enduring keyword and truthfulness guardrails, because its
    older layout and GPA instructions must never override v4.
    """
    try:
        primary = (root / "SKILL.md").read_text(encoding="utf-8")
    except OSError as exc:
        raise TailoringError(f"Could not read the primary resume-writing skill: {exc}") from exc

    legacy_guardrails = ""
    legacy_path = Path(settings.resume_writer_legacy_dir).expanduser() / "SKILL.md"
    try:
        legacy = legacy_path.read_text(encoding="utf-8")
        marker = "## TRUTHFULNESS GUARDRAILS"
        if marker in legacy:
            legacy_guardrails = legacy.split(marker, 1)[1]
    except OSError:
        # The primary skill is independently safe. A missing optional overlay
        # must not block a local user from building a document.
        log.warning("legacy_resume_skill_unavailable path=%s", legacy_path)

    return (
        "POLICY PRECEDENCE\n"
        "The primary v4 resume-writing skill is authoritative for candidate facts, canonical data, "
        "eligibility, formatting, the Node DOCX builder, and the Python audit. The legacy skill "
        "contributes only complementary keyword and truthfulness guardrails. If they conflict, "
        "follow v4 and never change canonical facts.\n\n"
        f"PRIMARY V4 SKILL:\n{_bounded_text(primary, _MAX_PRIMARY_SKILL_CHARS)}\n\n"
        f"LEGACY COMPLEMENTARY GUARDRAILS:\n{_bounded_text(legacy_guardrails, _MAX_LEGACY_GUARDRAILS_CHARS)}"
    )


def tailored_resume_path(profile_id: str, job_id: str) -> Path:
    """Return the only permitted local output path for a tailored DOCX."""
    return Path(settings.tailored_resume_storage_dir) / profile_id / f"{job_id}.docx"


def resume_tailoring_gate(job: Job, profile: UserProfile) -> list[str]:
    """Apply the skill's deterministic JD gate before spending any model tokens."""
    country = (job.country or "").strip().lower()
    if country and country not in {"us", "usa", "united states", "united states of america"}:
        raise EligibilityError("This tailored-resume workflow is restricted to US-based roles.")

    restrictions = " ".join(filter(None, (job.work_auth_required, job.restrictions, job.description)))
    if job.citizenship_required or job.security_clearance == "required" or _BLOCKED_WORK_AUTH.search(restrictions):
        raise EligibilityError("This role has a citizenship, clearance, ITAR/EAR, or export-control requirement.")
    if profile.needs_sponsorship and job.visa_sponsorship == "no":
        raise EligibilityError("This role explicitly does not sponsor work authorization.")
    if job.yoe_min is not None and job.yoe_min >= 5:
        raise EligibilityError("This role requires 5+ years of experience and fails the early-career gate.")

    warnings: list[str] = []
    if job.yoe_min is not None and job.yoe_min >= 2:
        warnings.append(f"Reach role: the posting asks for {job.yoe_min}+ years; document stays honest.")
    if job.employment_type in {"contract", "temporary"}:
        warnings.append("Contract/temporary role: confirm OPT employment eligibility with your DSO before accepting.")
    if job.cap_exempt in {"yes", "likely"} or job.employer_type in {"university", "hospital", "nonprofit", "government"}:
        warnings.append("Cap-exempt or likely cap-exempt employer: prioritize this role if the posting allows sponsorship.")
    return warnings


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TailoringError(f"Could not read resume-writing data: {exc}") from exc


def _owner_is_safe(profile: UserProfile, canonical: dict[str, Any]) -> bool:
    """Do not apply the configured candidate's private canonical facts to another candidate."""
    owner = str((canonical.get("header") or {}).get("name") or "").strip().lower()
    if not owner:
        return False
    candidate = " ".join((profile.label or "", profile.resume_text or "")).lower()
    return owner in candidate


def _job_context(job: Job) -> str:
    return "\n".join(
        [
            f"TITLE: {job.title}",
            f"COMPANY: {job.company or 'Unknown'}",
            f"LOCATION: {job.location_raw or 'Not stated'}; country: {job.country or 'Not stated'}; work mode: {job.remote_mode}",
            f"SENIORITY: {job.seniority}; minimum years: {job.yoe_min if job.yoe_min is not None else 'not stated'}",
            f"SKILLS: {', '.join(job.skills)}",
            f"WORK AUTHORIZATION: {job.work_auth_required or 'Not stated'}; restrictions: {job.restrictions or 'Not stated'}",
            f"APPLICATION URL: {job.url}",
            "DESCRIPTION:",
            _bounded_text(job.description or "No description available", _MAX_JOB_CONTEXT_CHARS),
        ]
    )


def _option_catalog(canonical: dict[str, Any], presets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Small allowed-ID catalog for the LLM; actual facts remain in canonical.json."""
    return {
        "presets": sorted(presets),
        "experience": {
            key: sorted((value.get("bullets") or {}).keys())
            for key, value in (canonical.get("experience") or {}).items()
        },
        "projects": {
            key: {
                "variants": sorted((value.get("variants") or {}).keys()),
                "stacks": sorted((value.get("stacks") or {}).keys()),
            }
            for key, value in (canonical.get("projects") or {}).items()
        },
    }


def _tailoring_prompt(job: Job, profile: UserProfile, catalog: dict[str, Any], policy: str) -> str:
    profile_context = {
        "label": profile.label,
        "target_titles": profile.target_titles,
        "skills": profile.skills,
        "years_of_experience": profile.yoe_max,
        "needs_sponsorship": profile.needs_sponsorship,
        "remote_preference": profile.remote_preference,
        "countries": profile.countries,
        "prefer_cap_exempt": profile.prefer_cap_exempt,
        "excluded_companies": profile.excluded_companies,
        "resume_sections": [section.model_dump() for section in profile.resume_sections],
        "resume_text": _bounded_text(profile.resume_text or "", _MAX_RESUME_CONTEXT_CHARS),
    }
    return f"""Create a safe resume-builder configuration for this job.

You are selecting existing, verified content only. Never write a new bullet, metric, skill, tool, company, or claim. The Node builder will resolve the selected IDs from a private canonical source of truth.

{policy}

Return ONLY a JSON object with these exact keys:
- preset: one allowed preset
- experience: 2 or 3 items, each {{\"id\": allowed experience id, \"bullets\": [allowed bullet IDs]}}
- projects: 2 to 5 items, each {{\"id\": allowed project id, \"variant\": allowed variant, \"stack\": allowed stack}}
- notes: 2 to 6 short factual notes describing the choices, not claims for the resume

Use the job's vocabulary only where the selected canonical evidence supports it. Favor relevant ordering. Do not add any keys and do not use literal bullets.

ALLOWED CATALOG:
{json.dumps(catalog, ensure_ascii=False)}

CANDIDATE PROFILE (full extracted resume included for context):
{json.dumps(profile_context, ensure_ascii=False)}

JOB:
{_job_context(job)}
"""


def _validate_config(raw: Any, canonical: dict[str, Any], presets: dict[str, dict[str, Any]], filename: str) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(raw, dict):
        raise TailoringError("The resume planner did not return a JSON object.")
    preset = raw.get("preset")
    if preset not in presets:
        raise TailoringError("The resume planner selected an unknown preset.")

    experience: list[dict[str, Any]] = []
    seen_experience: set[str] = set()
    for item in raw.get("experience") or []:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        source = (canonical.get("experience") or {}).get(identifier)
        bullets = item.get("bullets")
        if not isinstance(identifier, str) or not source or not isinstance(bullets, list):
            continue
        valid_bullets = (source.get("bullets") or {})
        chosen = [bullet for bullet in bullets if isinstance(bullet, str) and bullet in valid_bullets]
        if identifier not in seen_experience and chosen:
            experience.append({"id": identifier, "bullets": chosen[:4]})
            seen_experience.add(identifier)
    if len(experience) < 2:
        raise TailoringError("The resume planner did not select enough verified experience evidence.")

    projects: list[dict[str, Any]] = []
    seen_projects: set[str] = set()
    for item in raw.get("projects") or []:
        if not isinstance(item, dict):
            continue
        identifier, variant, stack = item.get("id"), item.get("variant"), item.get("stack")
        source = (canonical.get("projects") or {}).get(identifier)
        if (
            not isinstance(identifier, str)
            or not source
            or not isinstance(variant, str)
            or variant not in (source.get("variants") or {})
            or not isinstance(stack, str)
            or stack not in (source.get("stacks") or {})
        ):
            continue
        if identifier not in seen_projects:
            projects.append({"id": identifier, "variant": variant, "stack": stack})
            seen_projects.add(identifier)
    if len(projects) < 2:
        raise TailoringError("The resume planner did not select enough verified project evidence.")

    notes = [str(note).strip() for note in (raw.get("notes") or []) if str(note).strip()]
    return {"preset": preset, "output": filename, "experience": experience, "projects": projects}, notes[:6]


def _run_checked(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> None:
    try:
        result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TailoringError(f"Resume builder could not start: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown tool error").strip()
        raise TailoringError(f"Resume audit/build failed: {detail[-1200:]}")


def _runtime_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    candidates = [settings.resume_writer_node_path, str(root / "scripts" / "node_modules"), str(Path.home() / "node_modules")]
    paths = [candidate for candidate in candidates if candidate and Path(candidate).exists()]
    if paths:
        env["NODE_PATH"] = os.pathsep.join(paths + ([env["NODE_PATH"]] if env.get("NODE_PATH") else []))
    return env


def _safe_output_name(job: Job, canonical: dict[str, Any]) -> str:
    header = canonical.get("header") or {}
    owner = _SAFE_FILENAME.sub("_", str(header.get("name") or "Resume")).strip("_") or "Resume"
    company = _SAFE_FILENAME.sub("_", job.company or "Company").strip("_") or "Company"
    role = _SAFE_FILENAME.sub("_", job.title or "Role").strip("_") or "Role"
    stem = f"{owner}_{company}_{role}_Resume"
    return f"{stem[:175].rstrip('_')}.docx"


def tailored_resume_filename(job: Job) -> str:
    """Human-readable download name, even when a prior toolkit path disappeared."""
    try:
        return _safe_output_name(job, _load_json(_toolkit_root() / "data" / "canonical.json"))
    except TailoringError:
        company = _SAFE_FILENAME.sub("_", job.company or "Company").strip("_") or "Company"
        role = _SAFE_FILENAME.sub("_", job.title or "Role").strip("_") or "Role"
        stem = f"Candidate_{company}_{role}_Resume"
        return f"{stem[:175].rstrip('_')}.docx"


def build_tailored_resume(job: Job, profile: UserProfile) -> TailoredResume:
    """Plan, build, audit, and retain one truthful DOCX for a saved job/profile."""
    warnings = resume_tailoring_gate(job, profile)
    root = _toolkit_root()
    canonical = _load_json(root / "data" / "canonical.json")
    if not _owner_is_safe(profile, canonical):
        owner = (canonical.get("header") or {}).get("name") or "the configured candidate"
        raise TailoringError(f"The configured resume-writing toolkit is private to {owner}; choose that candidate profile before tailoring.")
    if not llm_is_configured():
        raise TailoringError("Select an LLM provider and add its API key before tailoring a resume.")

    presets = {
        path.stem: _load_json(path)
        for path in (root / "presets").glob("*.json")
    }
    if not presets:
        raise TailoringError("The resume-writing toolkit has no presets.")
    policy = _tailoring_policy(root)

    try:
        # Route through the shared chat_json so tailoring gets the same NVIDIA→DeepSeek
        # 429 failover + circuit breaker as every other LLM task (it used to call the
        # provider directly and hard-fail on a primary-provider 429).
        content = chat_json(
            "You are a careful resume configuration selector. Return only valid JSON.",
            _tailoring_prompt(job, profile, _option_catalog(canonical, presets), policy),
        )
        raw = json.loads(_strip_code_fences(content or ""))
    except (EnrichmentError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise TailoringError(f"Resume planner returned unusable JSON: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - expose a bounded request failure to the API
        raise TailoringError(f"Resume planner request failed: {exc}") from exc

    output_name = _safe_output_name(job, canonical)
    config, notes = _validate_config(raw, canonical, presets, output_name)
    output_path = tailored_resume_path(profile.id, job.job_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    env = _runtime_env(root)

    with tempfile.TemporaryDirectory(prefix="jobscout-tailor-") as temp_dir:
        config_path = Path(temp_dir) / "resume-config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        staging = Path(temp_dir) / "out"
        _run_checked(["node", str(root / "scripts" / "build_resume.js"), "--config", str(config_path), "--out", str(staging)], cwd=root, env=env, timeout=45)
        built = staging / output_name
        if not built.is_file():
            raise TailoringError("Resume builder finished without producing the expected DOCX.")
        _run_checked(["python3", str(root / "scripts" / "audit.py"), str(built), "--render"], cwd=root, env=env, timeout=75)
        shutil.copy2(built, output_path)

    provider, _key, model = active_llm_configuration()
    log.info("tailored_resume_built profile=%s job=%s provider=%s model=%s", profile.id, job.job_id, provider, model)
    return TailoredResume(path=output_path, filename=output_name, notes=notes, warnings=warnings, provider=provider, model=model)
