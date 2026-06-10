#!/usr/bin/env python3
"""Auto-discovery engine: find companies actively hiring on public ATS boards.

Given a seed list of candidate company slugs (data/company_seeds.txt), this
probes each company's PUBLIC ATS API across Greenhouse, Lever, Ashby, Workable,
and Rippling, and records which ones have open roles matching your keywords.
Output is a ranked CSV — your *reach-out target list* of companies that are
hiring right now.

    python scripts/discover_companies.py --keywords "data,engineer,analyst,scientist"
    python scripts/discover_companies.py --ats greenhouse,ashby --limit 300
    python scripts/discover_companies.py --write-sources   # merge hits into sources.yaml

WHAT THIS IS / ISN'T:
  - It surfaces COMPANIES (name, ATS, careers URL, open-role counts, a sample
    role + apply link). That is the target list for outreach.
  - It does NOT find or store any person's contact info (CEO/recruiter email).
    That is a manual LinkedIn step — JobScout never scrapes PII (compliance.yaml
    collect_personal_data: false). The CSV is structured to support that step.
  - "Small company" is APPROXIMATED at discovery time by open-role count (fewer
    total open roles roughly tracks smaller headcount). True size is refined later
    by the DeepSeek company_size enrichment when these companies are ingested.

All requests go through CompliantHttpClient (robots/rate-limit/backoff). Probing
hundreds of slugs is a slow batch by design — run it in the background.
Run from the repo root.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from jobscout.adapters.base import CompliantHttpClient  # noqa: E402

SEED_FILE = REPO_ROOT / "data" / "company_seeds.txt"
OUT_FILE = REPO_ROOT / "data" / "discovered_companies.csv"

# A probe returns (total_roles, [titles]) or None if the board doesn't exist.
Probe = Callable[[str, CompliantHttpClient], "tuple[int, list[str], str] | None"]


def slugify(name: str) -> str:
    """Heuristic company-name → ATS slug (lowercase, strip non-alphanumerics)."""
    return re.sub(r"[^a-z0-9]", "", name.strip().lower())


# A title carrying any of these markers is senior, NOT a 0-2yr fit — even if it
# matches a relevant keyword (e.g. "Founding Engineer", "Staff Data Scientist").
_SENIOR_MARKER = re.compile(
    r"\b(founding|senior|sr|staff|principal|lead|director|head|vp|chief|"
    r"manager|architect|ii|iii|iv)\b",
    re.IGNORECASE,
)


def is_junior_title(title: str) -> bool:
    """True if the title looks 0-2yr-friendly (no senior marker)."""
    return not _SENIOR_MARKER.search(title)


# ── Per-ATS probes (public list endpoints; titles only, no detail fetch) ──────

def probe_greenhouse(slug: str, http: CompliantHttpClient):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    r = http.get(url, api_source=True)
    if r.status_code != 200:
        return None
    jobs = r.json().get("jobs") or []
    titles = [str(j.get("title") or "") for j in jobs]
    return len(titles), titles, f"https://boards.greenhouse.io/{slug}"


def probe_lever(slug: str, http: CompliantHttpClient):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    r = http.get(url, api_source=True)
    if r.status_code != 200:
        return None
    data = r.json()
    if not isinstance(data, list):
        return None
    titles = [str(j.get("text") or "") for j in data]
    return len(titles), titles, f"https://jobs.lever.co/{slug}"


def probe_ashby(slug: str, http: CompliantHttpClient):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    r = http.get(url, api_source=True)
    if r.status_code != 200:
        return None
    jobs = r.json().get("jobs") or []
    titles = [str(j.get("title") or "") for j in jobs if j.get("isListed") is not False]
    if not jobs:
        return None
    return len(titles), titles, f"https://jobs.ashbyhq.com/{slug}"


def probe_workable(slug: str, http: CompliantHttpClient):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    r = http.get(url, api_source=True)
    if r.status_code != 200:
        return None
    jobs = r.json().get("jobs") or []
    if not jobs:
        return None
    titles = [str(j.get("title") or "") for j in jobs]
    return len(titles), titles, f"https://apply.workable.com/{slug}"


def probe_rippling(slug: str, http: CompliantHttpClient):
    url = f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"
    r = http.get(url, api_source=True)
    if r.status_code != 200:
        return None
    data = r.json()
    jobs = data if isinstance(data, list) else (data.get("items") or [])
    if not jobs:
        return None
    titles = [str(j.get("name") or "") for j in jobs]
    return len(titles), titles, f"https://ats.rippling.com/{slug}/jobs"


def probe_recruitee(slug: str, http: CompliantHttpClient):
    url = f"https://{slug}.recruitee.com/api/offers/"
    r = http.get(url, api_source=True)
    if r.status_code != 200:
        return None
    offers = (r.json() or {}).get("offers") or []
    if not offers:
        return None
    titles = [str(o.get("title") or "") for o in offers]
    return len(titles), titles, f"https://{slug}.recruitee.com/"


def probe_smartrecruiters(slug: str, http: CompliantHttpClient):
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
    r = http.get(url, api_source=True)
    if r.status_code != 200:
        return None
    content = (r.json() or {}).get("content") or []
    if not content:
        return None
    titles = [str(p.get("name") or "") for p in content]
    return len(titles), titles, f"https://jobs.smartrecruiters.com/{slug}"


PROBES: dict[str, Probe] = {
    "greenhouse": probe_greenhouse,
    "lever": probe_lever,
    "ashby": probe_ashby,
    "workable": probe_workable,
    "rippling": probe_rippling,
    "recruitee": probe_recruitee,
    "smartrecruiters": probe_smartrecruiters,
}


def load_seeds(seed_file: Path = SEED_FILE) -> list[str]:
    if not seed_file.exists():
        sys.exit(f"seed file not found: {seed_file}")
    seeds: list[str] = []
    for line in seed_file.read_text().splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            seeds.append(slugify(s))
    # de-dupe preserving order
    seen: set[str] = set()
    return [s for s in seeds if not (s in seen or seen.add(s))]


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover companies hiring on public ATS boards.")
    ap.add_argument("--keywords", default="data,engineer,analyst,scientist,software,developer,ml,ai",
                    help="comma-separated title keywords that mark a role 'relevant'")
    # Default to the startup-dense ATS that don't aggressively rate-limit bulk
    # probing. Workable 429s hard (42s of backoff per hit) and Rippling is sparse
    # — pass them explicitly via --ats when you want them.
    ap.add_argument("--ats", default="greenhouse,lever,ashby",
                    help="comma-separated ATS to probe (default: greenhouse,lever,ashby; "
                         "all: greenhouse,lever,ashby,workable,rippling)")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the number of seed slugs probed (0 = all)")
    ap.add_argument("--min-relevant", type=int, default=1,
                    help="only keep companies with at least this many relevant open roles")
    ap.add_argument("--write-sources", action="store_true",
                    help="(opt-in) write verified slugs to sources.discovered.yaml "
                         "(merged into sources.yaml at load)")
    ap.add_argument("--seeds", default=str(SEED_FILE),
                    help="path to a seed list (default: data/company_seeds.txt; "
                         "e.g. data/cap_exempt_seeds.txt for cap-exempt discovery)")
    args = ap.parse_args()

    keywords = [k.strip().lower() for k in args.keywords.split(",") if k.strip()]
    ats_list = [a.strip() for a in args.ats.split(",") if a.strip() in PROBES]
    seeds = load_seeds(Path(args.seeds))
    if args.limit:
        seeds = seeds[: args.limit]

    print(f"Seeds: {len(seeds)}  |  ATS: {ats_list}  |  keywords: {keywords}")
    print("NOTE: this is a COMPANY reach-out list (no contact info / PII). "
          "'Small' is approximated by open-role count; refined by enrichment at ingest.\n")

    def relevant(titles: list[str]) -> list[str]:
        return [t for t in titles if any(k in t.lower() for k in keywords)]

    rows: list[dict] = []
    http = CompliantHttpClient()
    t0 = time.monotonic()
    try:
        for i, slug in enumerate(seeds, 1):
            for ats in ats_list:
                try:
                    result = PROBES[ats](slug, http)
                except Exception:  # noqa: BLE001
                    result = None
                if result is None:
                    continue
                total, titles, careers = result
                rel = relevant(titles)
                if len(rel) < args.min_relevant:
                    continue
                # Junior-relevant = keyword match AND not a senior title. This is
                # what stops "Founding Engineer" / "Staff DS" from looking like a
                # 0-2yr fit. (Real YoE is still decided by enrichment at ingest.)
                junior = [t for t in rel if is_junior_title(t)]
                rows.append({
                    "ats": ats,
                    "slug": slug,
                    "company": slug,
                    "careers_url": careers,
                    "total_open_roles": total,
                    "relevant_open_roles": len(rel),
                    "junior_relevant_open_roles": len(junior),
                    "sample_relevant_title": (junior[0] if junior else rel[0]),
                })
                print(f"  [{i}/{len(seeds)}] HIT {ats:<10} {slug:<22} "
                      f"{len(junior)} jr / {len(rel)} rel / {total} total")
    finally:
        http.close()

    # Rank: most JUNIOR-relevant roles first, then most relevant, then SMALLEST
    # total footprint (size proxy — fewer total roles ≈ smaller company).
    rows.sort(key=lambda r: (
        -r["junior_relevant_open_roles"], -r["relevant_open_roles"], r["total_open_roles"]
    ))

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUT_FILE.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "ats", "slug", "company", "careers_url", "total_open_roles",
            "relevant_open_roles", "junior_relevant_open_roles", "sample_relevant_title",
        ])
        w.writeheader()
        w.writerows(rows)

    elapsed = time.monotonic() - t0
    print(f"\nDiscovered {len(rows)} actively-hiring companies in {elapsed:.0f}s "
          f"-> {OUT_FILE.relative_to(REPO_ROOT)}")
    by_ats: dict[str, int] = {}
    for r in rows:
        by_ats[r["ats"]] = by_ats.get(r["ats"], 0) + 1
    print("  by ATS:", by_ats)

    if args.write_sources:
        _write_sources(rows)
    return 0


# Generated file — merged into sources.yaml at load by _load_sources_cfg. Kept
# separate so the hand-curated sources.yaml (with its comments) is never mangled.
DISCOVERED_FILE = REPO_ROOT / "sources.discovered.yaml"
_KEY_FIELD = {"greenhouse": "companies", "lever": "companies", "ashby": "companies",
              "rippling": "companies", "workable": "accounts"}


def _write_sources(rows: list[dict]) -> None:
    """Write discovered slugs to sources.discovered.yaml (per-ATS, deduped)."""
    import yaml  # local import; only needed for the opt-in path

    sources: dict[str, dict] = {}
    seen: set[tuple[str, str]] = set()
    for r in rows:
        ats = r["ats"]
        field = _KEY_FIELD[ats]
        if (ats, r["slug"]) in seen:
            continue
        seen.add((ats, r["slug"]))
        sources.setdefault(ats, {}).setdefault(field, []).append(
            {"token": r["slug"], "type": "for_profit"}
        )
    DISCOVERED_FILE.write_text(
        "# AUTO-GENERATED by scripts/discover_companies.py --write-sources.\n"
        "# Merged into sources.yaml at load. Safe to delete/regenerate.\n"
        + yaml.safe_dump({"sources": sources}, sort_keys=False, width=120)
    )
    n = sum(len(v[f]) for v in sources.values() for f in v)
    print(f"  wrote {n} discovered slugs across {len(sources)} ATS "
          f"-> {DISCOVERED_FILE.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main())
