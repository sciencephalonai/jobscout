"""Postgres (Supabase) implementation of the RelationalStore seam.

Reuses the SQL and every method body of :class:`DuckDBRelationalStore` verbatim —
JobScout's SQL is deliberately kept to the Postgres-portable subset (``INSERT …
ON CONFLICT … DO UPDATE SET x = excluded.x``, plain ``VARCHAR``/``TIMESTAMP``
types). We only swap the connection: ``self._conn`` becomes a thin adapter over a
psycopg connection pool that mimics DuckDB's ``conn.execute(sql, params).fetchall()``
API and translates ``?`` placeholders to psycopg's ``%s``.

Because each ``execute`` checks out its own pooled connection (thread-safe), the
DuckDB single-connection serialization lock is unnecessary here — ``self._lock`` is
a no-op, so real request concurrency is preserved. Selected when a Postgres DSN is
configured (see ``make_relational_store``); DuckDB remains the local/test fallback.
"""

from __future__ import annotations

import threading
from typing import Any

from psycopg_pool import ConnectionPool

from jobscout.relational import DuckDBRelationalStore


class _NullLock:
    """A no-op context manager — the psycopg pool already provides thread safety."""

    def __enter__(self) -> _NullLock:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _PgResult:
    """Mimics the fetch API of a DuckDB result over already-fetched rows."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchmany(self, size: int) -> list[tuple]:
        return self._rows[:size]


class _PgConn:
    """psycopg-pool adapter presenting DuckDB's ``execute``/``description``/``close`` API.

    Rows are fetched eagerly inside the pooled-connection block so results stay valid
    after the connection returns to the pool. ``description`` is kept per-thread so two
    threads reading column names never clobber each other.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool
        self._local = threading.local()

    @staticmethod
    def _translate(sql: str) -> str:
        # The store uses `?` placeholders; no `?` ever appears inside a string literal
        # (all queries are parameterized), so a plain swap to psycopg's `%s` is safe.
        return sql.replace("?", "%s")

    def execute(self, sql: str, params: list | None = None) -> _PgResult:
        query = self._translate(sql)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(query, params if params is not None else None)
            self._local.description = cur.description
            rows = cur.fetchall() if cur.description is not None else []
        return _PgResult(rows)

    def executemany(self, sql: str, seq_of_params: list) -> _PgResult:
        query = self._translate(sql)
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.executemany(query, seq_of_params)
            self._local.description = None
        return _PgResult([])

    @property
    def description(self) -> Any:
        return getattr(self._local, "description", None)

    def close(self) -> None:
        self._pool.close()


class PostgresRelationalStore(DuckDBRelationalStore):
    """RelationalStore backed by Supabase/Postgres. See module docstring."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        # Deliberately does NOT call super().__init__ (that opens DuckDB). We build the
        # psycopg-backed connection, then run the SHARED schema bootstrap + method bodies.
        if not dsn:
            raise ValueError("PostgresRelationalStore requires a non-empty DSN.")
        self._lock = _NullLock()  # type: ignore[assignment]
        self._pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size, open=True)
        self._conn = _PgConn(self._pool)  # type: ignore[assignment]
        # Fail fast on a bad DSN rather than on the first query.
        self._pool.wait(timeout=10.0)
        self._create_tables()
