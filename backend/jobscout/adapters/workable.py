"""
Workable public job-board API adapter.

Workable hosts each company's careers page under a *Workable subdomain* (the
"account" token). The public, unauthenticated widget endpoint exposes a
company's openings::

    https://apply.workable.com/api/v1/widget/accounts/{account}?details=true

No API key is required. With ``details=true`` the response includes the full
HTML ``description`` for each job. There is no server-side keyword or location
search, so the adapter fetches the full board for each configured account and
filters client-side.

Confirmed response shape (``apply.workable.com`` widget endpoint)::

    {"name": "...", "description": "...", "jobs": [
        {"title", "shortcode", "url", "application_url", "published_on",
         "created_at", "country", "city", "state", "telecommuting",
         "locations", "description", ...}
    ]}

``apply.workable.com/robots.txt`` publishes ``Disallow:`` (nothing disallowed),
so the API path is permitted regardless; the request is additionally marked as
an API source.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = "https://apply.workable.com/api/v1/widget/accounts/{account}?details=true"


def _parse_created_at(value: str | None) -> datetime | None:
    """Parse a Workable date string (e.g. ``2025-11-21`` or an ISO timestamp)
    into an aware UTC datetime. Returns ``None`` if falsy or unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class WorkableAdapter:
    """Wraps the Workable public job-board widget API.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official Workable public widget API.
    risk:
        ``"low"`` — uses an official, unauthenticated public API endpoint.
    store_full_description:
        ``True`` — full HTML description is available (``details=true``).
    companies:
        List of Workable account tokens (careers subdomain) to query.
        Defaults to an empty list.
    """

    name = "workable"
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
        """Yield raw job dicts from the Workable widget API.

        Iterates over all configured account tokens, fetching each board's full
        job list and filtering client-side until *results_wanted* jobs have been
        yielded in total. Never raises — all errors are logged and skipped.

        Parameters
        ----------
        keywords:
            Search terms. A job is kept only if the (space-joined,
            case-insensitive) keyword string appears in the job title. If
            *keywords* is empty, all jobs are kept.
        location:
            Unused — Workable's widget API has no server-side location filter;
            location handling happens downstream.
        results_wanted:
            Upper bound on the total number of results to yield.
        since:
            If given, only jobs whose ``created_at`` is on or after this
            datetime are yielded.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if not self.companies:
            log.warning("WorkableAdapter has no companies configured — skipping")
            return

        if results_wanted <= 0:
            return

        # Single case-insensitive needle from the keywords. No server-side
        # search, so match this substring against the job title client-side.
        needle = " ".join(k.strip() for k in keywords if k.strip()).lower()

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0

        for account in self.companies:
            if total_yielded >= results_wanted:
                break

            url = _BASE_URL.format(account=account)

            try:
                resp = http.get(url, api_source=self.method == "api")
            except DomainBlockedError as exc:
                log.warning(
                    "Workable domain blocked (%s) — skipping account %s", exc, account
                )
                continue
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "HTTP error fetching Workable board for %s: %s", account, exc
                )
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
            log.debug(
                "Workable account=%s: fetched %d jobs (total yielded so far: %d)",
                account,
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

                    # Client-side `since` filter on created_at.
                    created_at_raw = job.get("created_at") or job.get("published_on")
                    if since_aware is not None:
                        created_at = _parse_created_at(created_at_raw)
                        if created_at is None or created_at < since_aware:
                            continue

                    shortcode = job.get("shortcode") or job.get("code")
                    url_value = (
                        job.get("url")
                        or job.get("application_url")
                        or job.get("shortlink")
                        or ""
                    ).strip() or None

                    description_html = job.get("description")
                    description = (
                        html.unescape(description_html) if description_html else None
                    )

                    # Build a human-readable location string from city/state/country.
                    location_parts = [
                        str(job.get(part)).strip()
                        for part in ("city", "state", "country")
                        if job.get(part)
                    ]
                    location_str = ", ".join(p for p in location_parts if p) or None

                    # Remote signal: explicit telecommuting flag, or a location
                    # entry flagged remote.
                    remote_value: str | None = None
                    if job.get("telecommuting"):
                        remote_value = "remote"
                    else:
                        for loc in job.get("locations") or []:
                            if isinstance(loc, dict) and (
                                loc.get("telecommuting") or loc.get("workplaceType") == "remote"
                            ):
                                remote_value = "remote"
                                break

                    yield {
                        "title": title,
                        "company": account,
                        "url": url_value,
                        "description": description,
                        "location": location_str,
                        "remote": remote_value,
                        "posted_date": created_at_raw,
                        "source_job_id": str(shortcode) if shortcode else None,
                    }
                    total_yielded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Failed to process Workable job (account=%s, shortcode=%s): %s",
                        account,
                        job.get("shortcode"),
                        exc,
                    )
                    continue
