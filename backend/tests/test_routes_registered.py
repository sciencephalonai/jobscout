"""Regression safety net: every API route must stay mounted across refactors.

This captures the exact (method, path) set the app exposed before the
service-layer / router-split refactor. If a route disappears or changes path,
this fails — so the refactor can't silently break an endpoint. Dependency-free
(no Weaviate/DuckDB needed; only imports the app object).
"""

from __future__ import annotations

from jobscout.api.main import app

# Baseline captured pre-refactor. Do NOT trim — only ADD when a new endpoint ships.
EXPECTED_ROUTES: set[tuple[str, str]] = {
    ("DELETE", "/api/profiles/{profile_id}"),
    ("GET", "/api/companies"),
    ("GET", "/api/jobs"),
    ("GET", "/api/jobs/by-state"),
    ("GET", "/api/jobs/{job_id}"),
    ("GET", "/api/profiles"),
    ("GET", "/api/profiles/{profile_id}"),
    ("GET", "/api/scheduler"),
    ("GET", "/api/sources/overrides"),
    ("GET", "/api/sources/status"),
    ("GET", "/api/stats"),
    ("POST", "/api/companies"),
    ("POST", "/api/companies/refresh"),
    ("POST", "/api/enrich/run"),
    ("POST", "/api/maintenance/purge"),
    ("POST", "/api/match"),
    ("POST", "/api/match/upload"),
    ("POST", "/api/profiles"),
    ("POST", "/api/profiles/{profile_id}/job-state"),
    ("POST", "/api/scheduler"),
    ("POST", "/api/search/run"),
    ("POST", "/api/sources/overrides"),
    ("GET", "/api/profiles/{profile_id}/pipeline"),
    ("GET", "/api/saved-searches"),
    ("POST", "/api/saved-searches"),
    ("POST", "/api/saved-searches/{search_id}/seen"),
    ("DELETE", "/api/saved-searches/{search_id}"),
}


def _actual_routes() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for r in app.routes:
        methods = getattr(r, "methods", None)
        path = getattr(r, "path", "")
        if not methods or not path.startswith("/api"):
            continue
        for m in methods:
            if m not in ("HEAD", "OPTIONS"):
                out.add((m, path))
    return out


def test_all_baseline_routes_still_mounted():
    actual = _actual_routes()
    missing = EXPECTED_ROUTES - actual
    assert not missing, f"Routes lost in refactor: {sorted(missing)}"


def test_route_ordering_by_state_before_path_param():
    # /api/jobs/by-state must be declared BEFORE /api/jobs/{job_id} or the literal
    # path is shadowed by the path param. Assert relative declaration order.
    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/api/jobs/by-state" in paths and "/api/jobs/{job_id}" in paths
    assert paths.index("/api/jobs/by-state") < paths.index("/api/jobs/{job_id}")
