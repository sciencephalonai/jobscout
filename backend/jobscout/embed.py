"""Embedding helpers using the Google Gemini embedding API (``google-genai`` SDK).

All functions are synchronous. The embedding model (e.g. ``gemini-embedding-001``)
and API key are pulled from ``jobscout.config.settings`` so there is a single
source of truth. Note: ``text-embedding-005`` is a Vertex-only model name and is
NOT served by the Gemini API — use a ``gemini-embedding-*`` model.

This module uses the current ``google-genai`` SDK (``from google import genai``),
not the deprecated ``google-generativeai`` package. The client is created once as
a module-level lazy singleton, mirroring ``jobscout.enrich``.
"""

from __future__ import annotations

from google import genai
from google.genai import types

from jobscout.config import settings


class EmbeddingQuotaError(RuntimeError):
    """Raised when the embedding provider rejects a call for quota/rate-limit
    reasons (e.g. Gemini free tier = 1,000 embeds/day). Callers can catch this to
    stop an ingest cleanly and surface a clear message instead of dropping jobs
    silently."""


def _is_quota_error(exc: Exception) -> bool:
    """True if *exc* looks like a 429 / quota / rate-limit from the embed API."""
    name = type(exc).__name__.lower()
    if "resourceexhausted" in name or "ratelimit" in name:
        return True
    msg = str(exc).lower()
    return (
        "429" in msg
        or "quota" in msg
        or "resource_exhausted" in msg
        or "exceeded your current quota" in msg
    )


# App-level embedding-quota signal: set when the provider 429s, cleared on the
# next successful embed (so it auto-recovers after the daily reset). Read by
# /api/stats so the UI can show one honest, self-clearing quota banner for BOTH
# "Get latest jobs" and "Get companies" (both embed via this module).
_quota_hit: bool = False


def embedding_quota_hit() -> bool:
    """True if the last embedding attempt hit the provider quota (and none has
    succeeded since)."""
    return _quota_hit


def _mark_quota(hit: bool) -> None:
    global _quota_hit
    _quota_hit = hit


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_embed_text(
    title: str,
    company: str | None,
    skills: list[str],
    description: str | None,
) -> str:
    """Concatenate fields into a single embedding document string.

    Layout: title + company + comma-joined skills + first 1500 chars of
    description.  Keeps the total well inside the embedding model's context
    window.
    """
    parts: list[str] = [title]
    if company:
        parts.append(company)
    if skills:
        parts.append(", ".join(skills))
    if description:
        parts.append(description[:1500])
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Lazy singleton client
# ---------------------------------------------------------------------------

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Return a process-wide singleton ``google-genai`` client."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.google_api_key)
    return _client


# Gemini embedding task types (uppercase in the google-genai SDK).
_TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
_TASK_QUERY = "RETRIEVAL_QUERY"


def _embed(text: str, task_type: str) -> list[float]:
    """Embed *text* for the given Gemini ``task_type`` and return the vector.

    Wraps the raw SDK call with quota detection: a 429/rate-limit raises
    :class:`EmbeddingQuotaError` and sets the app-level quota flag; any success
    clears it.
    """
    client = _get_client()
    try:
        result = client.models.embed_content(
            model=settings.embed_model,
            contents=text,
            config=types.EmbedContentConfig(task_type=task_type),
        )
    except Exception as exc:
        if _is_quota_error(exc):
            _mark_quota(True)
            raise EmbeddingQuotaError(
                "Gemini embedding quota exhausted (free tier = 1,000/day); resets daily."
            ) from exc
        raise
    _mark_quota(False)  # a success means the quota has room again
    embeddings = result.embeddings
    values = embeddings[0].values if embeddings else None
    if not values:
        raise RuntimeError("Gemini embedding response contained no vector.")
    return list(values)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def embed_text(text: str) -> list[float]:
    """Embed an arbitrary document string.

    Uses ``task_type="RETRIEVAL_DOCUMENT"`` — the correct task for indexing
    passages into a vector store.

    Returns:
        Dense float vector produced by the configured Gemini embedding model.
    """
    return _embed(text, _TASK_DOCUMENT)


def embed_job(
    title: str,
    company: str | None,
    skills: list[str],
    description: str | None,
) -> list[float]:
    """Build the canonical embedding string for a job and embed it.

    This is the function called by the enrichment worker after LLM extraction
    has populated ``skills`` and other structured fields.

    Args:
        title:       Job title.
        company:     Company name (may be None).
        skills:      List of skill strings extracted by the LLM.
        description: Full job description (only first 1 500 chars are used).

    Returns:
        Dense float vector.
    """
    return embed_text(_build_embed_text(title, company, skills, description))


def embed_query(text: str) -> list[float]:
    """Embed a user search query string.

    Uses ``task_type="RETRIEVAL_QUERY"`` so the model optimises the vector for
    querying against document-task vectors.

    Args:
        text: Free-form query string (keywords, resume snippet, NL request).

    Returns:
        Dense float vector.
    """
    return _embed(text, _TASK_QUERY)
