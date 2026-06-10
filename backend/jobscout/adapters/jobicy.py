"""
Jobicy public remote-jobs API adapter.

Docs: https://jobi.cy/apidocs
Endpoint: https://jobicy.com/api/v2/remote-jobs

The Jobicy API is public and requires no key. It returns only remote jobs, so
every yielded record is marked ``remote``. Per Jobicy's friendly notice we link
back to the canonical job URL and attribute Jobicy as the source; the feed
should not be polled more than a few times a day.

Query parameters used:
- ``count``  — number of results (max 50 per call).
- ``tag``    — optional keyword filter (space-joined search terms).
- ``geo``    — optional region filter (not used here; all jobs are remote).
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = "https://jobicy.com/api/v2/remote-jobs"
_MAX_COUNT = 50  # Jobicy caps a single feed request at 50 results


def _parse_pub_date(value: str | None) -> datetime | None:
    """Loosely parse Jobicy's ``pubDate`` string to a UTC datetime.

    Used only for the *since* comparison; the raw string is what we yield so
    the ingestion layer can re-parse it. Returns ``None`` on parse failure so
    callers can choose to keep the job rather than drop it silently.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class JobicyAdapter:
    """Wraps the Jobicy ``/api/v2/remote-jobs`` endpoint.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official public Jobicy REST API.
    risk:
        ``"low"`` — uses an official, sanctioned public API endpoint.
    store_full_description:
        ``True`` — full (HTML) description text is available and stored.
    """

    name = "jobicy"
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
        """Yield raw job dicts from the Jobicy API.

        Parameters
        ----------
        keywords:
            Search terms joined with a space as the ``tag`` parameter. Omitted
            entirely when no keywords are supplied.
        location:
            Ignored — Jobicy only returns remote jobs. Each job's ``jobGeo`` is
            surfaced as ``location``.
        results_wanted:
            Upper bound on results to yield. Passed (capped at 50) as ``count``.
        since:
            If given, jobs whose ``pubDate`` is older than this datetime are
            skipped. Jobs with an unparseable ``pubDate`` are kept.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if results_wanted <= 0:
            return

        tag = " ".join(k.strip() for k in keywords if k.strip()) or None

        params: dict = {"count": min(results_wanted, _MAX_COUNT), "tag": tag}

        try:
            resp = http.get(_BASE_URL, params=params, api_source=True)
        except DomainBlockedError as exc:
            log.warning("Jobicy domain blocked (%s) — skipping", exc)
            return
        except Exception as exc:  # noqa: BLE001
            log.error("HTTP error fetching Jobicy jobs: %s", exc)
            return

        if resp.status_code != 200:
            log.error("Jobicy returned HTTP %s — skipping", resp.status_code)
            return

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to decode Jobicy JSON: %s", exc)
            return

        jobs: list[dict] = data.get("jobs") or []
        if not jobs:
            log.debug("Jobicy returned no jobs for tag=%r", tag)
            return

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0
        for job in jobs:
            if total_yielded >= results_wanted:
                break

            if since_aware is not None:
                published = _parse_pub_date(job.get("pubDate"))
                if published is not None and published < since_aware:
                    continue

            normalised = _normalise(job)
            if normalised is not None:
                yield normalised
                total_yielded += 1

        log.debug(
            "Jobicy tag=%r: yielded %d jobs (of %d returned)",
            tag,
            total_yielded,
            len(jobs),
        )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(job: dict) -> dict | None:
    """Convert a raw Jobicy job dict to the JobScout canonical shape.

    Returns ``None`` (and logs) if mandatory fields (title/url) are missing.
    """
    try:
        title = (job.get("jobTitle") or "").strip() or None
        url = (job.get("url") or "").strip() or None

        if not title or not url:
            log.debug("Jobicy job missing title/url, skipping: %s", job.get("id"))
            return None

        company = (job.get("companyName") or "").strip() or None
        location = (job.get("jobGeo") or "").strip() or None

        raw_desc = job.get("jobDescription") or job.get("jobExcerpt") or None
        description = html.unescape(raw_desc) if raw_desc else None

        # Keep the raw pubDate string; the ingestion layer parses it.
        posted_date = job.get("pubDate") or None

        job_id = job.get("id")
        source_job_id = str(job_id) if job_id is not None else None

        return {
            "title": title,
            "company": company,
            "url": url,
            "description": description,
            "remote": "remote",  # every Jobicy job is remote
            "location": location,
            "posted_date": posted_date,
            "source_job_id": source_job_id,
        }

    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to normalise Jobicy job (id=%s): %s", job.get("id"), exc)
        return None
