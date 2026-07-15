"""Seed V's job-search profile + two saved-search presets via the running API.

V is on F-1 -> STEM OPT -> H-1B and needs visa sponsorship, US-only, targeting
entry-level Data Scientist / ML / Data / AI-SWE roles. This creates:

  * one UserProfile with sensible defaults (drives verdicts + clearance/citizenship
    exclusion via profile_id), and
  * two one-click saved searches:
      1. "Cap-exempt / research (safest sponsorship)" -- cap-exempt
         university/hospital/nonprofit/gov only; inherently sponsorship-favorable.
      2. "Broad -- sponsorship-friendly (rubric-compliant)" -- surfaces every role
         that isn't an explicit no-sponsorship / citizenship-only reject, per the
         "surface sponsorship-unconfirmed roles, flag them, never reject" rule.

E-Verify / cap-exempt / known-sponsor are RANKING signals (via sort="match" +
prefer_cap_exempt), NOT hard gates -- a hard E-Verify gate would hide every role
not on the curated (non-exhaustive) E-Verify list, which contradicts the rule to
surface sponsorship-unconfirmed roles. US-only is already enforced app-wide at
ingest, so no country filter is needed here.

Run against a running backend (default http://localhost:8001):

    python scripts/seed_presets.py

Idempotent: the profile upserts on id, and existing saved searches with these
labels are deleted before re-inserting, so re-running never duplicates.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("JOBSCOUT_API", "http://localhost:8001")

PROFILE = {
    "label": "V — DS/ML (F-1, needs sponsorship)",
    "target_titles": [
        "data scientist",
        "machine learning engineer",
        "ml engineer",
        "applied scientist",
        "data engineer",
        "ai engineer",
        "analytics engineer",
        "research engineer",
        "software engineer",
    ],
    "seniority_max": "junior",
    "yoe_max": 2,
    "needs_sponsorship": True,
    "reject_clearance": True,
    "reject_citizenship_only": True,
    "prefer_cap_exempt": True,
    # Deep-match steering (was hardcoded in the prompt; now profile-driven).
    "avoid_role_types": [
        "pure BI / reporting / dashboards / Excel / Tableau / Power BI work with no production ML, engineering, or modeling",
        "business strategy / operations / consulting work",
        "a product or business analyst role without genuine technical modeling or experimentation",
    ],
    "avoid_domains": [
        "trade lifecycle", "middle-office finance", "transaction management",
        "real estate operations", "healthcare billing systems",
        "Shopify", "Amazon Seller Central", "Recharge",
    ],
    "remote_preference": "any",
    "countries": ["us"],
    "skills": [
        "python", "sql", "pandas", "numpy", "scikit-learn", "pytorch",
        "tensorflow", "nlp", "llm", "rag", "spark", "aws", "docker", "git",
    ],
}

# Shared quality/level gates for both presets. E-Verify is deliberately NOT here —
# it's a ranking signal (sort="match" + prefer_cap_exempt), not a hard gate.
_COMMON = {
    "exclude_no_sponsorship": True,      # hide explicit "no sponsorship"
    "exclude_citizenship_required": True,  # hide citizenship / GC / ITAR-only
    "true_entry_only": True,
    "exclude_recruiter": True,
    "exclude_ghost": True,
    "date_range": "14d",
    "sort": "match",                     # profile_id present -> match/cap-exempt-aware ordering
}

PRESET_1 = {
    "label": "Cap-exempt / research (safest sponsorship)",
    "filters": {
        **_COMMON,
        "cap_exempt": ["yes", "likely"],
        "employer_type": ["university", "hospital", "nonprofit", "government"],
    },
}

PRESET_2 = {
    # Rubric-compliant broad lane: surfaces sponsorship-unconfirmed roles (verdict
    # flags them "verify"); cap-exempt / known-sponsor float to the top via ranking.
    "label": "Broad — sponsorship-friendly (rubric-compliant)",
    "filters": {**_COMMON},
}


def _req(method: str, path: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:  # surface the server's error body
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:400]}") from e


def _count(filters: dict, profile_id: str) -> int:
    from urllib.parse import urlencode
    params: list[tuple[str, str]] = [("profile_id", profile_id), ("page_size", "1")]
    for key, val in filters.items():
        if isinstance(val, bool):
            params.append((key, "true" if val else "false"))
        elif isinstance(val, list):
            params.extend((key, str(v)) for v in val)
        else:
            params.append((key, str(val)))
    res = _req("GET", f"/api/jobs?{urlencode(params)}")
    return int(res.get("total", 0)) if isinstance(res, dict) else 0


# Labels to clear before seeding (current + superseded) so re-runs never duplicate.
_STALE_LABELS = {
    PRESET_1["label"],
    PRESET_2["label"],
    "Industry + proven H-1B sponsor",  # superseded by the broad rubric-compliant lane
}


def _clear_stale() -> None:
    existing = _req("GET", "/api/saved-searches")
    if not isinstance(existing, list):
        return
    for s in existing:
        if s.get("label") in _STALE_LABELS:
            _req("DELETE", f"/api/saved-searches/{s['id']}")
            print(f"· removed stale saved search: {s['label']}")


def _existing_profile_id(label: str) -> str | None:
    profiles = _req("GET", "/api/profiles")
    if isinstance(profiles, list):
        for p in profiles:
            if p.get("label") == label:
                return p.get("id")
    return None


def main() -> None:
    _clear_stale()

    # Reuse the profile id if it already exists so re-runs upsert in place
    # (POST /api/profiles is idempotent on id) rather than piling up duplicates.
    body = dict(PROFILE)
    existing_id = _existing_profile_id(PROFILE["label"])
    if existing_id:
        body["id"] = existing_id
    profile = _req("POST", "/api/profiles", body)
    pid = profile["id"]
    print(f"✓ profile: {profile['label']}  (id={pid})")

    for preset in (PRESET_1, PRESET_2):
        saved = _req("POST", "/api/saved-searches", {
            "label": preset["label"],
            "filters": preset["filters"],
            "profile_id": pid,
        })
        n = _count(preset["filters"], pid)
        print(f"✓ saved search: {preset['label']}  (id={saved['id']})  → {n} matching jobs")

    print("\nDone. In the UI: pick the profile in the top-right selector, then open the "
          "Saved tab and click a preset.")


if __name__ == "__main__":
    main()
