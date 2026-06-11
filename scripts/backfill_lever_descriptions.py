"""Backfill full Lever job descriptions in Weaviate without re-embedding.

The Lever adapter previously only stored `descriptionPlain` (intro paragraph).
This script fetches all configured Lever companies, pulls their current postings,
and patches the `description` field in Weaviate using `update_fields()` — no
embedding calls, so it works even when the Gemini quota is exhausted.

Usage (from repo root):
    python scripts/backfill_lever_descriptions.py
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
import sys

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from jobscout.normalize import compute_job_id  # noqa: E402
from jobscout.services.source_config import _load_sources_cfg  # noqa: E402
from jobscout.store import COLLECTION_NAME, WeaviateStore, _job_uuid  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", _html.unescape(text)).strip()


def _full_description(posting: dict) -> str | None:
    parts: list[str] = []
    intro = (posting.get("descriptionPlain") or "").strip()
    if intro:
        parts.append(intro)
    for section in posting.get("lists") or []:
        header = (section.get("text") or "").strip()
        content = _strip_html(section.get("content") or "")
        if header:
            parts.append(f"\n{header}")
        if content:
            parts.append(content)
    footer = (posting.get("additionalPlain") or "").strip()
    if footer:
        parts.append(f"\n{footer}")
    return "\n".join(parts) or None


def get_lever_companies() -> list[str]:
    """Return all Lever company slugs from sources.yaml config."""
    cfg = _load_sources_cfg()
    lever_cfg = cfg.get("sources", {}).get("lever", {})
    entries = lever_cfg.get("companies", [])
    slugs = []
    for entry in entries:
        if isinstance(entry, str):
            slugs.append(entry)
        elif isinstance(entry, dict):
            token = entry.get("token") or entry.get("slug") or entry.get("name")
            if token:
                slugs.append(token)
    return slugs


def fetch_lever_postings(slug: str) -> dict[str, tuple[str, str | None, str]]:
    """Fetch postings for a Lever company. Returns {source_job_id: (title, location, full_desc)}."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        postings = r.json()
    except Exception as e:
        log.error("Failed to fetch Lever postings for %s: %s", slug, e)
        return {}

    result = {}
    for p in postings:
        job_id = str(p.get("id") or "")
        title = (p.get("text") or "").strip()
        if not job_id or not title:
            continue
        desc = _full_description(p)
        if not desc:
            continue
        categories = p.get("categories") or {}
        location = (categories.get("location") or "").strip() or None
        result[job_id] = (title, location, desc)
    return result


def main() -> None:
    store = WeaviateStore()
    collection = store._client.collections.get(COLLECTION_NAME)

    companies = get_lever_companies()
    if not companies:
        log.error("No Lever companies found in sources config.")
        return
    log.info("Found %d Lever companies: %s", len(companies), companies)

    updated = 0
    skipped = 0

    for slug in companies:
        log.info("Fetching postings for %s ...", slug)
        postings = fetch_lever_postings(slug)
        log.info("  %d postings retrieved", len(postings))

        for source_job_id, (title, location, full_desc) in postings.items():
            # Compute the canonical job_id used as the Weaviate UUID key
            city = location  # location from Lever is city-level
            job_id = compute_job_id(slug, title, city)
            uuid = _job_uuid(job_id)

            # Check existing description length before patching
            try:
                obj = collection.query.fetch_object_by_id(uuid)
            except Exception:
                obj = None

            if obj is None:
                skipped += 1
                continue

            existing_desc = str(obj.properties.get("description") or "")
            if len(full_desc) <= len(existing_desc):
                skipped += 1
                continue

            try:
                collection.data.update(uuid=uuid, properties={"description": full_desc})
                updated += 1
                log.info("  Updated %s '%s' (%d → %d chars)", slug, title[:50], len(existing_desc), len(full_desc))
            except Exception as e:
                log.warning("Update failed for %s/%s: %s", slug, source_job_id, e)
                skipped += 1

    store.close()
    log.info("Done. updated=%d skipped=%d", updated, skipped)


if __name__ == "__main__":
    main()
