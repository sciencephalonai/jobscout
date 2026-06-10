"""
Working Nomads public job board API adapter.

Endpoint: https://www.workingnomads.com/api/exposed_jobs/

The Working Nomads ``exposed_jobs`` endpoint is public and requires no key. It
returns a flat JSON *list* of remote jobs (no pagination, no server-side
search), so every yielded record is marked ``remote`` and keyword filtering is
performed client-side against the job title. We link back to the canonical job
URL and attribute Working Nomads as the source.

Each raw job object has the keys::

    url, title, description, company_name, category_name,
    tags (comma-separated string), location, pub_date (ISO string)

There is no ``id`` field, so ``source_job_id`` is derived from the job URL.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = "https://www.workingnomads.com/api/exposed_jobs/"


def _parse_pub_date(value: str | None) -> datetime | None:
    """Parse Working Nomads' ISO ``pub_date`` (e.g. ``2026-06-03T10:28:43-04:00``).

    Returns a timezone-aware UTC datetime, or ``None`` if it cannot be parsed.
    Used only for the *since* comparison; the raw string is what we yield so the
    ingestion layer can re-parse it.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class WorkingNomadsAdapter:
    """Wraps the Working Nomads ``/api/exposed_jobs/`` endpoint.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the public Working Nomads REST endpoint.
    risk:
        ``"low"`` — uses an official, sanctioned public API endpoint.
    store_full_description:
        ``True`` — full (HTML) description text is available and stored.
    """

    name = "workingnomads"
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
        """Yield raw job dicts from the Working Nomads API.

        Parameters
        ----------
        keywords:
            Terms joined with a space and matched case-insensitively against
            each job's title. If empty, all jobs pass the keyword filter.
        location:
            Ignored — Working Nomads only lists remote jobs and the endpoint
            offers no geographic filter. Each job's ``location`` field (e.g.
            "Europe", "Worldwide") is surfaced as ``location``.
        results_wanted:
            Upper bound on the total number of results to yield.
        since:
            If given, jobs whose ``pub_date`` is older than this datetime are
            skipped.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` used for the
            request.
        """
        if results_wanted <= 0:
            return

        search_term = " ".join(k.strip() for k in keywords if k.strip()).lower().strip()

        try:
            resp = http.get(_BASE_URL, api_source=self.method == "api")
        except DomainBlockedError as exc:
            log.warning("Working Nomads domain blocked (%s) — skipping", exc)
            return
        except Exception as exc:  # noqa: BLE001
            log.error("HTTP error fetching Working Nomads jobs: %s", exc)
            return

        if resp.status_code != 200:
            log.error("Working Nomads returned HTTP %s — skipping", resp.status_code)
            return

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to decode Working Nomads JSON: %s", exc)
            return

        if not isinstance(data, list):
            log.error("Working Nomads returned unexpected payload type %s", type(data).__name__)
            return

        if not data:
            log.debug("Working Nomads returned no jobs")
            return

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0
        for job in data:
            if total_yielded >= results_wanted:
                break

            try:
                if not isinstance(job, dict):
                    continue

                title = (job.get("title") or "").strip()
                if not title:
                    continue

                # Client-side keyword filter (title only, case-insensitive).
                if search_term and search_term not in title.lower():
                    continue

                # Date filter.
                if since_aware is not None:
                    published = _parse_pub_date(job.get("pub_date"))
                    if published is not None and published < since_aware:
                        continue

                url = (job.get("url") or "").strip()
                if not url:
                    continue

                description = job.get("description") or None
                if description and ("<" in description and ">" in description):
                    description = html.unescape(description)

                company = (job.get("company_name") or "").strip() or None
                loc = (job.get("location") or "").strip() or None

                # No id field — derive a stable source id from the URL.
                source_job_id = url.rstrip("/").rsplit("/", 1)[-1] or url

                yield {
                    "title": title,
                    "company": company,
                    "url": url,
                    "description": description,
                    "remote": "remote",  # every Working Nomads job is remote
                    "location": loc,
                    "posted_date": job.get("pub_date") or None,
                    "source_job_id": source_job_id,
                }
                total_yielded += 1

            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to process Working Nomads job: %s", exc)
                continue

        log.debug("Working Nomads: yielded %d jobs (of %d returned)", total_yielded, len(data))
