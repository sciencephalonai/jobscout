#!/usr/bin/env python3
"""One-time maintenance: purge stale blank-company Workday jobs.

Background: ``job_id = sha256(company | title | city)`` includes the company, so
when the Workday adapter started stamping the tenant's display name (e.g.
"Cornell University") onto previously-nameless jobs, the re-ingested rows got a
NEW id and the old ``company=""`` rows orphaned. Every configured Workday tenant
now carries a ``name``, so any ``source == "workday"`` job with a blank company is
a pre-fix artifact that will never regenerate — safe to delete. The named version
is (re)created by normal ingestion.

    python scripts/purge_workday_unnamed.py          # DRY RUN — just counts
    python scripts/purge_workday_unnamed.py --yes     # actually delete

Scoped + idempotent. Re-running after a clean ingest deletes nothing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from jobscout.store import COLLECTION_NAME, WeaviateStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Purge blank-company Workday jobs.")
    ap.add_argument("--yes", action="store_true", help="perform the delete (default: dry run)")
    args = ap.parse_args()

    from weaviate.classes.query import Filter

    store = WeaviateStore()
    try:
        coll = store._client.collections.get(COLLECTION_NAME)
        # Note: blank companies are stored as "" but Weaviate doesn't index empty
        # strings, so is_none(True) matches them (and an equal("") filter errors as
        # "only stopwords provided").
        where = (
            Filter.by_property("source").equal("workday")
            & Filter.by_property("company").is_none(True)
        )
        if not args.yes:
            # dry_run counts without deleting
            res = coll.data.delete_many(where=where, dry_run=True, verbose=True)
            n = getattr(res, "matches", None)
            print(f"DRY RUN — would delete {n} blank-company Workday job(s). "
                  f"Re-run with --yes to delete.")
        else:
            res = coll.data.delete_many(where=where)
            print(f"Deleted {res.successful} blank-company Workday job(s) "
                  f"({res.failed} failed).")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
