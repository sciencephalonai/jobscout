"""
Rippling ATS public job-board API adapter.

Rippling ATS hosts a separate public job board per company at
``ats.rippling.com/{slug}`` (e.g. ``tavernresearch``) — it is NOT an aggregator;
each slug is one employer's board, analogous to a Greenhouse board. The official
Rippling Job Board API is unauthenticated:

    list:   GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs
            → a top-level JSON array of {uuid, name, department, url, workLocation}
    detail: GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}
            → adds description{company,role} (HTML), createdOn, companyName, ...

The listing carries no description, so (when configured) the adapter makes one
follow-up GET per job to the detail endpoint — the same pattern as the Workday
adapter. All boards share the single ``api.rippling.com`` domain, so the
per-domain rate limit serialises every Rippling request.

Docs: https://developer.rippling.com/documentation/job-board-api
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError, keyword_title_match
from jobscout.adapters.greenhouse import normalize_company_entries

log = logging.getLogger(__name__)

_LIST_URL = "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
_DETAIL_URL = "https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}"


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class RipplingAdapter:
    """Wraps the public Rippling ATS Job Board API across curated company boards."""

    name = "rippling"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(
        self, companies: list[Any] | None = None, fetch_descriptions: bool = True
    ) -> None:
        self.companies: list[tuple[str, str]] = normalize_company_entries(companies)
        self.fetch_descriptions = fetch_descriptions

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts from each configured Rippling board (client-side filtered)."""
        if not self.companies:
            log.warning("RipplingAdapter has no companies configured — skipping")
            return
        if results_wanted <= 0:
            return

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0

        for slug, employer_type in self.companies:
            if total_yielded >= results_wanted:
                break

            url = _LIST_URL.format(slug=slug)
            try:
                resp = http.get(url, api_source=True)
            except DomainBlockedError as exc:
                log.warning("Rippling domain blocked (%s) — skipping board %s", exc, slug)
                continue
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching Rippling board %s: %s", slug, exc)
                continue

            if resp.status_code != 200:
                log.error(
                    "Rippling returned HTTP %s for board=%s — skipping", resp.status_code, slug
                )
                continue

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode Rippling JSON for board=%s: %s", slug, exc)
                continue

            # The v1 listing endpoint returns a top-level array.
            jobs: list[dict] = data if isinstance(data, list) else (data.get("items") or [])

            for job in jobs:
                if total_yielded >= results_wanted:
                    break
                raw = self._build_raw(job, slug, employer_type, keywords, since_aware, http)
                if raw is not None:
                    yield raw
                    total_yielded += 1

    def _build_raw(
        self,
        job: dict,
        slug: str,
        employer_type: str,
        keywords: list[str],
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> dict | None:
        """Convert one Rippling listing entry to the canonical raw dict (+ optional detail)."""
        try:
            title = str(job.get("name") or "").strip()
            job_url = str(job.get("url") or "").strip()
            if not title or not job_url:
                return None
            if not keyword_title_match(title, keywords):
                return None

            uuid = job.get("uuid")
            work_location = job.get("workLocation") or {}
            location = (work_location.get("label") or "").strip() or None

            company: str | None = slug
            description: str | None = None
            posted_date: str | datetime | None = None

            if self.fetch_descriptions and uuid:
                detail = self._fetch_detail(slug, str(uuid), http)
                if detail is not None:
                    desc_obj = detail.get("description") or {}
                    if isinstance(desc_obj, dict):
                        parts = [desc_obj.get("company") or "", desc_obj.get("role") or ""]
                        joined = "\n".join(p for p in parts if p)
                        description = html.unescape(joined) if joined else None
                    posted_date = _parse_iso(detail.get("createdOn"))
                    company = (detail.get("companyName") or "").strip() or slug
                    # Detail's workLocations array is richer than the listing's.
                    locs = detail.get("workLocations")
                    if isinstance(locs, list) and locs:
                        location = str(locs[0]).strip() or location

            # `since` filtering needs a date, which only the detail call provides.
            if since is not None and isinstance(posted_date, datetime) and posted_date < since:
                return None

            return {
                "title": title,
                "company": company,
                "url": job_url,
                "description": description,
                "location": location,
                "posted_date": posted_date,
                "source_job_id": str(uuid) if uuid is not None else None,
                "employer_type": employer_type,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to process Rippling job on board %s: %s", slug, exc)
            return None

    @staticmethod
    def _fetch_detail(slug: str, uuid: str, http: CompliantHttpClient) -> dict | None:
        """GET a single job's detail object (description, createdOn, companyName)."""
        detail_url = _DETAIL_URL.format(slug=slug, uuid=uuid)
        try:
            resp = http.get(detail_url, api_source=True)
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001
            log.debug("Rippling detail fetch failed for %s: %s", detail_url, exc)
            return None
