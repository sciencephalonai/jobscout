"""
JobrightAI adapter — extracts jobs from the __NEXT_DATA__ JSON embedded in
https://jobright.ai/jobs/{keyword-slug} pages.

robots.txt allows /jobs/* for all crawlers.  No API key required.
Pagination via ?page=N, 16 results per page.

Compliance notes
----------------
- ``store_full_description = True``: jobSummary is a structured excerpt, not
  a scraped full-page copy, so storage is permissible.
- All HTTP goes through CompliantHttpClient (robots.txt check, rate limiting,
  backoff).
- ``risk = "low"``: publicly accessible SSR pages, explicitly allowed by
  robots.txt.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = "https://jobright.ai/jobs/{slug}"
_PAGE_SIZE = 16


def _keyword_to_slug(keywords: list[str]) -> str:
    """Join keywords and convert to a URL slug (lowercase, hyphen-separated)."""
    term = " ".join(k.strip() for k in keywords if k.strip())
    return re.sub(r"\s+", "-", term.strip().lower())


def _parse_salary(salary_desc: str | None) -> tuple[float | None, float | None]:
    """Parse salary strings like '$125K/yr - $188K/yr' or '$36/hr - $52/hr'.

    Returns (min, max) as annualised USD floats, or (None, None) on failure.
    """
    if not salary_desc:
        return None, None
    # Extract all numeric values with optional K suffix
    nums = re.findall(r"\$?([\d.]+)([Kk]?)", salary_desc)
    values: list[float] = []
    for num, suffix in nums:
        try:
            v = float(num) * (1000 if suffix.lower() == "k" else 1)
            values.append(v)
        except ValueError:
            continue
    if not values:
        return None, None
    # Annualise hourly rates (heuristic: values < 500 are hourly)
    if "/hr" in salary_desc.lower() or (values and values[0] < 500):
        values = [v * 2080 for v in values]
    lo = min(values)
    hi = max(values)
    return lo, hi


def _map_h1b(h1b_status: str | None) -> str:
    """Map JobrightAI H1B status strings to JobScout visa_sponsorship values."""
    if not h1b_status:
        return "not_mentioned"
    s = h1b_status.lower()
    if "yes" in s or "sponsor" in s and "past" not in s and "no" not in s:
        return "yes"
    if "past" in s:
        return "not_mentioned"
    if "no" in s:
        return "no"
    return "not_mentioned"


def _map_size(size_str: str | None) -> str | None:
    """Map JobrightAI company size strings to JobScout size buckets."""
    if not size_str:
        return None
    s = size_str.lower().replace(",", "").replace(" employees", "").strip()
    mapping = {
        "1-10": "1-50", "11-50": "1-50", "1-50": "1-50",
        "51-200": "51-200",
        "201-500": "201-500",
        "501-1000": "501-1000",
        "1001-5000": "1001-5000",
        "5001-10000": "5000+", "10001+": "5000+", "5000+": "5000+",
    }
    for key, bucket in mapping.items():
        if key in s:
            return bucket
    return None


def _map_employment_type(emp_type: str | None) -> str | None:
    """Map JobrightAI employment type to JobScout canonical values."""
    if not emp_type:
        return None
    s = emp_type.lower()
    if "full" in s:
        return "full_time"
    if "part" in s:
        return "part_time"
    if "contract" in s:
        return "contract"
    if "intern" in s:
        return "internship"
    return None


class JobrightAIAdapter:
    """Fetches job listings from jobright.ai via embedded SSR JSON.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — reads structured JSON embedded in SSR pages (not raw HTML
        scraping).
    risk:
        ``"low"`` — /jobs/* is explicitly allowed in robots.txt.
    store_full_description:
        ``True`` — jobSummary is a structured excerpt provided by the site.
    """

    name = "jobrightai"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self) -> None:
        pass

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

        slug = _keyword_to_slug(keywords)
        if not slug:
            log.warning("JobrightAIAdapter.search called with empty keywords — skipping")
            return

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0
        page = 1

        while total_yielded < results_wanted:
            url = _BASE_URL.format(slug=slug)
            params: dict = {}
            if page > 1:
                params["page"] = page

            try:
                resp = http.get(url, params=params if params else None)
            except DomainBlockedError as exc:
                log.warning("JobrightAI domain blocked (%s) — skipping", exc)
                return
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching JobrightAI page %d: %s", page, exc)
                return

            if resp.status_code != 200:
                log.error("JobrightAI returned HTTP %s on page %d — stopping", resp.status_code, page)
                return

            try:
                match = re.search(
                    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                    resp.text,
                    re.DOTALL,
                )
                if not match:
                    log.error("JobrightAI: __NEXT_DATA__ not found on page %d", page)
                    return
                data = json.loads(match.group(1))
                job_list: list[dict] = data["props"]["pageProps"].get("jobList") or []
            except Exception as exc:  # noqa: BLE001
                log.error("JobrightAI: failed to parse page %d: %s", page, exc)
                return

            if not job_list:
                log.debug("JobrightAI: no jobs on page %d for slug=%r — stopping", page, slug)
                return

            for item in job_list:
                if total_yielded >= results_wanted:
                    return

                normalised = _normalise(item, since_aware)
                if normalised is not None:
                    yield normalised
                    total_yielded += 1

            page += 1

        log.debug("JobrightAI slug=%r: yielded %d jobs", slug, total_yielded)


def _normalise(item: dict, since_aware: datetime | None) -> dict | None:
    """Convert a single jobList entry to the JobScout canonical shape."""
    try:
        jr: dict = item.get("jobResult") or {}
        cr: dict = item.get("companyResult") or {}

        title = (jr.get("jobTitle") or "").strip() or None
        url = (jr.get("url") or jr.get("applyLink") or "").strip() or None
        if not title or not url:
            return None

        # Date filter
        published_str = jr.get("publishTime")
        posted_date: str | None = None
        if published_str:
            try:
                dt = datetime.fromisoformat(str(published_str))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                if since_aware is not None and dt < since_aware:
                    return None
                posted_date = dt.isoformat()
            except ValueError:
                posted_date = str(published_str)

        company = (cr.get("companyName") or "").strip() or None
        location_raw = (jr.get("jobLocation") or "").strip() or None

        is_remote = jr.get("isRemote")
        work_model = (jr.get("workModel") or "").lower()
        if is_remote or "remote" in work_model:
            remote = "remote"
        elif "hybrid" in work_model:
            remote = "hybrid"
        elif work_model in ("onsite", "on-site", "in-office"):
            remote = "onsite"
        else:
            remote = None

        salary_min, salary_max = _parse_salary(jr.get("salaryDesc"))

        description = (jr.get("jobSummary") or "").strip() or None

        source_job_id = str(jr["jobId"]) if jr.get("jobId") else None

        return {
            "title": title,
            "company": company,
            "url": url,
            "source_job_id": source_job_id,
            "description": description,
            "location": location_raw,
            "remote": remote,
            "posted_date": posted_date,
            "employment_type": _map_employment_type(jr.get("employmentType")),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": "USD" if (salary_min or salary_max) else None,
            "visa_sponsorship": _map_h1b(jr.get("h1BStatus")),
            "company_size": _map_size(cr.get("companySize")),
            "yoe_min": jr.get("minYearsOfExperience"),
        }

    except Exception as exc:  # noqa: BLE001
        log.warning("JobrightAI: failed to normalise item: %s", exc)
        return None
