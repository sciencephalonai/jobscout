"""
Ashby public job-board API adapter.

Ashby hosts a public, unauthenticated posting feed per company (job board name),
e.g. ``ramp`` / ``notion``:

    GET https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true
    → {"jobs": [{id, title, location, employmentType, jobUrl, applyUrl,
                 publishedAt, isListed, isRemote, descriptionPlain/Html, ...}]}

Ashby is a dominant ATS for venture-backed startups, so it is high-value for
sourcing small, actively-hiring companies. Like the other per-company ATS
adapters, it takes a curated list of org slugs and filters client-side.

Docs: https://developers.ashbyhq.com/reference/posting-api
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError, keyword_title_match
from jobscout.adapters.greenhouse import normalize_company_entries

log = logging.getLogger(__name__)

_BASE_URL = "https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"


class AshbyAdapter:
    """Wraps the Ashby public posting API across curated company job boards."""

    name = "ashby"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self, companies: list[Any] | None = None) -> None:
        self.companies: list[tuple[str, str]] = normalize_company_entries(companies)

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts from each configured Ashby board (client-side filtered)."""
        if not self.companies:
            log.warning("AshbyAdapter has no companies configured — skipping")
            return
        if results_wanted <= 0:
            return

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0

        for org, employer_type in self.companies:
            if total_yielded >= results_wanted:
                break

            url = _BASE_URL.format(org=org)
            try:
                resp = http.get(url, api_source=True)
            except DomainBlockedError as exc:
                log.warning("Ashby domain blocked (%s) — skipping org %s", exc, org)
                continue
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching Ashby org %s: %s", org, exc)
                continue

            if resp.status_code != 200:
                log.error("Ashby returned HTTP %s for org=%s — skipping", resp.status_code, org)
                continue

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode Ashby JSON for org=%s: %s", org, exc)
                continue

            jobs: list[dict] = data.get("jobs") or []

            for job in jobs:
                if total_yielded >= results_wanted:
                    break
                # Only public/listed postings.
                if job.get("isListed") is False:
                    continue
                try:
                    title = str(job.get("title") or "").strip()
                    job_url = (job.get("jobUrl") or job.get("applyUrl") or "").strip() or None
                    if not title or not job_url:
                        continue
                    if not keyword_title_match(title, keywords):
                        continue

                    posted_raw = job.get("publishedAt") or job.get("publishedDate")
                    if since_aware is not None:
                        parsed = _parse_iso(posted_raw)
                        if parsed is None or parsed < since_aware:
                            continue

                    location_str = (job.get("location") or "").strip() or None
                    description = job.get("descriptionPlain") or job.get("descriptionHtml") or None
                    raw: dict = {
                        "title": title,
                        "company": org,
                        "url": job_url,
                        "description": description,
                        "location": location_str,
                        "posted_date": posted_raw,
                        "source_job_id": str(job.get("id")) if job.get("id") else None,
                        "employer_type": employer_type,
                    }
                    if job.get("isRemote"):
                        raw["remote"] = "remote"

                    yield raw
                    total_yielded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to process Ashby job (org=%s): %s", org, exc)
                    continue


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
