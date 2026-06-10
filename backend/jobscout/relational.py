"""DuckDB side store: run logs and job_sources dedup map."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import duckdb

from jobscout.config import settings
from jobscout.models import JobSource, RunLog


class RelationalStore:
    """Lightweight DuckDB wrapper for operational/audit data.

    Holds two tables:
    - ``runs``        — one row per ingestion / enrichment run.
    - ``job_sources`` — maps a canonical ``job_id`` to every upstream source
                        that lists it, enabling the "posted on N portals" UX.
    """

    def __init__(self, db_path: str = settings.relational_db_path) -> None:
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


def _row_to_run_log(row: tuple) -> RunLog:  # type: ignore[type-arg]
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
