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

Like the other per-company ATS adapters in JobScout, ``companies`` accepts
``{token, type}`` entries so the cap-exempt ``employer_type`` is stamped per
company (see :func:`~jobscout.adapters.greenhouse.normalize_company_entries`).
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

    candidate = raw
    if candidate.endswith(" UTC"):
        candidate = candidate[: -len(" UTC")].strip()
    candidate = candidate.replace("Z", "+00:00")
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
    """Return ``"remote"`` if the offer appears remote, else ``""``."""
    if offer.get("remote") is True:
        return "remote"
    if location and "remote" in location.lower():
        return "remote"
    return ""


class RecruiteeAdapter:
    """Wraps the Recruitee public offers API (per-company boards)."""

    name = "recruitee"
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
        """Yield raw job dicts from each configured Recruitee board (client-side filtered)."""
        if not self.companies:
            log.warning("RecruiteeAdapter has no companies configured — skipping")
            return
        if results_wanted <= 0:
            return

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0

        for company, employer_type in self.companies:
            if total_yielded >= results_wanted:
                break

            url = _BASE_URL.format(company=company)
            try:
                resp = http.get(url, api_source=True)
            except DomainBlockedError as exc:
                log.warning("Recruitee domain blocked (%s) — skipping company %s", exc, company)
                continue
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching Recruitee offers for %s: %s", company, exc)
                continue

            if resp.status_code != 200:
                log.error(
                    "Recruitee returned HTTP %s for company=%s — skipping",
                    resp.status_code, company,
                )
                continue

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode Recruitee JSON for company=%s: %s", company, exc)
                continue

            offers: list[dict] = (data or {}).get("offers") or []

            for offer in offers:
                if total_yielded >= results_wanted:
                    break
                try:
                    title = str(offer.get("title") or "").strip()
                    if not title:
                        continue
                    if not keyword_title_match(title, keywords):
                        continue

                    created_at_raw = offer.get("created_at")
                    if since_aware is not None:
                        created_at = _parse_created_at(created_at_raw)
                        if created_at is None or created_at < since_aware:
                            continue

                    offer_id = offer.get("id")
                    apply_url = (
                        str(offer.get("careers_url") or offer.get("url") or "").strip() or None
                    )
                    if not apply_url:
                        continue

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

                    location_value = (offer.get("location") or "").strip() or None
                    if not location_value:
                        parts = [
                            str(offer.get(k)).strip()
                            for k in ("city", "country")
                            if offer.get(k)
                        ]
                        location_value = ", ".join(p for p in parts if p) or None

                    raw: dict = {
                        "title": title,
                        "company": company,
                        "url": apply_url,
                        "description": description,
                        "location": location_value,
                        "posted_date": created_at_raw,
                        "source_job_id": str(offer_id) if offer_id is not None else None,
                        "employer_type": employer_type,
                    }
                    remote_value = _looks_remote(offer, location_value)
                    if remote_value:
                        raw["remote"] = remote_value

                    yield raw
                    total_yielded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Failed to process Recruitee offer (company=%s, id=%s): %s",
                        company, offer.get("id"), exc,
                    )
                    continue
