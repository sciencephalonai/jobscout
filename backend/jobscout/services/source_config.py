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
    HimalayasAdapter,
    JobicyAdapter,
    JobrightAIAdapter,
    JobSpyAdapter,
    LeverAdapter,
    RecruiteeAdapter,
    RemoteOKAdapter,
    RemotiveAdapter,
    RipplingAdapter,
    RssAdapter,
    SimplifyAdapter,
    SmartRecruitersAdapter,
    TheMuseAdapter,
    USAJobsAdapter,
    WorkableAdapter,
    WorkdayAdapter,
    WorkingNomadsAdapter,
)
from jobscout.source_intelligence import source_authority

# Runtime source enable/disable overrides (in-memory, default off). Only these
# high-risk sources can be toggled on from the UI; everything else uses sources.yaml.
_TOGGLABLE_SOURCES = {"jobspy", "jobrightai"}
_RUNTIME_SOURCE_OVERRIDES: dict[str, bool] = {}

# Order here is also the ingestion order. Prioritize direct ATS / employer boards
# first so the freshest, most authoritative jobs land before aggregators.
_SOURCE_ORDER = [
    "greenhouse", "lever", "ashby", "workable", "workday", "rippling",
    "recruitee", "smartrecruiters", "usajobs", "simplify", "adzuna", "remotive", "arbeitnow",
    "jobicy", "remoteok", "workingnomads", "themuse", "himalayas", "rss",
    "jobrightai", "jobspy",
]

# Kept as compatibility exports for the query and ingestion services.  The
# canonical classification lives in source_intelligence.py so the API can expose
# exactly the same provenance that deduplication uses.
_SOURCE_AUTHORITY = {name: source_authority(name) for name in _SOURCE_ORDER}
_DEFAULT_AUTHORITY = source_authority(None)


def _load_sources_cfg() -> dict[str, Any]:
    """Load sources.yaml, merging in auto-discovered companies + runtime overrides."""
    with open("sources.yaml") as f:
        cfg = yaml.safe_load(f) or {}
    cfg = _merge_discovered(cfg)
    cfg = _merge_company_targets(cfg)
    # Apply runtime overrides (e.g. the UI's high-risk JobSpy toggle) last.
    if _RUNTIME_SOURCE_OVERRIDES:
        sources = cfg.setdefault("sources", {})
        for name, enabled in _RUNTIME_SOURCE_OVERRIDES.items():
            sources.setdefault(name, {})["enabled"] = enabled
    return cfg


def _merge_company_targets(cfg: dict[str, Any]) -> dict[str, Any]:
    """Attach the curated Companies-tab targets without making them adapters.

    Only rows with a verified public ATS are also represented under ``sources``.
    Direct-only targets stay registry links and can never accidentally enter an
    ingestion run.
    """
    try:
        with open("data/company_targets.yaml") as f:
            target_cfg = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return cfg
    targets = target_cfg.get("companies") or []
    if isinstance(targets, list):
        cfg["company_targets"] = targets
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
        if name == "usajobs":
            adapters.append(USAJobsAdapter())
        elif name == "simplify":
            adapters.append(SimplifyAdapter())
        elif name == "adzuna":
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
        elif name == "himalayas":
            adapters.append(HimalayasAdapter())
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
    """Map configured board tokens and display names to their size bucket."""
    out: dict[str, str] = {}
    for key in ("greenhouse", "lever"):
        for c in sources_cfg.get(key, {}).get("companies", []) or []:
            if isinstance(c, dict):
                tok = c.get("token") or c.get("name")
                size = c.get("size")
                if tok and size:
                    out[str(tok).lower()] = str(size)
                    display_name = c.get("display_name") or c.get("company")
                    if display_name:
                        out[str(display_name).lower()] = str(size)
    return out


def _enabled_source_names(sources_cfg: dict[str, Any]) -> list[str]:
    return [n for n in _SOURCE_ORDER if sources_cfg.get(n, {}).get("enabled", False)]
