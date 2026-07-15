"""Backfill the `new_grad_program` property on all existing Weaviate Job objects.

Run from the repo root (inside the project venv):
    .venv/bin/python scripts/backfill_new_grad.py

Pages through the entire Job collection, detects whether each posting is an
explicit new-grad / early-career / rotational program from its title (+ start of
description), and updates only the `new_grad_program` property — vectors and all
other fields are untouched. Idempotent: safe to re-run.

Note: the property is added to the live collection automatically by
WeaviateStore._migrate_collection on backend startup; this script only stamps the
values, so restart the backend (or instantiate WeaviateStore) at least once first.
"""

from __future__ import annotations

import os
import sys

# Ensure the package is importable when run from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from jobscout.normalize import detect_new_grad_program
from jobscout.store import COLLECTION_NAME, WeaviateStore

PAGE_SIZE = 500


def main() -> None:
    store = WeaviateStore()  # __init__ runs _migrate_collection → ensures the property exists
    collection = store._client.collections.get(COLLECTION_NAME)

    updated = 0
    flagged = 0
    errors = 0
    cursor = None

    print("Starting new_grad_program backfill …")

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
            is_new_grad = detect_new_grad_program(title, description)
            try:
                collection.data.update(
                    uuid=obj.uuid,
                    properties={"new_grad_program": is_new_grad},
                )
                updated += 1
                flagged += int(is_new_grad)
            except Exception as exc:
                print(f"  ERROR updating {job_id!r}: {exc}")
                errors += 1

        cursor = objects[-1].uuid
        print(f"  processed {updated + errors} objects so far …")

    store.close()
    print(f"Done. Updated: {updated}  Flagged new-grad: {flagged}  Errors: {errors}")


if __name__ == "__main__":
    main()
