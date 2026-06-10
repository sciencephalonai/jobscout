"""Project curated cap-exempt employers from sources config into the company
registry, so the Companies tab shows them and "Get companies" refreshes them.

`sources.yaml` (+ `sources.discovered.yaml`) stays the single source of truth;
the DuckDB `companies` table is a projection synced at startup. Adding a Workday
tenant via `scripts/probe_workday.py` therefore flows into "Get companies"
automatically on the next boot.
"""

from __future__ import annotations

from typing import Any, cast

from jobscout.models import Company
from jobscout.relational import RelationalStore

CAP_EXEMPT_TYPES = {"university", "hospital", "nonprofit", "government"}

# Slug-based ATS that are (a) refreshable by "Get companies" (_REFRESH_ADAPTER)
# and (b) valid Company.ats values. Workable uses ``accounts``; others ``companies``.
_SLUG_ATS = ("greenhouse", "lever", "ashby", "workable")


def register_cap_exempt_companies(relational: RelationalStore, cfg: dict[str, Any]) -> int:
    """Upsert every curated cap-exempt employer from *cfg* into the registry.

    Idempotent (keyed ``ats+slug``). Returns the number of employers upserted.
    """
    sources = cfg.get("sources", {})
    count = 0

    for ats in _SLUG_ATS:
        block = sources.get(ats, {}) or {}
        field = "accounts" if ats == "workable" else "companies"
        for entry in block.get(field, []) or []:
            if not isinstance(entry, dict):
                continue
            etype = entry.get("type")
            token = entry.get("token")
            if etype not in CAP_EXEMPT_TYPES or not token:
                continue
            relational.upsert_company(Company(
                ats=cast(Any, ats), slug=str(token), name=str(entry.get("name") or token),
                employer_type=str(etype), cap_exempt_hint="likely", enabled=True,
            ))
            count += 1

    for t in (sources.get("workday", {}) or {}).get("tenants", []) or []:
        if not isinstance(t, dict):
            continue
        etype = t.get("type") or "for_profit"
        tenant = t.get("tenant")
        site = t.get("site")
        if not tenant or not site:
            continue
        # Register all verified Workday tenants (probe_workday.py confirmed they return
        # jobs). Cap-exempt ones get "likely"; for_profit ones get "unknown" — they can
        # still sponsor H-1B, just not off-lottery.
        cap_hint = "likely" if etype in CAP_EXEMPT_TYPES else "unknown"
        relational.upsert_company(Company(
            ats="workday", slug=str(tenant), name=str(t.get("name") or tenant),
            employer_type=str(etype), region=str(t.get("region") or "wd1"),
            site=str(site), cap_exempt_hint=cap_hint, enabled=True,
        ))
        count += 1

    return count
