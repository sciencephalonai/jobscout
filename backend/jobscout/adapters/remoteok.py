"""
RemoteOK public job board API adapter.

Docs / terms: https://remoteok.com/api
Endpoint: https://remoteok.com/api

The RemoteOK API is public and requires no key. It returns only remote jobs,
so every yielded record is marked ``remote``.

ATTRIBUTION NOTE: RemoteOK's API Terms of Service require that consumers link
back to the canonical job URL on Remote OK (a follow link, not nofollow) and
mention Remote OK as the source — otherwise they suspend API access. We store
each job's ``url`` (which points at the RemoteOK listing), satisfying the
backlink requirement; the ``source`` column records "remoteok" as attribution.

The response is a JSON LIST. The FIRST element is a legal/metadata notice (it
carries a ``legal`` key and has no ``id``/``position``) and is skipped. There
is no server-side search parameter, so keyword and date filtering happens
client-side.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from jobscout.adapters.base import CompliantHttpClient, DomainBlockedError

log = logging.getLogger(__name__)

_BASE_URL = "https://remoteok.com/api"


def _parse_date(value: str | None) -> datetime | None:
    """Parse RemoteOK's ISO ``date`` field (e.g. ``2026-06-02T17:41:46+00:00``).

    Returns a timezone-aware UTC datetime, or ``None`` if it cannot be parsed.
    Used only for the *since* comparison; the raw string is what we yield so
    the ingestion layer can re-parse it.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class RemoteOKAdapter:
    """Wraps the RemoteOK ``/api`` endpoint.

    Attributes
    ----------
    name:
        Adapter identifier used as the ``source`` column in the DB.
    method:
        ``"api"`` — fetches from the official public RemoteOK REST API.
    risk:
        ``"low"`` — uses an official, sanctioned public API endpoint.
    store_full_description:
        ``True`` — full (HTML) description text is available and stored.
    """

    name = "remoteok"
    method = "api"
    risk = "low"
    store_full_description = True

    def __init__(self) -> None:
        pass

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
        """Yield raw job dicts from the RemoteOK API.

        Parameters
        ----------
        keywords:
            Search terms joined with a space and matched case-insensitively
            against each job's title (``position``). If empty, all jobs match.
        location:
            Ignored for filtering — RemoteOK only returns remote jobs and has
            no geographic search parameter. Each job's ``location`` is
            surfaced as ``location``.
        results_wanted:
            Upper bound on the total number of results to yield.
        since:
            If given, jobs whose ``date`` is older than this datetime are
            skipped.
        http:
            :class:`~jobscout.adapters.base.CompliantHttpClient` instance used
            for all HTTP requests.
        """
        if results_wanted <= 0:
            return

        search_term = " ".join(k.strip() for k in keywords if k.strip()).lower()

        try:
            resp = http.get(_BASE_URL, api_source=self.method == "api")
        except DomainBlockedError as exc:
            log.warning("RemoteOK domain blocked (%s) — skipping", exc)
            return
        except Exception as exc:  # noqa: BLE001
            log.error("HTTP error fetching RemoteOK jobs: %s", exc)
            return

        if resp.status_code != 200:
            log.error("RemoteOK returned HTTP %s — skipping", resp.status_code)
            return

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to decode RemoteOK JSON: %s", exc)
            return

        if not isinstance(data, list):
            log.error("RemoteOK returned unexpected payload type: %s", type(data).__name__)
            return

        since_aware: datetime | None = None
        if since is not None:
            since_aware = since if since.tzinfo is not None else since.replace(tzinfo=UTC)

        total_yielded = 0
        for item in data:
            if total_yielded >= results_wanted:
                break

            if not isinstance(item, dict):
                continue

            # Skip the legal/metadata notice (first element) and any element
            # that lacks the mandatory job fields.
            if "id" not in item or "position" not in item:
                continue

            try:
                position = (item.get("position") or "").strip()
                if not position:
                    continue

                # Client-side keyword filter against the title.
                if search_term and search_term not in position.lower():
                    continue

                # Client-side date filter.
                if since_aware is not None:
                    posted = _parse_date(item.get("date"))
                    if posted is not None and posted < since_aware:
                        continue

                url = (item.get("url") or "").strip() or None
                company = (item.get("company") or "").strip() or None
                job_location = (item.get("location") or "").strip() or None
                description = item.get("description")
                description = html.unescape(description) if description else None
                job_id = item.get("id")
                source_job_id = str(job_id) if job_id is not None else None

                yield {
                    "title": position,
                    "company": company,
                    "url": url,
                    "description": description,
                    "remote": "remote",  # every RemoteOK job is remote
                    "location": job_location,
                    "posted_date": item.get("date") or None,
                    "source_job_id": source_job_id,
                }
                total_yielded += 1

            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Failed to process RemoteOK job (id=%s): %s", item.get("id"), exc
                )
                continue

        log.debug(
            "RemoteOK search=%r: yielded %d jobs (of %d returned)",
            search_term,
            total_yielded,
            len(data),
        )
