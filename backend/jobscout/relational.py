"""DuckDB side store: run logs and job_sources dedup map."""

from __future__ import annotations

import contextlib
import functools
import json
import threading
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

import duckdb

from jobscout.config import settings
from jobscout.models import (
    Company,
    JobSource,
    ResumeRecord,
    RunLog,
    SavedSearch,
    TailoredResumeRecord,
    UserProfile,
)

_T = TypeVar("_T")


class RelationalStore(Protocol):
    """The relational-store contract (DB seam).

    Callers depend on THIS interface, never on a concrete backend, so swapping the
    database is a localized change: implement these methods against Postgres, add a
    branch to ``make_relational_store``, flip ``settings.relational_backend``. The
    only backend today is :class:`DuckDBRelationalStore`. Keep every implementation's
    SQL to the portable subset (``INSERT … ON CONFLICT … DO UPDATE SET x = excluded.x``,
    standard column types) so a second backend is a drop-in. See docs/multi-tenancy.md.
    """

    # Run log
    def start_run(self, source: str) -> RunLog: ...
    def finish_run(
        self, run_id: str, count_ingested: int, count_failed: int,
        count_seen: int = 0, count_filtered: int = 0, count_closed: int = 0,
        error: str | None = None,
    ) -> None: ...
    def get_recent_runs(self, limit: int = 50) -> list[RunLog]: ...
    def get_sources_status(self) -> list[dict[str, Any]]: ...
    def reap_stale_runs(self) -> int: ...
    # Direct ATS board lifecycle
    def mark_board_job_seen(self, ats: str, board_slug: str, job_id: str) -> None: ...
    def close_missing_board_jobs(
        self, ats: str, board_slug: str, seen_job_ids: set[str]
    ) -> list[str]: ...
    def job_has_active_board_presence(self, job_id: str) -> bool: ...
    # Job sources
    def upsert_job_source(self, js: JobSource) -> None: ...
    def get_job_sources(self, job_id: str) -> list[JobSource]: ...
    # Users (accounts + admin/operator monitoring)
    def ensure_local_user(self) -> None: ...
    def list_users(self) -> list[dict[str, Any]]: ...
    def get_user(self, user_id: str) -> dict[str, Any] | None: ...
    def update_user(
        self, user_id: str, *, plan: str | None = None,
        limits_json: str | None = None, is_admin: bool | None = None,
    ) -> dict[str, Any] | None: ...
    def usage_rollup(self, user_id: str, since_period: str) -> dict[str, int]: ...
    # Profiles
    def upsert_profile(self, profile: UserProfile) -> UserProfile: ...
    def get_profile(self, profile_id: str) -> UserProfile | None: ...
    def list_profiles(self, user_id: str | None = None) -> list[UserProfile]: ...
    def delete_profile(self, profile_id: str) -> None: ...
    # Meta
    def get_meta(self, key: str) -> str | None: ...
    def set_meta(self, key: str, value: str) -> None: ...
    # Resume library
    def add_resume(self, resume: ResumeRecord) -> ResumeRecord: ...
    def list_resumes(self, profile_id: str) -> list[ResumeRecord]: ...
    def get_resume(self, resume_id: str) -> ResumeRecord | None: ...
    def delete_resume(self, resume_id: str) -> None: ...
    # Tailored catalog
    def upsert_tailored(self, record: TailoredResumeRecord) -> TailoredResumeRecord: ...
    def list_tailored(self, profile_id: str) -> list[TailoredResumeRecord]: ...
    # Deep-match cache
    def get_deep_match(
        self, job_id: str, profile_id: str, fingerprint: str
    ) -> dict[str, Any] | None: ...
    def put_deep_match(
        self, job_id: str, profile_id: str, fingerprint: str, data: dict[str, Any]
    ) -> None: ...
    # Job state
    def set_job_state(
        self, profile_id: str, job_id: str, status: str, note: str | None = None
    ) -> None: ...
    def list_pipeline(self, profile_id: str) -> list[dict[str, Any]]: ...
    def get_excluded_job_ids(self, profile_id: str) -> set[str]: ...
    def get_job_state_ids(self, profile_id: str, status: str) -> list[str]: ...
    # Companies
    def upsert_company(self, c: Company) -> Company: ...
    def list_companies(
        self, tier: str | None = None, ats: str | None = None, size: str | None = None,
        h1b_sponsor: bool | None = None, enabled: bool | None = None,
        direct_apply_only: bool | None = None, order_by: str = "open_roles",
    ) -> list[Company]: ...
    def get_company(self, ats: str, slug: str) -> Company | None: ...
    def enabled_companies(self) -> list[Company]: ...
    def touch_company(self, ats: str, slug: str, open_roles: int) -> None: ...
    # Saved searches
    def create_saved_search(self, s: SavedSearch) -> SavedSearch: ...
    def list_saved_searches(self, user_id: str | None = None) -> list[SavedSearch]: ...
    def get_saved_search(self, search_id: str) -> SavedSearch | None: ...
    def delete_saved_search(self, search_id: str) -> None: ...
    def mark_saved_search_seen(self, search_id: str) -> SavedSearch | None: ...
    # Usage metering (dormant quota enforcement — entitlements.py)
    def get_usage(self, user_id: str, metric: str, period: str) -> int: ...
    def incr_usage(self, user_id: str, metric: str, period: str, amount: int = 1) -> None: ...
    # Lifecycle
    def close(self) -> None: ...


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
                """Serialize the wrapped method on the store's re-entrant lock."""
                with self._lock:
                    return __fn(self, *args, **kwargs)
            setattr(cls, name, wrapper)
    return cls


@_synchronized_methods
class DuckDBRelationalStore:
    """DuckDB implementation of the :class:`RelationalStore` seam.

    Lightweight DuckDB wrapper for operational/audit data.

    Holds two tables:
    - ``runs``        — one row per ingestion / enrichment run.
    - ``job_sources`` — maps a canonical ``job_id`` to every upstream source
                        that lists it, enabling the "posted on N portals" UX.
    """

    def __init__(self, db_path: str = settings.relational_db_path) -> None:
        """Open (or create) the DuckDB file and ensure the schema exists."""
        self._lock = threading.RLock()
        self._conn = duckdb.connect(db_path)
        self._create_tables()

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        """Create every table/index this store uses (idempotent)."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id           VARCHAR PRIMARY KEY,
                source       VARCHAR NOT NULL,
                started_at   TIMESTAMP NOT NULL,
                finished_at  TIMESTAMP,
                count_ingested INTEGER DEFAULT 0,
                count_failed   INTEGER DEFAULT 0,
                count_seen     INTEGER DEFAULT 0,
                count_filtered INTEGER DEFAULT 0,
                count_closed   INTEGER DEFAULT 0,
                error        VARCHAR,
                status       VARCHAR DEFAULT 'running'
            )
        """)
        # The board-level presence table lets direct ATS feeds close a role only
        # after a complete, successful board snapshot no longer contains it.
        # This deliberately avoids guessing from a failed or truncated fetch.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS board_job_presence (
                ats        VARCHAR NOT NULL,
                board_slug VARCHAR NOT NULL,
                job_id     VARCHAR NOT NULL,
                first_seen TIMESTAMP NOT NULL,
                last_seen  TIMESTAMP NOT NULL,
                closed_at  TIMESTAMP,
                PRIMARY KEY (ats, board_slug, job_id)
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
        # Account owner of per-user data. Generic across auth methods: auth_provider
        # /auth_subject fit Google OAuth ('google', sub) or email ('password', email);
        # password_hash is added when email+password auth ships. `plan` + `limits_json`
        # are the per-account entitlements hook (see docs/multi-tenancy.md): limits_json
        # is an OPEN JSON override map so a new limit never needs a schema change.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            VARCHAR PRIMARY KEY,
                email         VARCHAR,
                display_name  VARCHAR,
                auth_provider VARCHAR DEFAULT 'local',
                auth_subject  VARCHAR,
                plan          VARCHAR DEFAULT 'local',
                limits_json   VARCHAR,
                is_admin      BOOLEAN DEFAULT FALSE,
                created_at    TIMESTAMP NOT NULL
            )
        """)
        # Back-fill on a users table created earlier without is_admin. IF NOT EXISTS
        # so it never errors on a fresh DB (a failed duplicate-column ALTER would abort
        # the transaction and break the rest of _create_tables).
        self._conn.execute(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE"
        )
        # JSON blob so the schema stays stable as UserProfile evolves. `user_id` is a
        # first-class indexed column (the tenancy key) so filtering is a SQL WHERE that
        # a Postgres backend runs identically — not a Python scan of the blob.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS user_profiles (
                id      VARCHAR PRIMARY KEY,
                label   VARCHAR NOT NULL,
                data    VARCHAR NOT NULL
            )
        """)
        self._add_owner_column("user_profiles")
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
        with contextlib.suppress(Exception):
            self._conn.execute("ALTER TABLE user_job_state ADD COLUMN note VARCHAR")
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
            with contextlib.suppress(Exception):
                self._conn.execute(f"ALTER TABLE companies ADD COLUMN {_col} VARCHAR")
        for _col in ("count_seen", "count_filtered", "count_closed"):
            with contextlib.suppress(Exception):
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {_col} INTEGER DEFAULT 0")
        # Saved searches — pinned query+filters for "new since last visit" alerts.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS saved_searches (
                id   VARCHAR PRIMARY KEY,
                data VARCHAR NOT NULL
            )
        """)
        self._add_owner_column("saved_searches")
        # Tiny key→value store for process-level markers (first-run seed stamp,
        # per-profile "last seen For You" timestamps, …). Keeps one-off flags out
        # of dedicated tables.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key   VARCHAR PRIMARY KEY,
                value VARCHAR NOT NULL
            )
        """)
        # Resume library — many uploads per profile, one active (active_resume_id
        # on the profile). The row's JSON blob is a full ResumeRecord.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS resumes (
                id          VARCHAR PRIMARY KEY,
                profile_id  VARCHAR NOT NULL,
                uploaded_at TIMESTAMP NOT NULL,
                data        VARCHAR NOT NULL
            )
        """)
        # Tailored-resume index — makes built DOCXs findable/re-downloadable long
        # after the response that produced them. The file lives on disk under
        # settings.tailored_resume_storage_dir; this is just the catalog.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS tailored_resumes (
                profile_id VARCHAR NOT NULL,
                job_id     VARCHAR NOT NULL,
                created_at TIMESTAMP NOT NULL,
                data       VARCHAR NOT NULL,
                PRIMARY KEY (profile_id, job_id)
            )
        """)
        # Persisted deep-match results, so a score computed once is never re-billed
        # (survives restart) and a changed profile/resume/job is detected via the
        # fingerprint. One row per (job, profile) holding the LATEST fingerprint.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS deep_match_cache (
                job_id      VARCHAR NOT NULL,
                profile_id  VARCHAR NOT NULL,
                fingerprint VARCHAR NOT NULL,
                data        VARCHAR NOT NULL,
                created_at  TIMESTAMP NOT NULL,
                PRIMARY KEY (job_id, profile_id)
            )
        """)
        # Per-account usage ledger for dormant quota enforcement (entitlements.py).
        # One counter per (user, metric, period) — e.g. ('u1','tailor','2026-07-14').
        # ponytail: unused until settings.quota_enforced flips on; empty table is free.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS usage_counters (
                user_id VARCHAR NOT NULL,
                metric  VARCHAR NOT NULL,
                period  VARCHAR NOT NULL,
                count   INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, metric, period)
            )
        """)
        self.ensure_local_user()

    def _add_owner_column(self, table: str) -> None:
        """Add + index the `user_id` tenancy column on *table* (idempotent).

        Existing rows predate multi-tenancy and all belong to the single local user,
        so NULLs backfill to ``settings.local_user_id``. New writes set the column
        explicitly (see ``upsert_profile`` / ``create_saved_search``).
        """
        # IF NOT EXISTS so a re-run on a persisted DB doesn't error — a failed
        # duplicate-column ALTER can abort the DuckDB transaction and break the rest
        # of _create_tables (matches the is_admin migration).
        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id VARCHAR")
        self._conn.execute(
            f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL",
            [settings.local_user_id],
        )
        self._conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_user_id ON {table}(user_id)"
        )

    # ------------------------------------------------------------------
    # Users (account owner of per-user data)
    # ------------------------------------------------------------------

    def ensure_local_user(self) -> None:
        """Seed the single local-user row if absent (the default owner of all data).

        This is the anchor the ``user_id`` columns point at. Real signup (Google /
        email+password) inserts more rows here; nothing else changes — see
        ``api/deps.current_user_id`` and docs/multi-tenancy.md.
        """
        self._conn.execute(
            """
            INSERT INTO users (id, display_name, auth_provider, plan, is_admin, created_at)
            VALUES (?, 'Local user', 'local', 'local', TRUE, ?)
            ON CONFLICT (id) DO NOTHING
            """,
            [settings.local_user_id, datetime.now(UTC)],
        )

    _USER_COLS = "id, email, display_name, auth_provider, plan, limits_json, is_admin, created_at"

    def _user_row_to_dict(self, row: tuple) -> dict[str, Any]:
        keys = self._USER_COLS.replace(" ", "").split(",")
        d = dict(zip(keys, row, strict=True))
        d["created_at"] = _to_utc(d.get("created_at"))
        return d

    def list_users(self) -> list[dict[str, Any]]:
        """All accounts (for the operator's monitoring table)."""
        rows = self._conn.execute(
            f"SELECT {self._USER_COLS} FROM users ORDER BY created_at"
        ).fetchall()
        return [self._user_row_to_dict(r) for r in rows]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        """One account by id, or None."""
        row = self._conn.execute(
            f"SELECT {self._USER_COLS} FROM users WHERE id = ?", [user_id]
        ).fetchone()
        return self._user_row_to_dict(row) if row else None

    def update_user(
        self, user_id: str, *, plan: str | None = None,
        limits_json: str | None = None, is_admin: bool | None = None,
    ) -> dict[str, Any] | None:
        """Set a user's plan / limits_json / is_admin (the operator's grant/revoke lever).

        Only non-None arguments are written. Returns the updated row (None if absent).
        """
        sets, params = [], []
        for col, val in (("plan", plan), ("limits_json", limits_json), ("is_admin", is_admin)):
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        if sets:
            params.append(user_id)
            self._conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)
        return self.get_user(user_id)

    def usage_rollup(self, user_id: str, since_period: str) -> dict[str, int]:
        """Sum usage counts per metric for a user since *since_period* ('YYYY-MM-DD')."""
        rows = self._conn.execute(
            """
            SELECT metric, SUM(count) FROM usage_counters
             WHERE user_id = ? AND period >= ?
             GROUP BY metric
            """,
            [user_id, since_period],
        ).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    # ------------------------------------------------------------------
    # Run log
    # ------------------------------------------------------------------

    def reap_stale_runs(self) -> int:
        """Mark runs stuck in 'running' (a crash mid-ingest) as 'interrupted'.

        In-process background jobs leave a run row 'running' forever if the process
        dies before ``finish_run``. Called once at startup so the audit log is honest.
        Returns the number of rows reaped.
        """
        rows = self._conn.execute(
            "SELECT count(*) FROM runs WHERE status = 'running'"
        ).fetchone()
        n = int(rows[0]) if rows else 0
        if n:
            self._conn.execute(
                """
                UPDATE runs
                   SET status = 'interrupted',
                       finished_at = ?,
                       error = COALESCE(error, 'process restarted before the run finished')
                 WHERE status = 'running'
                """,
                [datetime.now(UTC)],
            )
        return n

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
        count_seen: int = 0,
        count_filtered: int = 0,
        count_closed: int = 0,
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
                   count_seen     = ?,
                   count_filtered = ?,
                   count_closed   = ?,
                   error          = ?,
                   status         = ?
             WHERE id = ?
            """,
            [
                finished_at, count_ingested, count_failed, count_seen,
                count_filtered, count_closed, error, status, run_id,
            ],
        )

    # ------------------------------------------------------------------
    # Direct ATS board lifecycle
    # ------------------------------------------------------------------

    def mark_board_job_seen(self, ats: str, board_slug: str, job_id: str) -> None:
        """Checkpoint one job from a direct employer board as currently open."""
        now = datetime.now(UTC)
        self._conn.execute(
            """
            INSERT INTO board_job_presence (ats, board_slug, job_id, first_seen, last_seen, closed_at)
            VALUES (?, ?, ?, ?, ?, NULL)
            ON CONFLICT (ats, board_slug, job_id) DO UPDATE
                SET last_seen = excluded.last_seen,
                    closed_at = NULL
            """,
            [ats, board_slug, job_id, now, now],
        )

    def close_missing_board_jobs(
        self, ats: str, board_slug: str, seen_job_ids: set[str]
    ) -> list[str]:
        """Close jobs absent from one known-complete board snapshot.

        This must only be called after an adapter confirms that it read the
        entire board. Returning job IDs keeps vector-store lifecycle updates
        separate from DuckDB's durable source-of-truth checkpoints.
        """
        rows = self._conn.execute(
            """
            SELECT job_id FROM board_job_presence
             WHERE ats = ? AND board_slug = ? AND closed_at IS NULL
            """,
            [ats, board_slug],
        ).fetchall()
        missing = [str(row[0]) for row in rows if str(row[0]) not in seen_job_ids]
        if not missing:
            return []
        now = datetime.now(UTC)
        self._conn.executemany(
            """
            UPDATE board_job_presence SET closed_at = ?
             WHERE ats = ? AND board_slug = ? AND job_id = ? AND closed_at IS NULL
            """,
            [[now, ats, board_slug, job_id] for job_id in missing],
        )
        return missing

    def job_has_active_board_presence(self, job_id: str) -> bool:
        """Whether a lifecycle-tracked source still lists the canonical role."""
        row = self._conn.execute(
            "SELECT EXISTS(SELECT 1 FROM board_job_presence WHERE job_id = ? AND closed_at IS NULL)",
            [job_id],
        ).fetchone()
        return bool(row and row[0])

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
                   count_ingested, count_failed, count_seen, count_filtered,
                   count_closed, error, status
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
                   r.count_seen     AS last_seen,
                   r.count_filtered AS last_filtered,
                   r.count_closed   AS last_closed,
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
                "last_seen": row[5],
                "last_filtered": row[6],
                "last_closed": row[7],
                "last_error": row[8],
                "total_ingested": row[9],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # User profiles
    # ------------------------------------------------------------------

    def upsert_profile(self, profile: UserProfile) -> UserProfile:
        """Insert or update a user profile (full replace of the JSON blob)."""
        if not profile.user_id:  # stamp ownership so every stored profile is owned
            profile.user_id = settings.local_user_id
        data = profile.model_dump_json()
        self._conn.execute(
            """
            INSERT INTO user_profiles (id, user_id, label, data)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE
                SET user_id = excluded.user_id,
                    label   = excluded.label,
                    data    = excluded.data
            """,
            [profile.id, profile.user_id, profile.label, data],
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

    def list_profiles(self, user_id: str | None = None) -> list[UserProfile]:
        """Return stored profiles ordered by label. When *user_id* is given, filter to
        that owner via the indexed `user_id` column (the SQL tenancy filter)."""
        if user_id is None:
            rows = self._conn.execute(
                "SELECT data FROM user_profiles ORDER BY label"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM user_profiles WHERE user_id = ? ORDER BY label",
                [user_id],
            ).fetchall()
        return [UserProfile.model_validate_json(r[0]) for r in rows]

    def delete_profile(self, profile_id: str) -> None:
        """Delete a profile and its job-state, resume, and tailored-resume rows."""
        self._conn.execute("DELETE FROM user_profiles WHERE id = ?", [profile_id])
        self._conn.execute(
            "DELETE FROM user_job_state WHERE profile_id = ?", [profile_id]
        )
        self._conn.execute("DELETE FROM resumes WHERE profile_id = ?", [profile_id])
        self._conn.execute(
            "DELETE FROM tailored_resumes WHERE profile_id = ?", [profile_id]
        )

    # ------------------------------------------------------------------
    # Key→value markers
    # ------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        """Return a stored marker value, or None."""
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", [key]).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        """Insert or replace a marker value."""
        self._conn.execute(
            """
            INSERT INTO meta (key, value) VALUES (?, ?)
            ON CONFLICT (key) DO UPDATE SET value = excluded.value
            """,
            [key, value],
        )

    # ------------------------------------------------------------------
    # Resume library
    # ------------------------------------------------------------------

    def add_resume(self, resume: ResumeRecord) -> ResumeRecord:
        """Insert (or replace by id) one resume record."""
        self._conn.execute(
            """
            INSERT INTO resumes (id, profile_id, uploaded_at, data)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE
                SET profile_id  = excluded.profile_id,
                    uploaded_at = excluded.uploaded_at,
                    data        = excluded.data
            """,
            [resume.id, resume.profile_id, resume.uploaded_at, resume.model_dump_json()],
        )
        return resume

    def list_resumes(self, profile_id: str) -> list[ResumeRecord]:
        """Return a profile's resumes, newest upload first."""
        rows = self._conn.execute(
            "SELECT data FROM resumes WHERE profile_id = ? ORDER BY uploaded_at DESC",
            [profile_id],
        ).fetchall()
        return [ResumeRecord.model_validate_json(r[0]) for r in rows]

    def get_resume(self, resume_id: str) -> ResumeRecord | None:
        """Fetch a single resume record by id, or None."""
        row = self._conn.execute(
            "SELECT data FROM resumes WHERE id = ?", [resume_id]
        ).fetchone()
        return ResumeRecord.model_validate_json(row[0]) if row else None

    def delete_resume(self, resume_id: str) -> None:
        """Delete one resume record (the file on disk is removed by the caller)."""
        self._conn.execute("DELETE FROM resumes WHERE id = ?", [resume_id])

    # ------------------------------------------------------------------
    # Tailored-resume catalog
    # ------------------------------------------------------------------

    def upsert_tailored(self, record: TailoredResumeRecord) -> TailoredResumeRecord:
        """Record (or refresh) a built tailored resume for a (profile, job)."""
        self._conn.execute(
            """
            INSERT INTO tailored_resumes (profile_id, job_id, created_at, data)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (profile_id, job_id) DO UPDATE
                SET created_at = excluded.created_at,
                    data       = excluded.data
            """,
            [record.profile_id, record.job_id, record.created_at, record.model_dump_json()],
        )
        return record

    def list_tailored(self, profile_id: str) -> list[TailoredResumeRecord]:
        """Return a profile's tailored resumes, newest build first."""
        rows = self._conn.execute(
            "SELECT data FROM tailored_resumes WHERE profile_id = ? ORDER BY created_at DESC",
            [profile_id],
        ).fetchall()
        return [TailoredResumeRecord.model_validate_json(r[0]) for r in rows]

    # ------------------------------------------------------------------
    # Deep-match result persistence (never re-bill the same job+profile)
    # ------------------------------------------------------------------

    def get_deep_match(
        self, job_id: str, profile_id: str, fingerprint: str
    ) -> dict[str, Any] | None:
        """Return a stored deep-match result ONLY if its fingerprint still matches.

        A different fingerprint means the profile/resume/job changed since — the
        stored score is stale, so we report a miss (the caller recomputes).
        """
        row = self._conn.execute(
            "SELECT data FROM deep_match_cache WHERE job_id = ? AND profile_id = ? AND fingerprint = ?",
            [job_id, profile_id, fingerprint],
        ).fetchone()
        if row is None:
            return None
        try:
            return dict(json.loads(row[0]))
        except (ValueError, TypeError):
            return None

    def put_deep_match(
        self, job_id: str, profile_id: str, fingerprint: str, data: dict[str, Any]
    ) -> None:
        """Store (replacing any prior fingerprint) the latest deep-match result."""
        self._conn.execute(
            """
            INSERT INTO deep_match_cache (job_id, profile_id, fingerprint, data, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (job_id, profile_id) DO UPDATE
                SET fingerprint = excluded.fingerprint,
                    data        = excluded.data,
                    created_at  = excluded.created_at
            """,
            [job_id, profile_id, fingerprint, json.dumps(data), datetime.now(UTC)],
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
        """Return one watchlist company by slug (None when absent)."""
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
        if not s.user_id:  # stamp ownership so every saved search is owned
            s.user_id = settings.local_user_id
        self._conn.execute(
            """
            INSERT INTO saved_searches (id, user_id, data) VALUES (?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                user_id = excluded.user_id,
                data    = excluded.data
            """,
            [s.id, s.user_id, s.model_dump_json()],
        )
        return s

    def list_saved_searches(self, user_id: str | None = None) -> list[SavedSearch]:
        """Return saved searches, newest first. When *user_id* is given, filter to that
        owner via the indexed `user_id` column."""
        if user_id is None:
            rows = self._conn.execute("SELECT data FROM saved_searches").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM saved_searches WHERE user_id = ?", [user_id]
            ).fetchall()
        items = [SavedSearch.model_validate_json(r[0]) for r in rows]
        items.sort(key=lambda s: s.created_at, reverse=True)
        return items

    def get_saved_search(self, search_id: str) -> SavedSearch | None:
        """Return one saved search by id (None when absent)."""
        row = self._conn.execute(
            "SELECT data FROM saved_searches WHERE id = ?", [search_id]
        ).fetchone()
        return SavedSearch.model_validate_json(row[0]) if row else None

    def delete_saved_search(self, search_id: str) -> None:
        """Delete a saved search and its new-match counter."""
        self._conn.execute("DELETE FROM saved_searches WHERE id = ?", [search_id])

    def mark_saved_search_seen(self, search_id: str) -> SavedSearch | None:
        """Set last_checked_at = now (clears the 'new' count) and return the row."""
        s = self.get_saved_search(search_id)
        if s is None:
            return None
        s.last_checked_at = datetime.now(UTC)
        return self.create_saved_search(s)

    # ------------------------------------------------------------------
    # Usage metering (dormant until settings.quota_enforced; see entitlements.py)
    # ------------------------------------------------------------------

    def get_usage(self, user_id: str, metric: str, period: str) -> int:
        """Return the usage count for (user, metric, period), or 0."""
        row = self._conn.execute(
            "SELECT count FROM usage_counters WHERE user_id = ? AND metric = ? AND period = ?",
            [user_id, metric, period],
        ).fetchone()
        return int(row[0]) if row else 0

    def incr_usage(self, user_id: str, metric: str, period: str, amount: int = 1) -> None:
        """Add *amount* to the (user, metric, period) counter (upsert)."""
        self._conn.execute(
            """
            INSERT INTO usage_counters (user_id, metric, period, count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id, metric, period) DO UPDATE
                SET count = usage_counters.count + excluded.count
            """,
            [user_id, metric, period, amount],
        )

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        self._conn.close()

    # Context manager support
    def __enter__(self) -> DuckDBRelationalStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def make_relational_store(db_path: str | None = None) -> RelationalStore:
    """Construct the relational store for the configured backend (DB seam).

    Today only ``duckdb`` exists. A future Postgres backend is a new branch here +
    a ``PostgresRelationalStore`` implementing the :class:`RelationalStore` Protocol;
    callers never change because they depend on the Protocol, not the concrete class.
    """
    backend = settings.relational_backend
    if backend == "duckdb":
        return DuckDBRelationalStore(db_path or settings.relational_db_path)
    raise ValueError(f"Unknown relational_backend: {backend!r}")


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
    """Map a runs-table row to the RunLog model."""
    (
        id_, source, started_at, finished_at, count_ingested, count_failed,
        count_seen, count_filtered, count_closed, error, status,
    ) = row
    return RunLog(
        id=id_,
        source=source,
        started_at=_to_utc(started_at) or datetime.now(UTC),
        finished_at=_to_utc(finished_at),
        count_ingested=count_ingested or 0,
        count_failed=count_failed or 0,
        count_seen=count_seen or 0,
        count_filtered=count_filtered or 0,
        count_closed=count_closed or 0,
        error=error,
        status=status or "running",
    )
