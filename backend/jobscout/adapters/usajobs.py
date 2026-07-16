"""USAJobs official job search API adapter.

Surfaces US federal research / data / science roles (the rubric's "USAJobs
research or data roles only" seam). Government employers are cap-exempt for H-1B,
so the survivors are high-value; but most federal postings require US citizenship,
which is a hard-reject for an F-1 candidate — so this adapter drops citizenship-only
postings up front to avoid wasting the embedding budget on ineligible jobs.

Docs: https://developer.usajobs.gov/api-reference/get-api-search
Requires a free API key (developer.usajobs.gov). The registered email is sent as
the User-Agent header; the key as the Authorization-Key header.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from math import ceil

from jobscout.adapters.base import (
    CompliantHttpClient,
    DomainBlockedError,
    keyword_title_match,
)
from jobscout.config import settings

log = logging.getLogger(__name__)

_BASE_URL = "https://data.usajobs.gov/api/search"
_PAGE_SIZE = 100  # USAJobs allows up to 500; 100 keeps pages light

# Cheap pre-filter: drop postings that explicitly require US citizenship. These are
# hard-rejects for an F-1 candidate, so filtering here saves enrichment/embed budget.
_CITIZEN_ONLY = re.compile(
    r"\b(u\.?\s?s\.?|united states)\s+citizen", re.IGNORECASE
)


class USAJobsAdapter:
    """Wraps the USAJobs /api/search endpoint.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official USAJobs REST API.
    risk:
        ``"low"`` — official, key-authenticated API.
    store_full_description:
        ``True`` — full job summary text is available and stored.
    """

    name = "usajobs"
    method = "api"
    risk = "low"
    store_full_description = True

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts from the USAJobs API.

        Paginates until *results_wanted* eligible jobs are yielded or a page comes
        back empty. Citizenship-only postings and title-mismatched postings are
        dropped and do not count toward *results_wanted*.
        """
        if not settings.usajobs_api_key or not settings.usajobs_email:
            log.warning(
                "usajobs_api_key / usajobs_email not configured — skipping USAJobsAdapter"
            )
            return

        # USAJobs Keyword behaves like a phrase match: many joined terms return
        # almost nothing. Query each keyword separately and dedup by position id.
        # Keyword-less calls (the daily watchlist refresh passes []) fall back
        # to a broad tech sweep instead of skipping the source entirely.
        queries = [k.strip() for k in keywords if k.strip()] or [
            "data scientist", "software engineer", "data engineer",
        ]

        headers = {
            "Host": "data.usajobs.gov",
            "User-Agent": settings.usajobs_email,
            "Authorization-Key": settings.usajobs_api_key,
        }

        date_posted: int | None = None
        if since is not None:
            now = datetime.now(tz=UTC)
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
            # USAJobs DatePosted accepts 0-60 days.
            date_posted = max(0, min(60, ceil((now - since_aware).total_seconds() / 86_400)))

        total_yielded = 0
        seen_ids: set[str] = set()

        for keyword in queries:
          page = 1
          while total_yielded < results_wanted:
            params: dict = {
                "Keyword": keyword,
                "ResultsPerPage": min(_PAGE_SIZE, results_wanted - total_yielded + 20),
                "Page": page,
            }
            if location:
                params["LocationName"] = location
            if date_posted is not None:
                params["DatePosted"] = date_posted

            try:
                resp = http.get(
                    _BASE_URL, params=params, api_source=True, headers=headers
                )
            except DomainBlockedError as exc:
                log.warning("USAJobs domain blocked (%s) — stopping", exc)
                break
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching USAJobs page %d: %s", page, exc)
                break

            if resp.status_code != 200:
                log.error(
                    "USAJobs returned HTTP %s for page=%d — stopping", resp.status_code, page
                )
                break

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode USAJobs JSON for page=%d: %s", page, exc)
                break

            items: list[dict] = (data.get("SearchResult") or {}).get(
                "SearchResultItems"
            ) or []
            if not items:
                log.debug("USAJobs page=%d returned no items — done", page)
                break

            for item in items:
                if total_yielded >= results_wanted:
                    break
                job = _normalise(item)
                if job is None:
                    continue
                if job.get("source_job_id") and job["source_job_id"] in seen_ids:
                    continue
                if not keyword_title_match(job["title"] or "", queries):
                    continue
                if job.get("source_job_id"):
                    seen_ids.add(job["source_job_id"])
                yield job
                total_yielded += 1

            if len(items) < params["ResultsPerPage"]:
                break
            page += 1

        log.debug("USAJobs: yielded %d eligible jobs", total_yielded)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(item: dict) -> dict | None:
    """Convert a raw USAJobs SearchResultItem to the JobScout canonical shape.

    Returns ``None`` if mandatory fields are missing or the posting is
    citizenship-only (an F-1 hard-reject we skip before enrichment).
    """
    try:
        d = item.get("MatchedObjectDescriptor") or {}

        title = (d.get("PositionTitle") or "").strip() or None
        if not title:
            return None

        apply_uris = d.get("ApplyURI") or []
        url = (apply_uris[0] if apply_uris else d.get("PositionURI") or "").strip() or None
        if not url:
            return None

        company = (d.get("OrganizationName") or d.get("DepartmentName") or "").strip() or None

        loc_display = (d.get("PositionLocationDisplay") or "").strip() or None
        locations = d.get("PositionLocation") or []
        city = None
        if locations and isinstance(locations, list):
            city = (locations[0].get("CityName") or "").strip() or None

        details = d.get("UserArea", {}).get("Details", {}) or {}
        summary = (d.get("QualificationSummary") or "").strip()
        job_summary = (details.get("JobSummary") or "").strip()
        description = "\n\n".join(p for p in (job_summary, summary) if p) or None

        # Skip citizenship-only postings (hard-reject for F-1) before enrichment.
        haystack = " ".join(p for p in (job_summary, summary) if p)
        if haystack and _CITIZEN_ONLY.search(haystack):
            log.debug("USAJobs skip citizenship-only: %s", title)
            return None

        # Salary (USAJobs reports a min/max range in USD).
        remun = d.get("PositionRemuneration") or []
        salary_min = salary_max = None
        if remun and isinstance(remun, list):
            try:
                salary_min = float(remun[0].get("MinimumRange")) or None
                salary_max = float(remun[0].get("MaximumRange")) or None
            except (TypeError, ValueError):
                salary_min = salary_max = None

        return {
            "source_job_id": str(d.get("PositionID") or item.get("MatchedObjectId") or "") or None,
            "title": title,
            "company": company,
            "location": loc_display,
            "city": city,
            "country": "us",
            "description": description,
            "url": url,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "USD",
            "posted_date": d.get("PublicationStartDate") or None,
            # Federal employer → cap-exempt "likely" via derive_cap_exempt.
            "employer_type": "government",
        }

    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to normalise USAJobs item: %s", exc)
        return None
