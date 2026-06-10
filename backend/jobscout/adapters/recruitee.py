"""
Recruitee public offers API adapter.

Recruitee (an ATS, now part of Tellent) exposes an unauthenticated, per-company
offers endpoint. Each company publishes its openings under a Recruitee
*subdomain* (the slug in its careers URL, e.g. ``tether``, ``o2h``). No API key
is required::

    https://{company}.recruitee.com/api/offers/

The response shape is ``{"offers": [ { ... } ]}`` where each offer carries
``id``, ``title``, ``careers_url`` (the direct apply/careers page), ``location``
/ ``city`` / ``country``, ``description`` (HTML), ``requirements`` (HTML),
``created_at`` and ``slug``. There is no server-side keyword or location search,
so the adapter fetches each company's full board and filters client-side.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = "https://{company}.recruitee.com/api/offers/"


def _parse_created_at(value: str | None) -> datetime | None:
    """Parse Recruitee's ``created_at`` timestamp into an aware UTC datetime.

    Recruitee emits timestamps like ``"2026-06-03 16:00:19 UTC"`` (a space
    separator and a trailing ``UTC`` token) as well as occasional ISO-8601
    values. Returns ``None`` if the value is falsy or unparseable.
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    # Normalise the common "... UTC" / "Z" suffixes to an ISO-parseable form.
    candidate = raw
    if candidate.endswith(" UTC"):
        candidate = candidate[: -len(" UTC")].strip()
    candidate = candidate.replace("Z", "+00:00")
    # Recruitee uses a space between date and time; isoformat wants a "T".
    if " " in candidate and "T" not in candidate:
        candidate = candidate.replace(" ", "T", 1)

    try:
        dt = datetime.fromisoformat(candidate)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _looks_remote(offer: dict, location: str | None) -> str:
    """Return ``"remote"`` if the offer appears remote, else ``""``.

    Prefers Recruitee's explicit boolean ``remote`` flag, falling back to a
    substring check on the location string.
    """
    if offer.get("remote") is True:
        return "remote"
    if location and "remote" in location.lower():
        return "remote"
    return ""


class RecruiteeAdapter:
    """Wraps the Recruitee public offers API.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official Recruitee public API.
    risk:
        ``"low"`` — uses an official, unauthenticated public API endpoint.
    store_full_description:
        ``True`` — full description text is available and stored.
    companies:
        List of Recruitee subdomains (company slugs) to query. Defaults to an
        empty list.
    """

    name = "recruitee"
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
        """Yield raw job dicts from the Recruitee API.

        Iterates over all configured company subdomains, fetching each board's
        full offer list and filtering client-side until *results_wanted* jobs
        have been yielded in total. Never raises — any per-company error is
        logged and the loop continues with the next company.

        Parameters
        ----------
        keywords:
            Search terms. A job is kept only if the (space-joined,
            case-insensitive) keyword string appears in the job title. If
            *keywords* is empty, all jobs are kept.
        location:
            Unused — Recruitee has no server-side location filter and the
            ingestion layer performs location handling downstream.
        results_wanted:
            Upper bound on the total number of results to yield.
        since:
            If given, only offers whose ``created_at`` is on or after this
            datetime are yielded.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if not self.companies:
            log.warning("RecruiteeAdapter has no companies configured — skipping")
            return

        if results_wanted <= 0:
            return

        # Single case-insensitive needle from the keywords. Recruitee offers no
        # server-side search, so we match this substring against the job title
        # client-side. Empty keywords → keep everything.
        needle = " ".join(k.strip() for k in keywords if k.strip()).lower()

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0

        for company in self.companies:
            if total_yielded >= results_wanted:
                break

            url = _BASE_URL.format(company=company)

            try:
                resp = http.get(url, api_source=self.method == "api")
            except DomainBlockedError as exc:
                log.warning(
                    "Recruitee domain blocked (%s) — skipping company %s", exc, company
                )
                continue
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "HTTP error fetching Recruitee offers for %s: %s", company, exc
                )
                continue

            if resp.status_code != 200:
                log.error(
                    "Recruitee returned HTTP %s for company=%s — skipping",
                    resp.status_code,
                    company,
                )
                continue

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Failed to decode Recruitee JSON for company=%s: %s", company, exc
                )
                continue

            offers: list[dict] = (data or {}).get("offers") or []
            log.debug(
                "Recruitee company=%s: fetched %d offers (total yielded so far: %d)",
                company,
                len(offers),
                total_yielded,
            )

            for offer in offers:
                if total_yielded >= results_wanted:
                    break

                try:
                    title = str(offer.get("title") or "").strip()
                    if not title:
                        continue

                    # Client-side keyword filter against the title.
                    if needle and needle not in title.lower():
                        continue

                    # Client-side `since` filter on created_at.
                    created_at_raw = offer.get("created_at")
                    if since_aware is not None:
                        created_at = _parse_created_at(created_at_raw)
                        if created_at is None or created_at < since_aware:
                            continue

                    offer_id = offer.get("id")

                    # Direct apply / careers page. Fall back to `url` if present.
                    apply_url = (
                        str(offer.get("careers_url") or offer.get("url") or "").strip()
                        or None
                    )

                    # Description (HTML) → unescape; append requirements if any.
                    description_html = offer.get("description")
                    description = (
                        html.unescape(str(description_html)) if description_html else None
                    )
                    requirements_html = offer.get("requirements")
                    if requirements_html:
                        requirements = html.unescape(str(requirements_html))
                        description = (
                            f"{description}\n\n{requirements}" if description else requirements
                        )

                    # Location: prefer the explicit `location`, else city/country.
                    location_value = (offer.get("location") or "").strip() or None
                    if not location_value:
                        parts = [
                            str(offer.get(k)).strip()
                            for k in ("city", "country")
                            if offer.get(k)
                        ]
                        location_value = ", ".join(p for p in parts if p) or None

                    remote_value = _looks_remote(offer, location_value)

                    yield {
                        "title": title,
                        "company": company,
                        "url": apply_url,
                        "description": description,
                        "location": location_value,
                        "remote": remote_value,
                        "posted_date": created_at_raw,
                        "source_job_id": str(offer_id) if offer_id is not None else None,
                        # Native Recruitee code (e.g. "fulltime_permanent");
                        # normalize maps it to a canonical employment type.
                        "employment_type": offer.get("employment_type_code") or None,
                    }
                    total_yielded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Failed to process Recruitee offer (company=%s, id=%s): %s",
                        company,
                        offer.get("id"),
                        exc,
                    )
                    continue
