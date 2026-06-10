#!/usr/bin/env python3
"""Export the Weaviate Job index (jobs + vectors) to a gzipped JSONL backup.

Pure download (``include_vector=True``) → **no embedding calls, no Gemini quota**.
The file (``data/weaviate_export.jsonl.gz``) then rides along in your Dropbox copy,
and `import_weaviate.py` restores it for $0. Run from the repo root.

    python scripts/export_weaviate.py
    python scripts/export_weaviate.py --out data/my_backup.jsonl.gz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from jobscout.backup import DEFAULT_EXPORT_PATH, export_index  # noqa: E402
from jobscout.store import WeaviateStore  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Back up the Weaviate index (jobs + vectors).")
    ap.add_argument("--out", default=str(REPO_ROOT / DEFAULT_EXPORT_PATH),
                    help=f"output path (default: {DEFAULT_EXPORT_PATH})")
    args = ap.parse_args()

    store = WeaviateStore()
    try:
        header = export_index(store, Path(args.out))
    finally:
        store.close()
    print(f"Exported {header['count']} jobs "
          f"({header['embed_model']}, {header['dim']}-dim) -> {args.out}")
    print("  No embedding calls were made — restore with scripts/import_weaviate.py costs $0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
