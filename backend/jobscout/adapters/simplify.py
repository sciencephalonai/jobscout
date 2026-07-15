"""
SimplifyJobs New-Grad-Positions feed adapter.

The SimplifyJobs community maintains a curated, continuously-updated list of
US new-grad tech roles as a single JSON file on GitHub (MIT-licensed repo):

    https://github.com/SimplifyJobs/New-Grad-Positions
    raw listings: .github/scripts/listings.json  (~17k records, ~2k active)

Each listing: {id, company_name, title, locations[], url, active, is_visible,
sponsorship, date_posted (epoch s), date_updated, category, degrees, source}.

Why this source matters: every record is an *explicit new-grad role* — the
exact segment an entry-level candidate competes best in — and the feed carries
its own ``sponsorship`` label, so citizenship-required and no-sponsorship rows
are dropped before they cost any enrichment budget. Listings have no
description; the adapter stamps ``new_grad_program`` so downstream scoring can
treat the missing YoE text honestly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.ats_detail import fetch_ats_description
from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError, keyword_title_match

log = logging.getLogger(__name__)

_LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions"
    "/dev/.github/scripts/listings.json"
)

# Feed sponsorship labels that are hard non-starters for a visa-needing user
# AND for US-only ingestion; dropped before normalization.
_DROP_SPONSORSHIP = {"U.S. Citizenship is Required", "Does Not Offer Sponsorship"}


class SimplifyAdapter:
    """Curated new-grad roles from the SimplifyJobs GitHub feed."""

    name = "simplify"
    method = "api"
    risk = "low"
    store_full_description = True  # resolved from the ATS behind each apply URL

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        if results_wanted <= 0:
            return
        try:
            resp = http.get(_LISTINGS_URL, api_source=True)
        except DomainBlockedError as exc:
            log.warning("Simplify feed blocked (%s) — skipping", exc)
            return
        except Exception as exc:  # noqa: BLE001
            log.error("HTTP error fetching Simplify feed: %s", exc)
            return
        if resp.status_code != 200:
            log.error("Simplify feed HTTP %s — skipping", resp.status_code)
            return
        try:
            listings = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.error("Simplify feed returned invalid JSON: %s", exc)
            return
        if not isinstance(listings, list):
            log.error("Simplify feed shape unexpected (%s) — skipping", type(listings))
            return

        # Newest first so results_wanted keeps the freshest postings.
        listings = sorted(
            (x for x in listings if isinstance(x, dict)),
            key=lambda x: x.get("date_posted") or 0,
            reverse=True,
        )

        yielded = 0
        for item in listings:
            if yielded >= results_wanted:
                break
            if not (item.get("active") and item.get("is_visible", True)):
                continue
            if item.get("sponsorship") in _DROP_SPONSORSHIP:
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if not title or not url:
                continue
            if not keyword_title_match(title, keywords):
                continue

            posted = item.get("date_posted")
            posted_iso: str | None = None
            if isinstance(posted, (int, float)) and posted > 0:
                posted_iso = datetime.fromtimestamp(posted, UTC).isoformat()

            locations = [str(x).strip() for x in (item.get("locations") or []) if str(x).strip()]
            if since is not None and posted_iso is not None:
                try:
                    if datetime.fromisoformat(posted_iso) < since:
                        continue
                except ValueError:
                    pass

            yield {
                "title": title,
                "company": str(item.get("company_name") or "").strip() or None,
                "url": url,
                # The feed itself has no descriptions, but the apply URL often
                # points at a known ATS whose public detail API we can call —
                # a real JD unlocks enrichment (YoE/visa/skills) downstream.
                "description": fetch_ats_description(url, http),
                "location": "; ".join(locations) or None,
                "posted_date": posted_iso,
                "source_job_id": str(item.get("id") or "") or None,
                # Every record in this feed is an explicit new-grad role.
                "new_grad_program": True,
            }
            yielded += 1
