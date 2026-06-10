"""Backfill the `category` property on all existing Weaviate Job objects.

Run from the repo root after activating the conda environment:
    python scripts/backfill_categories.py

The script pages through the entire Job collection (500 objects at a time),
derives the category from each title, and batch-updates only the `category`
property — vectors and all other fields are left untouched.
"""

from __future__ import annotations

import sys
import os

# Ensure the package is importable when run from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from jobscout.normalize import derive_category
from jobscout.store import COLLECTION_NAME, WeaviateStore, _job_uuid

PAGE_SIZE = 500


def main() -> None:
    store = WeaviateStore()
    collection = store._client.collections.get(COLLECTION_NAME)

    updated = 0
    errors = 0
    cursor = None

    print("Starting category backfill …")

    while True:
        kwargs: dict = dict(limit=PAGE_SIZE, return_properties=["job_id", "title"])
        if cursor is not None:
            kwargs["after"] = cursor

        result = collection.query.fetch_objects(**kwargs)
        objects = result.objects
        if not objects:
            break

        with collection.batch.dynamic() as batch:
            for obj in objects:
                title = str(obj.properties.get("title") or "")
                job_id = str(obj.properties.get("job_id") or "")
                cat = derive_category(title)
                try:
                    collection.data.update(
                        uuid=obj.uuid,
                        properties={"category": cat},
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
