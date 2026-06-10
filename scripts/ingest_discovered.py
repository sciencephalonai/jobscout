#!/usr/bin/env python3
"""Bounded, cost-controlled enriched ingest of the discovered ATS companies.

Runs the REAL JobScout pipeline (normalize → US filter → DeepSeek enrichment →
Gemini embedding → Weaviate Cloud upsert) over the curated + auto-discovered
Greenhouse / Lever / Ashby boards, with a HARD global cap on the number of jobs
so DeepSeek spend stays bounded. Writes to the same Weaviate the API reads, so
the results are immediately queryable in the UI (`GET /api/jobs?exp=entry`).

    python scripts/ingest_discovered.py --max 300
    python scripts/ingest_discovered.py --max 30          # quick smoke
    python scripts/ingest_discovered.py --keywords engineer --per-ats 150

Each ingested job = one DeepSeek call + one embedding call. `--max` is the hard
ceiling across all sources. Run from the repo root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from jobscout.adapters import AshbyAdapter, GreenhouseAdapter, LeverAdapter  # noqa: E402
from jobscout.adapters.base import CompliantHttpClient  # noqa: E402
from jobscout.api.main import _load_sources_cfg  # noqa: E402
from jobscout.config import settings  # noqa: E402
from jobscout.embed import embed_job  # noqa: E402
from jobscout.enrich import (  # noqa: E402
    EnrichmentError,
    derive_cap_exempt,
    detect_recruiter_post,
    extract_enrichment,
)
from jobscout.models import JobSource  # noqa: E402
from jobscout.normalize import is_us_job, raw_to_job  # noqa: E402
from jobscout.relational import RelationalStore  # noqa: E402
from jobscout.sponsors import is_everify_employer, is_known_h1b_sponsor  # noqa: E402
from jobscout.store import WeaviateStore  # noqa: E402

# The three curated-ATS adapters this script ingests (per-company boards).
_ADAPTER_FOR = {
    "greenhouse": GreenhouseAdapter,
    "lever": LeverAdapter,
    "ashby": AshbyAdapter,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Bounded enriched ingest of discovered ATS companies.")
    ap.add_argument("--max", type=int, default=300,
                    help="HARD global cap on jobs ingested (= DeepSeek calls). Default 300.")
    ap.add_argument("--per-ats", type=int, default=120,
                    help="per-ATS cap passed to each adapter's search (default 120)")
    ap.add_argument("--keywords",
                    default="engineer,developer,analyst,data,scientist,associate,"
                            "new grad,intern,junior,ml,ai,research",
                    help="comma-separated title keywords")
    args = ap.parse_args()

    if not settings.deepseek_api_key:
        sys.exit("DEEPSEEK_API_KEY not set — enrichment would be skipped. Aborting.")

    # NOTE: the per-company adapters keyword-filter on the title as a single
    # JOINED phrase, so passing many keywords matches nothing. We instead pull
    # boards unfiltered and do our own ANY-keyword title match here — and skip
    # obviously-senior titles so we don't spend DeepSeek on 5+ yr roles.
    keywords = [k.strip().lower() for k in args.keywords.split(",") if k.strip()]
    cfg = _load_sources_cfg().get("sources", {})

    # Build the three curated-ATS adapters from the merged (curated + discovered) config.
    adapters = []
    for name, cls in _ADAPTER_FOR.items():
        companies = cfg.get(name, {}).get("companies", [])
        if companies:
            adapters.append((name, cls(companies=companies)))
            print(f"  {name}: {len(companies)} boards configured")

    print(f"\nIngesting up to {args.max} jobs (~{args.max} DeepSeek calls), "
          f"keywords={keywords}\n")

    import re
    senior = re.compile(
        r"\b(founding|senior|sr|staff|principal|lead|director|head|vp|chief|"
        r"manager|architect|ii|iii|iv)\b", re.IGNORECASE)

    http = CompliantHttpClient()
    store = WeaviateStore()
    rel = RelationalStore()
    total = enriched = failed = dropped = skipped = 0
    try:
        for name, adapter in adapters:
            if total >= args.max:
                break
            # Pull boards UNFILTERED (adapter phrase-match would match nothing for
            # multi-keyword); filter titles ourselves below.
            for raw in adapter.search(
                keywords=[], location=None, results_wanted=args.per_ats,
                since=None, http=http,
            ):
                if total >= args.max:
                    break
                title = str(raw.get("title") or "")
                tl = title.lower()
                # ANY-keyword match + skip senior titles (don't waste DeepSeek).
                if (keywords and not any(k in tl for k in keywords)) or senior.search(title):
                    skipped += 1
                    continue
                job = raw_to_job(raw, source=name)
                if not is_us_job(job.country, job.location_raw, job.remote_mode):
                    dropped += 1
                    continue
                try:
                    enr = extract_enrichment(job.title, job.company, job.description)
                    job.yoe_min = enr.get("yoe_min")
                    job.yoe_max = enr.get("yoe_max")
                    job.visa_sponsorship = enr.get("visa_sponsorship", "not_mentioned")
                    job.skills = enr.get("skills", [])
                    job.seniority = enr.get("seniority", "unclear")
                    job.security_clearance = enr.get("security_clearance", "unclear")
                    job.citizenship_required = enr.get("citizenship_required", False)
                    if job.employer_type == "unclear":
                        job.employer_type = enr.get("employer_type", "unclear")
                    job.enrichment_status = "done"
                    enriched += 1
                except EnrichmentError:
                    job.enrichment_status = "failed"
                    failed += 1
                job.cap_exempt = derive_cap_exempt(job.employer_type)
                job.known_h1b_sponsor = is_known_h1b_sponsor(job.company)
                job.known_everify = is_everify_employer(job.company)
                job.is_recruiter_post = detect_recruiter_post(
                    job.company, job.source, job.description
                )
                vec = embed_job(
                    title=job.title, company=job.company,
                    skills=job.skills, description=job.description,
                )
                store.upsert(job, vec)
                rel.upsert_job_source(JobSource(
                    job_id=job.job_id, source=job.source, url=job.url,
                    posted_date=job.posted_date,
                ))
                total += 1
                if total % 20 == 0:
                    print(f"  ... {total} ingested ({enriched} enriched, {failed} failed)")
    finally:
        http.close()
        store.close()
        rel.close()

    print(f"\nDone. Ingested {total} jobs "
          f"(enriched={enriched}, failed={failed}, non-US dropped={dropped}, "
          f"off-target skipped={skipped}).")
    print("Query them: GET /api/jobs?exp=entry  (or the UI).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
