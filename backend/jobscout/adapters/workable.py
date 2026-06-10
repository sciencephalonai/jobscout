"""
Workable public job-widget API adapter.

Workable exposes an unauthenticated, per-account JSON feed of a company's
published jobs at:

    https://apply.workable.com/api/v1/widget/accounts/{account}

The ``{account}`` is the company's Workable subdomain/slug. No API key is
required and there is no server-side keyword search, so the adapter fetches each
configured account's full job list and filters client-side — exactly like the
Greenhouse and Lever adapters.

Docs: https://help.workable.com/hc/en-us/articles/115013356548-Workable-API-Documentation
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

_BASE_URL = "https://apply.workable.com/api/v1/widget/accounts/{account}?details=true"


def _build_location(job: dict) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (location_str, city, country_code, remote) from a Workable job.

    The public widget API puts location at the TOP LEVEL (``country``, ``city``,
    ``state``, ``telecommuting``) plus a richer ``locations`` array whose entries
    carry ``countryCode``. We read the array first (it has the ISO code), then
    fall back to the top-level fields.
    """
    locs = job.get("locations")
    first = locs[0] if isinstance(locs, list) and locs and isinstance(locs[0], dict) else {}

    city = (first.get("city") or job.get("city") or "").strip() or None
    region = (first.get("region") or job.get("state") or "").strip() or None
    country = (first.get("country") or job.get("country") or "").strip() or None
    country_code = (first.get("countryCode") or "").strip() or None

    parts = [p for p in (city, region, country) if p]
    location_str = ", ".join(parts) or None
    remote = "remote" if job.get("telecommuting") else None
    return location_str, city, country_code, remote


class WorkableAdapter:
    """Wraps the Workable public job-widget API.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the public Workable widget endpoint.
    risk:
        ``"low"`` — uses an official, unauthenticated public API endpoint.
    store_full_description:
        ``True`` — full description text is stored when the feed provides it.
    accounts:
        List of account entries — each a slug string or a ``{token, type}`` dict.
        Stored as ``(account, employer_type)`` pairs.
    """

    name = "workable"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self, accounts: list[Any] | None = None) -> None:
        self.accounts: list[tuple[str, str]] = normalize_company_entries(accounts)

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts from the Workable widget feed (client-side filtered)."""
        if not self.accounts:
            log.warning("WorkableAdapter has no accounts configured — skipping")
            return
        if results_wanted <= 0:
            return

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0

        for account, employer_type in self.accounts:
            if total_yielded >= results_wanted:
                break

            url = _BASE_URL.format(account=account)
            try:
                resp = http.get(url, api_source=True)
            except DomainBlockedError as exc:
                log.warning("Workable domain blocked (%s) — skipping account %s", exc, account)
                continue
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching Workable account %s: %s", account, exc)
                continue

            if resp.status_code != 200:
                log.error(
                    "Workable returned HTTP %s for account=%s — skipping",
                    resp.status_code,
                    account,
                )
                continue

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode Workable JSON for account=%s: %s", account, exc)
                continue

            jobs: list[dict] = data.get("jobs") or []
            company_name = (data.get("name") or account).strip() or account

            for job in jobs:
                if total_yielded >= results_wanted:
                    break
                try:
                    title = str(job.get("title") or "").strip()
                    if not title:
                        continue
                    if not keyword_title_match(title, keywords):
                        continue

                    posted_raw = job.get("published_on") or job.get("created_at")
                    if since_aware is not None:
                        parsed = _parse_iso(posted_raw)
                        if parsed is None or parsed < since_aware:
                            continue

                    job_url = (
                        (job.get("url") or job.get("application_url") or job.get("shortlink") or "")
                        .strip()
                        or None
                    )
                    if not job_url:
                        continue

                    location_str, city, country_code, remote = _build_location(job)
                    desc = job.get("description")
                    description = html.unescape(desc) if desc else None
                    shortcode = job.get("shortcode") or job.get("id")

                    raw: dict = {
                        "title": title,
                        "company": company_name,
                        "url": job_url,
                        "description": description,
                        "location": location_str,
                        "city": city,
                        "country": country_code,
                        "posted_date": posted_raw,
                        "source_job_id": str(shortcode) if shortcode is not None else None,
                        "employer_type": employer_type,
                    }
                    if remote:
                        raw["remote"] = remote

                    yield raw
                    total_yielded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Failed to process Workable job (account=%s): %s", account, exc
                    )
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
