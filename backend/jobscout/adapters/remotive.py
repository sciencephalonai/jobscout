"""
Remotive public job board API adapter.

Docs / terms: https://remotive.com/api-documentation
Endpoint: https://remotive.com/api/remote-jobs

The Remotive API is public and requires no key. It returns only remote jobs,
so every yielded record is marked ``remote``. Per Remotive's terms we link
back to the canonical job URL and attribute Remotive as the source; the API
should not be polled more than a few times a day.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = "https://remotive.com/api/remote-jobs"
_MAX_LIMIT = 100  # sensible cap on results requested in a single call

# Matches "$80k - $100k", "$80,000 - $100,000", "USD 80000", "€50k", etc.
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
_SALARY_AMOUNT = re.compile(r"([$€£]?)\s*([\d,]+(?:\.\d+)?)\s*([kK])?")


def _parse_salary(raw: str | None) -> tuple[str | None, float | None, float | None]:
    """Best-effort parse of Remotive's free-text ``salary`` field.

    Returns ``(currency, salary_min, salary_max)``. Any component that cannot
    be determined is returned as ``None``. Returns ``(None, None, None)`` when
    nothing parseable is found, so callers can simply skip salary fields.
    """
    if not raw or not isinstance(raw, str):
        return None, None, None

    text = raw.strip()
    if not text:
        return None, None, None

    matches = _SALARY_AMOUNT.findall(text)
    amounts: list[float] = []
    currency: str | None = None

    for symbol, number, suffix in matches:
        if symbol and currency is None:
            currency = _CURRENCY_SYMBOLS.get(symbol)
        try:
            value = float(number.replace(",", ""))
        except ValueError:
            continue
        if suffix:  # "k" / "K" multiplier
            value *= 1000.0
        # Ignore implausibly small numbers that are likely not salaries.
        if value <= 0:
            continue
        amounts.append(value)

    # Fall back to an explicit ISO-ish currency code mentioned in the text.
    if currency is None:
        code_match = re.search(r"\b(USD|EUR|GBP|CAD|AUD|CHF|INR)\b", text, re.IGNORECASE)
        if code_match:
            currency = code_match.group(1).upper()

    if not amounts:
        return currency, None, None

    salary_min = min(amounts)
    salary_max = max(amounts) if len(amounts) > 1 else None
    return currency, salary_min, salary_max


def _parse_publication_date(value: str | None) -> datetime | None:
    """Parse Remotive's ISO ``publication_date`` (e.g. ``2026-06-02T07:53:42``).

    Returns a timezone-aware UTC datetime, or ``None`` if it cannot be parsed.
    Used only for the *since* comparison; the raw string is what we yield so
    the ingestion layer can re-parse it.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class RemotiveAdapter:
    """Wraps the Remotive ``/api/remote-jobs`` endpoint.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official public Remotive REST API.
    risk:
        ``"low"`` — uses an official, sanctioned public API endpoint.
    store_full_description:
        ``True`` — full (HTML) description text is available and stored.
    """

    name = "remotive"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self) -> None:
        pass

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
        """Yield raw job dicts from the Remotive API.

        Parameters
        ----------
        keywords:
            Search terms joined with a space as the ``search`` parameter.
        location:
            Ignored for filtering — Remotive only returns remote jobs and has
            no geographic ``where`` parameter. Each job's
            ``candidate_required_location`` is surfaced as ``location``.
        results_wanted:
            Upper bound on the total number of results to yield. Also passed
            (capped) as the API ``limit`` parameter.
        since:
            If given, jobs whose ``publication_date`` is older than this
            datetime are skipped.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if results_wanted <= 0:
            return

        search_term = " ".join(k.strip() for k in keywords if k.strip())

        params: dict = {"limit": min(results_wanted, _MAX_LIMIT)}
        if search_term:
            params["search"] = search_term

        try:
            resp = http.get(_BASE_URL, params=params, api_source=self.method == "api")
        except DomainBlockedError as exc:
            log.warning("Remotive domain blocked (%s) — skipping", exc)
            return
        except Exception as exc:  # noqa: BLE001
            log.error("HTTP error fetching Remotive jobs: %s", exc)
            return

        if resp.status_code != 200:
            log.error("Remotive returned HTTP %s — skipping", resp.status_code)
            return

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to decode Remotive JSON: %s", exc)
            return

        jobs: list[dict] = data.get("jobs") or []
        if not jobs:
            log.debug("Remotive returned no jobs for search=%r", search_term)
            return

        total_yielded = 0
        for job in jobs:
            if total_yielded >= results_wanted:
                break

            if since is not None:
                published = _parse_publication_date(job.get("publication_date"))
                if published is not None:
                    since_aware = (
                        since if since.tzinfo is not None else since.replace(tzinfo=UTC)
                    )
                    if published < since_aware:
                        continue

            normalised = _normalise(job)
            if normalised is not None:
                yield normalised
                total_yielded += 1

        log.debug(
            "Remotive search=%r: yielded %d jobs (of %d returned)",
            search_term,
            total_yielded,
            len(jobs),
        )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(job: dict) -> dict | None:
    """Convert a raw Remotive job dict to the JobScout canonical shape.

    Returns ``None`` (and logs) if mandatory fields (title/url) are missing.
    """
    try:
        title = (job.get("title") or "").strip() or None
        url = (job.get("url") or "").strip() or None

        if not title or not url:
            log.debug("Remotive job missing title/url, skipping: %s", job.get("id"))
            return None

        company = (job.get("company_name") or "").strip() or None
        location = (job.get("candidate_required_location") or "").strip() or None
        description = job.get("description") or None
        # Keep the raw ISO string; the ingestion layer parses it.
        posted_date = job.get("publication_date") or None

        job_id = job.get("id")
        source_job_id = str(job_id) if job_id is not None else None

        currency, salary_min, salary_max = _parse_salary(job.get("salary"))

        result: dict = {
            "title": title,
            "company": company,
            "url": url,
            "description": description,
            "remote": "remote",  # every Remotive job is remote
            "location": location,
            "posted_date": posted_date,
            "source_job_id": source_job_id,
        }

        # Only include salary fields when we could parse something useful.
        if salary_min is not None:
            result["salary_min"] = salary_min
        if salary_max is not None:
            result["salary_max"] = salary_max
        if currency is not None:
            result["salary_currency"] = currency

        return result

    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to normalise Remotive job (id=%s): %s", job.get("id"), exc)
        return None
