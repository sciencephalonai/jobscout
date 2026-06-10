"""
Greenhouse public job-board API adapter.

Docs: https://developers.greenhouse.io/job-board.html

Greenhouse exposes an unauthenticated, per-company job board API. Each company
publishes its openings under a *board token* (the slug used in its careers URL,
e.g. ``stripe``, ``airbnb``, ``databricks``). No API key is required.

There is no server-side keyword or location search, so the adapter fetches the
full board for each configured company and filters client-side.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError, keyword_title_match

log = logging.getLogger(__name__)

_BASE_URL = "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true"


def normalize_company_entries(companies: list[Any] | None) -> list[tuple[str, str]]:
    """Normalise a config ``companies`` list to ``[(token, employer_type), ...]``.

    Each entry may be a plain token string or a ``{token, type, size}`` dict. The
    ``type`` (employer_type, e.g. ``"university"``/``"nonprofit"``) lets a curated
    board stamp the cap-exempt employer class; it defaults to ``"unclear"``.
    """
    out: list[tuple[str, str]] = []
    for c in companies or []:
        if isinstance(c, dict):
            token = c.get("token") or c.get("name")
            employer_type = c.get("type") or "unclear"
            if token:
                out.append((str(token), str(employer_type)))
        elif c:
            out.append((str(c), "unclear"))
    return out


def _parse_updated_at(value: str | None) -> datetime | None:
    """Parse Greenhouse's ``updated_at`` ISO-8601 timestamp into an aware UTC
    datetime.  Returns ``None`` if the value is falsy or unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class GreenhouseAdapter:
    """Wraps the Greenhouse public job-board API.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official Greenhouse public API.
    risk:
        ``"low"`` — uses an official, unauthenticated public API endpoint.
    store_full_description:
        ``True`` — full description text is available and stored.
    companies:
        List of Greenhouse board entries — each a token string or a
        ``{token, type}`` dict. Stored as ``(token, employer_type)`` pairs.
        Defaults to an empty list.
    """

    name = "greenhouse"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self, companies: list[Any] | None = None) -> None:
        self.companies: list[tuple[str, str]] = normalize_company_entries(companies)

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts from the Greenhouse API.

        Iterates over all configured company board tokens, fetching each board's
        full job list and filtering client-side until *results_wanted* jobs have
        been yielded in total.

        Parameters
        ----------
        keywords:
            Search terms.  A job is kept only if the (space-joined,
            case-insensitive) keyword string appears in the job title.  If
            *keywords* is empty, all jobs are kept.
        location:
            Unused — Greenhouse has no server-side location filter and the
            ingestion layer performs location handling downstream.
        results_wanted:
            Upper bound on the total number of results to yield.
        since:
            If given, only jobs whose ``updated_at`` is on or after this
            datetime are yielded.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if not self.companies:
            log.warning("GreenhouseAdapter has no companies configured — skipping")
            return

        if results_wanted <= 0:
            return

        # Greenhouse has no server-side search; filter titles client-side by ANY
        # keyword (keyword_title_match). Empty keywords → keep everything.
        total_yielded = 0

        for board_token, employer_type in self.companies:
            if total_yielded >= results_wanted:
                break

            url = _BASE_URL.format(board_token=board_token)

            try:
                resp = http.get(url, api_source=self.method == "api")
            except DomainBlockedError as exc:
                log.warning(
                    "Greenhouse domain blocked (%s) — skipping company %s", exc, board_token
                )
                continue
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "HTTP error fetching Greenhouse board for %s: %s", board_token, exc
                )
                continue

            if resp.status_code != 200:
                log.error(
                    "Greenhouse returned HTTP %s for board_token=%s — skipping",
                    resp.status_code,
                    board_token,
                )
                continue

            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "Failed to decode Greenhouse JSON for board_token=%s: %s",
                    board_token,
                    exc,
                )
                continue

            jobs: list[dict] = data.get("jobs") or []
            log.debug(
                "Greenhouse board_token=%s: fetched %d jobs (total yielded so far: %d)",
                board_token,
                len(jobs),
                total_yielded,
            )

            for job in jobs:
                if total_yielded >= results_wanted:
                    break

                try:
                    title = str(job.get("title") or "").strip()
                    if not title:
                        continue

                    # Client-side keyword filter against the title (ANY keyword).
                    if not keyword_title_match(title, keywords):
                        continue

                    # Client-side `since` filter on updated_at.
                    updated_at_raw = job.get("updated_at")
                    if since is not None:
                        updated_at = _parse_updated_at(updated_at_raw)
                        since_aware = (
                            since if since.tzinfo is not None else since.replace(tzinfo=UTC)
                        )
                        if updated_at is None or updated_at < since_aware:
                            continue

                    job_id = job.get("id")
                    url_value = (job.get("absolute_url") or "").strip() or None
                    location_obj = job.get("location") or {}
                    location_name = location_obj.get("name") or None
                    content = job.get("content")
                    description = html.unescape(content) if content else None

                    yield {
                        "title": title,
                        "company": board_token,
                        "url": url_value,
                        "description": description,
                        "location": location_name,
                        "posted_date": updated_at_raw,
                        "source_job_id": str(job_id) if job_id is not None else None,
                        "employer_type": employer_type,
                    }
                    total_yielded += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "Failed to process Greenhouse job (board_token=%s, id=%s): %s",
                        board_token,
                        job.get("id"),
                        exc,
                    )
                    continue
