#!/usr/bin/env python3
"""Seed/refresh the company registry (DuckDB `companies` table).

Merges three sources into one queryable registry:
  1. data/discovered_companies.csv  — 194 verified-reachable boards (ats, slug, careers, open roles)
  2. data/company_tiers.csv         — cleaned tier labels + reachable_ats (or "none" → direct-apply)
  3. data/h1b_sponsors.txt          — known H-1B filer flag (via sponsors.is_known_h1b_sponsor)

Reachable companies are stored enabled (refresh watchlist); companies whose cleaned tier map says
reachable_ats="none" (FAANG / big finance / consulting on Workday/Taleo) are stored
``direct_apply_only=True`` with a best-effort careers URL — surfaced as a link, never scraped.

    python scripts/build_company_registry.py

Run from the repo root.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from jobscout.models import Company  # noqa: E402
from jobscout.relational import RelationalStore  # noqa: E402
from jobscout.sponsors import is_known_h1b_sponsor  # noqa: E402

DISCOVERED = REPO / "data" / "discovered_companies.csv"
TIERS = REPO / "data" / "company_tiers.csv"

# Best-effort careers URL for direct-apply (unreachable) employers.
_CAREERS = {
    "amazon": "https://www.amazon.jobs/", "apple": "https://jobs.apple.com/",
    "google": "https://careers.google.com/", "meta": "https://www.metacareers.com/",
    "microsoft": "https://careers.microsoft.com/", "netflix": "https://jobs.netflix.com/",
    "nvidia": "https://www.nvidia.com/en-us/about-nvidia/careers/",
}


def _load_tiers() -> dict[str, dict]:
    """slug -> {name, tier, reachable_ats} from the cleaned tier map."""
    out: dict[str, dict] = {}
    with TIERS.open() as f:
        for row in csv.DictReader((ln for ln in f if not ln.startswith("#"))):
            if row.get("slug"):
                out[row["slug"]] = row
    return out


def main() -> int:
    tiers = _load_tiers()
    store = RelationalStore()
    seen: set[tuple[str, str]] = set()
    reachable = direct = 0
    try:
        # 1. Discovered (reachable) boards — the bulk of the watchlist.
        with DISCOVERED.open() as f:
            for r in csv.DictReader(f):
                slug, ats = r["slug"], r["ats"]
                tinfo = tiers.get(slug, {})
                c = Company(
                    slug=slug, ats=ats, name=r.get("company") or slug,
                    careers_url=r.get("careers_url"),
                    tier=tinfo.get("tier", "Startups"),       # discovered ≈ startups by default
                    known_h1b_sponsor=is_known_h1b_sponsor(r.get("company") or slug),
                    open_roles=int(r.get("total_open_roles") or 0),
                    enabled=True, direct_apply_only=False,
                )
                store.upsert_company(c)
                seen.add((ats, slug))
                reachable += 1

        # 2. Tier-map companies (adds the big-name + direct-apply entries).
        for slug, t in tiers.items():
            r_ats = (t.get("reachable_ats") or "none").strip()
            name = t.get("name") or slug
            if r_ats == "none":
                key = ("none", slug)
                if key in seen:
                    continue
                store.upsert_company(Company(
                    slug=slug, ats="none", name=name, tier=t["tier"],
                    careers_url=_CAREERS.get(slug),
                    known_h1b_sponsor=is_known_h1b_sponsor(name),
                    enabled=False, direct_apply_only=True,
                ))
                seen.add(key)
                direct += 1
            elif (r_ats, slug) not in seen:
                store.upsert_company(Company(
                    slug=slug, ats=r_ats, name=name, tier=t["tier"],
                    careers_url=f"https://jobs.{r_ats}.io/{slug}",
                    known_h1b_sponsor=is_known_h1b_sponsor(name),
                    enabled=True, direct_apply_only=False,
                ))
                seen.add((r_ats, slug))
                reachable += 1

        total = len(store.list_companies())
    finally:
        store.close()

    print(f"Registry seeded: {total} companies "
          f"({reachable} reachable/enabled, {direct} direct-apply-only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
