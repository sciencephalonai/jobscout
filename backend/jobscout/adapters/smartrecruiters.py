"""
SmartRecruiters public Posting API adapter.

Docs: https://developers.smartrecruiters.com/reference/postings-1

SmartRecruiters exposes an unauthenticated, per-company Posting API. Each
company publishes its openings under a *company identifier* (the slug used in
its SmartRecruiters careers URL, e.g. ``Visa``, ``Bosch``, ``ServiceNow``).
No API key is required.

Two endpoints are used:

* LIST   ``/v1/companies/{company}/postings?limit=100``
  Returns a page of postings under ``content`` with metadata but no
  description body.
* DETAIL ``/v1/companies/{company}/postings/{posting_id}``
  Returns the full posting including the HTML job description at
  ``jobAd.sections.jobDescription.text`` and the public apply/landing URLs
  (``postingUrl`` / ``applyUrl``).

There is no server-side keyword filter exposed here, so the adapter fetches the
list per company and filters client-side, fetching the detail endpoint only for
postings it intends to yield.

robots.txt for ``api.smartrecruiters.com`` publishes ``Disallow: /`` for the
generic ``User-agent: *`` (with an explicit ``Allow: /v1/companies/`` only for
LinkedInBot). This is the standard "block scrapers" pattern aimed at crawlers,
not at the sanctioned public REST API. Because this adapter's ``method == "api"``
it issues requests with ``api_source=True``, so the blocklist is still enforced
but the robots.txt check is skipped — identical to the Greenhouse/Adzuna
adapters.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_LIST_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100"
_DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"


def _parse_released_date(value: str | None) -> datetime | None:
    """Parse SmartRecruiters' ``releasedDate`` ISO-8601 timestamp into an aware
    UTC datetime.  Returns ``None`` if the value is falsy or unparseable.

    SmartRecruiters emits a trailing ``Z`` (e.g. ``2026-04-23T16:54:54.835Z``)
    which older Pythons' ``fromisoformat`` rejects, so normalise it first.
    """
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _join_location(location: dict) -> str | None:
    """Join the city / region / country fields of a posting ``location`` dict
    into a single human-readable string.  Returns ``None`` if all are empty."""
    if not isinstance(location, dict):
        return None
    parts = [
        str(location.get(k) or "").strip()
        for k in ("city", "region", "country")
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


class SmartRecruitersAdapter:
    """Wraps the SmartRecruiters public Posting API.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official SmartRecruiters public API.
    risk:
        ``"low"`` — uses an official, unauthenticated public API endpoint.
    store_full_description:
        ``True`` — full description text is available and stored.
    companies:
        List of SmartRecruiters company identifiers to query.
        Defaults to an empty list.
    """

    name = "smartrecruiters"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self, companies: list[str] | None = None) -> None:
        self.companies: list[str] = list(companies or [])

    # ------------------------------------------------------------------
    # Detail fetch helper
    # ------------------------------------------------------------------

    def _fetch_detail(
        self, company: str, posting_id: str, http: CompliantHttpClient
    ) -> dict | None:
        """Fetch the DETAIL endpoint for one posting.

        Returns the parsed JSON dict, or ``None`` on any error (network,
        non-200, decode).  Never raises.
        """
        url = _DETAIL_URL.format(company=company, posting_id=posting_id)
        try:
            resp = http.get(url, api_source=self.method == "api")
        except DomainBlockedError as exc:
            log.warning(
                "SmartRecruiters detail blocked (%s) — company=%s id=%s",
                exc,
                company,
                posting_id,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "HTTP error fetching SmartRecruiters detail (company=%s id=%s): %s",
                company,
                posting_id,
                exc,
            )
            return None

        if resp.status_code != 200:
            log.warning(
                "SmartRecruiters detail returned HTTP %s (company=%s id=%s)",
                resp.status_code,
                company,
                posting_id,
            )
            return None

        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Failed to decode SmartRecruiters detail JSON (company=%s id=%s): %s",
                company,
                posting_id,
                exc,
            )
            return None

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
        """Yield raw job dicts from the SmartRecruiters Posting API.

        Iterates over all configured company identifiers, fetching each
        company's posting list and filtering client-side until *results_wanted*
        jobs have been yielded in total.  For each kept posting the DETAIL
        endpoint is fetched to obtain the HTML description.

        Parameters
        ----------
        keywords:
            Search terms.  A posting is kept only if the (space-joined,
            case-insensitive) keyword string appears in the posting ``name``
            (title).  If *keywords* is empty, all postings are kept.
        location:
            Unused — SmartRecruiters' list endpoint has no server-side location
            filter here and location handling happens downstream.
        results_wanted:
            Upper bound on the total number of results to yield across all
            companies.
        since:
            If given, only postings whose ``releasedDate`` is on or after this
            datetime are yielded.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if not self.companies:
            log.warning("SmartRecruitersAdapter has no companies configured — skipping")
            return

        if results_wanted <= 0:
            return

        # Build a single case-insensitive needle from the keywords. The list
        # endpoint offers no server-side search, so we match this substring
        # against the posting name client-side. Empty keywords → keep all.
        needle = " ".join(k.strip() for k in keywords if k.strip()).lower()

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0

        for company in self.companies:
            if total_yielded >= results_wanted:
                break

            url = _LIST_URL.format(company=company)

            try:
                resp = http.get(url, api_source=self.method == "api")
            except DomainBlockedError as exc:
                log.warning(
                    "SmartRecruiters domain blocked (%s) — skipping company %s",
                    exc,
                    company,
                )
                continue
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "HTTP error fetching SmartRecruiters postings for %s: %s",
                    company,
                    exc,
                )
                continue

            if resp.status_code != 200:
                log.error(
                    "SmartRecruiters returned HTTP %s for company=%s — skipping",
                    resp.status_code,
                    company,
                )
                continue

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Failed to decode SmartRecruiters JSON for company=%s: %s",
                    company,
                    exc,
                )
                continue

            postings: list[dict] = data.get("content") or []
            log.debug(
                "SmartRecruiters company=%s: fetched %d postings (total yielded so far: %d)",
                company,
                len(postings),
                total_yielded,
            )

            for posting in postings:
                if total_yielded >= results_wanted:
                    break

                try:
                    title = str(posting.get("name") or "").strip()
                    if not title:
                        continue

                    # Client-side keyword filter against the title.
                    if needle and needle not in title.lower():
                        continue

                    # Client-side `since` filter on releasedDate.
                    released_raw = posting.get("releasedDate")
                    if since_aware is not None:
                        released = _parse_released_date(released_raw)
                        if released is None or released < since_aware:
                            continue

                    posting_id = posting.get("id")
                    if posting_id is None:
                        continue
                    posting_id = str(posting_id)

                    company_obj = posting.get("company") or {}
                    company_name = (company_obj.get("name") or "").strip() or company

                    location_obj = posting.get("location") or {}
                    location_str = _join_location(location_obj)
                    remote = "remote" if location_obj.get("remote") else None

                    # Fetch the detail endpoint for the description and the
                    # public apply/landing URL. Detail failures are tolerated:
                    # we still yield the posting with whatever we have.
                    detail = self._fetch_detail(company, posting_id, http)

                    description: str | None = None
                    url_value: str | None = None
                    employment_type: str | None = None
                    if detail is not None:
                        # Public landing page (falls back to apply URL).
                        url_value = (
                            (detail.get("postingUrl") or "").strip()
                            or (detail.get("applyUrl") or "").strip()
                            or None
                        )
                        sections = (
                            (detail.get("jobAd") or {}).get("sections") or {}
                        )
                        desc_text = (
                            (sections.get("jobDescription") or {}).get("text") or ""
                        )
                        if desc_text:
                            description = html.unescape(desc_text)

                        # Native employment type (e.g. "Full-time"); normalize maps it.
                        type_of_employment = detail.get("typeOfEmployment") or {}
                        if isinstance(type_of_employment, dict):
                            employment_type = (
                                (type_of_employment.get("label") or "").strip() or None
                            )

                    yield {
                        "title": title,
                        "company": company_name,
                        "url": url_value,
                        "description": description,
                        "location": location_str,
                        "remote": remote,
                        "posted_date": released_raw,
                        "source_job_id": posting_id,
                        "employment_type": employment_type,
                    }
                    total_yielded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Failed to process SmartRecruiters posting (company=%s, id=%s): %s",
                        company,
                        posting.get("id"),
                        exc,
                    )
                    continue
