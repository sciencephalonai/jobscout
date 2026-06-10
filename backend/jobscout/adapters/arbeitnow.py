"""
Arbeitnow public job board API adapter.

Docs / endpoint: https://www.arbeitnow.com/api/job-board-api

The Arbeitnow API is public and requires no key. It returns a paginated list
of (largely Europe / Germany focused) jobs. There is no server-side keyword
search, so keyword filtering is applied client-side against the job title.
Each page is fetched via the ``?page=N`` query parameter; we keep paginating
until ``results_wanted`` jobs have been yielded or a page returns no data.

Response shape (per ``data`` item)::

    {
        "slug": "...",
        "title": "...",
        "company_name": "...",
        "description": "<p>...</p>",   # HTML
        "remote": true,
        "url": "https://...",
        "tags": ["...", ...],
        "job_types": ["...", ...],
        "location": "...",
        "created_at": 1717372800        # unix epoch seconds
    }
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES = 50  # hard cap so we never paginate forever


class ArbeitnowAdapter:
    """Wraps the Arbeitnow ``/api/job-board-api`` endpoint.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official public Arbeitnow REST API.
    risk:
        ``"low"`` — uses an official, sanctioned public API endpoint.
    store_full_description:
        ``True`` — full (HTML) description text is available and stored.
    """

    name = "arbeitnow"
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
        """Yield raw job dicts from the Arbeitnow API.

        Parameters
        ----------
        keywords:
            Joined with a space and matched case-insensitively against each
            job *title*. If empty, all jobs are kept (subject to *since*).
        location:
            Accepted for protocol compatibility but not used for filtering —
            Arbeitnow has no server-side location filter. Each job's
            ``location`` is surfaced unchanged.
        results_wanted:
            Upper bound on the total number of results to yield.
        since:
            If given, jobs whose ``created_at`` is older than this datetime are
            skipped (``created_at`` is unix epoch seconds).
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if results_wanted <= 0:
            return

        search_term = " ".join(k.strip() for k in keywords if k.strip()).lower()

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0
        for page in range(1, _MAX_PAGES + 1):
            if total_yielded >= results_wanted:
                break

            try:
                resp = http.get(
                    _BASE_URL, params={"page": page}, api_source=self.method == "api"
                )
            except DomainBlockedError as exc:
                log.warning("Arbeitnow domain blocked (%s) — skipping", exc)
                break
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching Arbeitnow page %d: %s", page, exc)
                break

            if resp.status_code != 200:
                log.error(
                    "Arbeitnow page %d returned HTTP %s — stopping",
                    page,
                    resp.status_code,
                )
                break

            try:
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode Arbeitnow JSON (page %d): %s", page, exc)
                break

            jobs: list[dict] = (payload or {}).get("data") or []
            if not jobs:
                log.debug("Arbeitnow page %d returned no jobs — stopping", page)
                break

            for job in jobs:
                if total_yielded >= results_wanted:
                    break

                normalised = _normalise(job, search_term, since_aware)
                if normalised is not None:
                    yield normalised
                    total_yielded += 1

        log.debug(
            "Arbeitnow search term=%r: yielded %d jobs", search_term, total_yielded
        )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(
    job: dict, search_term: str, since_aware: datetime | None
) -> dict | None:
    """Convert a raw Arbeitnow job dict to the JobScout canonical shape.

    Applies client-side keyword (title) and *since* filtering. Returns ``None``
    when the job is filtered out, lacks mandatory fields (title/url), or cannot
    be parsed.
    """
    try:
        title = (job.get("title") or "").strip() or None
        url = (job.get("url") or "").strip() or None

        if not title or not url:
            log.debug("Arbeitnow job missing title/url, skipping: %s", job.get("slug"))
            return None

        # Client-side keyword filter against the title.
        if search_term and search_term not in title.lower():
            return None

        # since filter — created_at is unix epoch seconds.
        posted_date: datetime | None = None
        created_at = job.get("created_at")
        if created_at is not None:
            try:
                posted_date = datetime.fromtimestamp(int(created_at), tz=UTC)
            except (TypeError, ValueError, OSError, OverflowError):
                posted_date = None

        if since_aware is not None and posted_date is not None:
            if posted_date < since_aware:
                return None

        company = (job.get("company_name") or "").strip() or None
        location = (job.get("location") or "").strip() or None
        description = job.get("description")
        if description:
            description = html.unescape(str(description))
        else:
            description = None

        # job_types is a list (e.g. ["Full Time"], ["Internship"]); take the
        # first value. normalize maps the native string to a canonical type.
        job_types = job.get("job_types")
        employment_type = (
            job_types[0] if isinstance(job_types, list) and job_types else None
        )

        result: dict = {
            "title": title,
            "company": company,
            "url": url,
            "description": description,
            "location": location,
            "posted_date": posted_date,
            "source_job_id": job.get("slug") or None,
            "employment_type": employment_type,
        }

        if job.get("remote"):
            result["remote"] = "remote"

        return result

    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Failed to normalise Arbeitnow job (slug=%s): %s", job.get("slug"), exc
        )
        return None
