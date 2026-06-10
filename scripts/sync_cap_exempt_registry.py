#!/usr/bin/env python3
"""Sync curated cap-exempt employers (sources.yaml + sources.discovered.yaml)
into the DuckDB company registry, so the Companies tab shows them and
"Get companies" refreshes them.

This also runs automatically at backend startup; use this for a manual sync
(e.g. right after adding Workday tenants with probe_workday.py, without a
restart). Requires the backend to NOT hold the DuckDB lock — stop it first.

    python scripts/sync_cap_exempt_registry.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from jobscout.relational import RelationalStore  # noqa: E402
from jobscout.services.registry import register_cap_exempt_companies  # noqa: E402
from jobscout.services.source_config import _load_sources_cfg  # noqa: E402


def main() -> int:
    store = RelationalStore()
    try:
        n = register_cap_exempt_companies(store, _load_sources_cfg())
        print(f"Synced {n} cap-exempt employer(s) into the company registry.")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
