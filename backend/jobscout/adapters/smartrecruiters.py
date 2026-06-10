"""
SmartRecruiters public Posting API adapter.

Docs: https://developers.smartrecruiters.com/reference/postings-1

SmartRecruiters exposes an unauthenticated, per-company Posting API. Each company
publishes its openings under a *company identifier* (e.g. ``Visa``, ``Bosch``,
``ServiceNow``) — notably bigger firms than Greenhouse/Lever usually carry. No
API key is required.

* LIST   ``/v1/companies/{company}/postings?limit=100`` — metadata, no description.
* DETAIL ``/v1/companies/{company}/postings/{posting_id}`` — full HTML description
  (``jobAd.sections.jobDescription.text``) + public URLs (``postingUrl``/``applyUrl``).

``fetch_descriptions`` (default True) controls whether the per-posting DETAIL call
is made (like the Workday/Rippling adapters). When False, jobs are yielded from the
list alone with a constructed public URL — faster, but no description (so DeepSeek
skill/visa enrichment is skipped for those).

robots.txt for ``api.smartrecruiters.com`` publishes ``Disallow: /`` aimed at
scrapers; because ``method == "api"`` requests go out with ``api_source=True`` (the
robots check is skipped, blocklist still enforced) — identical to Greenhouse/Adzuna.
``companies`` accepts ``{token, type}`` entries to stamp ``employer_type``.
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

_LIST_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100"
_DETAIL_URL = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"
_PUBLIC_URL = "https://jobs.smartrecruiters.com/{company}/{posting_id}"


def _parse_released_date(value: str | None) -> datetime | None:
    """Parse SmartRecruiters' ``releasedDate`` ISO-8601 (trailing Z) into UTC."""
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
    """Join city/region/country of a posting ``location`` dict into one string."""
    if not isinstance(location, dict):
        return None
    parts = [str(location.get(k) or "").strip() for k in ("city", "region", "country")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


class SmartRecruitersAdapter:
    """Wraps the SmartRecruiters public Posting API (per-company boards)."""

    name = "smartrecruiters"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(
        self, companies: list[Any] | None = None, fetch_descriptions: bool = True
    ) -> None:
        self.companies: list[tuple[str, str]] = normalize_company_entries(companies)
        self.fetch_descriptions = fetch_descriptions

    def _fetch_detail(
        self, company: str, posting_id: str, http: CompliantHttpClient
    ) -> dict | None:
        """Fetch the DETAIL endpoint for one posting. Returns JSON dict or None."""
        url = _DETAIL_URL.format(company=company, posting_id=posting_id)
        try:
            resp = http.get(url, api_source=True)
        except Exception as exc:  # noqa: BLE001
            log.debug("SmartRecruiters detail failed (%s/%s): %s", company, posting_id, exc)
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts from each configured SmartRecruiters board."""
        if not self.companies:
            log.warning("SmartRecruitersAdapter has no companies configured — skipping")
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

            url = _LIST_URL.format(company=company)
            try:
                resp = http.get(url, api_source=True)
            except DomainBlockedError as exc:
                log.warning("SmartRecruiters blocked (%s) — skipping %s", exc, company)
                continue
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error on SmartRecruiters %s: %s", company, exc)
                continue

            if resp.status_code != 200:
                log.error(
                    "SmartRecruiters HTTP %s for company=%s — skipping",
                    resp.status_code, company,
                )
                continue

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error("Failed to decode SmartRecruiters JSON (%s): %s", company, exc)
                continue

            postings: list[dict] = data.get("content") or []

            for posting in postings:
                if total_yielded >= results_wanted:
                    break
                try:
                    title = str(posting.get("name") or "").strip()
                    if not title:
                        continue
                    if not keyword_title_match(title, keywords):
                        continue

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

                    # Public URL fallback (valid even without the detail call).
                    url_value = _PUBLIC_URL.format(company=company, posting_id=posting_id)
                    description: str | None = None
                    if self.fetch_descriptions:
                        detail = self._fetch_detail(company, posting_id, http)
                        if detail is not None:
                            url_value = (
                                (detail.get("postingUrl") or "").strip()
                                or (detail.get("applyUrl") or "").strip()
                                or url_value
                            )
                            sections = (detail.get("jobAd") or {}).get("sections") or {}
                            desc_text = (sections.get("jobDescription") or {}).get("text") or ""
                            if desc_text:
                                description = html.unescape(desc_text)

                    raw: dict = {
                        "title": title,
                        "company": company_name,
                        "url": url_value,
                        "description": description,
                        "location": location_str,
                        "posted_date": released_raw,
                        "source_job_id": posting_id,
                        "employer_type": employer_type,
                    }
                    if remote:
                        raw["remote"] = remote

                    yield raw
                    total_yielded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Failed to process SmartRecruiters posting (%s/%s): %s",
                        company, posting.get("id"), exc,
                    )
                    continue
