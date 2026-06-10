#!/usr/bin/env python3
"""Live smoke test for JobScout source adapters.

Runs each pure-HTTP adapter against its real public API and prints the jobs it
returns — NO Weaviate, NO API keys, NO enrichment required (adapters only make
compliant HTTP calls). This is the fastest way to answer "are the adapters
actually working?" and to eyeball the roles each source surfaces.

    python scripts/smoke_adapters.py --keywords "data,engineer,analyst" --limit 10
    python scripts/smoke_adapters.py --entry-level
    python scripts/smoke_adapters.py --source rippling

IMPORTANT — about the --entry-level view:
    Adapters do NOT know years-of-experience. They keyword-filter on the title.
    --entry-level applies a *title heuristic* only: keep roles without obvious
    senior markers, even when the title does not literally say "junior". TRUE
    0-2yr (YoE) filtering is a downstream backend step: DeepSeek enrichment
    reads each description to fill yoe_min/max, then GET /api/jobs?exp=entry
    filters yoe_min <= 2. That needs the full stack (Weaviate + GOOGLE_API_KEY
    + DEEPSEEK_API_KEY) running.

Run from the repo root so blocklist.yaml resolves.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Make the backend package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jobscout.adapters.arbeitnow import ArbeitnowAdapter  # noqa: E402
from jobscout.adapters.ashby import AshbyAdapter  # noqa: E402
from jobscout.adapters.base import CompliantHttpClient  # noqa: E402
from jobscout.adapters.greenhouse import GreenhouseAdapter  # noqa: E402
from jobscout.adapters.jobicy import JobicyAdapter  # noqa: E402
from jobscout.adapters.lever import LeverAdapter  # noqa: E402
from jobscout.adapters.recruitee import RecruiteeAdapter  # noqa: E402
from jobscout.adapters.remoteok import RemoteOKAdapter  # noqa: E402
from jobscout.adapters.remotive import RemotiveAdapter  # noqa: E402
from jobscout.adapters.rippling import RipplingAdapter  # noqa: E402
from jobscout.adapters.rss import RssAdapter  # noqa: E402
from jobscout.adapters.smartrecruiters import SmartRecruitersAdapter  # noqa: E402
from jobscout.adapters.themuse import TheMuseAdapter  # noqa: E402
from jobscout.adapters.workable import WorkableAdapter  # noqa: E402
from jobscout.adapters.workday import WorkdayAdapter  # noqa: E402
from jobscout.adapters.workingnomads import WorkingNomadsAdapter  # noqa: E402
from jobscout.normalize import is_us_job, raw_to_job  # noqa: E402

# Curated, real seeds verified live (see plan). Each adapter is pure-HTTP (no key).
# Adzuna (needs key) and JobSpy (high-risk scraper) are intentionally excluded.
ADAPTERS = {
    # ── Real aggregators (global keyword search) ──
    "remotive": lambda: RemotiveAdapter(),
    "arbeitnow": lambda: ArbeitnowAdapter(),
    "jobicy": lambda: JobicyAdapter(),
    "remoteok": lambda: RemoteOKAdapter(),
    "workingnomads": lambda: WorkingNomadsAdapter(),
    "themuse": lambda: TheMuseAdapter(),
    # ── Per-company ATS boards (curated slugs) ──
    "greenhouse": lambda: GreenhouseAdapter(
        companies=[{"token": "givewell", "type": "nonprofit"},
                   {"token": "anthropic", "type": "for_profit"}]
    ),
    "lever": lambda: LeverAdapter(companies=[{"token": "spotify", "type": "for_profit"}]),
    "ashby": lambda: AshbyAdapter(companies=[{"token": "ramp", "type": "for_profit"}]),
    "workable": lambda: WorkableAdapter(
        accounts=[{"token": "braven", "type": "nonprofit"}]
    ),
    "workday": lambda: WorkdayAdapter(
        tenants=[{"tenant": "cornell", "region": "wd1",
                  "site": "CornellCareerPage", "type": "university"}],
        fetch_descriptions=False,
    ),
    "rippling": lambda: RipplingAdapter(
        companies=[{"token": "tavernresearch", "type": "for_profit"}],
        fetch_descriptions=False,  # keep the smoke test fast (skip per-job detail GETs)
    ),
    "recruitee": lambda: RecruiteeAdapter(
        companies=[{"token": "tether", "type": "for_profit"}]
    ),
    "smartrecruiters": lambda: SmartRecruitersAdapter(
        companies=[{"token": "Visa", "type": "for_profit"}],
        fetch_descriptions=False,
    ),
    "rss": lambda: RssAdapter(
        feeds=[{"url": "https://www.higheredjobs.com/rss/categoryFeed.cfm?catID=159",
                "type": "university"}]
    ),
}

# Title-only entry-level heuristic (NOT real YoE — see module docstring).
# Match the discovery script's definition: a role is junior-friendly when it
# does not advertise an obvious seniority marker. Requiring an explicit
# "junior"/"new grad" token hides many real early-career roles.
_SENIOR_MARKER = re.compile(
    r"\b(founding|senior|sr|staff|principal|lead|director|head|vp|chief|"
    r"manager|architect|ii|iii|iv)\b",
    re.IGNORECASE,
)


def looks_entry_level(title: str) -> bool:
    """Rough title heuristic for a 0-2yr role. Not a substitute for YoE enrichment."""
    return not _SENIOR_MARKER.search(title)


def run_adapter(name: str, http: CompliantHttpClient, keywords: list[str],
                limit: int, entry_only: bool) -> tuple[int, int, str | None]:
    """Run one adapter, print its US jobs, and return (us_count, shown, error).

    The per-company adapters keyword-filter on the title as a single joined
    *phrase* (e.g. "data engineer" must appear verbatim), so a multi-keyword
    query would match nothing. For a smoke test we instead pull the board with
    no keyword filter and apply our own ANY-keyword title match here.
    """
    needles = [k.lower() for k in keywords]
    rows: list[tuple[str, str, str]] = []
    try:
        adapter = ADAPTERS[name]()
        for raw in adapter.search(
            keywords=[], location=None, results_wanted=limit * 4, since=None, http=http
        ):
            job = raw_to_job(raw, source=name)
            if not is_us_job(job.country, job.location_raw, job.remote_mode):
                continue
            title_l = job.title.lower()
            if needles and not any(n in title_l for n in needles):
                continue
            if entry_only and not looks_entry_level(job.title):
                continue
            rows.append((job.title, job.location_raw or job.remote_mode, job.url))
            if len(rows) >= limit:
                break
    except Exception as exc:  # noqa: BLE001
        return 0, 0, f"{type(exc).__name__}: {exc}"

    print(f"\n=== {name}  ({len(rows)} US{' entry-level' if entry_only else ''} jobs) ===")
    for title, loc, url in rows:
        print(f"  • {title[:58]:<58}  {loc[:24]:<24}  {url}")
    if not rows:
        print("  (none)")
    return len(rows), len(rows), None


def main() -> int:
    ap = argparse.ArgumentParser(description="Live smoke test for JobScout adapters.")
    ap.add_argument("--keywords", default="data,engineer,analyst,scientist",
                    help="comma-separated title keywords (default: data,engineer,analyst,scientist)")
    ap.add_argument("--limit", type=int, default=15, help="max results per source")
    ap.add_argument("--entry-level", action="store_true",
                    help="title heuristic for 0-2yr roles (NOT real YoE — see header)")
    ap.add_argument("--source", help="run only this adapter (e.g. rippling)")
    args = ap.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    names = [args.source] if args.source else list(ADAPTERS)
    bad = [n for n in names if n not in ADAPTERS]
    if bad:
        ap.error(f"unknown source(s): {bad}. choices: {list(ADAPTERS)}")

    print(f"Keywords: {keywords}  |  limit/source: {args.limit}"
          f"  |  entry-level: {args.entry_level}")
    if args.entry_level:
        print("NOTE: --entry-level is a TITLE heuristic. Real 0-2yr (YoE) filtering "
              "needs the enrichment backend (Weaviate + Gemini + DeepSeek).")

    http = CompliantHttpClient()
    summary: list[tuple[str, int, str | None]] = []
    try:
        for name in names:
            count, _shown, err = run_adapter(
                name, http, keywords, args.limit, args.entry_level
            )
            summary.append((name, count, err))
    finally:
        http.close()

    print("\n----- summary -----")
    for name, count, err in summary:
        status = f"ERROR ({err})" if err else (f"{count} jobs" if count else "empty")
        print(f"  {name:<12} {status}")
    total = sum(c for _, c, _ in summary)
    print(f"\nTotal US{' entry-level' if args.entry_level else ''} jobs shown: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
