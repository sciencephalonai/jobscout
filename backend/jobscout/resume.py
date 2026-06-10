"""Resume ingestion: extract text from a dropped file + parse it into a UserProfile.

Two pure-ish steps:
  1. ``extract_resume_text(filename, data)`` — pull plain text out of a PDF / DOCX /
     TXT / JSON / arbitrary upload. No LLM.
  2. ``parse_resume_to_profile(text, label)`` — one DeepSeek call that extracts the
     candidate's REAL skills / years / target titles into a :class:`UserProfile`.

Truthfulness rule (borrowed from the resume-writing skill, applied generically): the
parser extracts only what the resume supports and never invents skills. Downstream,
a JD keyword counts as a match only if it is in this extracted skill set; everything
else is surfaced as a gap.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

from jobscout.config import settings
from jobscout.enrich import EnrichmentError, _get_client, _strip_code_fences
from jobscout.models import UserProfile

logger = logging.getLogger(__name__)

_MAX_RESUME_CHARS = 12_000  # cap text sent to the model (cost control)
_VALID_SENIORITY = {
    "intern", "junior", "mid", "senior", "staff", "principal", "lead",
    "manager", "director", "vp", "c_level",
}


# ---------------------------------------------------------------------------
# 1. Text extraction (no LLM)
# ---------------------------------------------------------------------------

def extract_resume_text(filename: str, data: bytes) -> str:
    """Extract plain text from a resume upload, dispatching on file extension.

    Supports .pdf (pypdf), .docx (python-docx), .txt/.md (decode), .json (flatten
    string values). Anything else falls back to a lenient utf-8 decode, so a
    user can drop "anything" with text in it.
    """
    name = (filename or "").lower()
    try:
        if name.endswith(".pdf"):
            return _extract_pdf(data)
        if name.endswith(".docx"):
            return _extract_docx(data)
        if name.endswith(".json"):
            return _extract_json(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("structured extract failed for %s (%s) — falling back to decode", filename, exc)
    # .txt / .md / unknown → lenient decode.
    return data.decode("utf-8", errors="ignore").strip()


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = [(page.extract_text() or "") for page in reader.pages]
    return "\n".join(parts).strip()


def _extract_docx(data: bytes) -> str:
    import docx

    document = docx.Document(io.BytesIO(data))
    return "\n".join(p.text for p in document.paragraphs).strip()


def _extract_json(data: bytes) -> str:
    """Flatten a JSON resume into a text blob (all string/number leaf values)."""
    obj = json.loads(data.decode("utf-8", errors="ignore"))
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                out.append(str(k))
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif node is not None:
            out.append(str(node))

    walk(obj)
    return " ".join(out).strip()


# ---------------------------------------------------------------------------
# 2. Resume → UserProfile (one DeepSeek call)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a precise resume parser. You return ONLY a single JSON object, no prose."
)

_USER_PROMPT = """Extract a candidate profile from the resume below.

Return ONLY a JSON object with EXACTLY these keys:
- "skills": array of concrete technical skills/tools the resume ACTUALLY shows (lowercased,
  deduped, max 40). Do NOT invent skills the resume does not support.
- "target_titles": array of role titles this candidate is a fit for, in their own words
  (e.g. "data scientist", "machine learning engineer"), max 8.
- "yoe_max": integer — total years of professional experience shown (0 for new grad / student).
- "seniority_max": one of "intern","junior","mid","senior","staff","principal","lead",
  "manager","director","vp","c_level".
- "needs_sponsorship": boolean — true if the resume suggests the person needs visa sponsorship
  (e.g. international student, F-1/OPT, "authorized with sponsorship"). If unclear, use true
  (the safer default for surfacing more roles).

Extract only what the resume supports. Never invent skills or inflate experience.

RESUME:
{text}
"""


def parse_resume_to_profile(text: str, label: str) -> UserProfile:
    """Parse resume text into a saved-ready :class:`UserProfile` via one DeepSeek call.

    Raises:
        EnrichmentError: if DeepSeek is unconfigured or the call/parse hard-fails.
    """
    if not settings.deepseek_api_key:
        raise EnrichmentError("DEEPSEEK_API_KEY not configured — cannot parse resume.")
    snippet = (text or "")[:_MAX_RESUME_CHARS]
    if not snippet.strip():
        raise EnrichmentError("Empty resume text — nothing to parse.")

    try:
        completion = _get_client().chat.completions.create(
            model=settings.deepseek_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _USER_PROMPT.format(text=snippet)},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = completion.choices[0].message.content
    except Exception as exc:  # noqa: BLE001
        raise EnrichmentError(f"Resume parse call failed: {exc}") from exc
    if not content:
        raise EnrichmentError("Resume parse returned empty content.")
    try:
        raw = json.loads(_strip_code_fences(content))
    except (ValueError, TypeError) as exc:
        raise EnrichmentError(f"Resume parse returned unparseable JSON: {exc}") from exc

    skills = [str(s).strip().lower() for s in (raw.get("skills") or []) if str(s).strip()]
    titles = [str(t).strip() for t in (raw.get("target_titles") or []) if str(t).strip()]
    seniority = raw.get("seniority_max")
    if seniority not in _VALID_SENIORITY:
        seniority = "mid"
    try:
        yoe_max = int(raw.get("yoe_max") or 0)
    except (TypeError, ValueError):
        yoe_max = 0

    return UserProfile(
        label=label,
        skills=skills[:40],
        target_titles=titles[:8],
        seniority_max=seniority,
        yoe_max=max(0, yoe_max),
        needs_sponsorship=bool(raw.get("needs_sponsorship", True)),
        resume_text=snippet,
    )
