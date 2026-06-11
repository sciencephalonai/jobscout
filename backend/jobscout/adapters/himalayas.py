"""
Himalayas public job board API adapter.

Himalayas exposes a free, unauthenticated JSON API:

    https://himalayas.app/jobs/api?limit={n}&offset={n}

No API key is required. The response includes full HTML descriptions,
structured salary data, seniority, and employment type in every record —
no separate detail call needed.

Docs: https://himalayas.app/api
"""

from __future__ import annotations

import html as _html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = "https://himalayas.app/jobs/api"
# The API caps each response at 20 records regardless of the requested `limit`,
# so this MUST be 20 — assuming a larger page makes the `len(jobs) < _PAGE_SIZE`
# end-of-data check fire on the very first page and kill pagination.
_PAGE_SIZE = 20
# Himalayas has ~99k jobs across all categories with no server-side keyword
# filter, returned newest-first. Bound how many newest jobs we scan so a sparse
# keyword (few matching titles) can't page through the entire board.
_MAX_PAGES = 25


def _parse_date(raw: int | float | str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        # pubDate is a Unix timestamp (integer seconds)
        return datetime.fromtimestamp(float(raw), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _parse_salary(job: dict) -> tuple[float | None, float | None, str | None]:
    mn = job.get("minSalary")
    mx = job.get("maxSalary")
    currency = (job.get("currency") or "").upper() or None
    try:
        return (float(mn) if mn is not None else None,
                float(mx) if mx is not None else None,
                currency)
    except (TypeError, ValueError):
        return None, None, currency


class HimalayasAdapter:
    """Wraps the Himalayas public job board API.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the public Himalayas JSON API.
    risk:
        ``"low"`` — uses an official, public API endpoint.
    store_full_description:
        ``True`` — full HTML description is returned in every record.
    """

    name = "himalayas"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self) -> None:
        pass

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts from the Himalayas API with pagination.

        Himalayas has no server-side keyword filter — filtering is done
        client-side against title. Location is not used (all jobs are remote).
        """
        if results_wanted <= 0:
            return

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        search_kws = [k.strip().lower() for k in keywords if k.strip()]
        total_yielded = 0
        offset = 0
        pages_fetched = 0

        while total_yielded < results_wanted and pages_fetched < _MAX_PAGES:
            params = {"limit": _PAGE_SIZE, "offset": offset}
            try:
                resp = http.get(_BASE_URL, params=params, api_source=True)
            except DomainBlockedError as exc:
                log.warning("Himalayas domain blocked (%s) — stopping", exc)
                break
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching Himalayas jobs (offset=%d): %s", offset, exc)
                break

            if resp.status_code != 200:
                log.error("Himalayas returned HTTP %s (offset=%d) — stopping", resp.status_code, offset)
                break

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode Himalayas JSON: %s", exc)
                break

            jobs: list[dict] = data.get("jobs") or []
            if not jobs:
                break

            for job in jobs:
                if total_yielded >= results_wanted:
                    break

                normalised = _normalise(job, search_kws, since_aware)
                if normalised is not None:
                    yield normalised
                    total_yielded += 1

            # Stop paginating if we got fewer than a full page
            if len(jobs) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
            pages_fetched += 1

        log.debug("Himalayas: yielded %d jobs", total_yielded)


def _normalise(
    job: dict,
    keywords: list[str],
    since: datetime | None,
) -> dict | None:
    title = (job.get("title") or "").strip()
    url = (job.get("applicationLink") or "").strip()
    if not title or not url:
        return None

    # Client-side keyword filter against title
    if keywords:
        low = title.lower()
        if not any(kw in low for kw in keywords):
            return None

    posted_date = _parse_date(job.get("pubDate"))
    if since is not None and posted_date is not None and posted_date < since:
        return None

    company = (job.get("companyName") or "").strip() or None
    description = job.get("description") or None
    if description:
        description = _html.unescape(description)

    salary_min, salary_max, currency = _parse_salary(job)

    # All Himalayas jobs are remote
    return {
        "title": title,
        "company": company,
        "url": url,
        "description": description,
        "location": "Remote",
        "remote": "remote",
        "posted_date": posted_date,
        "source_job_id": str(job["guid"]) if job.get("guid") else None,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": currency,
        "employer_type": "unclear",
    }
