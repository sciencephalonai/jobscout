#!/usr/bin/env python3
"""Restore the Weaviate Job index from an export made by export_weaviate.py.

Writes the stored vectors back as-is → **no embedding calls, $0**. Refuses to
import into an index whose vectors are a different dimension (different embedding
model) so a restore can never corrupt/mix an index. Run from the repo root.

    python scripts/import_weaviate.py
    python scripts/import_weaviate.py --in data/my_backup.jsonl.gz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from jobscout.backup import DEFAULT_EXPORT_PATH, import_index, read_header  # noqa: E402
from jobscout.store import WeaviateStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Restore the Weaviate index from a backup.")
    ap.add_argument("--in", dest="infile", default=str(REPO_ROOT / DEFAULT_EXPORT_PATH),
                    help=f"input path (default: {DEFAULT_EXPORT_PATH})")
    args = ap.parse_args()

    path = Path(args.infile)
    if not path.exists():
        sys.exit(f"backup file not found: {path}")
    header = read_header(path)
    print(f"Restoring {header['count']} jobs ({header['embed_model']}, {header['dim']}-dim) "
          f"from {path} — no embedding calls.")

    store = WeaviateStore()
    try:
        n = import_index(store, path)
    except ValueError as exc:
        sys.exit(str(exc))
    finally:
        store.close()
    print(f"Imported {n} jobs ($0 — vectors restored as-is).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
