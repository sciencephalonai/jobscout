"""Source configuration + adapter construction.

Loads ``sources.yaml`` (merging auto-discovered companies + runtime overrides),
and instantiates the enabled :class:`JobSourceAdapter`s. Pure config logic — no
stores, no network.
"""

from __future__ import annotations

from typing import Any

import yaml

from jobscout.adapters import (
    AdzunaAdapter,
    ArbeitnowAdapter,
    AshbyAdapter,
    GreenhouseAdapter,
    JobicyAdapter,
    JobrightAIAdapter,
    JobSpyAdapter,
    LeverAdapter,
    RecruiteeAdapter,
    RemoteOKAdapter,
    RemotiveAdapter,
    RipplingAdapter,
    RssAdapter,
    SmartRecruitersAdapter,
    TheMuseAdapter,
    WorkableAdapter,
    WorkdayAdapter,
    WorkingNomadsAdapter,
)

# Runtime source enable/disable overrides (in-memory, default off). Only these
# high-risk sources can be toggled on from the UI; everything else uses sources.yaml.
_TOGGLABLE_SOURCES = {"jobspy", "jobrightai"}
_RUNTIME_SOURCE_OVERRIDES: dict[str, bool] = {}

# Order here is also the ingestion order. Prioritize direct ATS / employer boards
# first so the freshest, most authoritative jobs land before aggregators.
_SOURCE_ORDER = [
    "greenhouse", "lever", "ashby", "workable", "workday", "rippling",
    "recruitee", "smartrecruiters", "adzuna", "remotive", "arbeitnow",
    "jobicy", "remoteok", "workingnomads", "themuse", "rss", "jobrightai",
    "jobspy",
]

# Source authority for dedup tiebreaks (lower = more authoritative).
_SOURCE_AUTHORITY: dict[str, int] = {
    "greenhouse": 0, "lever": 0, "workday": 0, "workable": 0, "rippling": 0, "ashby": 0,
    "recruitee": 0, "smartrecruiters": 0,
    "adzuna": 1,
    "remotive": 2, "arbeitnow": 2, "jobicy": 2,
    "remoteok": 2, "workingnomads": 2, "themuse": 2, "rss": 2, "jobrightai": 2,
    "jobspy": 3,
}
_DEFAULT_AUTHORITY = 2


def _load_sources_cfg() -> dict[str, Any]:
    """Load sources.yaml, merging in auto-discovered companies + runtime overrides."""
    with open("sources.yaml") as f:
        cfg = yaml.safe_load(f) or {}
    cfg = _merge_discovered(cfg)
    # Apply runtime overrides (e.g. the UI's high-risk JobSpy toggle) last.
    if _RUNTIME_SOURCE_OVERRIDES:
        sources = cfg.setdefault("sources", {})
        for name, enabled in _RUNTIME_SOURCE_OVERRIDES.items():
            sources.setdefault(name, {})["enabled"] = enabled
    return cfg


def _merge_discovered(cfg: dict[str, Any]) -> dict[str, Any]:
    """Merge sources.discovered.yaml company/account lists into cfg (dedup by token)."""
    try:
        with open("sources.discovered.yaml") as f:
            discovered = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return cfg

    def _dedup_key(entry: Any) -> Any:
        """Identity for dedup: token/account string, or (tenant, site) for Workday."""
        if isinstance(entry, dict):
            return entry.get("token") or (entry.get("tenant"), entry.get("site"))
        return entry

    sources = cfg.setdefault("sources", {})
    for name, block in (discovered.get("sources") or {}).items():
        if not isinstance(block, dict):
            continue
        target = sources.setdefault(name, {})
        # "tenants" carries Workday {tenant, region, site, type, name} entries.
        for field in ("companies", "accounts", "tenants"):
            extra = block.get(field) or []
            if not extra:
                continue
            existing_list = target.setdefault(field, [])
            seen = {_dedup_key(c) for c in existing_list}
            for entry in extra:
                key = _dedup_key(entry)
                if key and key not in seen:
                    existing_list.append(entry)
                    seen.add(key)
    return cfg


def _build_adapters(sources_cfg: dict[str, Any]) -> list[Any]:
    """Instantiate every enabled source adapter from sources.yaml."""
    adapters: list[Any] = []
    for name in _SOURCE_ORDER:
        cfg = sources_cfg.get(name, {})
        if not cfg.get("enabled", False):
            continue
        if name == "adzuna":
            adapters.append(AdzunaAdapter(countries=cfg.get("countries", ["us"])))
        elif name == "remotive":
            adapters.append(RemotiveAdapter())
        elif name == "arbeitnow":
            adapters.append(ArbeitnowAdapter())
        elif name == "jobicy":
            adapters.append(JobicyAdapter())
        elif name == "remoteok":
            adapters.append(RemoteOKAdapter())
        elif name == "workingnomads":
            adapters.append(WorkingNomadsAdapter())
        elif name == "themuse":
            adapters.append(TheMuseAdapter())
        elif name == "greenhouse":
            adapters.append(GreenhouseAdapter(companies=cfg.get("companies", [])))
        elif name == "lever":
            adapters.append(LeverAdapter(companies=cfg.get("companies", [])))
        elif name == "ashby":
            adapters.append(AshbyAdapter(companies=cfg.get("companies", [])))
        elif name == "workable":
            adapters.append(WorkableAdapter(accounts=cfg.get("accounts", [])))
        elif name == "workday":
            adapters.append(
                WorkdayAdapter(
                    tenants=cfg.get("tenants", []),
                    fetch_descriptions=cfg.get("fetch_descriptions", True),
                )
            )
        elif name == "rippling":
            adapters.append(
                RipplingAdapter(
                    companies=cfg.get("companies", []),
                    fetch_descriptions=cfg.get("fetch_descriptions", True),
                )
            )
        elif name == "recruitee":
            adapters.append(RecruiteeAdapter(companies=cfg.get("companies", [])))
        elif name == "smartrecruiters":
            adapters.append(
                SmartRecruitersAdapter(
                    companies=cfg.get("companies", []),
                    fetch_descriptions=cfg.get("fetch_descriptions", True),
                )
            )
        elif name == "rss":
            adapters.append(RssAdapter(feeds=cfg.get("feeds", [])))
        elif name == "jobrightai":
            adapters.append(JobrightAIAdapter())
        elif name == "jobspy":
            adapters.append(
                JobSpyAdapter(sites=cfg.get("sites", []), hours_old=cfg.get("hours_old", 168))
            )
    return adapters


def _company_size_map(sources_cfg: dict[str, Any]) -> dict[str, str]:
    """Map company token (lowercased) -> size bucket, from greenhouse/lever config."""
    out: dict[str, str] = {}
    for key in ("greenhouse", "lever"):
        for c in sources_cfg.get(key, {}).get("companies", []) or []:
            if isinstance(c, dict):
                tok = c.get("token") or c.get("name")
                size = c.get("size")
                if tok and size:
                    out[str(tok).lower()] = str(size)
    return out


def _enabled_source_names(sources_cfg: dict[str, Any]) -> list[str]:
    return [n for n in _SOURCE_ORDER if sources_cfg.get(n, {}).get("enabled", False)]
