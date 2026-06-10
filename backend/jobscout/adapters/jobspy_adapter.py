"""
JobSpy scraping adapter — HIGH RISK, disabled by default.

python-jobspy drives headless scrapers against Google Jobs, ZipRecruiter,
Indeed, LinkedIn, etc.  Because these are web scrapers (not official APIs)
this adapter is classified as high-risk and must be explicitly enabled in
sources.yaml / configuration before the ingestion scheduler will use it.

Compliance notes
----------------
- ``store_full_description = False``: only a 280-character snippet is stored,
  as required by the compliance policy for scraped sources.
- ``http`` (CompliantHttpClient) is accepted by the protocol but is NOT passed
  to jobspy — jobspy manages its own HTTP stack.  The parameter exists solely
  to satisfy the JobSourceAdapter protocol.
- NaN / None handling: pandas uses ``float('nan')`` for missing cells; we
  check each value with ``pandas.notna()`` before including it in the output.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

_DESCRIPTION_SNIPPET_LEN = 280


def _safe(value: Any) -> Any:
    """Return *value* if it is not NaN/None, otherwise return ``None``.

    Works for pandas scalars (float NaN), ``None``, and regular values.
    """
    try:
        import pandas as pd  # pandas is a jobspy dep, always available if jobspy is

        if pd.isna(value):
            return None
    except (ImportError, TypeError, ValueError):
        # pd.isna raises TypeError/ValueError on some object types
        pass
    return value if value is not None else None


class JobSpyAdapter:
    """Wraps python-jobspy's ``scrape_jobs`` function.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"scrape"`` — uses web scraping internally.
    risk:
        ``"high"`` — relies on unofficial scrapers that can be blocked or
        break without warning.
    store_full_description:
        ``False`` — only a 280-character snippet is stored per compliance
        policy for scraped sources.
    sites:
        jobspy site identifiers to query.  Defaults to
        ``["google", "zip_recruiter"]``.
    hours_old:
        Maximum age of listings in hours.  Defaults to 168 (7 days).
    """

    name = "jobspy"
    method = "scrape"
    risk = "high"
    store_full_description = False

    def __init__(
        self,
        sites: list[str] | None = None,
        hours_old: int = 168,
    ) -> None:
        self.sites: list[str] = sites or ["google", "zip_recruiter"]
        self.hours_old: int = hours_old

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: Any,  # CompliantHttpClient — required by protocol, not used here
    ) -> Iterator[dict]:
        """Yield raw job dicts via python-jobspy.

        Parameters
        ----------
        keywords:
            Search terms joined with a space as ``search_term``.
        location:
            Free-text location.  Passed verbatim to jobspy; use ``""`` for
            remote/global searches.
        results_wanted:
            Upper bound on the number of results to yield.
        since:
            If given, ``hours_old`` is overridden with the number of hours
            since *since* (clamped to 1).
        http:
            Ignored.  Accepted to satisfy the :class:`JobSourceAdapter`
            protocol.
        """
        try:
            from jobspy import scrape_jobs
        except ImportError:
            log.warning(
                "python-jobspy is not installed — skipping JobSpyAdapter.  "
                "Install it with: pip install python-jobspy"
            )
            return

        search_term = " ".join(k.strip() for k in keywords if k.strip())
        if not search_term:
            log.warning("JobSpyAdapter.search called with empty keywords — skipping")
            return

        # Allow `since` to override hours_old
        effective_hours_old = self.hours_old
        if since is not None:
            now = datetime.now(tz=UTC)
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
            hours_delta = max(1, int((now - since_aware).total_seconds() / 3600))
            effective_hours_old = hours_delta
            log.debug(
                "JobSpyAdapter: since=%s → hours_old=%d", since.isoformat(), effective_hours_old
            )

        try:
            df = scrape_jobs(
                site_name=self.sites,
                search_term=search_term,
                location=location or "",
                results_wanted=results_wanted,
                hours_old=effective_hours_old,
                country_indeed="USA",
            )
        except Exception as exc:  # noqa: BLE001
            log.error("JobSpyAdapter: scrape_jobs raised an error: %s", exc)
            return

        if df is None or df.empty:
            log.debug("JobSpyAdapter: scrape_jobs returned no results for '%s'", search_term)
            return

        yielded = 0
        for _, row in df.iterrows():
            if yielded >= results_wanted:
                break
            job = _row_to_dict(row)
            if job is not None:
                yield job
                yielded += 1

        log.debug("JobSpyAdapter: yielded %d jobs for '%s'", yielded, search_term)


# ---------------------------------------------------------------------------
# Row normalisation
# ---------------------------------------------------------------------------


def _row_to_dict(row: Any) -> dict | None:
    """Convert a single pandas DataFrame row to a JobScout canonical dict.

    Returns ``None`` if an unrecoverable error occurs during normalisation.
    """
    try:
        # Mandatory: at least a URL must be present
        url = _safe(row.get("job_url") if hasattr(row, "get") else getattr(row, "job_url", None))
        if not url:
            log.debug("JobSpyAdapter: row missing job_url, skipping")
            return None

        title = _safe(_get(row, "title"))
        company = _safe(_get(row, "company"))
        location = _safe(_get(row, "location"))

        is_remote_raw = _safe(_get(row, "is_remote"))
        remote: str | None = None
        if is_remote_raw is not None:
            try:
                remote = "remote" if bool(is_remote_raw) else None
            except (ValueError, TypeError):
                remote = None

        # Description — store snippet only (compliance policy)
        raw_desc = _safe(_get(row, "description"))
        description: str | None = None
        if raw_desc is not None:
            description = str(raw_desc)[:_DESCRIPTION_SNIPPET_LEN]

        salary_min = _safe(_get(row, "min_amount"))
        salary_max = _safe(_get(row, "max_amount"))
        currency = _safe(_get(row, "currency"))
        posted_date = _safe(_get(row, "date_posted"))

        result: dict = {
            "source_job_id": None,
            "url": str(url),
        }

        if title is not None:
            result["title"] = str(title)
        if company is not None:
            result["company"] = str(company)
        if location is not None:
            result["location"] = str(location)
        if remote is not None:
            result["remote"] = remote
        if description is not None:
            result["description"] = description
        if salary_min is not None:
            try:
                result["salary_min"] = float(salary_min)
            except (ValueError, TypeError):
                pass
        if salary_max is not None:
            try:
                result["salary_max"] = float(salary_max)
            except (ValueError, TypeError):
                pass
        if currency is not None:
            result["salary_currency"] = str(currency)
        if posted_date is not None:
            result["posted_date"] = posted_date

        return result

    except Exception as exc:  # noqa: BLE001
        log.warning("JobSpyAdapter: failed to normalise row: %s", exc)
        return None


def _get(row: Any, key: str) -> Any:
    """Retrieve *key* from a pandas Series row, returning ``None`` on error."""
    try:
        if hasattr(row, "get"):
            return row.get(key)
        return getattr(row, key, None)
    except Exception:  # noqa: BLE001
        return None
