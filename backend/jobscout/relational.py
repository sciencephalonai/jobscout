"""DuckDB side store: run logs and job_sources dedup map."""

from __future__ import annotations

import functools
import threading
from datetime import UTC, datetime
from typing import Any, TypeVar

import duckdb

from jobscout.config import settings
from jobscout.models import Company, JobSource, RunLog, SavedSearch, UserProfile

_T = TypeVar("_T")


def _synchronized_methods(cls: type[_T]) -> type[_T]:
    """Wrap every public method to acquire ``self._lock`` first.

    DuckDB connections are NOT thread-safe, and ingestion runs in a background
    thread (BackgroundTasks) while request handlers read on the same shared
    connection. Without serialization a concurrent write clobbers a reader's
    cursor description → ``KeyError`` on column access. A re-entrant lock makes
    every public DB op atomic (nested public calls re-acquire safely).
    """
    for name, attr in list(vars(cls).items()):
        if callable(attr) and not name.startswith("_"):
            @functools.wraps(attr)  # type: ignore[arg-type]
            def wrapper(self: Any, *args: Any, __fn: Any = attr, **kwargs: Any) -> Any:
                with self._lock:
                    return __fn(self, *args, **kwargs)
            setattr(cls, name, wrapper)
    return cls


@_synchronized_methods
class RelationalStore:
    """Lightweight DuckDB wrapper for operational/audit data.

    Holds two tables:
    - ``runs``        — one row per ingestion / enrichment run.
    - ``job_sources`` — maps a canonical ``job_id`` to every upstream source
                        that lists it, enabling the "posted on N portals" UX.
    """

    def __init__(self, db_path: str = settings.relational_db_path) -> None:
        self._lock = threading.RLock()
        self._conn = duckdb.connect(db_path)
        self._create_tables()

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id           VARCHAR PRIMARY KEY,
                source       VARCHAR NOT NULL,
                started_at   TIMESTAMP NOT NULL,
                finished_at  TIMESTAMP,
                count_ingested INTEGER DEFAULT 0,
                count_failed   INTEGER DEFAULT 0,
                error        VARCHAR,
                status       VARCHAR DEFAULT 'running'
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS job_sources (
                job_id      VARCHAR,
                source      VARCHAR,
                url         VARCHAR,
                posted_date TIMESTAMP,
                PRIMARY KEY (job_id, source)
            )
        """)
        # Per-user search preferences. List/scalar fields are stored as a single
        # JSON blob so the schema stays stable as UserProfile evolves.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                id      VARCHAR PRIMARY KEY,
                label   VARCHAR NOT NULL,
                data    VARCHAR NOT NULL
            )
        """)
        # Per-user job state: applied / seen / hidden. Drives result exclusion.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS user_job_state (
                profile_id VARCHAR,
                job_id     VARCHAR,
                status     VARCHAR NOT NULL,
                updated_at TIMESTAMP NOT NULL,
                note       VARCHAR,
                PRIMARY KEY (profile_id, job_id)
            )
        """)
        # Back-fill the note column on pre-existing DBs (idempotent).
        try:
            self._conn.execute("ALTER TABLE user_job_state ADD COLUMN note VARCHAR")
        except Exception:  # noqa: BLE001 — column already exists
            pass
        # Company registry — the durable employer entity (ATS, slug, tier, …).
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                ats               VARCHAR NOT NULL,
                slug              VARCHAR NOT NULL,
                name              VARCHAR NOT NULL,
                careers_url       VARCHAR,
                tier              VARCHAR DEFAULT 'unknown',
                employer_type     VARCHAR DEFAULT 'for_profit',
                size_bucket       VARCHAR,
                known_h1b_sponsor BOOLEAN DEFAULT FALSE,
                cap_exempt_hint   VARCHAR DEFAULT 'unknown',
                open_roles        INTEGER DEFAULT 0,
                last_checked      TIMESTAMP,
                enabled           BOOLEAN DEFAULT TRUE,
                direct_apply_only BOOLEAN DEFAULT FALSE,
                region            VARCHAR,
                site              VARCHAR,
                PRIMARY KEY (ats, slug)
            )
        """)
        # Back-fill region/site on pre-existing DBs (Workday tenant connection).
        for _col in ("region", "site"):
            try:
                self._conn.execute(f"ALTER TABLE companies ADD COLUMN {_col} VARCHAR")
            except Exception:  # noqa: BLE001 — column already exists
                pass
        # Saved searches — pinned query+filters for "new since last visit" alerts.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS saved_searches (
                id   VARCHAR PRIMARY KEY,
                data VARCHAR NOT NULL
            )
        """)

    # ------------------------------------------------------------------
    # Run log
    # ------------------------------------------------------------------

    def start_run(self, source: str) -> RunLog:
        """Insert a new 'running' run record and return it."""
        run = RunLog(
            source=source,
            started_at=datetime.now(UTC),
            status="running",
        )
        self._conn.execute(
            """
            INSERT INTO runs (id, source, started_at, status)
            VALUES (?, ?, ?, ?)
            """,
            [run.id, run.source, run.started_at, run.status],
        )
        return run

    def finish_run(
        self,
        run_id: str,
        count_ingested: int,
        count_failed: int,
        error: str | None = None,
    ) -> None:
        """Mark a run as done (or failed if *error* is provided) and record counts."""
        finished_at = datetime.now(UTC)
        status = "failed" if error else "done"
        self._conn.execute(
            """
            UPDATE runs
               SET finished_at    = ?,
                   count_ingested = ?,
                   count_failed   = ?,
                   error          = ?,
                   status         = ?
             WHERE id = ?
            """,
            [finished_at, count_ingested, count_failed, error, status, run_id],
        )

    # ------------------------------------------------------------------
    # Job sources dedup map
    # ------------------------------------------------------------------

    def upsert_job_source(self, js: JobSource) -> None:
        """Insert or update a job_sources row (ON CONFLICT DO UPDATE)."""
        self._conn.execute(
            """
            INSERT INTO job_sources (job_id, source, url, posted_date)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (job_id, source) DO UPDATE
                SET url         = excluded.url,
                    posted_date = excluded.posted_date
            """,
            [js.job_id, js.source, js.url, js.posted_date],
        )

    def get_job_sources(self, job_id: str) -> list[JobSource]:
        """Return all upstream source rows for a canonical job_id."""
        rows = self._conn.execute(
            "SELECT job_id, source, url, posted_date FROM job_sources WHERE job_id = ?",
            [job_id],
        ).fetchall()
        return [
            JobSource(
                job_id=row[0],
                source=row[1],
                url=row[2],
                posted_date=_to_utc(row[3]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_recent_runs(self, limit: int = 50) -> list[RunLog]:
        """Return the *limit* most recently started runs."""
        rows = self._conn.execute(
            """
            SELECT id, source, started_at, finished_at,
                   count_ingested, count_failed, error, status
              FROM runs
             ORDER BY started_at DESC
             LIMIT ?
            """,
            [limit],
        ).fetchall()
        return [_row_to_run_log(r) for r in rows]

    def get_sources_status(self) -> list[dict[str, Any]]:
        """Return the latest run per source plus cumulative ingestion counts.

        Each dict contains:
        ``source``, ``last_run_at``, ``last_run_status``,
        ``last_ingested``, ``last_failed``, ``last_error``,
        ``total_ingested``.
        """
        # Latest run per source
        rows = self._conn.execute(
            """
            WITH ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY source
                           ORDER BY started_at DESC
                       ) AS rn
                  FROM runs
            )
            SELECT r.source,
                   r.started_at   AS last_run_at,
                   r.status       AS last_run_status,
                   r.count_ingested AS last_ingested,
                   r.count_failed   AS last_failed,
                   r.error          AS last_error,
                   agg.total_ingested
              FROM ranked r
              JOIN (
                  SELECT source, SUM(count_ingested) AS total_ingested
                    FROM runs
                   GROUP BY source
              ) agg ON agg.source = r.source
             WHERE r.rn = 1
             ORDER BY r.source
            """
        ).fetchall()

        return [
            {
                "source": row[0],
                "last_run_at": _to_utc(row[1]),
                "last_run_status": row[2],
                "last_ingested": row[3],
                "last_failed": row[4],
                "last_error": row[5],
                "total_ingested": row[6],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # User profiles
    # ------------------------------------------------------------------

    def upsert_profile(self, profile: UserProfile) -> UserProfile:
        """Insert or update a user profile (full replace of the JSON blob)."""
        data = profile.model_dump_json()
        self._conn.execute(
            """
            INSERT INTO user_profiles (id, label, data)
            VALUES (?, ?, ?)
            ON CONFLICT (id) DO UPDATE
                SET label = excluded.label,
                    data  = excluded.data
            """,
            [profile.id, profile.label, data],
        )
        return profile

    def get_profile(self, profile_id: str) -> UserProfile | None:
        """Fetch a single profile by id, or None."""
        row = self._conn.execute(
            "SELECT data FROM user_profiles WHERE id = ?", [profile_id]
        ).fetchone()
        if row is None:
            return None
        return UserProfile.model_validate_json(row[0])

    def list_profiles(self) -> list[UserProfile]:
        """Return all stored profiles ordered by label."""
        rows = self._conn.execute(
            "SELECT data FROM user_profiles ORDER BY label"
        ).fetchall()
        return [UserProfile.model_validate_json(r[0]) for r in rows]

    def delete_profile(self, profile_id: str) -> None:
        """Delete a profile and its job-state rows."""
        self._conn.execute("DELETE FROM user_profiles WHERE id = ?", [profile_id])
        self._conn.execute(
            "DELETE FROM user_job_state WHERE profile_id = ?", [profile_id]
        )

    # ------------------------------------------------------------------
    # Per-user job state (applied / seen / hidden)
    # ------------------------------------------------------------------

    def set_job_state(
        self, profile_id: str, job_id: str, status: str, note: str | None = None
    ) -> None:
        """Record a profile's state for a job. Status is triage (saved/seen/hidden)
        or a pipeline stage (applied/oa/interview/offer/rejected). Optional note is
        preserved when not provided (only overwritten when a note is passed)."""
        self._conn.execute(
            """
            INSERT INTO user_job_state (profile_id, job_id, status, updated_at, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (profile_id, job_id) DO UPDATE
                SET status     = excluded.status,
                    updated_at = excluded.updated_at,
                    note       = COALESCE(excluded.note, user_job_state.note)
            """,
            [profile_id, job_id, status, datetime.now(UTC), note],
        )

    def list_pipeline(self, profile_id: str) -> list[dict[str, Any]]:
        """Return all pipeline-stage rows for a profile (applied/oa/interview/offer/
        rejected) with stage + note + updated_at, newest first. Powers the tracker."""
        rows = self._conn.execute(
            """
            SELECT job_id, status, note, updated_at FROM user_job_state
             WHERE profile_id = ?
               AND status IN ('applied','oa','interview','offer','rejected')
             ORDER BY updated_at DESC
            """,
            [profile_id],
        ).fetchall()
        return [
            {"job_id": r[0], "status": r[1], "note": r[2], "updated_at": _to_utc(r[3])}
            for r in rows
        ]

    def get_excluded_job_ids(self, profile_id: str) -> set[str]:
        """Return job_ids to hide from the main list: anything hidden or already in
        the application pipeline (applied/oa/interview/offer/rejected). 'saved' and
        'seen' stay visible."""
        rows = self._conn.execute(
            """
            SELECT job_id FROM user_job_state
             WHERE profile_id = ?
               AND status IN ('applied','oa','interview','offer','rejected','hidden')
            """,
            [profile_id],
        ).fetchall()
        return {r[0] for r in rows}

    def get_job_state_ids(self, profile_id: str, status: str) -> list[str]:
        """Return job_ids a profile marked with *status* (applied|saved|seen|hidden),
        newest first. Powers the Shortlist (saved) and Applied views."""
        rows = self._conn.execute(
            """
            SELECT job_id FROM user_job_state
             WHERE profile_id = ? AND status = ?
             ORDER BY updated_at DESC
            """,
            [profile_id, status],
        ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Company registry
    # ------------------------------------------------------------------

    def upsert_company(self, c: Company) -> Company:
        """Insert or update a company (keyed by ats+slug)."""
        self._conn.execute(
            """
            INSERT INTO companies (
                ats, slug, name, careers_url, tier, employer_type, size_bucket,
                known_h1b_sponsor, cap_exempt_hint, open_roles, last_checked,
                enabled, direct_apply_only, region, site
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (ats, slug) DO UPDATE SET
                name = excluded.name,
                careers_url = excluded.careers_url,
                tier = excluded.tier,
                employer_type = excluded.employer_type,
                size_bucket = excluded.size_bucket,
                known_h1b_sponsor = excluded.known_h1b_sponsor,
                cap_exempt_hint = excluded.cap_exempt_hint,
                open_roles = excluded.open_roles,
                last_checked = excluded.last_checked,
                enabled = excluded.enabled,
                direct_apply_only = excluded.direct_apply_only,
                region = excluded.region,
                site = excluded.site
            """,
            [c.ats, c.slug, c.name, c.careers_url, c.tier, c.employer_type,
             c.size_bucket, c.known_h1b_sponsor, c.cap_exempt_hint, c.open_roles,
             c.last_checked, c.enabled, c.direct_apply_only, c.region, c.site],
        )
        return c

    def list_companies(
        self,
        tier: str | None = None,
        ats: str | None = None,
        size: str | None = None,
        h1b_sponsor: bool | None = None,
        enabled: bool | None = None,
        direct_apply_only: bool | None = None,
        order_by: str = "open_roles",
    ) -> list[Company]:
        """Return registry companies matching the given filters."""
        where: list[str] = []
        params: list[Any] = []
        for col, val in (
            ("tier", tier),
            ("ats", ats),
            ("size_bucket", size),
            ("known_h1b_sponsor", h1b_sponsor),
            ("enabled", enabled),
            ("direct_apply_only", direct_apply_only),
        ):
            if val is not None and val != "":
                where.append(f"{col} = ?")
                params.append(val)
        # Whitelist the sort column (no user SQL injection via order_by).
        order_col = order_by if order_by in {"open_roles", "last_checked", "name", "tier"} else "open_roles"
        direction = "ASC" if order_col == "name" else "DESC"
        sql = "SELECT * FROM companies"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order_col} {direction} NULLS LAST"
        rows = self._conn.execute(sql, params).fetchall()
        cols = [d[0] for d in self._conn.description]
        return [_row_to_company(dict(zip(cols, r, strict=False))) for r in rows]

    def get_company(self, ats: str, slug: str) -> Company | None:
        row = self._conn.execute(
            "SELECT * FROM companies WHERE ats = ? AND slug = ?", [ats, slug]
        ).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in self._conn.description]
        return _row_to_company(dict(zip(cols, row, strict=False)))

    def enabled_companies(self) -> list[Company]:
        """Reachable companies on the refresh watchlist (enabled, not direct-apply)."""
        return self.list_companies(enabled=True, direct_apply_only=False)

    def touch_company(self, ats: str, slug: str, open_roles: int) -> None:
        """Update last_checked + open_roles after a refresh pass."""
        self._conn.execute(
            "UPDATE companies SET last_checked = ?, open_roles = ? WHERE ats = ? AND slug = ?",
            [datetime.now(UTC), open_roles, ats, slug],
        )

    # ------------------------------------------------------------------
    # Saved searches
    # ------------------------------------------------------------------

    def create_saved_search(self, s: SavedSearch) -> SavedSearch:
        """Insert (or replace) a saved search by id (JSON blob)."""
        self._conn.execute(
            """
            INSERT INTO saved_searches (id, data) VALUES (?, ?)
            ON CONFLICT (id) DO UPDATE SET data = excluded.data
            """,
            [s.id, s.model_dump_json()],
        )
        return s

    def list_saved_searches(self) -> list[SavedSearch]:
        """Return all saved searches, newest first."""
        rows = self._conn.execute("SELECT data FROM saved_searches").fetchall()
        items = [SavedSearch.model_validate_json(r[0]) for r in rows]
        items.sort(key=lambda s: s.created_at, reverse=True)
        return items

    def get_saved_search(self, search_id: str) -> SavedSearch | None:
        row = self._conn.execute(
            "SELECT data FROM saved_searches WHERE id = ?", [search_id]
        ).fetchone()
        return SavedSearch.model_validate_json(row[0]) if row else None

    def delete_saved_search(self, search_id: str) -> None:
        self._conn.execute("DELETE FROM saved_searches WHERE id = ?", [search_id])

    def mark_saved_search_seen(self, search_id: str) -> SavedSearch | None:
        """Set last_checked_at = now (clears the 'new' count) and return the row."""
        s = self.get_saved_search(search_id)
        if s is None:
            return None
        s.last_checked_at = datetime.now(UTC)
        return self.create_saved_search(s)

    def close(self) -> None:
        self._conn.close()

    # Context manager support
    def __enter__(self) -> RelationalStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _to_utc(value: Any) -> datetime | None:
    """Coerce a DuckDB timestamp (datetime or None) to UTC-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return None


def _row_to_company(d: dict[str, Any]) -> Company:
    """Build a Company from a DuckDB row dict."""
    return Company(
        ats=d["ats"],
        slug=d["slug"],
        name=d["name"],
        careers_url=d.get("careers_url"),
        tier=d.get("tier") or "unknown",
        employer_type=d.get("employer_type") or "for_profit",
        size_bucket=d.get("size_bucket"),
        known_h1b_sponsor=bool(d.get("known_h1b_sponsor")),
        cap_exempt_hint=d.get("cap_exempt_hint") or "unknown",
        open_roles=int(d.get("open_roles") or 0),
        last_checked=_to_utc(d.get("last_checked")),
        enabled=bool(d.get("enabled")),
        direct_apply_only=bool(d.get("direct_apply_only")),
        region=d.get("region"),
        site=d.get("site"),
    )


def _row_to_run_log(row: tuple) -> RunLog:
    id_, source, started_at, finished_at, count_ingested, count_failed, error, status = row
    return RunLog(
        id=id_,
        source=source,
        started_at=_to_utc(started_at) or datetime.now(UTC),
        finished_at=_to_utc(finished_at),
        count_ingested=count_ingested or 0,
        count_failed=count_failed or 0,
        error=error,
        status=status or "running",
    )
