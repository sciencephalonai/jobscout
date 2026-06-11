"""
Lever public postings API adapter.

Lever exposes a public, unauthenticated JSON feed of a company's open
postings, keyed by the company's Lever account slug:

    https://api.lever.co/v0/postings/{company}?mode=json

No API key is required. The response is a JSON *list* of posting objects
(not wrapped in a ``jobs`` key). There is no server-side keyword search, so
keyword filtering is performed client-side against the posting title.

Docs: https://help.lever.co/hc/en-us/articles/360042743932-Lever-Postings-API
"""

from __future__ import annotations

import html as _html
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError, keyword_title_match
from jobscout.adapters.greenhouse import normalize_company_entries
from jobscout.normalize import normalize_remote

log = logging.getLogger(__name__)

_BASE_URL = "https://api.lever.co/v0/postings/{company}?mode=json"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", _html.unescape(text)).strip()


def _full_description(posting: dict) -> str | None:
    """Assemble the complete job description from all Lever sections.

    Lever splits the JD across three fields:
      - descriptionPlain  — intro paragraph
      - lists             — [{text: "Section header", content: "<HTML bullets>"}]
      - additionalPlain   — footer (company info, benefits, etc.)
    Using only descriptionPlain gives a 400-600 char intro; this combines all.
    """
    parts: list[str] = []
    intro = (posting.get("descriptionPlain") or "").strip()
    if intro:
        parts.append(intro)
    for section in posting.get("lists") or []:
        header = (section.get("text") or "").strip()
        content = _strip_html(section.get("content") or "")
        if header:
            parts.append(f"\n{header}")
        if content:
            parts.append(content)
    footer = (posting.get("additionalPlain") or "").strip()
    if footer:
        parts.append(f"\n{footer}")
    return "\n".join(parts) or None


class LeverAdapter:
    """Wraps the Lever public ``/v0/postings/{company}`` endpoint.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the public Lever JSON feed.
    risk:
        ``"low"`` — uses an official, public API endpoint.
    store_full_description:
        ``True`` — full description text (``descriptionPlain``) is stored.
    companies:
        List of Lever account entries — each a slug string or a
        ``{token, type}`` dict. Stored as ``(slug, employer_type)`` pairs.
        Defaults to an empty list.
    """

    name = "lever"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self, companies: list[Any] | None = None) -> None:
        self.companies: list[tuple[str, str]] = normalize_company_entries(companies)

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts from the Lever postings feed.

        Iterates over every configured company slug, fetching its full postings
        list and filtering client-side. Stops once *results_wanted* jobs have
        been yielded across all companies.

        Parameters
        ----------
        keywords:
            Search terms joined with a space; matched case-insensitively as a
            substring against the posting title. If empty, all postings are kept.
        location:
            Unused — Lever's feed is per-company, not geographically searchable.
        results_wanted:
            Upper bound on the total number of results to yield.
        since:
            If given, postings created before this datetime are skipped.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if results_wanted <= 0:
            return


        # Ensure `since` is timezone-aware for comparison.
        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0

        for company, employer_type in self.companies:
            if total_yielded >= results_wanted:
                break

            slug = company.strip()
            if not slug:
                continue

            url = _BASE_URL.format(company=slug)

            try:
                resp = http.get(url, api_source=self.method == "api")
            except DomainBlockedError as exc:
                log.warning("Lever domain blocked (%s) — skipping company %s", exc, slug)
                continue
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching Lever postings for company %s: %s", slug, exc)
                continue

            if resp.status_code != 200:
                log.error(
                    "Lever returned HTTP %s for company=%s — skipping", resp.status_code, slug
                )
                continue

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode Lever JSON for company=%s: %s", slug, exc)
                continue

            if not isinstance(data, list):
                log.warning(
                    "Lever response for company=%s was not a list (got %s) — skipping",
                    slug,
                    type(data).__name__,
                )
                continue

            for posting in data:
                if total_yielded >= results_wanted:
                    break
                job = self._normalise(posting, slug, keywords, since_aware, employer_type)
                if job is not None:
                    yield job
                    total_yielded += 1

            log.debug(
                "Lever company=%s: %d postings (total yielded so far: %d)",
                slug,
                len(data),
                total_yielded,
            )

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(
        posting: dict,
        company: str,
        keywords: list[str],
        since: datetime | None,
        employer_type: str = "unclear",
    ) -> dict | None:
        """Convert a raw Lever posting to the JobScout canonical shape.

        Returns ``None`` (and logs at debug) if the posting is filtered out by
        keyword/date, lacks mandatory fields, or fails to parse.
        """
        try:
            title = (posting.get("text") or "").strip() or None
            url = (posting.get("hostedUrl") or "").strip() or None
            if not title or not url:
                log.debug("Lever posting missing title/url, skipping: %s", posting.get("id"))
                return None

            # Client-side keyword filter (case-insensitive substring on title).
            if not keyword_title_match(title, keywords):
                return None

            # createdAt is epoch milliseconds (int) → tz-aware UTC datetime.
            posted_date: datetime | None = None
            created_at = posting.get("createdAt")
            if created_at is not None:
                try:
                    posted_date = datetime.fromtimestamp(int(created_at) / 1000, tz=UTC)
                except (TypeError, ValueError, OverflowError, OSError):
                    posted_date = None

            if since is not None and posted_date is not None and posted_date < since:
                return None

            categories = posting.get("categories") or {}
            location = (categories.get("location") or "").strip() or None

            job: dict = {
                "title": title,
                "company": company,
                "url": url,
                "description": _full_description(posting),
                "location": location,
                "posted_date": posted_date,
                "source_job_id": str(posting["id"]) if posting.get("id") is not None else None,
                "employer_type": employer_type,
            }

            # Only set `remote` when the location looks remote; otherwise leave unset.
            if location and normalize_remote(location) == "remote":
                job["remote"] = "remote"

            return job

        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Failed to normalise Lever posting (company=%s id=%s): %s",
                company,
                posting.get("id") if isinstance(posting, dict) else "?",
                exc,
            )
            return None
