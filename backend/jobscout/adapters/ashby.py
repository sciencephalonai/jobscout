"""
Ashby public job-board API adapter.

Ashby exposes an unauthenticated, per-company posting API. Each company
publishes its openings under a *board name* (the slug used in its Ashby
careers URL, e.g. ``openai``, ``ramp``). No API key is required.

Endpoint (per company):
    https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true

The API returns the full set of listed jobs for a board with no server-side
keyword or location search, so the adapter fetches each board in full and
filters client-side.

Confirmed live response shape (apiVersion 1)::

    {
      "jobs": [
        {
          "id": "...", "title": "...", "department": "...", "team": "...",
          "employmentType": "FullTime", "location": "San Francisco",
          "secondaryLocations": [...], "publishedAt": "2026-...",
          "isListed": true, "isRemote": null, "workplaceType": null,
          "address": {"postalAddress": {...}},
          "jobUrl": "https://jobs.ashbyhq.com/{board}/{id}",
          "applyUrl": "https://jobs.ashbyhq.com/{board}/{id}/application",
          "descriptionHtml": "...", "descriptionPlain": "...",
          "compensation": {...}
        }
      ],
      "apiVersion": 1
    }
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = (
    "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
)


def _parse_published_at(value: str | None) -> datetime | None:
    """Parse Ashby's ``publishedAt`` ISO-8601 timestamp into an aware UTC
    datetime.  Returns ``None`` if the value is falsy or unparseable."""
    if not value:
        return None
    raw = str(value).strip()
    # Ashby emits e.g. "2026-03-12T16:38:15.322+00:00"; fromisoformat handles
    # the offset directly. Also tolerate a trailing "Z".
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _location_string(job: dict) -> str | None:
    """Best-effort location string: the ``location`` field, else a joined
    ``address.postalAddress`` (locality/region/country)."""
    location = job.get("location")
    if location:
        loc = str(location).strip()
        if loc:
            return loc

    address = job.get("address") or {}
    postal = (address.get("postalAddress") or {}) if isinstance(address, dict) else {}
    if isinstance(postal, dict):
        parts = [
            postal.get("addressLocality"),
            postal.get("addressRegion"),
            postal.get("addressCountry"),
        ]
        joined = ", ".join(str(p).strip() for p in parts if p and str(p).strip())
        if joined:
            return joined
    return None


class AshbyAdapter:
    """Wraps the Ashby public job-board API.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official Ashby public posting API.
    risk:
        ``"low"`` — uses an official, unauthenticated public API endpoint.
    store_full_description:
        ``True`` — full description text is available and stored.
    companies:
        List of Ashby board names (company slugs) to query.
        Defaults to an empty list.
    """

    name = "ashby"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self, companies: list[str] | None = None) -> None:
        self.companies: list[str] = list(companies or [])

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
        """Yield raw job dicts from the Ashby API.

        Iterates over all configured company board names, fetching each board's
        full job list and filtering client-side until *results_wanted* jobs have
        been yielded in total.

        Parameters
        ----------
        keywords:
            Search terms.  A job is kept only if the (space-joined,
            case-insensitive) keyword string appears in the job title.  If
            *keywords* is empty, all jobs are kept.
        location:
            Unused — Ashby has no server-side location filter and the ingestion
            layer performs location handling downstream.
        results_wanted:
            Upper bound on the total number of results to yield.
        since:
            If given, only jobs whose ``publishedAt`` is on or after this
            datetime are yielded.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if not self.companies:
            log.warning("AshbyAdapter has no companies configured — skipping")
            return

        if results_wanted <= 0:
            return

        # Build a single case-insensitive needle from the keywords. Ashby offers
        # no server-side search, so we match this substring against the job
        # title client-side. Empty keywords → keep everything.
        needle = " ".join(k.strip() for k in keywords if k.strip()).lower()

        since_aware: datetime | None = None
        if since is not None:
            since_aware = (
                since if since.tzinfo is not None else since.replace(tzinfo=UTC)
            )

        total_yielded = 0

        for board in self.companies:
            if total_yielded >= results_wanted:
                break

            url = _BASE_URL.format(board=board)

            try:
                resp = http.get(url, api_source=self.method == "api")
            except DomainBlockedError as exc:
                log.warning(
                    "Ashby domain blocked (%s) — skipping board %s", exc, board
                )
                continue
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching Ashby board for %s: %s", board, exc)
                continue

            if resp.status_code != 200:
                log.error(
                    "Ashby returned HTTP %s for board=%s — skipping",
                    resp.status_code,
                    board,
                )
                continue

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode Ashby JSON for board=%s: %s", board, exc)
                continue

            jobs: list[dict] = data.get("jobs") or []
            log.debug(
                "Ashby board=%s: fetched %d jobs (total yielded so far: %d)",
                board,
                len(jobs),
                total_yielded,
            )

            for job in jobs:
                if total_yielded >= results_wanted:
                    break

                try:
                    title = str(job.get("title") or "").strip()
                    if not title:
                        continue

                    # Client-side keyword filter against the title.
                    if needle and needle not in title.lower():
                        continue

                    # Client-side `since` filter on publishedAt.
                    published_raw = job.get("publishedAt")
                    if since_aware is not None:
                        published = _parse_published_at(published_raw)
                        if published is None or published < since_aware:
                            continue

                    job_id = job.get("id")

                    # Prefer the direct employer apply link over the listing URL.
                    url_value = (
                        str(job.get("applyUrl") or "").strip()
                        or str(job.get("applicationUrl") or "").strip()
                        or str(job.get("jobUrl") or "").strip()
                        or None
                    )

                    description_html = job.get("descriptionHtml")
                    if description_html:
                        description = html.unescape(str(description_html))
                    else:
                        description = job.get("descriptionPlain") or None

                    remote = "remote" if job.get("isRemote") else None

                    yield {
                        "title": title,
                        "company": board,
                        "url": url_value,
                        "description": description,
                        "location": _location_string(job),
                        "remote": remote,
                        "posted_date": published_raw,
                        "source_job_id": str(job_id) if job_id is not None else None,
                        # Native Ashby type (e.g. "FullTime", "Contract", "Intern");
                        # normalize maps it to a canonical employment type.
                        "employment_type": job.get("employmentType") or None,
                    }
                    total_yielded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Failed to process Ashby job (board=%s, id=%s): %s",
                        board,
                        job.get("id"),
                        exc,
                    )
                    continue
