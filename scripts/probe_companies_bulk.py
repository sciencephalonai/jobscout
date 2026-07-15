#!/usr/bin/env python3
"""Probe 150 target companies for Greenhouse / Lever / Ashby ATS boards.

Runs in parallel, validates each slug returns at least 1 job, then writes
verified companies into sources.discovered.yaml (same format as probe_workday.py).

Usage:
    python scripts/probe_companies_bulk.py
    python scripts/probe_companies_bulk.py --write-sources   # auto-merge results
    python scripts/probe_companies_bulk.py --dry-run         # print results only
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import yaml

# ──────────────────────────────────────────────────────────────────────────────
# Target company list: (display_name, ats, slug)
# ATS: "greenhouse" | "lever" | "ashby"
# Slug: the company-specific identifier in the ATS URL
# ──────────────────────────────────────────────────────────────────────────────

TARGETS: list[tuple[str, str, str]] = [
    # ── Greenhouse ────────────────────────────────────────────────────────────
    ("Airbnb",              "greenhouse", "airbnb"),
    ("Lyft",                "greenhouse", "lyft"),
    ("DoorDash",            "greenhouse", "doordash"),
    ("Cloudflare",          "greenhouse", "cloudflare"),
    ("MongoDB",             "greenhouse", "mongodb"),
    ("Databricks",          "greenhouse", "databricks"),
    ("Snowflake",           "greenhouse", "snowflake"),
    ("Okta",                "greenhouse", "okta"),
    ("Twilio",              "greenhouse", "twilio"),
    ("Duolingo",            "greenhouse", "duolingo"),
    ("Airtable",            "greenhouse", "airtable"),
    ("Scale AI",            "greenhouse", "scaleai"),
    ("Gusto",               "greenhouse", "gusto"),
    ("Klaviyo",             "greenhouse", "klaviyo"),
    ("SentinelOne",         "greenhouse", "sentinelone"),
    ("dbt Labs",            "greenhouse", "dbtlabs"),
    ("Grafana Labs",        "greenhouse", "grafana"),
    ("Chime",               "greenhouse", "chime"),
    ("CockroachDB",         "greenhouse", "cockroachlabs"),
    ("Fivetran",            "greenhouse", "fivetran"),
    ("PagerDuty",           "greenhouse", "pagerduty"),
    ("Amplitude",           "greenhouse", "amplitude"),
    ("GitLab",              "greenhouse", "gitlab"),
    ("New Relic",           "greenhouse", "newrelic"),
    ("Samsara",             "greenhouse", "samsara"),
    ("Stripe",              "greenhouse", "stripe"),
    ("Plaid",               "greenhouse", "plaid"),
    ("Figma",               "greenhouse", "figma"),
    ("Wiz",                 "greenhouse", "wiz"),
    ("Abnormal Security",   "greenhouse", "abnormalsecurity"),
    ("Cohere",              "greenhouse", "cohere"),
    ("Glean",               "greenhouse", "glean"),
    ("Anyscale",            "greenhouse", "anyscale"),
    ("Together AI",         "greenhouse", "togetherai"),
    ("Figure AI",           "greenhouse", "figure"),
    ("Skydio",              "greenhouse", "skydio"),
    ("Nuro",                "greenhouse", "nuro"),
    ("Waymo",               "greenhouse", "waymo"),
    ("Harvey AI",           "greenhouse", "harvey"),
    ("Weights & Biases",    "greenhouse", "wandb"),
    ("Snyk",                "greenhouse", "snyk"),
    ("HashiCorp",           "greenhouse", "hashicorp"),
    ("Fastly",              "greenhouse", "fastly"),
    ("Temporal",            "greenhouse", "temporal"),
    ("Supabase",            "greenhouse", "supabase"),
    ("Redis",               "greenhouse", "redislabs"),
    ("Monte Carlo Data",    "greenhouse", "montecarlodata"),
    ("Alation",             "greenhouse", "alation"),
    ("Hex Technologies",    "greenhouse", "hex"),
    ("Doximity",            "greenhouse", "doximity"),
    ("Asana",               "greenhouse", "asana"),
    ("Flexport",            "greenhouse", "flexport"),
    ("Recorded Future",     "greenhouse", "recordedfuture"),
    ("Vanta",               "greenhouse", "vanta"),
    ("Health Catalyst",     "greenhouse", "healthcatalyst"),
    ("Komodo Health",       "greenhouse", "komodohealth"),
    ("Starburst Data",      "greenhouse", "starburst"),
    ("JFrog",               "greenhouse", "jfrog"),
    ("Calendly",            "greenhouse", "calendly"),
    ("ServiceTitan",        "greenhouse", "servicetitan"),
    ("Noom",                "greenhouse", "noom"),
    ("Coursera",            "greenhouse", "coursera"),
    ("Lattice",             "greenhouse", "lattice"),
    ("Included Health",     "greenhouse", "includedhealth"),
    ("Drata",               "greenhouse", "drata"),
    ("Contentful",          "greenhouse", "contentful"),
    ("Yext",                "greenhouse", "yext"),
    ("BigCommerce",         "greenhouse", "bigcommerce"),
    ("Perplexity AI",       "greenhouse", "perplexity"),
    ("Replit",              "greenhouse", "replit"),
    ("Cognition AI",        "greenhouse", "cognition"),
    ("Runway ML",           "greenhouse", "runwayml"),
    ("Character AI",        "greenhouse", "characterai"),
    ("Mistral AI",          "greenhouse", "mistral"),
    ("Wiz",                 "greenhouse", "wizio"),       # alt slug
    ("PlanetScale",         "greenhouse", "planetscale"),
    ("Docusign",            "greenhouse", "docusign"),    # fallback if Workday 404s
    ("Shopify",             "greenhouse", "shopify"),     # fallback if Workday 404s
    ("Brex",                "greenhouse", "brex"),
    ("Faire",               "greenhouse", "faire"),
    ("Klarna",              "greenhouse", "klarna"),
    ("Ramp",                "greenhouse", "ramp"),
    ("Zendesk",             "greenhouse", "zendesk"),
    ("HubSpot",             "greenhouse", "hubspot"),
    ("DocuSign",            "greenhouse", "docusign"),
    ("Intercom",            "greenhouse", "intercom"),
    ("ServiceNow",          "greenhouse", "servicenow"),
    ("Datadog",             "greenhouse", "datadoghq"),
    ("Akamai",              "greenhouse", "akamai"),
    ("Veeva",               "greenhouse", "veeva"),
    ("Cybereason",          "greenhouse", "cybereason"),
    ("Drata",               "greenhouse", "drata"),
    ("Orca Security",       "greenhouse", "orca"),
    ("Teleport",            "greenhouse", "teleport"),
    ("Ping Identity",       "greenhouse", "pingidentity"),
    ("Viz.ai",              "greenhouse", "vizai"),
    ("Tempus AI",           "greenhouse", "tempus"),
    ("Nuro",                "greenhouse", "nuro"),
    ("Commonwealth Fusion", "greenhouse", "cfs"),
    ("Arcadia",             "greenhouse", "arcadia"),
    ("Redwood Materials",   "greenhouse", "redwoodmaterials"),
    ("Afforded / Affirm",   "greenhouse", "affirm"),
    ("Monday.com",          "greenhouse", "monday"),
    ("Carta",               "greenhouse", "carta"),
    ("Rippling",            "greenhouse", "rippling"),

    # ── Lever ────────────────────────────────────────────────────────────────
    ("Notion",              "lever", "notion"),
    ("Robinhood",           "lever", "robinhood"),
    ("Affirm",              "lever", "affirm"),
    ("Marqeta",             "lever", "marqeta"),
    ("Lacework",            "lever", "lacework"),
    ("Mixpanel",            "lever", "mixpanel"),
    ("Klarna",              "lever", "klarna"),           # alt if Greenhouse 404s
    ("Intercom",            "lever", "intercom"),
    ("Amplitude",           "lever", "amplitude"),        # alt if Greenhouse 404s
    ("Sentry",              "lever", "sentry"),
    ("HashiCorp",           "lever", "hashicorp"),        # alt
    ("Temporal",            "lever", "temporal"),         # alt
    ("Census",              "lever", "getcensus"),
    ("Starburst",           "lever", "starburstdata"),
    ("Grafana",             "lever", "grafana"),          # alt
    ("Nuro",                "lever", "nuro"),             # alt
    ("Cockroach Labs",      "lever", "cockroachlabs"),    # alt

    # ── Ashby ────────────────────────────────────────────────────────────────
    ("Vercel",              "ashby", "vercel"),
    ("Linear",              "ashby", "linear"),
    ("Modal Labs",          "ashby", "modal"),
    ("Brex",                "ashby", "brex"),
    ("Faire",               "ashby", "faire"),
    ("Carta",               "ashby", "carta"),
    ("Cognition AI",        "ashby", "cognition"),        # alt
    ("Harvey AI",           "ashby", "harvey"),           # alt
    ("Mistral AI",          "ashby", "mistral"),          # alt
    ("Together AI",         "ashby", "togetherai"),       # alt
    ("Runway",              "ashby", "runwayml"),         # alt
    ("Perplexity",          "ashby", "perplexity"),       # alt
    ("Scale AI",            "ashby", "scaleai"),          # alt
]

# Dedup by (ats, slug) — first entry wins
seen: set[tuple[str, str]] = set()
DEDUPED: list[tuple[str, str, str]] = []
for entry in TARGETS:
    key = (entry[1], entry[2])
    if key not in seen:
        seen.add(key)
        DEDUPED.append(entry)

_HEADERS = {"User-Agent": "JobScout/1.0"}
_TIMEOUT = 6


def _probe(name: str, ats: str, slug: str) -> dict | None:
    """Return probe result dict or None if no jobs found / 404."""
    try:
        if ats == "greenhouse":
            r = requests.get(
                f"https://boards.greenhouse.io/v1/boards/{slug}/jobs",
                timeout=_TIMEOUT, headers=_HEADERS,
            )
            if r.status_code != 200:
                return None
            jobs = r.json().get("jobs", [])
            if not jobs:
                return None
            return {"name": name, "ats": ats, "slug": slug,
                    "job_count": len(jobs), "sample": jobs[0].get("title", "")}

        if ats == "lever":
            r = requests.get(
                f"https://api.lever.co/v0/postings/{slug}",
                timeout=_TIMEOUT, headers=_HEADERS,
            )
            if r.status_code != 200:
                return None
            jobs = r.json()
            if not jobs:
                return None
            return {"name": name, "ats": ats, "slug": slug,
                    "job_count": len(jobs), "sample": jobs[0].get("text", "")}

        if ats == "ashby":
            r = requests.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
                timeout=_TIMEOUT, headers={**_HEADERS, "Accept": "application/json"},
            )
            if r.status_code != 200:
                return None
            jobs = [j for j in r.json().get("jobs", []) if j.get("isListed") is not False]
            if not jobs:
                return None
            return {"name": name, "ats": ats, "slug": slug,
                    "job_count": len(jobs), "sample": jobs[0].get("title", "")}

    except Exception:
        return None
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Probe 150 target companies for ATS boards")
    ap.add_argument("--write-sources", action="store_true",
                    help="Merge verified companies into sources.discovered.yaml")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print results without writing anything")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    print(f"Probing {len(DEDUPED)} company/ATS combinations (workers={args.workers}) …")
    t0 = time.time()

    hits: list[dict] = []
    misses: list[tuple[str, str, str]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_probe, name, ats, slug): (name, ats, slug)
            for name, ats, slug in DEDUPED
        }
        for future in as_completed(futures):
            name, ats, slug = futures[future]
            result = future.result()
            if result:
                hits.append(result)
                print(f"  HIT  {name:<30} {ats:<12} slug={slug:<20} jobs={result['job_count']}")
            else:
                misses.append((name, ats, slug))
                print(f"  MISS {name:<30} {ats:<12} slug={slug}")

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"Verified {len(hits)}/{len(DEDUPED)} in {elapsed:.1f}s")

    if not hits:
        print("No companies verified — nothing to write.")
        return

    # Deduplicate hits by name (keep highest job_count per company)
    by_name: dict[str, dict] = {}
    for h in hits:
        prev = by_name.get(h["name"])
        if prev is None or h["job_count"] > prev["job_count"]:
            by_name[h["name"]] = h
    unique_hits = sorted(by_name.values(), key=lambda x: x["name"])

    print(f"\nUnique companies verified: {len(unique_hits)}")
    for h in unique_hits:
        print(f"  {h['name']:<30} {h['ats']:<12} {h['slug']:<20} ({h['job_count']} jobs) e.g. {h['sample'][:50]!r}")

    if args.dry_run:
        print("\n[dry-run] Not writing to sources.discovered.yaml")
        return

    if not args.write_sources:
        print("\nPass --write-sources to merge into sources.discovered.yaml")
        return

    # ── Merge into sources.discovered.yaml ───────────────────────────────────
    repo_root = Path(__file__).resolve().parent.parent
    discovered = repo_root / "sources.discovered.yaml"

    cfg: dict = {}
    if discovered.exists():
        cfg = yaml.safe_load(discovered.read_text()) or {}
    cfg.setdefault("sources", {})

    # Group by ATS
    by_ats: dict[str, list] = {"greenhouse": [], "lever": [], "ashby": []}
    for h in unique_hits:
        by_ats[h["ats"]].append(h)

    added = 0
    for ats_name, companies in by_ats.items():
        if not companies:
            continue
        block = cfg["sources"].setdefault(ats_name, {})
        existing: list = block.get("companies", []) or []
        existing_slugs = {
            (e.get("token") or e) if isinstance(e, dict) else e
            for e in existing
        }
        for h in companies:
            if h["slug"] not in existing_slugs:
                existing.append({"token": h["slug"], "type": "for_profit", "name": h["name"]})
                added += 1
        block["companies"] = existing

    discovered.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True))
    print(f"\n✓ Added {added} new companies to {discovered}")
    print("  Restart the backend to pick them up in the Companies tab.")


if __name__ == "__main__":
    main()
