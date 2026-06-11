"""
Adzuna official job search API adapter.

Docs: https://developer.adzuna.com/activedocs#!/adzuna/search
Rate limits / terms: https://developer.adzuna.com/
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from math import ceil

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError
from jobscout.config import settings
from jobscout.normalize import normalize_remote

log = logging.getLogger(__name__)

_BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
_PAGE_SIZE = 50  # Adzuna's maximum results_per_page

# Adzuna returns salaries in the local currency; for simplicity we map
# country code → ISO 4217 currency.  Add more mappings as countries are added.
_COUNTRY_CURRENCY: dict[str, str] = {
    "gb": "GBP",
    "us": "USD",
    "ca": "CAD",
    "au": "AUD",
    "de": "EUR",
    "fr": "EUR",
    "nl": "EUR",
    "be": "EUR",
    "at": "EUR",
    "ch": "CHF",
    "nz": "NZD",
    "in": "INR",
    "za": "ZAR",
    "br": "BRL",
    "mx": "MXN",
    "sg": "SGD",
    "pl": "PLN",
    "it": "EUR",
    "es": "EUR",
    "ru": "RUB",
}


def _currency_for(country: str) -> str:
    return _COUNTRY_CURRENCY.get(country.lower(), "USD")


def _parse_date(value: str | None) -> str | None:
    """Pass through the ISO timestamp string from Adzuna unchanged so that the
    ingestion layer can parse it.  Returns None if value is falsy."""
    return value or None


class AdzunaAdapter:
    """Wraps the Adzuna /jobs/{country}/search endpoint.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official Adzuna REST API.
    risk:
        ``"low"`` — uses an official, authenticated API endpoint.
    store_full_description:
        ``True`` — full description text is available and stored.
    countries:
        List of ISO 3166-1 alpha-2 country codes to query.
        Defaults to ``["us"]``.
    """

    name = "adzuna"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self, countries: list[str] | None = None) -> None:
        self.countries: list[str] = [c.lower() for c in (countries or ["us"])]

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
        """Yield raw job dicts from the Adzuna API.

        Iterates over all configured countries and paginates through results
        until *results_wanted* jobs have been yielded or the API returns an
        empty page.

        Parameters
        ----------
        keywords:
            Search terms joined with a space as the ``what`` parameter.
        location:
            Free-text location forwarded as the ``where`` parameter.
            Pass ``None`` to search globally (within the country).
        results_wanted:
            Upper bound on the total number of results to yield.
        since:
            If given, only jobs posted on or after this datetime are returned
            (passed to Adzuna as ``max_days_old``).
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if not settings.adzuna_app_id or not settings.adzuna_app_key:
            log.warning(
                "adzuna_app_id / adzuna_app_key not configured — skipping AdzunaAdapter"
            )
            return

        what = " ".join(k.strip() for k in keywords if k.strip())
        if not what:
            log.warning("AdzunaAdapter.search called with empty keywords — skipping")
            return

        # Adzuna's `where` is a *geographic* filter only — passing "remote"
        # matches no location and returns zero results. When a remote location
        # is requested, fold the term into the keyword query and leave `where`
        # unset so the search runs country-wide.
        where: str | None = location
        if location and normalize_remote(location) == "remote":
            what = f"{what} remote"
            where = None

        total_yielded = 0

        for country in self.countries:
            if total_yielded >= results_wanted:
                break

            yielded_for_country = 0
            page = 1

            while total_yielded < results_wanted:
                remaining = results_wanted - total_yielded
                page_size = min(_PAGE_SIZE, remaining)

                url = _BASE_URL.format(country=country, page=page)
                params: dict = {
                    "app_id": settings.adzuna_app_id,
                    "app_key": settings.adzuna_app_key,
                    "what": what,
                    "results_per_page": page_size,
                    "content-type": "application/json",
                    "full_description": "1",
                }
                if where:
                    params["where"] = where
                if since is not None:
                    # Adzuna expects a positive integer number of days
                    now = datetime.now(tz=UTC)
                    # Ensure since is timezone-aware for arithmetic
                    since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
                    days_old = max(1, ceil((now - since_aware).total_seconds() / 86_400))
                    params["max_days_old"] = days_old

                try:
                    resp = http.get(url, params=params, api_source=self.method == "api")
                except DomainBlockedError as exc:
                    log.warning("Adzuna domain blocked (%s) — skipping country %s", exc, country)
                    break
                except Exception as exc:  # noqa: BLE001
                    log.error("HTTP error fetching Adzuna page %d for country %s: %s", page, country, exc)
                    break

                if resp.status_code != 200:
                    log.error(
                        "Adzuna returned HTTP %s for country=%s page=%d — stopping pagination",
                        resp.status_code,
                        country,
                        page,
                    )
                    break

                try:
                    data = resp.json()
                except Exception as exc:  # noqa: BLE001
                    log.error("Failed to decode Adzuna JSON for country=%s page=%d: %s", country, page, exc)
                    break

                results: list[dict] = data.get("results") or []
                if not results:
                    log.debug(
                        "Adzuna returned empty results for country=%s page=%d — done", country, page
                    )
                    break

                for result in results:
                    if total_yielded >= results_wanted:
                        break
                    job = _normalise(result, country)
                    if job is not None:
                        yield job
                        total_yielded += 1
                        yielded_for_country += 1

                log.debug(
                    "Adzuna country=%s page=%d: got %d results (total yielded so far: %d)",
                    country,
                    page,
                    len(results),
                    total_yielded,
                )

                # If the page came back with fewer results than requested, we've
                # exhausted this country.
                if len(results) < page_size:
                    break

                page += 1


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(result: dict, country: str) -> dict | None:
    """Convert a raw Adzuna result dict to the JobScout canonical shape.

    Returns ``None`` (and logs a warning) if mandatory fields are missing.
    """
    try:
        job_id = result.get("id")
        title = result.get("title", "").strip() or None
        url = result.get("redirect_url", "").strip() or None

        if not url:
            log.debug("Adzuna result missing redirect_url, skipping: %s", result.get("id"))
            return None

        # Company
        company_obj = result.get("company") or {}
        company = company_obj.get("display_name") or None

        # Location
        location_obj = result.get("location") or {}
        location_display = location_obj.get("display_name") or None
        area: list[str] = location_obj.get("area") or []
        city = area[-1].strip() if area else None

        # Salary
        salary_min = result.get("salary_min")
        salary_max = result.get("salary_max")
        # Adzuna sometimes sends 0.0 for unknown salaries
        if salary_min == 0:
            salary_min = None
        if salary_max == 0:
            salary_max = None

        currency = _currency_for(country)

        # Posted date — keep as raw ISO string; ingestion layer parses it
        posted_date = _parse_date(result.get("created"))

        return {
            "source_job_id": str(job_id) if job_id is not None else None,
            "title": title,
            "company": company,
            "location": location_display,
            "city": city,
            "country": country,
            "description": result.get("description") or None,
            "url": url,
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": currency,
            "posted_date": posted_date,
        }

    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to normalise Adzuna result (id=%s): %s", result.get("id"), exc)
        return None
