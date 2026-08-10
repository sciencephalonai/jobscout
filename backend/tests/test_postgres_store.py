"""PostgresRelationalStore integration test against a real Postgres.

Validates that the psycopg adapter + the shared (DuckDB) SQL bodies run correctly
on Postgres: schema DDL, `?`→`%s` placeholder translation, ON CONFLICT upserts, the
`.description` column-name path, and fetchone/fetchall. The store's *logic* is already
covered by the DuckDB suite — this proves portability of the SQL and the adapter.

Uses a throwaway Postgres in Docker (or TEST_DATABASE_URL if provided). Skipped when
neither is available, so CI without Docker stays green.
"""

from __future__ import annotations

import os
import subprocess
import time

import psycopg
import pytest

from jobscout.models import Company, UserProfile

_CONTAINER = "jobscout-pgtest"
_PORT = 55432


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


@pytest.fixture(scope="session")
def pg_dsn():  # noqa: ANN201
    dsn = os.environ.get("TEST_DATABASE_URL")
    if dsn:
        yield dsn
        return
    if not _docker_available():
        pytest.skip("no TEST_DATABASE_URL and Docker is unavailable")

    subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--name", _CONTAINER,
         "-e", "POSTGRES_PASSWORD=test", "-e", "POSTGRES_DB=jobscout",
         "-p", f"{_PORT}:5432", "postgres:16-alpine"],
        check=True, capture_output=True,
    )
    dsn = f"postgresql://postgres:test@localhost:{_PORT}/jobscout"
    try:
        for _ in range(60):
            try:
                with psycopg.connect(dsn, connect_timeout=1) as c:
                    c.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                time.sleep(1)
        else:
            pytest.skip("Postgres container never became ready")
        yield dsn
    finally:
        subprocess.run(["docker", "rm", "-f", _CONTAINER], capture_output=True)


@pytest.fixture
def pg_store(pg_dsn):  # noqa: ANN201
    # Fresh schema per test for isolation.
    with psycopg.connect(pg_dsn, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
    from jobscout.relational_postgres import PostgresRelationalStore
    store = PostgresRelationalStore(pg_dsn)
    yield store
    store.close()


class TestPostgresStore:
    def test_schema_bootstrap_and_local_user(self, pg_store) -> None:  # noqa: ANN001
        pg_store.ensure_local_user()
        user = pg_store.get_user("local")
        assert user is not None and user["is_admin"] is True

    def test_profile_upsert_get_list(self, pg_store) -> None:  # noqa: ANN001
        p = UserProfile(label="Jane", user_id="u1", skills=["python", "sql"])
        pg_store.upsert_profile(p)
        got = pg_store.get_profile(p.id)
        assert got is not None and got.label == "Jane" and got.skills == ["python", "sql"]
        # user_id filtering is a real WHERE (tenancy key), not a blob scan.
        assert [x.id for x in pg_store.list_profiles("u1")] == [p.id]
        assert pg_store.list_profiles("someone-else") == []

    def test_auth_user_resolution(self, pg_store) -> None:  # noqa: ANN001
        created = pg_store.create_auth_user(
            user_id="abc", email="a@b.com", display_name="A",
            auth_provider="auth0", auth_subject="auth0|xyz",
        )
        assert created["id"] == "abc"
        assert pg_store.get_user_by_subject("auth0", "auth0|xyz")["id"] == "abc"
        assert pg_store.get_user_by_email("A@B.COM")["id"] == "abc"  # case-insensitive
        pg_store.link_user_subject("abc", "auth0", "auth0|new")
        assert pg_store.get_user_by_subject("auth0", "auth0|new")["id"] == "abc"

    def test_job_state_and_pipeline(self, pg_store) -> None:  # noqa: ANN001
        pg_store.set_job_state("p1", "j1", "applied")
        pg_store.set_job_state("p1", "j2", "interview", note="onsite")
        pipeline = {r["job_id"]: r for r in pg_store.list_pipeline("p1")}
        assert pipeline["j1"]["status"] == "applied"
        assert pipeline["j2"]["status"] == "interview" and pipeline["j2"]["note"] == "onsite"

    def test_company_roundtrip_uses_description(self, pg_store) -> None:  # noqa: ANN001
        # list_companies/get_company read column names off conn.description — the
        # adapter must surface those correctly.
        pg_store.upsert_company(Company(ats="greenhouse", slug="acme", name="Acme"))
        got = pg_store.get_company("greenhouse", "acme")
        assert got is not None and got.name == "Acme"
        assert any(c.slug == "acme" for c in pg_store.list_companies())

    def test_usage_counter_on_conflict(self, pg_store) -> None:  # noqa: ANN001
        pg_store.incr_usage("u1", "tailor", "2026-07-16", 2)
        pg_store.incr_usage("u1", "tailor", "2026-07-16", 3)
        assert pg_store.get_usage("u1", "tailor", "2026-07-16") == 5
