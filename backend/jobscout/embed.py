"""Embedding helpers using Google text-embedding-005.

All functions are synchronous. The embedding model and API key are pulled from
``jobscout.config.settings`` so there is a single source of truth.
"""

from __future__ import annotations

import google.generativeai as genai

from jobscout.config import settings

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
    description.  Keeps the total well inside the 2 048-token context window
    of ``text-embedding-005``.
    """
    parts: list[str] = [title]
    if company:
        parts.append(company)
    if skills:
        parts.append(", ".join(skills))
    if description:
        parts.append(description[:1500])
    return " ".join(parts)


def _configure() -> None:
    """Configure the google-generativeai SDK with the API key from settings."""
    genai.configure(api_key=settings.google_api_key)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def embed_text(text: str) -> list[float]:
    """Embed an arbitrary document string.

    Uses ``task_type="retrieval_document"`` — the correct task for indexing
    passages into a vector store.

    Returns:
        Dense float vector produced by ``text-embedding-005``.
    """
    _configure()
    result = genai.embed_content(
        model=f"models/{settings.embed_model}",
        content=text,
        task_type="retrieval_document",
    )
    return result["embedding"]


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

    Uses ``task_type="retrieval_query"`` so the model optimises the vector for
    querying against document-task vectors.

    Args:
        text: Free-form query string (keywords, resume snippet, NL request).

    Returns:
        Dense float vector.
    """
    _configure()
    result = genai.embed_content(
        model=f"models/{settings.embed_model}",
        content=text,
        task_type="retrieval_query",
    )
    return result["embedding"]
