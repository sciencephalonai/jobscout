#!/usr/bin/env python3
"""Copy an existing local DuckDB relational store into Postgres (Supabase).

One-time helper for moving a local single-user JobScout onto the hosted Postgres
backend. Idempotent: every insert is ``ON CONFLICT DO NOTHING``, so re-running only
fills gaps. The Postgres schema is created by ``PostgresRelationalStore`` first, so
tables/columns already match.

Usage:
    python scripts/migrate_duckdb_to_postgres.py \
        --duckdb ./jobscout.duckdb \
        --postgres "postgresql://user:pass@host:5432/db"

If --postgres is omitted, DATABASE_URL / SUPABASE_DB_URL from the environment is used.
"""

from __future__ import annotations

import argparse
import sys

import duckdb

from jobscout.config import settings
from jobscout.relational_postgres import PostgresRelationalStore


def _duckdb_tables(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
    ).fetchall()
    return [r[0] for r in rows]


def migrate(duckdb_path: str, dsn: str) -> None:
    """Copy every table from the DuckDB file into the Postgres store."""
    store = PostgresRelationalStore(dsn)  # bootstraps the Postgres schema
    duck = duckdb.connect(duckdb_path, read_only=True)
    total = 0
    for table in _duckdb_tables(duck):
        cols = [d[0] for d in duck.execute(f"SELECT * FROM {table} LIMIT 0").description]
        rows = duck.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: 0 rows")
            continue
        col_list = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        with store._pool.connection() as conn, conn.cursor() as cur:  # noqa: SLF001
            cur.executemany(sql, rows)
        print(f"  {table}: {len(rows)} rows")
        total += len(rows)
    duck.close()
    store.close()
    print(f"Done. Migrated {total} rows.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdb", default=settings.relational_db_path, help="DuckDB file path")
    parser.add_argument("--postgres", default="", help="Postgres DSN (else env DATABASE_URL/SUPABASE_DB_URL)")
    args = parser.parse_args()

    dsn = args.postgres or settings.effective_database_url
    if not dsn:
        print("No Postgres DSN. Pass --postgres or set DATABASE_URL / SUPABASE_DB_URL.", file=sys.stderr)
        return 2
    print(f"Migrating {args.duckdb} → Postgres…")
    migrate(args.duckdb, dsn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
