"""Deep LLM match — send the full job + candidate profile to DeepSeek and get a
nuanced apply/borderline/skip verdict with reasoning.

This is a *second opinion* on top of the deterministic rule-based verdict in
verdict.py. It costs ~$0.001-0.003 per call and runs on-demand only (never
automatically for all jobs).

Results are cached in-process by (job_id, profile_id) so re-opening the same job
does not re-bill DeepSeek; a cache hit is returned with ``"cached": True``.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections import OrderedDict
from hashlib import sha256
from typing import Any

from jobscout.enrich import llm_is_configured
from jobscout.models import Job, UserProfile


def deep_match_fingerprint(job: Job, profile: UserProfile) -> str:
    """Stable hash of everything a deep-match verdict depends on.

    Includes the full profile (⊃ resume_text + active_resume_id + preferences)
    and the job's title/company/description, so ANY change to the resume,
    profile, or job produces a different fingerprint — that's what lets a stored
    score be detected as stale and never shown after the resume changes.
    """
    fingerprint_source = "\n".join([
        profile.model_dump_json(),
        job.title or "",
        job.company or "",
        job.description or "",
    ])
    return sha256(fingerprint_source.encode()).hexdigest()[:20]

log = logging.getLogger(__name__)

_MAX_DESC_CHARS = 12_000
_MAX_RESUME_CHARS = 12_000

# Bounded in-process cache: (job_id, profile_id, evidence fingerprint) -> result
# dict. Including the evidence prevents an edited profile or refreshed JD from
# silently reusing an obsolete LLM judgment.
_CACHE: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
_CACHE_MAX = 512


def _bounded_context(text: str, limit: int) -> str:
    """Retain both the opening context and the requirements/evidence at the end."""
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    marker = "\n\n[... middle omitted for context limit ...]\n\n"
    if limit <= len(marker):
        return value[:limit]
    content_budget = limit - len(marker)
    head = content_budget * 2 // 3
    tail = content_budget - head
    return f"{value[:head]}{marker}{value[-tail:]}"

_SYSTEM_PROMPT = (
    "You are an expert career coach and hiring expert. "
    "Evaluate a job posting against a candidate's profile. "
    "Return ONLY a JSON object — no prose, no markdown fences."
)

_USER_PROMPT_TEMPLATE = """\
Evaluate this job-candidate match and return a JSON object with EXACTLY these keys:
- "verdict": one of "apply", "borderline", "skip"
- "score": integer 0-100 (100 = perfect fit)
- "strengths": array of 2-3 short strings explaining why the candidate is a strong fit
- "gaps": array of 1-3 short strings explaining the main concerns or mismatches
- "summary": one sentence (max 30 words) overall assessment

CANDIDATE PROFILE:
- Target roles: {target_titles}
- Interests: {interests}
- Skills: {skills}
- Seniority target: {seniority_max}
- Years of experience: {yoe_max}
- Needs H-1B sponsorship: {needs_sponsorship}
- Remote preference: {remote_preference}
{resume_section}

JOB POSTING:
- Title: {title}
- Company: {company}
- Source: {source}
- Enriched signals: min years required={yoe_min}, seniority={seniority}, category={category}
- Description:
{description}

Scoring guidance:
- 80-100: Strong match — skills/experience clearly align, apply immediately
- 60-79: Good match — minor gaps, worth applying with tailored cover letter
- 40-59: Borderline — significant gaps or uncertainty, apply selectively
- 0-39: Poor match — fundamental mismatch in skills, seniority, or eligibility

Hard overrides (apply BEFORE scoring; each forces the stated verdict regardless of skill fit):
- If the candidate needs H-1B sponsorship and the job says "no sponsorship" or "US citizens
  only", set verdict to "skip" and score to 0.
{avoid_sections}- If the role demands specialized multi-year large-scale production depth beyond {yoe_max} years
  (e.g. several years deploying large-scale NLP or ranking systems in production) that the resume
  does not evidence, cap the verdict at "borderline" (never "apply").
Only count a skill or domain as a match when the resume genuinely supports it; otherwise treat it
as a gap. Never invent experience the resume does not show.
"""


def compute_deep_match(
    job: Job, profile: UserProfile, store: Any = None
) -> dict[str, Any]:
    """Call the LLM to evaluate job-candidate fit.

    Returns a dict with keys: verdict, score, strengths, gaps, summary, cached.
    Cached in-process AND — when *store* (a RelationalStore) is given — persisted
    to DuckDB keyed by (job, profile, fingerprint), so a score is never re-billed
    and survives restarts. Falls back to a safe default on any error (never raises).
    """
    avoid_sections = ""
    if profile.avoid_role_types:
        avoid_sections += (
            "- If the role is primarily "
            + "; or ".join(profile.avoid_role_types)
            + ", set verdict to \"skip\".\n"
        )
    if profile.avoid_domains:
        avoid_sections += (
            "- If the role centrally requires domain expertise that is clearly absent from the "
            "resume — e.g. " + ", ".join(profile.avoid_domains)
            + " — set verdict to \"skip\".\n"
        )

    fp = deep_match_fingerprint(job, profile)
    key = (job.job_id, profile.id, fp)
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return {**hit, "cached": True}
    # Persistent cache: survives restart so a score is never re-billed. A changed
    # profile/resume/job flips the fingerprint → stored row won't match → miss.
    if store is not None:
        persisted = store.get_deep_match(job.job_id, profile.id, fp)
        if persisted is not None:
            _cache_put(key, persisted)
            return {**persisted, "cached": True}

    if not llm_is_configured():
        return _fallback("Selected LLM API key is not configured")

    # Build resume section — use stored resume text if available, else skills list
    if profile.resume_text and len(profile.resume_text.strip()) > 100:
        resume_section = (
            f"- Resume evidence:\n{_bounded_context(profile.resume_text, _MAX_RESUME_CHARS)}"
        )
    else:
        resume_section = ""  # skills list already included above

    prompt = _USER_PROMPT_TEMPLATE.format(
        avoid_sections=avoid_sections,
        target_titles=", ".join(profile.target_titles) or "not specified",
        interests=", ".join(profile.interests) or "not specified",
        skills=", ".join(profile.skills[:80]) or "not specified",
        seniority_max=profile.seniority_max,
        yoe_max=profile.yoe_max,
        needs_sponsorship=profile.needs_sponsorship,
        remote_preference=profile.remote_preference,
        resume_section=resume_section,
        title=job.title or "Unknown title",
        company=job.company or "Unknown company",
        source=job.source,
        yoe_min=job.yoe_min if job.yoe_min is not None else "not stated",
        seniority=job.seniority or "unclear",
        category=job.category or "other",
        description=_bounded_context(
            job.description or "No description available", _MAX_DESC_CHARS
        ),
    )

    try:
        from jobscout.enrich import chat_json  # shared client + 429 provider failover

        raw = (chat_json(_SYSTEM_PROMPT, prompt) or "").strip()
        data = json.loads(raw)
        result = _validate(data)
        _cache_put(key, result)
        if store is not None:
            # Persisting must never break the response.
            with contextlib.suppress(Exception):
                store.put_deep_match(job.job_id, profile.id, fp, result)
        return result

    except json.JSONDecodeError as exc:
        log.warning("deep_match_json_parse_error job=%s err=%s", job.job_id, exc)
        return _fallback(f"JSON parse error: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.warning("deep_match_error job=%s err=%s", job.job_id, exc)
        return _fallback(str(exc))


def _cache_put(key: tuple[str, str, str], result: dict[str, Any]) -> None:
    """Store a fresh result, evicting the oldest entry past the cap."""
    _CACHE[key] = result
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce the LLM response into a safe, typed result dict."""
    verdict = str(data.get("verdict") or "borderline").lower()
    if verdict not in ("apply", "borderline", "skip"):
        verdict = "borderline"

    try:
        score = int(data.get("score") or 50)
        score = max(0, min(100, score))
    except (TypeError, ValueError):
        score = 50

    strengths = [str(s) for s in (data.get("strengths") or []) if s][:3]
    gaps = [str(g) for g in (data.get("gaps") or []) if g][:3]
    summary = str(data.get("summary") or "")[:200]

    return {
        "verdict": verdict,
        "score": score,
        "strengths": strengths,
        "gaps": gaps,
        "summary": summary,
        "cached": False,
    }


def _fallback(reason: str) -> dict[str, Any]:
    return {
        "verdict": "borderline",
        "score": 50,
        "strengths": [],
        "gaps": [],
        "summary": f"Deep analysis unavailable: {reason}",
        "cached": False,
    }
