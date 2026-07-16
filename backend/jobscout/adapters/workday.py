"""
Workday CXS public job-search API adapter.

Every public Workday career site exposes an unauthenticated JSON search endpoint
under its tenant host:

    POST https://{tenant}.{region}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
         body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "..."}

    → {"total": N, "jobPostings": [
          {"title", "externalPath", "locationsText", "postedOn", "bulletFields": [reqId]}, ...]}

The listing response carries no description (and hides multi-location postings
behind a "2 Locations" placeholder), so (when configured) the adapter makes one
follow-up GET per posting to the CXS job-detail endpoint
``/wday/cxs/{tenant}/{site}{externalPath}`` and reads
``jobPostingInfo.{jobDescription, location, additionalLocations, country}``.

Workday is the dominant ATS for universities, academic medical centers, and large
nonprofits — the H-1B cap-exempt employer classes. Each curated tenant is tagged
with an ``employer_type`` in config so the cap-exempt class is stamped directly
(no reliance on LLM inference).
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urlparse

from jobscout.adapters.base import (
    CompliantHttpClient,
    DomainBlockedError,
    keyword_title_match,
)

log = logging.getLogger(__name__)

_PAGE_LIMIT = 20
_MAX_PAGES = 25  # hard ceiling per tenant to bound request volume
_POSTED_PREFIX = re.compile(r"^\s*posted\s+", re.IGNORECASE)
_LOCALE_RE = re.compile(r"^[a-z]{2}[-_][A-Za-z]{2}$")  # e.g. en-US / en_US
_MULTI_LOCATION_PLACEHOLDER_RE = re.compile(
    r"^(?:\d+|multiple)\s+locations?$", re.IGNORECASE
)
_US_CURATED_EMPLOYER_TYPES = {"university", "hospital", "nonprofit", "government"}


def parse_workday_url(url: str) -> dict[str, str] | None:
    """Parse a Workday career-site URL into ``{tenant, region, site}``.

    e.g. ``https://cornell.wd1.myworkdayjobs.com/en-US/CornellCareerPage``
         → ``{"tenant": "cornell", "region": "wd1", "site": "CornellCareerPage"}``

    The host is ``{tenant}.{region}.myworkdayjobs.com`` and the *site* is the first
    path segment after an optional locale (``en-US``). Returns ``None`` if the URL
    isn't a Workday tenant host or has no site segment.
    """
    try:
        parsed = urlparse(url.strip())
    except Exception:  # noqa: BLE001
        return None
    host = (parsed.hostname or "").lower()
    if not host.endswith("myworkdayjobs.com"):
        return None
    parts = host.split(".")
    if len(parts) < 4:  # need tenant.region.myworkdayjobs.com
        return None
    tenant, region = parts[0], parts[1]
    segments = [s for s in parsed.path.split("/") if s]
    if segments and _LOCALE_RE.match(segments[0]):
        segments = segments[1:]
    if not tenant or not region or not segments:
        return None
    return {"tenant": tenant, "region": region, "site": segments[0]}


def parse_workday_job_url(url: str) -> dict[str, str] | None:
    """Parse a public Workday job URL into its CXS connection and job path.

    Both locale-prefixed URLs (``/en-US/site/job/...``) and canonical URLs
    without a locale (``/site/job/...``) are accepted.  An optional trailing
    ``/apply`` is removed because it is not part of the CXS detail path.
    """
    try:
        parsed = urlparse(url.strip())
    except Exception:  # noqa: BLE001
        return None
    host = (parsed.hostname or "").lower()
    parts = host.split(".")
    if not host.endswith("myworkdayjobs.com") or len(parts) < 4:
        return None
    segments = [unquote(s) for s in parsed.path.split("/") if s]
    if segments and _LOCALE_RE.match(segments[0]):
        segments = segments[1:]
    if len(segments) < 3 or segments[1].lower() != "job":
        return None
    if segments[-1].lower() == "apply":
        segments = segments[:-1]
    if len(segments) < 4:
        return None
    return {
        "tenant": parts[0],
        "region": parts[1],
        "site": segments[0],
        "external_path": "/" + "/".join(segments[1:]),
    }


def workday_location_hint(external_path: str | None) -> str | None:
    """Return the human-readable location slug embedded in a Workday path.

    This is a conservative fallback for detail-endpoint failures.  Workday job
    paths normally begin ``/job/<location-slug>/<posting>``.  Turning that slug
    into text gives the downstream US-only classifier enough evidence to reject
    ``Vietnam-Ho-Chi-Minh-City`` or ``Hanoi`` instead of trusting a board-level
    country default.
    """
    bits = [unquote(bit) for bit in (external_path or "").split("/") if bit]
    if len(bits) < 3 or bits[0].lower() != "job":
        return None
    hint = re.sub(r"[-_]+", " ", bits[1]).strip()
    if not hint or _MULTI_LOCATION_PLACEHOLDER_RE.fullmatch(hint):
        return None
    return hint


def _descriptor(value: Any) -> str | None:
    """Read a Workday descriptor that may be a string or descriptor object."""
    if isinstance(value, dict):
        value = value.get("descriptor") or value.get("name") or value.get("alpha2Code")
    text = str(value or "").strip()
    return text or None


def parse_workday_detail(data: dict[str, Any] | None) -> tuple[str | None, str | None, str | None]:
    """Return ``(description, locations, country)`` from a CXS detail payload."""
    info = (data or {}).get("jobPostingInfo") or {}
    desc = info.get("jobDescription")
    raw_locations = [info.get("location")]
    raw_locations.extend(info.get("additionalLocations") or [])
    locations: list[str] = []
    for raw_location in raw_locations:
        location = _descriptor(raw_location)
        if location and location not in locations:
            locations.append(location)

    country = _descriptor(info.get("country"))
    if not country:
        requisition_location = info.get("jobRequisitionLocation") or {}
        country = _descriptor(requisition_location.get("country"))

    return (
        html.unescape(str(desc)) if desc else None,
        "; ".join(locations) or None,
        country,
    )


def _normalize_tenants(tenants: list[Any] | None) -> list[dict[str, str]]:
    """Validate config tenant entries → list of {tenant, region, site, type}."""
    out: list[dict[str, str]] = []
    for t in tenants or []:
        if not isinstance(t, dict):
            continue
        tenant = t.get("tenant")
        site = t.get("site")
        if not tenant or not site:
            continue
        employer_type = str(t.get("type") or "unclear")
        # A Workday tenant is a board, not a country. Global employers such as
        # NVIDIA use one tenant for worldwide openings, so silently defaulting
        # every tenant to US is incorrect. Keep the historical US fallback only
        # for the curated US cap-exempt classes; all other boards must prove the
        # job's country through detail data or the per-job location/path.
        configured_country = str(t.get("country") or "").strip()
        default_country = configured_country or (
            "us" if employer_type in _US_CURATED_EMPLOYER_TYPES else ""
        )
        out.append(
            {
                "tenant": str(tenant),
                "region": str(t.get("region") or "wd1"),
                "site": str(site),
                "type": employer_type,
                # Workday listings don't name the employer, but the tenant IS the
                # employer — a display name stamps it as the job's company so
                # cap-exempt university/hospital jobs don't render blank.
                "name": str(t.get("name") or ""),
                # Curated tenants are US institutions; Workday's locationsText is
                # often a bare campus name ("Ithaca (Main Campus)") with no US
                # token, which the downstream US filter would otherwise drop.
                "country": default_country,
            }
        )
    return out


def _clean_posted(value: str | None) -> str | None:
    """Strip Workday's 'Posted ' prefix so dateparser can read '3 Days Ago'."""
    if not value:
        return None
    return _POSTED_PREFIX.sub("", str(value)).strip() or None


class WorkdayAdapter:
    """Wraps the public Workday CXS job-search API across curated tenants."""

    name = "workday"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(
        self, tenants: list[Any] | None = None, fetch_descriptions: bool = True
    ) -> None:
        self.tenants = _normalize_tenants(tenants)
        self.fetch_descriptions = fetch_descriptions

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts from each configured Workday tenant."""
        if not self.tenants:
            log.warning("WorkdayAdapter has no tenants configured — skipping")
            return
        if results_wanted <= 0:
            return

        # Workday CXS searchText behaves like a near-phrase match: joining many
        # keywords ("data engineer software engineer …") returns almost nothing
        # (measured: 5 joined terms → ~4 hits vs ~40 for a single term). So query
        # each keyword separately server-side and dedup per tenant by externalPath.
        queries = [k.strip() for k in keywords if k.strip()] or [""]
        total_yielded = 0

        for t in self.tenants:
            if total_yielded >= results_wanted:
                break
            host = f"{t['tenant']}.{t['region']}.myworkdayjobs.com"
            cxs_base = f"https://{host}/wday/cxs/{t['tenant']}/{t['site']}"
            jobs_url = f"{cxs_base}/jobs"
            seen_paths: set[str] = set()

            for query in queries:
                if total_yielded >= results_wanted:
                    break
                offset = 0
                for _page in range(_MAX_PAGES):
                    if total_yielded >= results_wanted:
                        break
                    body = {
                        "appliedFacets": {},
                        "limit": _PAGE_LIMIT,
                        "offset": offset,
                        "searchText": query,
                    }
                    try:
                        resp = http.post(jobs_url, json=body, api_source=True)
                    except DomainBlockedError as exc:
                        log.warning("Workday blocked (%s) — skipping tenant %s", exc, t["tenant"])
                        break
                    except Exception as exc:  # noqa: BLE001
                        log.error("HTTP error on Workday tenant %s: %s", t["tenant"], exc)
                        break

                    if resp.status_code != 200:
                        log.error(
                            "Workday HTTP %s for tenant=%s — skipping",
                            resp.status_code,
                            t["tenant"],
                        )
                        break

                    try:
                        data = resp.json()
                    except Exception as exc:  # noqa: BLE001
                        log.error("Failed to decode Workday JSON (tenant=%s): %s", t["tenant"], exc)
                        break

                    postings: list[dict] = data.get("jobPostings") or []
                    total = int(data.get("total") or 0)
                    if not postings:
                        break

                    for posting in postings:
                        if total_yielded >= results_wanted:
                            break
                        path = (posting.get("externalPath") or "").strip()
                        if path and path in seen_paths:
                            continue
                        if path:
                            seen_paths.add(path)
                        # Workday searchText matches the full description, so a
                        # query for "data" returns e.g. an HVAC role that mentions
                        # data. Keep only TITLE-relevant roles (same rule as the
                        # other ATS); empty keywords keep everything (prober path).
                        if not keyword_title_match(str(posting.get("title") or ""), keywords):
                            continue
                        raw = self._build_raw(
                            posting, host, cxs_base, t["site"], t["type"],
                            t["country"], t["name"], http
                        )
                        if raw is not None:
                            yield raw
                            total_yielded += 1

                    offset += _PAGE_LIMIT
                    if offset >= total:
                        break

    def _build_raw(
        self,
        posting: dict,
        host: str,
        cxs_base: str,
        site: str,
        employer_type: str,
        country: str,
        name: str,
        http: CompliantHttpClient,
    ) -> dict | None:
        """Convert one Workday posting to the canonical raw dict (+ optional description)."""
        try:
            title = str(posting.get("title") or "").strip()
            external_path = (posting.get("externalPath") or "").strip()
            if not title or not external_path:
                return None

            apply_url = f"https://{host}/en-US/{site}{external_path}"
            bullets = posting.get("bulletFields") or []
            source_job_id = str(bullets[0]) if bullets else None

            description: str | None = None
            detail_location: str | None = None
            detail_country: str | None = None
            if self.fetch_descriptions:
                description, detail_location, detail_country = self._fetch_detail(
                    cxs_base, external_path, http
                )

            # The search listing's locationsText hides multi-location postings
            # behind "2 Locations"; the detail JSON carries the real city names
            # AND the actual country — prefer both. A global tenant (e.g. a
            # multinational's single Workday board) posts worldwide roles, so a
            # non-US detail country must override the tenant-level US stamp.
            listing_location = (posting.get("locationsText") or "").strip() or None
            location = detail_location or listing_location
            if not detail_location and (
                not location or _MULTI_LOCATION_PLACEHOLDER_RE.fullmatch(location)
            ):
                location = workday_location_hint(external_path) or location
            if detail_country:
                country = detail_country

            return {
                "title": title,
                # Tenant display name (e.g. "Cornell University") if configured;
                # Workday listings themselves don't name the employer.
                "company": name or None,
                "url": apply_url,
                "description": description,
                "location": location,
                # Curated US tenant → stamp country so the bare campus-name
                # locationsText isn't dropped by the downstream US filter
                # (overridden above when the detail JSON names the country).
                "country": country or None,
                "posted_date": _clean_posted(posting.get("postedOn")),
                "source_job_id": source_job_id,
                "employer_type": employer_type,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to process Workday posting on %s: %s", host, exc)
            return None

    @staticmethod
    def _fetch_detail(
        cxs_base: str, external_path: str, http: CompliantHttpClient
    ) -> tuple[str | None, str | None, str | None]:
        """GET the CXS job-detail endpoint → (description, location, country).

        ``jobPostingInfo`` carries ``jobDescription`` (HTML), ``location`` +
        ``additionalLocations`` (real city strings, unlike the search listing's
        "2 Locations" placeholder), and ``country.descriptor`` (e.g. "Vietnam").
        All three degrade to None on any failure.
        """
        detail_url = f"{cxs_base}{external_path}"
        try:
            resp = http.get(detail_url, api_source=True)
            if resp.status_code != 200:
                return None, None, None
            return parse_workday_detail(resp.json())
        except Exception as exc:  # noqa: BLE001
            log.debug("Workday detail fetch failed for %s: %s", detail_url, exc)
            return None, None, None
