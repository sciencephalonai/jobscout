"""Backfill the `employment_type` property on all existing Weaviate Job objects.

Run from the repo root (inside the project venv):
    .venv/bin/python scripts/backfill_employment_type.py

The script pages through the entire Job collection (500 objects at a time),
derives the employment type from each title (+ description when available), and
updates only the `employment_type` property — vectors and all other fields are
left untouched. Idempotent: safe to re-run.
"""

from __future__ import annotations

import os
import sys

# Ensure the package is importable when run from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from jobscout.normalize import derive_employment_type
from jobscout.store import COLLECTION_NAME, WeaviateStore

PAGE_SIZE = 500


def main() -> None:
    store = WeaviateStore()
    collection = store._client.collections.get(COLLECTION_NAME)

    updated = 0
    errors = 0
    cursor = None

    print("Starting employment_type backfill …")

    while True:
        kwargs: dict = dict(
            limit=PAGE_SIZE,
            return_properties=["job_id", "title", "description"],
        )
        if cursor is not None:
            kwargs["after"] = cursor

        result = collection.query.fetch_objects(**kwargs)
        objects = result.objects
        if not objects:
            break

        for obj in objects:
            title = str(obj.properties.get("title") or "")
            description = obj.properties.get("description") or None
            job_id = str(obj.properties.get("job_id") or "")
            emp = derive_employment_type(title, description)
            try:
                collection.data.update(
                    uuid=obj.uuid,
                    properties={"employment_type": emp},
                )
                updated += 1
            except Exception as exc:
                print(f"  ERROR updating {job_id!r}: {exc}")
                errors += 1

        cursor = objects[-1].uuid
        print(f"  processed {updated + errors} objects so far …")

    store.close()
    print(f"Done. Updated: {updated}  Errors: {errors}")


if __name__ == "__main__":
    main()
