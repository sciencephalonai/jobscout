#!/usr/bin/env python3
"""Backfill `known_h1b_sponsor` + `known_everify` on existing Weaviate jobs —
WITHOUT re-embedding.

Jobs ingested before these fields existed have them null/false. This walks the
Weaviate Job collection, looks each company up in the curated H-1B filer list
(data/h1b_sponsors.txt) and the E-Verify list (data/everify_employers.txt), and
writes the flags via WeaviateStore.update_fields — which preserves the stored
vector, so it costs ZERO Gemini embedding quota.

    python scripts/restamp_sponsors.py

Run from the repo root.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jobscout.sponsors import is_everify_employer, is_known_h1b_sponsor  # noqa: E402
from jobscout.store import COLLECTION_NAME, WeaviateStore  # noqa: E402


def main() -> int:
    store = WeaviateStore()
    collection = store._client.collections.get(COLLECTION_NAME)
    scanned = stamped = 0
    try:
        for obj in collection.iterator():
            p = dict(obj.properties)
            job_id = p.get("job_id")
            company = p.get("company") or None
            if not job_id:
                continue
            scanned += 1
            h1b = is_known_h1b_sponsor(company)
            ev = is_everify_employer(company)
            fields: dict[str, bool] = {}
            if bool(p.get("known_h1b_sponsor")) != h1b:
                fields["known_h1b_sponsor"] = h1b
            if bool(p.get("known_everify")) != ev:
                fields["known_everify"] = ev
            if fields:
                store.update_fields(str(job_id), fields)
                stamped += 1
                if h1b or ev:
                    flags = ", ".join(k for k, v in (("h1b", h1b), ("everify", ev)) if v)
                    print(f"  + {company} ({flags})")
    finally:
        store.close()

    print(f"\nScanned {scanned} jobs, updated {stamped} (no embeddings used).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
