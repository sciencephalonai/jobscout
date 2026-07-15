"""One-shot cleanup: delete saved jobs that fail the (hardened) US-only check.

Re-runs ``normalize.is_us_job`` (incl. the title-city guard) over every job in
Weaviate — catching rows saved before the fixes for (a) tenant-level
``country="us"`` over-stamping on global Workday boards, (b) "Worldwide"-scoped
remote postings, and (c) foreign cities hiding in titles.

Workday rows whose location is the "N Locations" placeholder are re-probed via
the free CXS detail endpoint: US rows get their real location written back
in place (no re-enrichment, no LLM cost); foreign rows are deleted.

Safety: dry-run is the default. Pass ``--apply`` to write repairs/deletions.

Usage:  .venv/bin/python scripts/purge_non_us.py [--apply]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "backend")

from jobscout.adapters.workday import (  # noqa: E402
    parse_workday_detail,
    parse_workday_job_url,
    workday_location_hint,
)
from jobscout.normalize import is_us_job  # noqa: E402
from jobscout.store import COLLECTION_NAME, WeaviateStore  # noqa: E402

_N_LOCATIONS = re.compile(r"^(?:\d+|multiple)\s+locations?$", re.IGNORECASE)


def _probe_workday_detail(
    url: str, timeout: float = 8.0
) -> tuple[str | None, str | None]:
    """Job URL → (real location string, country descriptor) via the CXS detail API."""
    parsed = parse_workday_job_url(url or "")
    if not parsed:
        return None, None
    cxs = (
        f"https://{parsed['tenant']}.{parsed['region']}.myworkdayjobs.com"
        f"/wday/cxs/{parsed['tenant']}/{parsed['site']}{parsed['external_path']}"
    )
    try:
        req = urllib.request.Request(cxs, headers={
            "Accept": "application/json",
            # Workday WAF 403s the default Python-urllib agent.
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.load(r)
        _, location, country = parse_workday_detail(payload)
        return location, country
    except Exception:  # noqa: BLE001
        # Posting gone (404) or WAF-blocked: fall back to the location slug the
        # job URL itself embeds ("…/job/Israel-Yokneam/Title_JR123" → "Israel
        # Yokneam"). Primary location only, but enough for the US check.
        return workday_location_hint(parsed["external_path"]), None


def _needs_workday_revalidation(properties: dict) -> bool:
    """Whether a legacy Workday row depends on unverified board-level geography."""
    if properties.get("source") != "workday":
        return False
    location = (properties.get("location_raw") or "").strip()
    if _N_LOCATIONS.match(location):
        return True
    # Remove the stored country stamp and ask whether the job's own location is
    # enough to prove US eligibility. If not, re-read the authoritative detail
    # payload before keeping or deleting the row.
    return not is_us_job(
        None,
        location,
        properties.get("remote_mode") or "unknown",
        title=properties.get("title"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Revalidate legacy Workday locations and remove non-US jobs."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write repairs/deletions (without this flag the command is a dry run)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="parallel Workday detail requests (default: 8; maximum: 32)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="seconds allowed for each Workday detail request (default: 8)",
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 32:
        parser.error("--workers must be between 1 and 32")
    if not 1 <= args.timeout <= 60:
        parser.error("--timeout must be between 1 and 60 seconds")
    dry = not args.apply
    store = WeaviateStore()
    try:
        collection = store._client.collections.get(COLLECTION_NAME)

        bad: list[tuple[object, dict]] = []
        fixups: list[tuple[str, dict]] = []  # (job_id, fields) for in-place repairs
        revalidate: list[tuple[object, dict]] = []
        total = unresolved = 0
        for obj in collection.iterator():
            total += 1
            properties = obj.properties
            if _needs_workday_revalidation(properties):
                revalidate.append((obj, properties))
                continue
            if not is_us_job(
                properties.get("country"),
                properties.get("location_raw"),
                properties.get("remote_mode") or "unknown",
                title=properties.get("title"),
            ):
                bad.append((obj.uuid, properties))

        print(
            f"Scanned {total} jobs; revalidating {len(revalidate)} Workday rows "
            f"with {args.workers} workers."
        )
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            pending = {
                executor.submit(
                    _probe_workday_detail,
                    properties.get("url") or "",
                    args.timeout,
                ): (obj, properties)
                for obj, properties in revalidate
            }
            for future in as_completed(pending):
                obj, properties = pending[future]
                location = (properties.get("location_raw") or "").strip()
                country = properties.get("country")
                real_location, real_country = future.result()
                if real_location or real_country:
                    location = real_location or location
                    country = real_country or country
                    if is_us_job(
                        country,
                        location,
                        properties.get("remote_mode") or "unknown",
                        title=properties.get("title"),
                    ):
                        fixups.append((properties["job_id"], {
                            "location_raw": location,
                            **({"country": real_country} if real_country else {}),
                        }))
                    else:
                        bad.append((obj.uuid, {
                            **properties,
                            "location_raw": location,
                            "country": country,
                        }))
                    continue

                # No authoritative or URL-slug evidence: do not delete an
                # ambiguous placeholder merely because the network failed.
                unresolved += 1
                if not location or _N_LOCATIONS.match(location):
                    continue
                if not is_us_job(
                    country,
                    location,
                    properties.get("remote_mode") or "unknown",
                    title=properties.get("title"),
                ):
                    bad.append((obj.uuid, properties))

        print(
            f"Revalidated {len(revalidate)} Workday rows; "
            f"{unresolved} remain unresolved."
        )
        print(f"{len(fixups)} US Workday rows to repair in place:")
        for job_id, fields in fixups[:15]:
            print(f"  ~ {job_id}: {fields['location_raw'][:70]!r}")
        if len(fixups) > 15:
            print(f"  … and {len(fixups) - 15} more")
        print(f"{len(bad)} rows fail the US-only check:")
        for _, properties in bad:
            print(
                f"  - [{properties.get('source')}] {properties.get('title')} "
                f"@ {properties.get('company')} | "
                f"{str(properties.get('location_raw'))[:50]!r} "
                f"country={properties.get('country')!r}"
            )

        if dry:
            print(
                "Dry run — nothing changed. Re-run with --apply after reviewing "
                "this report."
            )
        else:
            for job_id, fields in fixups:
                store.update_fields(job_id, fields)
            for uid, _ in bad:
                collection.data.delete_by_id(uid)
            print(f"Repaired {len(fixups)} rows, deleted {len(bad)} jobs.")
            # Orphan job_sources rows in DuckDB are harmless because they are
            # only joined for jobs that still exist.
    finally:
        store.close()


if __name__ == "__main__":
    main()
