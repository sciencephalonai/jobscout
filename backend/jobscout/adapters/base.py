"""
Compliant HTTP client and JobSourceAdapter protocol for JobScout adapters.

All network requests made by adapters MUST go through CompliantHttpClient,
which enforces robots.txt compliance, per-domain rate limiting, exponential
backoff on 429/503, and domain blocklist checking.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlparse

import httpx
import protego
import yaml

log = logging.getLogger(__name__)

USER_AGENT = "JobScoutBot/1.0 (+https://example.com/about-jobscout)"
DEFAULT_DELAY = 3.0          # seconds between requests to the same domain
ROBOTS_TTL = 86_400.0        # re-fetch robots.txt after 24 hours
BACKOFF = [2, 8, 32]         # seconds to sleep before each retry attempt


class DomainBlockedError(Exception):
    """Raised when a domain is in the blocklist or disallowed by robots.txt."""


class CompliantHttpClient:
    """
    A thin httpx wrapper that enforces:
    - Domain blocklist (blocklist.yaml)
    - robots.txt compliance (fetched once per domain per 24 h, cached in memory)
    - Per-domain rate limiting (default 3 s, or robots Crawl-delay if present)
    - Exponential backoff on 429 / 503 (2 s → 8 s → 32 s, then raise)
    - Fixed User-Agent, no cookies, no auth headers
    """

    def __init__(self, blocklist_path: str | Path = "blocklist.yaml") -> None:
        self._session = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
            timeout=30.0,
        )
        # domain → (Protego instance, fetch_timestamp)
        self._robots_cache: dict[str, tuple[protego.Protego, float]] = {}
        # domain → last successful request timestamp
        self._last_request: dict[str, float] = {}
        self._blocked_domains: set[str] = set()
        self._load_blocklist(blocklist_path)

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------

    def _load_blocklist(self, path: str | Path) -> None:
        """Load blocked_domains list from a YAML file.

        If the file is missing or malformed we log a warning and continue with
        an empty blocklist — the client is still usable.
        """
        p = Path(path)
        if not p.is_absolute():
            # Try relative to the project root (parent of backend/)
            candidates = [
                Path(__file__).resolve().parents[4] / path,  # repo root
                Path(__file__).resolve().parents[3] / path,  # backend/
                Path.cwd() / path,
            ]
            for c in candidates:
                if c.exists():
                    p = c
                    break

        if not p.exists():
            log.warning("blocklist.yaml not found at %s — proceeding with empty blocklist", path)
            return

        try:
            data = yaml.safe_load(p.read_text()) or {}
            domains = data.get("blocked_domains") or []
            self._blocked_domains = {d.lower().strip() for d in domains if d}
            log.debug("Loaded %d blocked domains from %s", len(self._blocked_domains), p)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to parse blocklist.yaml (%s) — proceeding with empty blocklist", exc)

    # ------------------------------------------------------------------
    # robots.txt
    # ------------------------------------------------------------------

    def _get_robots(self, domain: str) -> protego.Protego:
        """Return a cached (or freshly fetched) Protego instance for *domain*.

        If robots.txt cannot be fetched (network error, non-200 status) we
        return a permissive Protego instance so as not to block all access.
        The cache entry is still written so we do not hammer the server on
        every request.
        """
        now = time.monotonic()
        cached = self._robots_cache.get(domain)
        if cached is not None:
            robot, fetched_at = cached
            if (now - fetched_at) < ROBOTS_TTL:
                return robot

        robots_url = f"https://{domain}/robots.txt"
        try:
            resp = self._session.get(robots_url)
            if resp.status_code == 200:
                robot = protego.Protego.parse(resp.text)
            else:
                # Non-200 (e.g. 404) → treat as no restrictions
                log.debug("robots.txt for %s returned %s — treating as permissive", domain, resp.status_code)
                robot = protego.Protego.parse("")
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not fetch robots.txt for %s (%s) — treating as permissive", domain, exc)
            robot = protego.Protego.parse("")

        self._robots_cache[domain] = (robot, now)
        return robot

    # ------------------------------------------------------------------
    # Guard helpers
    # ------------------------------------------------------------------

    def _check_allowed(self, url: str, api_source: bool = False) -> None:
        """Raise DomainBlockedError if the URL is blocked or disallowed by robots.txt.

        The blocklist is *always* enforced. The robots.txt check is skipped for
        ``api_source=True`` requests: robots.txt governs crawlers, whereas an
        official key-authenticated REST API (e.g. Adzuna) is sanctioned access
        the publisher issued credentials for. Many such APIs publish a blanket
        ``Disallow: /`` aimed at scrapers, which must not block their own API.
        """
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower().lstrip("www.")

        # 1. Explicit blocklist — always enforced, even for API sources.
        if domain in self._blocked_domains:
            raise DomainBlockedError(f"Domain '{domain}' is in the blocklist")

        # 2. robots.txt — skipped for sanctioned API sources.
        if api_source:
            return
        robot = self._get_robots(parsed.netloc or domain)
        if not robot.can_fetch(url, USER_AGENT):
            raise DomainBlockedError(
                f"robots.txt for '{domain}' disallows '{parsed.path}' for {USER_AGENT}"
            )

    def _rate_limit(self, domain: str, delay: float) -> None:
        """Sleep if needed so that at least *delay* seconds have elapsed since
        the last request to *domain*."""
        last = self._last_request.get(domain, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < delay:
            sleep_for = delay - elapsed
            log.debug("Rate-limiting %s: sleeping %.2f s", domain, sleep_for)
            time.sleep(sleep_for)

    def _crawl_delay(self, domain: str) -> float:
        """Return the Crawl-delay for our user-agent, falling back to DEFAULT_DELAY."""
        robot = self._get_robots(domain)
        try:
            cd = robot.crawl_delay(USER_AGENT)
            if cd is not None:
                return float(cd)
        except Exception:  # noqa: BLE001
            pass
        return DEFAULT_DELAY

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get(
        self, url: str, params: dict | None = None, *, api_source: bool = False
    ) -> httpx.Response:
        """Perform a GET request, enforcing all compliance rules.

        Steps:
        1. Parse domain.
        2. Check blocklist → raise DomainBlockedError if blocked.
        3. Check robots.txt → raise DomainBlockedError if disallowed.
        4. Rate-limit (sleep until domain delay elapsed).
        5. Issue request; on 429/503 apply exponential backoff up to 3 retries.
        6. Return the response (caller is responsible for checking status_code).

        ``api_source=True`` marks the request as a sanctioned, key-authenticated
        API call: the robots.txt check (step 3) and crawl-delay lookup are
        skipped, but the blocklist and a default rate limit still apply. Pass it
        from adapters whose ``method == "api"``.
        """
        parsed = urlparse(url)
        domain = parsed.netloc or parsed.path  # fallback for unusual URLs

        # Steps 2 & 3: compliance checks
        self._check_allowed(url, api_source=api_source)

        # Step 4: rate limit — use crawl-delay from robots.txt for crawled
        # sources; API sources use the default delay (no robots.txt fetch).
        delay = DEFAULT_DELAY if api_source else self._crawl_delay(domain)
        self._rate_limit(domain, delay)

        # Step 5: request with backoff
        last_exc: Exception | None = None
        for attempt, backoff_secs in enumerate([0, *BACKOFF]):
            if backoff_secs:
                log.warning(
                    "Backing off %s s before retry %d/%d for %s",
                    backoff_secs,
                    attempt,
                    len(BACKOFF),
                    url,
                )
                time.sleep(backoff_secs)

            try:
                resp = self._session.get(url, params=params)
            except httpx.RequestError as exc:
                last_exc = exc
                log.warning("Request error for %s: %s", url, exc)
                if attempt < len(BACKOFF):
                    continue
                raise

            # Record the time of this attempt regardless of status
            self._last_request[domain] = time.monotonic()

            if resp.status_code in (429, 503):
                last_exc = httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )
                log.warning("Received %s for %s (attempt %d/%d)", resp.status_code, url, attempt + 1, len(BACKOFF) + 1)
                if attempt < len(BACKOFF):
                    continue
                # Exhausted all retries
                raise last_exc  # type: ignore[misc]

            # Success (any non-429/503 status)
            return resp

        # Should be unreachable, but satisfy type checker
        if last_exc:
            raise last_exc
        raise RuntimeError(f"Failed to GET {url} after {len(BACKOFF) + 1} attempts")

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> CompliantHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Protocol — all adapters must satisfy this structural type
# ---------------------------------------------------------------------------


class JobSourceAdapter(Protocol):
    """Structural type (Protocol) that all job-source adapters must implement."""

    name: str
    method: Literal["api", "scrape", "rss", "manual"]
    risk: Literal["none", "low", "high"]
    store_full_description: bool

    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,
        http: CompliantHttpClient,
    ) -> Iterator[dict]:
        """Yield raw job dicts.  Never raises — log errors internally."""
        ...
