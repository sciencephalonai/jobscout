"""LLM enrichment of a single job via a selectable OpenAI-compatible client.

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
import re
import time
from typing import Any, Literal

from openai import OpenAI

from jobscout.config import settings
from jobscout.normalize import normalize_text
from jobscout.source_intelligence import source_kind

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
# Bound a single provider stall so one new job cannot freeze the sequential
# ingestion pipeline for the OpenAI client's much longer default timeout.
_LLM_TIMEOUT_SECONDS = 45.0
# The OpenAI SDK retries 429/5xx with exponential backoff + jitter. Parallel
# ingestion bursts enrichment calls, so a single retry regularly lost jobs to
# provider rate limits (they parked as enrichment_status='failed').
_LLM_MAX_RETRIES = 4

_VALID_VISA = {"yes", "no", "unclear", "not_mentioned"}
_VALID_SENIORITY = {
    "intern", "junior", "mid", "senior", "staff", "principal", "lead",
    "manager", "director", "vp", "c_level", "unclear",
}
_VALID_SIZE_BUCKETS = {
    "1-50", "51-200", "201-500", "501-1000", "1001-5000", "5000+",
}
_VALID_CLEARANCE = {"required", "preferred", "none", "unclear"}
_VALID_EMPLOYER_TYPE = {
    "university", "hospital", "nonprofit", "government", "for_profit", "unclear",
}

# Employer types that are typically H-1B cap-exempt (or affiliated). Used to
# derive cap_exempt deterministically — the model is NOT asked to assert it,
# mirroring the rule "only mark cap_exempt when clear from employer type".
_CAP_EXEMPT_LIKELY = {"university", "government", "nonprofit", "hospital"}


def derive_cap_exempt(employer_type: str) -> Literal["yes", "likely", "no", "unknown"]:
    """Map an employer_type to a cap_exempt stance (deterministic, no LLM).

    Cap-exempt status is only ever "likely" here; a definitive "yes" requires
    human/verified confirmation. Used both during LLM validation and when a
    curated adapter stamps employer_type from config.
    """
    if employer_type in _CAP_EXEMPT_LIKELY:
        return "likely"
    if employer_type == "for_profit":
        return "no"
    return "unknown"


def _safe_defaults() -> dict:
    """Return a fresh copy of the safe-default enrichment dict."""
    return {
        "yoe_min": None,
        "yoe_max": None,
        "visa_sponsorship": "not_mentioned",
        "skills": [],
        "seniority": "unclear",
        "company_size_bucket": None,
        "security_clearance": "unclear",
        "citizenship_required": False,
        "employer_type": "unclear",
        "cap_exempt": "unknown",
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
- "security_clearance": one of "required", "preferred", "none", "unclear" — does the role require a US security clearance?
- "citizenship_required": boolean — true ONLY if the posting requires US citizenship, permanent residency, US Person status, or ITAR/EAR/export-control eligibility
- "employer_type": one of "university", "hospital", "nonprofit", "government", "for_profit", "unclear"

For "company_size_bucket": estimate the COMPANY's employee headcount bucket from the
company name using your world knowledge. If you genuinely do not recognize the company,
return null. Do NOT guess wildly.

For "employer_type": classify the hiring organization. Use "for_profit" for normal
private companies. Only use "university"/"hospital"/"nonprofit"/"government" when the
employer clearly is one. If unsure, return "unclear".

For "visa_sponsorship": use "no" ONLY when the posting explicitly refuses to sponsor or
demands permanent/indefinite authorization — e.g. "no visa sponsorship", "unable to
sponsor", "must be authorized to work without sponsorship now or in the future", "must
have permanent work authorization". Two things that are NOT "no" on their own: (1) a
question asking whether the candidate will need future sponsorship, and (2) a statement
that the candidate must be "currently authorized to work in the US" — for both, use
"not_mentioned" or "unclear" unless it is paired with an explicit refusal to sponsor or a
permanent-authorization requirement.

TITLE: {title}
COMPANY: {company}
DESCRIPTION:
{description}
"""


# ---------------------------------------------------------------------------
# Lazy singleton client
# ---------------------------------------------------------------------------

_client: OpenAI | None = None
_client_signature: tuple[str, str, str] | None = None

SUPPORTED_LLM_PROVIDERS = frozenset({"deepseek", "nvidia"})


def active_llm_configuration() -> tuple[str, str, str]:
    """Return the active ``(provider, api_key, model)`` without exposing it.

    NVIDIA's hosted endpoint implements the OpenAI chat-completions contract, so
    the existing ``openai`` client is the smallest and most reliable integration
    surface. A LangChain wrapper would be an equivalent client, not extra model
    capability, and would add an unnecessary runtime dependency.
    """
    provider = (settings.llm_provider or "deepseek").strip().lower()
    # A selected provider without a key must not disable LLM features when the
    # other provider IS configured — NVIDIA is optional; DeepSeek is the
    # workhorse fallback (missing-key here, 429 at call time).
    if provider == "nvidia":
        if not settings.nvidia_api_key and settings.deepseek_api_key:
            return "deepseek", settings.deepseek_api_key, settings.deepseek_model
        return "nvidia", settings.nvidia_api_key, settings.nvidia_model
    if not settings.deepseek_api_key and settings.nvidia_api_key:
        return "nvidia", settings.nvidia_api_key, settings.nvidia_model
    return "deepseek", settings.deepseek_api_key, settings.deepseek_model


# Circuit breaker: once the primary provider 429s, send calls straight to the
# fallback for a cooldown window instead of paying the full retry/backoff tax
# (~1 min/job against an exhausted free tier) on every single job.
_PRIMARY_RATE_LIMIT_COOLDOWN_S = 900.0
_primary_rate_limited_until: float = 0.0


def fallback_llm_configuration() -> tuple[str, str, str] | None:
    """The OTHER provider's ``(provider, api_key, model)``, if it has a key.

    Lets enrichment survive a rate-limited/exhausted primary (e.g. NVIDIA's
    free tier) by retrying once on the alternate provider instead of parking
    jobs as ``enrichment_status='failed'``.
    """
    primary, _key, _model = active_llm_configuration()
    if primary == "nvidia" and settings.deepseek_api_key:
        return "deepseek", settings.deepseek_api_key, settings.deepseek_model
    if primary == "deepseek" and settings.nvidia_api_key:
        return "nvidia", settings.nvidia_api_key, settings.nvidia_model
    return None


def llm_is_configured() -> bool:
    """Whether the selected provider has a configured key."""
    _provider, api_key, _model = active_llm_configuration()
    return bool(api_key)


def _get_client() -> OpenAI:
    """Return a singleton client, rebuilding it if the provider changes."""
    global _client, _client_signature
    provider, api_key, _model = active_llm_configuration()
    base_url = settings.nvidia_base_url if provider == "nvidia" else settings.deepseek_base_url
    signature = (provider, api_key, base_url)
    if _client is None or _client_signature != signature:
        _client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=_LLM_TIMEOUT_SECONDS,
            max_retries=_LLM_MAX_RETRIES,
        )
        _client_signature = signature
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

    clearance = raw.get("security_clearance")
    if isinstance(clearance, str) and clearance in _VALID_CLEARANCE:
        result["security_clearance"] = clearance

    result["citizenship_required"] = bool(raw.get("citizenship_required"))

    employer_type = raw.get("employer_type")
    if isinstance(employer_type, str) and employer_type in _VALID_EMPLOYER_TYPE:
        result["employer_type"] = employer_type

    # Derive cap_exempt deterministically from employer_type — never let the
    # model assert it directly.
    result["cap_exempt"] = derive_cap_exempt(result["employer_type"])

    return result


# ---------------------------------------------------------------------------
# Recruiter / aggregator detection (heuristic, no LLM)
# ---------------------------------------------------------------------------

# Strong signals in the *company name* that the named poster is a staffing or
# recruiting business rather than the end employer.  These deliberately do not
# run against the full description: ordinary employers routinely mention their
# recruiting team, accommodations, agency-submission policy, or an ATS URL.
_STAFFING_COMPANY_RE = re.compile(
    r"\b(?:staffing|recruit(?:ing|ment)|talent solutions?|executive search|"
    r"employment agency|placement services?|headhunt(?:ing|ers?)?|rpo)\b",
    re.IGNORECASE,
)
_KNOWN_STAFFING_COMPANIES = {
    "adecco",
    "cybercoders",
    "insight global",
    "jobot",
    "kforce",
    "manpower",
    "manpowergroup",
    "motion recruitment",
    "randstad",
    "robert half",
    "teksystems",
}

# Description text must explicitly say that the posting represents a client.
# Word-boundary regexes avoid the former substring bug where ``rpo`` matched
# inside ordinary URLs such as ``.../corporate-responsibility``.
_CLIENT_WRAPPER_RE = re.compile(
    r"\b(?:"
    r"on behalf of (?:our|a|the) client|"
    r"(?:our|a|the) client (?:is|has|seeks|is seeking|is looking)|"
    r"(?:for|with) (?:one of )?our clients|"
    r"client of ours|confidential client|direct client|end client"
    r")\b",
    re.IGNORECASE,
)


def detect_recruiter_post(
    company: str | None,
    source: str,
    description: str | None,
) -> bool:
    """Heuristically flag recruiter/aggregator postings (vs. direct employers).

    Pure string heuristic — no LLM call. Used so the verdict layer can prefer
    direct-employer postings and treat unnamed-client recruiter wrappers with
    skepticism.
    """
    # Discovery aggregators and scrapers are not canonical employer postings.
    if source_kind(source) in {"aggregator", "scraper"}:
        return True
    if not company or not company.strip():
        return True  # hidden/unnamed end employer

    normalized_company = normalize_text(company)
    if (
        normalized_company in _KNOWN_STAFFING_COMPANIES
        or _STAFFING_COMPANY_RE.search(normalized_company)
    ):
        return True

    return bool(_CLIENT_WRAPPER_RE.search(description or ""))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chat_json(system_prompt: str, user_prompt: str) -> str | None:
    """One JSON-mode chat completion, with provider failover.

    Every LLM task in the app (enrichment, deep match, resume parsing/structuring,
    bullet polish, tailoring) goes through here so they ALL share the same
    resilience: the configured primary first, an automatic switch to the other
    provider on a 429, and a short circuit-breaker so an exhausted free tier
    doesn't tax every subsequent call.
    """

    def _complete(client: OpenAI, model: str) -> str | None:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return completion.choices[0].message.content

    def _fallback_client() -> tuple[OpenAI, str] | None:
        fallback = fallback_llm_configuration()
        if fallback is None:
            return None
        fb_provider, fb_key, fb_model = fallback
        fb_base = (
            settings.nvidia_base_url if fb_provider == "nvidia" else settings.deepseek_base_url
        )
        return OpenAI(
            api_key=fb_key, base_url=fb_base,
            timeout=_LLM_TIMEOUT_SECONDS, max_retries=_LLM_MAX_RETRIES,
        ), fb_model

    global _primary_rate_limited_until
    provider, api_key, model = active_llm_configuration()
    if not api_key:
        raise RuntimeError(f"{provider} API key is not configured")

    if time.monotonic() < _primary_rate_limited_until:
        fb = _fallback_client()
        if fb is not None:
            return _complete(*fb)  # primary recently rate-limited

    try:
        return _complete(_get_client(), model)
    except Exception as exc:  # noqa: BLE001
        fb = _fallback_client() if "429" in str(exc) else None
        if fb is None:
            raise
        _primary_rate_limited_until = time.monotonic() + _PRIMARY_RATE_LIMIT_COOLDOWN_S
        logger.warning(
            "LLM %s rate-limited — using the alternate provider and pausing the primary for %.0fs",
            provider, _PRIMARY_RATE_LIMIT_COOLDOWN_S,
        )
        return _complete(*fb)


def extract_enrichment(
    title: str,
    company: str | None,
    description: str | None,
) -> dict:
    """Extract structured enrichment fields for a single job via the active LLM.

    Args:
        title:       Job title.
        company:     Company name (may be None).
        description: Full job description (truncated to ~4000 chars before send).

    Returns:
        A dict with exactly these keys: ``yoe_min`` (int|None), ``yoe_max``
        (int|None), ``visa_sponsorship`` (str), ``skills`` (list[str]),
        ``seniority`` (str), ``company_size_bucket`` (str|None),
        ``security_clearance`` (str), ``citizenship_required`` (bool),
        ``employer_type`` (str), ``cap_exempt`` (str, derived from employer_type).

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
        content = chat_json(_SYSTEM_PROMPT, user_prompt)
    except Exception as exc:
        logger.warning("LLM enrichment call failed: %s", exc)
        raise EnrichmentError(f"LLM enrichment call failed: {exc}") from exc

    if not content:
        logger.warning("LLM enrichment returned empty content")
        raise EnrichmentError("LLM enrichment returned empty content")

    try:
        parsed = json.loads(_strip_code_fences(content))
    except (ValueError, TypeError) as exc:
        logger.warning("Failed to parse LLM enrichment JSON: %s", exc)
        raise EnrichmentError(f"Failed to parse LLM enrichment JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        logger.warning("DeepSeek enrichment JSON was not an object: %r", type(parsed))
        raise EnrichmentError(
            f"DeepSeek enrichment JSON was not an object: {type(parsed)!r}"
        )

    return _validate(parsed)
