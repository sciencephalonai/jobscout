"""
Generic RSS / Atom job-feed adapter.

Ingests any job RSS feed — most usefully HigherEdJobs category feeds
(``higheredjobs.com/rss/categoryFeed.cfm?catID={id}``; e.g. 290 Data Science,
102 Computer Science, 159 Software Engineer) and per-institution university
career feeds. Each feed is curated with an ``employer_type`` so the cap-exempt
employer class is stamped directly.

The feed is fetched through ``CompliantHttpClient`` (robots.txt + rate limiting
apply) and parsed with ``feedparser``. Keyword and ``since`` filtering happen
client-side.
"""

from __future__ import annotations

import calendar
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import feedparser

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError, keyword_title_match

log = logging.getLogger(__name__)


def _normalize_feeds(feeds: list[Any] | None) -> list[tuple[str, str]]:
    """Normalise config feeds → ``[(url, employer_type), ...]``."""
    out: list[tuple[str, str]] = []
    for f in feeds or []:
        if isinstance(f, dict):
            url = f.get("url")
            if url:
                out.append((str(url), str(f.get("type") or "unclear")))
        elif f:
            out.append((str(f), "unclear"))
    return out


def _entry_datetime(entry: Any) -> datetime | None:
    """Return an aware UTC datetime from a feedparser entry's parsed date, or None."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


class RssAdapter:
    """Wraps generic RSS/Atom job feeds."""

    name = "rss"
    method = "rss"
    risk = "low"
    store_full_description = True

    def __init__(self, feeds: list[Any] | None = None) -> None:
        self.feeds: list[tuple[str, str]] = _normalize_feeds(feeds)

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts from each configured RSS feed (client-side filtered)."""
        if not self.feeds:
            log.warning("RssAdapter has no feeds configured — skipping")
            return
        if results_wanted <= 0:
            return

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0

        for feed_url, employer_type in self.feeds:
            if total_yielded >= results_wanted:
                break

            try:
                resp = http.get(feed_url)
            except DomainBlockedError as exc:
                log.warning("RSS feed blocked (%s) — skipping %s", exc, feed_url)
                continue
            except Exception as exc:  # noqa: BLE001
                log.error("HTTP error fetching RSS feed %s: %s", feed_url, exc)
                continue

            if resp.status_code != 200:
                log.error("RSS feed %s returned HTTP %s — skipping", feed_url, resp.status_code)
                continue

            parsed = feedparser.parse(resp.text)
            feed_title = (parsed.feed.get("title") if parsed.feed else None) or None

            for entry in parsed.entries:
                if total_yielded >= results_wanted:
                    break
                try:
                    title = str(entry.get("title") or "").strip()
                    link = str(entry.get("link") or "").strip()
                    if not title or not link:
                        continue
                    if not keyword_title_match(title, keywords):
                        continue

                    entry_dt = _entry_datetime(entry)
                    if since_aware is not None and (entry_dt is None or entry_dt < since_aware):
                        continue

                    description = entry.get("summary") or entry.get("description") or None
                    company = (entry.get("author") or feed_title or "").strip() or None

                    yield {
                        "title": title,
                        "company": company,
                        "url": link,
                        "description": description,
                        "location": None,
                        "posted_date": entry_dt or entry.get("published"),
                        "source_job_id": str(entry.get("id")) if entry.get("id") else None,
                        "employer_type": employer_type,
                    }
                    total_yielded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to process RSS entry from %s: %s", feed_url, exc)
                    continue
