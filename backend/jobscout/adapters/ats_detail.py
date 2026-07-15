"""Fetch a job description from a KNOWN ATS's public detail API, given its URL.

Curated feeds (e.g. the SimplifyJobs new-grad list) carry no description, but
their apply URLs point at ATS hosts whose public JSON detail endpoints we
already integrate elsewhere. Resolving the description here unlocks DeepSeek
enrichment (YoE/visa/skills) for those jobs and gives the UI a real JD.

Deliberately allow-listed: unknown hosts return ``None`` — this module never
scrapes arbitrary pages. All HTTP goes through ``CompliantHttpClient``.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# boards.greenhouse.io/{board}/jobs/{id}  or  job-boards.greenhouse.io/{board}/jobs/{id}
_GREENHOUSE = re.compile(r"^/(?P<board>[^/]+)/jobs/(?P<job_id>\d+)")
# jobs.lever.co/{org}/{posting-uuid}
_LEVER = re.compile(r"^/(?P<org>[^/]+)/(?P<posting>[0-9a-f-]{36})")
# {tenant}.{region}.myworkdayjobs.com[/{locale}]/{site}/job/... — the locale
# segment ("en-US") is optional; many boards (and the Simplify feed's URLs)
# link straight to /{site}/job/....
_WORKDAY_HOST = re.compile(r"^(?P<tenant>[^.]+)\.(?P<region>[^.]+)\.myworkdayjobs\.com$")
_WORKDAY_PATH = re.compile(
    r"^(?:/[a-zA-Z]{2}-[a-zA-Z]{2})?/(?P<site>[^/]+)(?P<path>/job/.+)$"
)


def _json(resp: Any) -> dict | None:
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def fetch_ats_description(url: str, http: Any) -> str | None:
    """Return the job description behind *url* when the host is a known ATS.

    Supported: Greenhouse boards, Lever postings, Workday CXS. Anything else
    (or any failure) → ``None``; callers treat the description as optional.
    """
    try:
        parsed = urlparse((url or "").strip())
    except Exception:  # noqa: BLE001
        return None
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    try:
        if host in ("boards.greenhouse.io", "job-boards.greenhouse.io"):
            m = _GREENHOUSE.match(path)
            if not m:
                return None
            data = _json(http.get(
                f"https://boards-api.greenhouse.io/v1/boards/{m['board']}/jobs/{m['job_id']}",
                api_source=True,
            ))
            content = (data or {}).get("content")
            return html.unescape(content) if content else None

        if host == "jobs.lever.co":
            m = _LEVER.match(path)
            if not m:
                return None
            data = _json(http.get(
                f"https://api.lever.co/v0/postings/{m['org']}/{m['posting']}",
                api_source=True,
            ))
            desc = (data or {}).get("description") or (data or {}).get("descriptionPlain")
            return html.unescape(desc) if desc else None

        hm = _WORKDAY_HOST.match(host)
        if hm:
            pm = _WORKDAY_PATH.match(path)
            if not pm:
                return None
            cxs = (
                f"https://{host}/wday/cxs/{hm['tenant']}/{pm['site']}{pm['path']}"
            )
            data = _json(http.get(cxs, api_source=True))
            desc = ((data or {}).get("jobPostingInfo") or {}).get("jobDescription")
            return html.unescape(desc) if desc else None
    except Exception as exc:  # noqa: BLE001
        log.debug("ats_detail fetch failed for %s: %s", url, exc)
        return None
    return None
