"""
Workday CXS public job-search API adapter.

Every public Workday career site exposes an unauthenticated JSON search endpoint
under its tenant host:

    POST https://{tenant}.{region}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
         body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "..."}

    → {"total": N, "jobPostings": [
          {"title", "externalPath", "locationsText", "postedOn", "bulletFields": [reqId]}, ...]}

The listing response carries no description, so (when configured) the adapter
makes one follow-up GET per posting to the CXS job-detail endpoint
``/wday/cxs/{tenant}/{site}{externalPath}`` and reads ``jobPostingInfo.jobDescription``.

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
from urllib.parse import urlparse

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
        out.append(
            {
                "tenant": str(tenant),
                "region": str(t.get("region") or "wd1"),
                "site": str(site),
                "type": str(t.get("type") or "unclear"),
                # Workday listings don't name the employer, but the tenant IS the
                # employer — a display name stamps it as the job's company so
                # cap-exempt university/hospital jobs don't render blank.
                "name": str(t.get("name") or ""),
                # Curated tenants are US institutions; Workday's locationsText is
                # often a bare campus name ("Ithaca (Main Campus)") with no US
                # token, which the downstream US filter would otherwise drop.
                "country": str(t.get("country") or "us"),
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
            if self.fetch_descriptions:
                description = self._fetch_description(cxs_base, external_path, http)

            return {
                "title": title,
                # Tenant display name (e.g. "Cornell University") if configured;
                # Workday listings themselves don't name the employer.
                "company": name or None,
                "url": apply_url,
                "description": description,
                "location": (posting.get("locationsText") or "").strip() or None,
                # Curated US tenant → stamp country so the bare campus-name
                # locationsText isn't dropped by the downstream US filter.
                "country": country,
                "posted_date": _clean_posted(posting.get("postedOn")),
                "source_job_id": source_job_id,
                "employer_type": employer_type,
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to process Workday posting on %s: %s", host, exc)
            return None

    @staticmethod
    def _fetch_description(
        cxs_base: str, external_path: str, http: CompliantHttpClient
    ) -> str | None:
        """GET the CXS job-detail endpoint and return the (unescaped) description HTML."""
        detail_url = f"{cxs_base}{external_path}"
        try:
            resp = http.get(detail_url, api_source=True)
            if resp.status_code != 200:
                return None
            info = (resp.json() or {}).get("jobPostingInfo") or {}
            desc = info.get("jobDescription")
            return html.unescape(desc) if desc else None
        except Exception as exc:  # noqa: BLE001
            log.debug("Workday description fetch failed for %s: %s", detail_url, exc)
            return None
