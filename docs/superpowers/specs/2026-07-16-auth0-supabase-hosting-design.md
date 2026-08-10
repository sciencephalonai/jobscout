# Auth0 identity + Supabase (Postgres + Storage) — design

- **Date:** 2026-07-16
- **Status:** ✅ Implemented (2026-07-16), all 3 phases. See `docs/ROADMAP-CURRENT.md` Round 7 and
  `docs/auth-and-hosting.md`. Realization note: `PostgresRelationalStore` swaps only the connection
  (a psycopg-pool adapter mimicking DuckDB's `execute().fetchall()`), reusing every method body — a
  lower-risk form of the "shared base class" in §5 that keeps the 613-test DuckDB suite as the guard.
- **Reference:** `/Users/ndingari/Dropbox/leelaa` (Auth0 = identity, Supabase = data), esp.
  `backend/leelaa/auth/auth0_verifier.py`, `backend/leelaa/db/client.py`.

## 1. Goal

Turn JobScout from a single local user into a hosted, multi-user app, mirroring Leelaa's split:
**Auth0** for authentication and **Supabase** for the database (Postgres) and file storage. Weaviate
stays the vector DB. This intentionally supersedes the earlier "local-only, don't deploy yet" note.

## 2. Confirmed decisions

| Question | Decision |
|---|---|
| Scope | Auth0 identity **+** Supabase Postgres (relational) **+** Supabase Storage (files). |
| DB access | **Direct Postgres SQL via psycopg3** implementing the existing `RelationalStore` Protocol — NOT supabase-py/PostgREST. The repo's SQL is already kept Postgres-portable. |
| DuckDB | **Fallback / test-and-dev double.** Postgres is the real backend whenever `DATABASE_URL`/`SUPABASE_DB_URL` is set; DuckDB is used otherwise (local dev + the in-memory test suite). Not deleted. |
| RLS | **No RLS.** Backend connects service-side; tenancy stays app-level via `owned_profile` (already leak-proof, 404-not-403). |
| Auth provider | **Auth0** (not Supabase Auth), matching Leelaa. |
| Frontend | JobScout is **Vite + React** (not Next.js) → `@auth0/auth0-react` SPA SDK, not Next.js middleware. |
| Config gating | Every integration is env-gated: absent config = today's zero-auth local behavior. |
| Weaviate | Unchanged (vector DB). |

## 3. Build order (each independently shippable)

1. **Auth0 identity** — works on the current DB, so it lands first.
2. **Supabase Postgres store** — behind the `RelationalStore` Protocol.
3. **Supabase Storage** — behind the `BlobStore` Protocol.

## 4. Subsystem: Auth0 identity

### 4.1 Backend
- New `backend/jobscout/auth/auth0.py` (ported from Leelaa's verifier, using **PyJWT**'s
  `PyJWKClient` for cached JWKS): verify a `Bearer` RS256/ES256 access token
  (issuer `https://{domain}/`, audience `AUTH0_AUDIENCE`). Raises 401 on invalid/expired.
- `api/deps.py::current_user_id(request)` — the ONE seam — becomes:
  - Auth0 configured **and** a valid Bearer token → resolve `users` by
    `(auth_provider='auth0', auth_subject=sub)`; else auto-link by `email`; else auto-provision a new
    `users` row → return its `id`.
  - `settings.require_auth` on and no/invalid token → **401**.
  - Not configured → today's `settings.local_user_id` (local dev unchanged).
- Relational: add `get_user_by_subject(provider, subject)` and reuse/extend user upsert. `single_user_mode`
  and `require_auth` are set true once Auth0 is configured (via env).
- **No other backend route changes** — ownership middleware, `owned_profile`, and `require_admin` already
  route through the seam.

### 4.2 Frontend
- `@auth0/auth0-react`: `Auth0Provider` (domain, clientId, audience, redirect) wraps the app in `main.tsx`.
- A login gate: when auth is required and the user is unauthenticated, show a Login screen
  (`loginWithRedirect` → Auth0 Universal Login).
- `api/client.ts::apiFetch` attaches `getAccessTokenSilently()` as `Authorization: Bearer`. The token
  getter is injected via a module-level setter wired from an `Auth0Provider`-aware component (so the
  non-hook `apiFetch` can reach it).
- A small user menu (email + logout) in the shell.
- If `VITE_AUTH0_DOMAIN` is unset, the SPA runs unauthenticated exactly as today.

## 5. Subsystem: Supabase Postgres store

- Refactor: extract the shared, already-portable SQL bodies of `DuckDBRelationalStore` into a
  **`_BaseSqlRelationalStore`** that calls four primitives — `_execute`, `_fetchone`, `_fetchall`,
  `_executemany` — plus a placeholder token. `DuckDBRelationalStore` and a new
  **`PostgresRelationalStore`** each supply those primitives (`?` vs `%s`, DDL/`ALTER … ADD COLUMN
  IF NOT EXISTS` quirks) and a connection. One copy of all ~50 business methods; the existing 613 tests
  exercise the shared bodies through the DuckDB subclass, guarding the refactor.
- `PostgresRelationalStore`: psycopg3 + `psycopg_pool.ConnectionPool`; preserve the existing re-entrant
  lock for multi-statement atomic sequences. DDL reuses the current `CREATE TABLE IF NOT EXISTS` strings
  (types are Postgres-valid).
- `make_relational_store`: `DATABASE_URL`/`SUPABASE_DB_URL` set → Postgres; else DuckDB (file/`:memory:`).
- No RLS; service connection; app-level tenancy unchanged.
- Optional `scripts/migrate_duckdb_to_postgres.py` — copy an existing local DuckDB into Postgres.

## 6. Subsystem: Supabase Storage

- New `SupabaseBlobStore` implementing the `BlobStore` Protocol against the Storage REST API
  (`{SUPABASE_URL}/storage/v1/object/{bucket}/{path}`, `Authorization: Bearer {service_key}` + `apikey`,
  via httpx) for uploaded resumes and tailored PDF/DOCX.
- `make_blob_store`: Supabase Storage configured → `SupabaseBlobStore`, else `LocalBlobStore`.
- The resume/tailored **download routes switch from `FileResponse(local path)` to streaming bytes via the
  blob seam** so they work against Supabase (the only route change). Local mode still streams from disk.

## 7. Config & env

New settings (all optional; absent = local behavior):
`AUTH0_DOMAIN`, `AUTH0_AUDIENCE`, `AUTH0_CLIENT_ID`; `DATABASE_URL` / `SUPABASE_DB_URL`;
`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_STORAGE_BUCKET`; `STORAGE_BACKEND`; frontend
`VITE_AUTH0_DOMAIN`, `VITE_AUTH0_CLIENT_ID`, `VITE_AUTH0_AUDIENCE`. Update `env.example`.

Dependencies: backend `pyjwt[crypto]` + `psycopg[binary,pool]`; frontend `@auth0/auth0-react`.

## 8. Testing

- Auth0: JWT verify against a mock JWKS + a locally-signed RS256 token (happy path, bad audience, bad
  issuer, expired); user resolution/auto-provision; `current_user_id` dev-vs-auth; `require_auth` → 401.
- Postgres store: the four primitives + a smoke over representative methods, **gated on `TEST_DATABASE_URL`**
  (skipped when absent so CI without Postgres still passes); shared bodies stay covered by the DuckDB suite.
- Storage: `SupabaseBlobStore` put/get/delete with httpx mocked.
- Frontend: `tsc --noEmit` + `vite build`; existing suite stays green on DuckDB.

## 9. Docs

New `docs/auth-and-hosting.md` (Auth0 + Supabase setup, env, the `.env` matrix, deploy notes) + updates to
`configuration.md`, `architecture.md` (auth + data-layer diagrams), `multi-tenancy.md` (the seam is now
wired — update the "exact spot" note), README, `docs/ROADMAP-CURRENT.md`, `PR_DESCRIPTION.md`, and the
`api/deps.py` docstring.

## 10. Verification

Unit tests + (if Docker present) a local-Postgres integration run. Live Auth0/Supabase needs the owner's
tenant/project creds; the code is config-driven and the docs give the exact Auth0 application + Supabase
project + `.env` steps to finish the live wiring. Owner runs git (assistant never commits).

## 11. Out of scope / YAGNI

- Supabase RLS policies (app-level tenancy is already leak-proof).
- Supabase Auth / social-login management UI beyond Auth0 Universal Login.
- Moving Weaviate off its current deployment.
- Child-mode / PIN sessions (Leelaa-specific; not applicable).
- Org/team multi-tenancy beyond per-user ownership.

## 12. Risks

- **Postgres dialect drift** from DuckDB (sequences, `ALTER ADD COLUMN`, timestamp tz). Mitigation: shared
  SQL is already portable; the base-class refactor keeps one copy; Postgres primitives tested on a real PG.
- **`apiFetch` token injection** in a non-React module. Mitigation: module-level setter wired once from a
  provider-aware effect; falls back to no-token in local mode.
- **Download routes** must stop assuming a local path. Mitigation: route them through the blob seam for
  both backends; local still streams from disk.
- **Connection/thread model**: background ingest thread + request handlers share the store. Mitigation:
  psycopg pool (thread-safe) + retain the existing lock for multi-statement atomic sequences.
