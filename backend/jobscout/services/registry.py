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
from jobscout.sponsors import is_known_h1b_sponsor

CAP_EXEMPT_TYPES = {"university", "hospital", "nonprofit", "government"}

# Slug-based ATS that are (a) refreshable by "Get companies" (_REFRESH_ADAPTER)
# and (b) valid Company.ats values. Workable uses ``accounts``; others ``companies``.
_SLUG_ATS = ("greenhouse", "lever", "ashby", "workable")
_ALL_SLUG_ATS = (
    "greenhouse", "lever", "ashby", "workable", "rippling", "recruitee",
    "smartrecruiters",
)


def _careers_url(ats: str, token: str) -> str:
    return {
        "greenhouse": f"https://job-boards.greenhouse.io/{token}",
        "lever": f"https://jobs.lever.co/{token}",
        "ashby": f"https://jobs.ashbyhq.com/{token}",
        "workable": f"https://apply.workable.com/{token}",
        "rippling": f"https://ats.rippling.com/{token}/jobs",
        "recruitee": f"https://{token}.recruitee.com/",
        "smartrecruiters": f"https://jobs.smartrecruiters.com/{token}",
    }[ats]


def _cap_exempt_hint(employer_type: str) -> str:
    if employer_type in CAP_EXEMPT_TYPES:
        return "likely"
    if employer_type == "for_profit":
        return "no"
    return "unknown"


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


def register_configured_companies(
    relational: RelationalStore, cfg: dict[str, Any],
) -> int:
    """Project every configured source and curated target into the registry.

    Source entries become refreshable watchlist rows. ``company_targets`` also
    carries employers whose public board is bespoke, Oracle, or known only from
    a direct careers page; those are stored as ``ats=none`` / direct-apply-only
    and therefore never handed to a scraper. Upserts remain idempotent by
    ``(ats, slug)`` and target metadata intentionally overrides generic source
    defaults for the same key.
    """
    sources = cfg.get("sources", {})
    seen: set[tuple[str, str]] = set()

    for ats in _ALL_SLUG_ATS:
        block = sources.get(ats, {}) or {}
        field = "accounts" if ats == "workable" else "companies"
        for entry in block.get(field, []) or []:
            if isinstance(entry, dict):
                token = entry.get("token")
                employer_type = str(entry.get("type") or "for_profit")
                name = str(
                    entry.get("display_name") or entry.get("name") or token or ""
                )
                careers_url = entry.get("careers_url")
                size_bucket = entry.get("size")
            else:
                token = entry
                employer_type = "for_profit"
                name = str(token or "")
                careers_url = None
                size_bucket = None
            if not token:
                continue
            token = str(token)
            relational.upsert_company(Company(
                ats=cast(Any, ats),
                slug=token,
                name=name or token,
                careers_url=str(careers_url or _careers_url(ats, token)),
                employer_type=employer_type,
                size_bucket=str(size_bucket) if size_bucket else None,
                known_h1b_sponsor=is_known_h1b_sponsor(name or token),
                cap_exempt_hint=_cap_exempt_hint(employer_type),
                enabled=True,
                direct_apply_only=False,
            ))
            seen.add((ats, token))

    for tenant in (sources.get("workday", {}) or {}).get("tenants", []) or []:
        if not isinstance(tenant, dict):
            continue
        token = tenant.get("tenant")
        site = tenant.get("site")
        if not token or not site:
            continue
        token = str(token)
        region = str(tenant.get("region") or "wd1")
        employer_type = str(tenant.get("type") or "for_profit")
        name = str(tenant.get("name") or token)
        relational.upsert_company(Company(
            ats="workday",
            slug=token,
            name=name,
            careers_url=f"https://{token}.{region}.myworkdayjobs.com/{site}",
            employer_type=employer_type,
            known_h1b_sponsor=is_known_h1b_sponsor(name),
            cap_exempt_hint=_cap_exempt_hint(employer_type),
            enabled=True,
            direct_apply_only=False,
            region=region,
            site=str(site),
        ))
        seen.add(("workday", token))

    for raw_target in cfg.get("company_targets", []) or []:
        if not isinstance(raw_target, dict):
            continue
        target = dict(raw_target)
        name = str(target.get("name") or target.get("slug") or "")
        if not name or not target.get("slug"):
            continue
        employer_type = str(target.get("employer_type") or "for_profit")
        target["employer_type"] = employer_type
        target.setdefault("known_h1b_sponsor", is_known_h1b_sponsor(name))
        target.setdefault(
            "cap_exempt_hint",
            _cap_exempt_hint(employer_type),
        )
        company = Company.model_validate(target)
        relational.upsert_company(company)
        seen.add((company.ats, company.slug))

    return len(seen)
