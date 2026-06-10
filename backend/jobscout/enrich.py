"""LLM enrichment of a single job via DeepSeek (OpenAI-compatible client).

Extracts structured signals — years-of-experience range, visa-sponsorship
stance, technical skills, seniority level, and an estimated company-size
bucket — from a job's title/company/description.

The model and credentials are pulled from ``jobscout.config.settings`` so there
is a single source of truth, mirroring ``jobscout.embed``. The OpenAI client is
created once as a module-level lazy singleton.

``extract_enrichment`` distinguishes HARD failures from soft ones. A hard
failure — the network/API call raising, an empty response, or an unparseable
response — raises :class:`EnrichmentError` so callers can mark the job
``enrichment_status="failed"`` rather than silently storing blank fields. Soft
issues (a missing or out-of-range individual field) still default sanely and do
NOT raise; the function returns a fully-validated dict in that case.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from jobscout.config import settings

logger = logging.getLogger(__name__)


class EnrichmentError(Exception):
    """Raised on a HARD enrichment failure (network/API error or unparseable response).

    Soft failures — a single missing/invalid field — are handled by coercion to
    safe defaults and do NOT raise.
    """

# Maximum description length sent to the model (cost control).
_MAX_DESCRIPTION_CHARS = 4000
# Cap on the number of skills returned.
_MAX_SKILLS = 15

_VALID_VISA = {"yes", "no", "unclear", "not_mentioned"}
_VALID_SENIORITY = {
    "intern", "junior", "mid", "senior", "staff", "principal", "lead",
    "manager", "director", "vp", "c_level", "unclear",
}
_VALID_SIZE_BUCKETS = {
    "1-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+",
}
_VALID_EMPLOYMENT = {
    "full_time", "part_time", "contract", "internship", "temporary", "unknown",
}


def _safe_defaults() -> dict:
    """Return a fresh copy of the safe-default enrichment dict."""
    return {
        "yoe_min": None,
        "yoe_max": None,
        "visa_sponsorship": "not_mentioned",
        "skills": [],
        "seniority": "unclear",
        "company_size_bucket": None,
        "employment_type": "unknown",
    }


_SYSTEM_PROMPT = (
    "You are a precise information-extraction engine for job postings. "
    "You return ONLY a single JSON object and nothing else."
)

_USER_PROMPT_TEMPLATE = """Extract structured fields from the job posting below.

Return ONLY a JSON object with EXACTLY these keys:
- "yoe_min": integer or null — minimum years of experience the role requires (null if not stated)
- "yoe_max": integer or null — maximum/upper years of experience (null if not stated)
- "visa_sponsorship": one of "yes", "no", "unclear", "not_mentioned"
- "skills": array of strings — concrete technical skills/tools mentioned, lowercased, deduped, at most 15
- "seniority": one of "intern", "junior", "mid", "senior", "staff", "principal", "lead", "manager", "director", "vp", "c_level", "unclear"
- "company_size_bucket": one of "1-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+", or null
- "employment_type": one of "full_time", "part_time", "contract", "internship", "temporary", "unknown" — the role's work arrangement (default "unknown" if not stated)

For "company_size_bucket": estimate the COMPANY's employee headcount bucket from the
company name using your world knowledge. If you genuinely do not recognize the company,
return null. Do NOT guess wildly.

TITLE: {title}
COMPANY: {company}
DESCRIPTION:
{description}
"""


# ---------------------------------------------------------------------------
# Lazy singleton client
# ---------------------------------------------------------------------------

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Return a process-wide singleton OpenAI client pointed at DeepSeek."""
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
        )
    return _client


# ---------------------------------------------------------------------------
# Parsing / coercion helpers
# ---------------------------------------------------------------------------

def _strip_code_fences(text: str) -> str:
    """Remove surrounding markdown code fences if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the opening fence line (e.g. ```json) and the trailing fence.
        lines = stripped.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _coerce_int(value: Any) -> int | None:
    """Coerce a value to int, returning None on failure."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(float(s))
        except ValueError:
            return None
    return None


def _coerce_skills(value: Any) -> list[str]:
    """Normalise the skills field: lowercase, dedupe, cap length."""
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        skill = item.strip().lower()
        if not skill or skill in seen:
            continue
        seen.add(skill)
        out.append(skill)
        if len(out) >= _MAX_SKILLS:
            break
    return out


def _validate(raw: dict[str, Any]) -> dict:
    """Validate/coerce a parsed model response into a safe enrichment dict."""
    result = _safe_defaults()

    result["yoe_min"] = _coerce_int(raw.get("yoe_min"))
    result["yoe_max"] = _coerce_int(raw.get("yoe_max"))

    visa = raw.get("visa_sponsorship")
    if isinstance(visa, str) and visa in _VALID_VISA:
        result["visa_sponsorship"] = visa

    result["skills"] = _coerce_skills(raw.get("skills"))

    seniority = raw.get("seniority")
    if isinstance(seniority, str) and seniority in _VALID_SENIORITY:
        result["seniority"] = seniority

    bucket = raw.get("company_size_bucket")
    if isinstance(bucket, str) and bucket in _VALID_SIZE_BUCKETS:
        result["company_size_bucket"] = bucket

    emp = raw.get("employment_type")
    if isinstance(emp, str) and emp in _VALID_EMPLOYMENT:
        result["employment_type"] = emp

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_enrichment(
    title: str,
    company: str | None,
    description: str | None,
) -> dict:
    """Extract structured enrichment fields for a single job via DeepSeek.

    Args:
        title:       Job title.
        company:     Company name (may be None).
        description: Full job description (truncated to ~4000 chars before send).

    Returns:
        A dict with exactly these keys: ``yoe_min`` (int|None), ``yoe_max``
        (int|None), ``visa_sponsorship`` (str), ``skills`` (list[str]),
        ``seniority`` (str), ``company_size_bucket`` (str|None).

    Raises:
        EnrichmentError: on a HARD failure — the API call raising, an empty
            response, or an unparseable / non-object response. Soft issues
            (missing/invalid individual fields) are coerced to safe defaults and
            do NOT raise.
    """
    truncated_description = (description or "")[:_MAX_DESCRIPTION_CHARS]
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        title=title or "",
        company=company or "(unknown)",
        description=truncated_description or "(no description provided)",
    )

    try:
        client = _get_client()
        completion = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = completion.choices[0].message.content
    except Exception as exc:
        logger.warning("DeepSeek enrichment call failed: %s", exc)
        raise EnrichmentError(f"DeepSeek enrichment call failed: {exc}") from exc

    if not content:
        logger.warning("DeepSeek enrichment returned empty content")
        raise EnrichmentError("DeepSeek enrichment returned empty content")

    try:
        parsed = json.loads(_strip_code_fences(content))
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to parse DeepSeek enrichment JSON: %s", exc)
        raise EnrichmentError(f"Failed to parse DeepSeek enrichment JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        logger.warning("DeepSeek enrichment JSON was not an object: %r", type(parsed))
        raise EnrichmentError(
            f"DeepSeek enrichment JSON was not an object: {type(parsed)!r}"
        )

    return _validate(parsed)
