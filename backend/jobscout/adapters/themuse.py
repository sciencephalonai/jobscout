"""
The Muse public jobs API adapter.

Docs: https://www.themuse.com/developers/api/v2
Endpoint: https://www.themuse.com/api/public/jobs?page=0

The API is public and free; an API key is optional (raises the rate limit) and
is read from ``settings.themuse_api_key`` when present. Pages are 0-indexed and
the response advertises a total ``page_count``.

The API exposes optional ``category=`` and ``location=`` filters but no reliable
keyword search, so keyword filtering is performed client-side against the job
title (``name``). robots.txt was checked: ``/api/public`` is not disallowed
(only ``/api/users*`` is), so this sanctioned public API is fetched with
``api_source=True``.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = "https://www.themuse.com/api/public/jobs"
_MAX_PAGES = 10  # cap on pages fetched per search to bound API usage


def _parse_publication_date(value: str | None) -> datetime | None:
    """Parse The Muse's ISO ``publication_date`` (e.g. ``2025-12-08T18:33:25Z``).

    Returns a timezone-aware UTC datetime, or ``None`` if it cannot be parsed.
    Used only for the *since* comparison; the raw string is what we yield so the
    ingestion layer can re-parse it.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class TheMuseAdapter:
    """Wraps the public The Muse ``/api/public/jobs`` endpoint.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official public The Muse REST API.
    risk:
        ``"low"`` — uses an official, sanctioned public API endpoint.
    store_full_description:
        ``True`` — full (HTML) description text is available and stored.
    """

    name = "themuse"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self) -> None:
        pass

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
        """Yield raw job dicts from the The Muse API.

        Paginates from page 0 (pages are 0-indexed) until *results_wanted* jobs
        have been yielded, the advertised ``page_count`` is reached, or the
        ``_MAX_PAGES`` cap is hit. The API has no reliable keyword search, so
        keywords are matched client-side (case-insensitive substring against the
        job title); when *keywords* is empty all jobs are kept.

        Parameters
        ----------
        keywords:
            Search terms joined with a space and matched case-insensitively
            against the job title. Empty → keep all jobs.
        location:
            Forwarded as the ``location`` query parameter when set.
        results_wanted:
            Upper bound on the total number of results to yield.
        since:
            If given, jobs whose ``publication_date`` is older than this
            datetime are skipped.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if results_wanted <= 0:
            return

        keyword_filter = " ".join(k.strip() for k in keywords if k.strip()).lower()

        # Optional API key (raises rate limit). Imported lazily so a missing
        # config attribute never breaks the adapter.
        api_key: str | None = None
        try:
            from jobscout.config import settings

            api_key = getattr(settings, "themuse_api_key", None) or None
        except Exception as exc:  # noqa: BLE001
            log.debug("Could not read themuse_api_key from settings: %s", exc)

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0
        page = 0

        while total_yielded < results_wanted and page < _MAX_PAGES:
            params: dict = {"page": page}
            if location:
                params["location"] = location
            if api_key:
                params["api_key"] = api_key

            try:
                resp = http.get(_BASE_URL, params=params, api_source=self.method == "api")
            except DomainBlockedError as exc:
                log.warning("The Muse domain blocked (%s) — skipping", exc)
                break
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching The Muse page %d: %s", page, exc)
                break

            if resp.status_code != 200:
                log.error(
                    "The Muse returned HTTP %s for page=%d — stopping pagination",
                    resp.status_code,
                    page,
                )
                break

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode The Muse JSON for page=%d: %s", page, exc)
                break

            results: list[dict] = data.get("results") or []
            if not results:
                log.debug("The Muse returned empty results for page=%d — done", page)
                break

            for result in results:
                if total_yielded >= results_wanted:
                    break

                # Client-side keyword filter against the title.
                if keyword_filter:
                    title = str(result.get("name") or "").lower()
                    if keyword_filter not in title:
                        continue

                # since filter.
                if since_aware is not None:
                    published = _parse_publication_date(result.get("publication_date"))
                    if published is not None and published < since_aware:
                        continue

                job = _normalise(result)
                if job is not None:
                    yield job
                    total_yielded += 1

            # Stop once we have advertised the last page.
            page_count = data.get("page_count")
            if isinstance(page_count, int) and (page + 1) >= page_count:
                break

            page += 1

        log.debug(
            "The Muse search keywords=%r location=%r: yielded %d jobs",
            keyword_filter,
            location,
            total_yielded,
        )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(result: dict) -> dict | None:
    """Convert a raw The Muse result dict to the JobScout canonical shape.

    Returns ``None`` (and logs) if mandatory fields (title/url) are missing.
    """
    try:
        title = (result.get("name") or "").strip() or None

        refs = result.get("refs") or {}
        url = (refs.get("landing_page") or "").strip() or None

        if not title or not url:
            log.debug("The Muse result missing title/url, skipping: %s", result.get("id"))
            return None

        company_obj = result.get("company") or {}
        company = (company_obj.get("name") or "").strip() or None

        locations = result.get("locations") or []
        location = None
        remote: str | None = None
        if locations:
            location = (locations[0].get("name") or "").strip() or None
        for loc in locations:
            name = str(loc.get("name") or "").lower()
            if "remote" in name or "flexible" in name:
                remote = "remote"
                break

        contents = result.get("contents")
        description = html.unescape(contents) if contents else None

        # Keep the raw ISO string; the ingestion layer parses it.
        posted_date = result.get("publication_date") or None

        job_id = result.get("id")
        source_job_id = str(job_id) if job_id is not None else None

        job: dict = {
            "title": title,
            "company": company,
            "url": url,
            "description": description,
            "location": location,
            "posted_date": posted_date,
            "source_job_id": source_job_id,
        }
        if remote is not None:
            job["remote"] = remote

        return job

    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to normalise The Muse result (id=%s): %s", result.get("id"), exc)
        return None
