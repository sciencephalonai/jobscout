#!/usr/bin/env python3
"""Workday cap-exempt tenant prober.

Cap-exempt H-1B employers (universities, academic medical centers, large
nonprofit research orgs) overwhelmingly use Workday. Unlike Greenhouse/Lever
(single slug), a Workday career site is a ``(tenant, region, site)`` triple
encoded in its URL, e.g.::

    https://cornell.wd1.myworkdayjobs.com/en-US/cornellCareerPage
            └tenant┘ └reg┘                       └──── site ────┘

Feed this script a list of career-site URLs (data/workday_cap_exempt_seeds.txt);
it parses each, hits the PUBLIC CXS search API (via the shipping WorkdayAdapter,
so robots/rate-limit/US-filter all apply), and reports which tenants are live +
have roles matching your keywords. ``--write-sources`` merges the verified ones
into sources.discovered.yaml (stamped ``type`` so cap_exempt derives "likely",
and ``name`` so the jobs show an employer).

    python scripts/probe_workday.py                  # report only
    python scripts/probe_workday.py --write-sources   # + emit verified tenants

All requests go through CompliantHttpClient. Run from the repo root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from jobscout.adapters.base import CompliantHttpClient  # noqa: E402
from jobscout.adapters.workday import WorkdayAdapter, parse_workday_url  # noqa: E402

SEED_FILE = REPO_ROOT / "data" / "workday_cap_exempt_seeds.txt"
DISCOVERED_FILE = REPO_ROOT / "sources.discovered.yaml"
_VALID_TYPES = {"university", "hospital", "nonprofit", "government"}


def load_seeds(path: Path) -> list[dict[str, str]]:
    """Parse ``<url> | <type> | <name>`` seed lines into tenant dicts."""
    if not path.exists():
        sys.exit(f"seed file not found: {path}")
    tenants: list[dict[str, str]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        url = parts[0]
        etype = parts[1] if len(parts) > 1 and parts[1] else "university"
        name = parts[2] if len(parts) > 2 else ""
        if etype not in _VALID_TYPES:
            print(f"  ! skipping bad type {etype!r} in: {line}")
            continue
        parsed = parse_workday_url(url)
        if not parsed:
            print(f"  ! could not parse Workday URL: {url}")
            continue
        tenants.append({**parsed, "type": etype, "name": name})
    return tenants


def probe(tenant: dict[str, str], keywords: list[str], sample: int,
          http: CompliantHttpClient) -> tuple[int, int, str | None]:
    """Sample a tenant's open roles → (sampled, keyword-relevant, sample_title)."""
    adapter = WorkdayAdapter(tenants=[tenant], fetch_descriptions=False)
    titles = [str(r.get("title") or "") for r in
              adapter.search([], None, sample, None, http)]
    rel = [t for t in titles if any(k in t.lower() for k in keywords)]
    sample_title = (rel[0] if rel else (titles[0] if titles else None))
    return len(titles), len(rel), sample_title


def write_sources(verified: list[dict[str, str]]) -> None:
    """Merge verified tenants into sources.discovered.yaml's workday block,
    preserving any other ATS blocks already written by discover_companies.py."""
    import yaml

    doc: dict[str, Any] = {}
    if DISCOVERED_FILE.exists():
        doc = yaml.safe_load(DISCOVERED_FILE.read_text()) or {}
    sources = doc.setdefault("sources", {})
    wd = sources.setdefault("workday", {})
    existing = wd.setdefault("tenants", [])
    seen = {(t.get("tenant"), t.get("site")) for t in existing}
    added = 0
    for t in verified:
        key = (t["tenant"], t["site"])
        if key not in seen:
            existing.append(t)
            seen.add(key)
            added += 1
    DISCOVERED_FILE.write_text(
        "# AUTO-GENERATED (workday tenants by scripts/probe_workday.py; other ATS by\n"
        "# discover_companies.py). Merged into sources.yaml at load. Safe to regenerate.\n"
        + yaml.safe_dump(doc, sort_keys=False, width=120)
    )
    print(f"\n  wrote {added} new tenant(s) -> {DISCOVERED_FILE.relative_to(REPO_ROOT)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe Workday cap-exempt career sites.")
    ap.add_argument("--seeds", default=str(SEED_FILE), help="seed URL file")
    ap.add_argument("--keywords", default="data,engineer,analyst,scientist,software,developer,ml,ai,research",
                    help="comma-separated title keywords marking a role 'relevant'")
    ap.add_argument("--sample", type=int, default=30, help="roles to sample per tenant")
    ap.add_argument("--min-relevant", type=int, default=1,
                    help="only keep tenants with at least this many relevant roles")
    ap.add_argument("--write-sources", action="store_true",
                    help="merge verified tenants into sources.discovered.yaml")
    args = ap.parse_args()

    keywords = [k.strip().lower() for k in args.keywords.split(",") if k.strip()]
    tenants = load_seeds(Path(args.seeds))
    print(f"Seeds: {len(tenants)} tenants | keywords: {keywords}\n")

    verified: list[dict[str, str]] = []
    http = CompliantHttpClient()
    try:
        for i, t in enumerate(tenants, 1):
            try:
                sampled, rel, title = probe(t, keywords, args.sample, http)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(tenants)}] ERR  {t['tenant']:<18} {exc}")
                continue
            if sampled == 0:
                print(f"  [{i}/{len(tenants)}] MISS {t['tenant']:<18} (no US roles / dead site)")
                continue
            status = "HIT " if rel >= args.min_relevant else "thin"
            print(f"  [{i}/{len(tenants)}] {status} {t['tenant']:<18} "
                  f"{rel} rel / {sampled} sampled | {t['name']} | e.g. {title!r}")
            if rel >= args.min_relevant:
                verified.append(t)
    finally:
        http.close()

    print(f"\nVerified {len(verified)}/{len(tenants)} tenants with relevant roles.")
    if args.write_sources and verified:
        write_sources(verified)
    elif args.write_sources:
        print("  nothing verified — not writing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
