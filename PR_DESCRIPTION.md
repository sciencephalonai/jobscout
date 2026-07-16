# PR: Personalized job-search engine — For You feed, structured profiles, multi-tenant-ready core & operator console

> Summary of everything since the fork point.

## Headline features
- **For You feed**: profile-driven recommendations — deterministic verdict engine (visa/citizenship/
  clearance/defense-ITAR/seniority/YoE/role-family hard gates + weighted fit incl. resume-embedding
  similarity), auto-refill from direct sources when sparse, up to 200 results, Best-match/Newest sort,
  user-pickable freshness window. Loads in ~3-12s (was 20s+).
- **Structured profiles**: resumes parse into typed, editable sections (education per school,
  experience per role, projects with tech pills, per-category skill pills, custom sections) with
  master-detail editors; edits recompose the canonical text and re-embed matching automatically.
  Raw-text escape hatch retained. **AI bullet polish** with per-bullet diff accept/reject
  (truthfulness-constrained).
- **Tailor pre-flight gate**: resume tailoring first runs verdict + deep match; skip conclusions
  block the build (override with "Tailor anyway").
- **Match-quality gates**: explicit sponsorship-refusal phrasings; defense/weapons domain + contractor
  list (near-certain US-person walls); enterprise-platform (Oracle HCM/SAP/etc.) specialist roles need
  resume evidence; umbrella skills (pytorch ⇒ "machine learning") no longer read as gaps.
- **Sources**: SimplifyJobs new-grad feed (curated, sponsorship-labeled, ATS descriptions resolved),
  USAJobs (opt-in), parallel source fetching (sweeps ~5x faster), US-only enforcement hardened
  (Workday multi-location, title-city, ISO-code leaks fixed + purged).
- **Resume library**: many resumes per profile, one **active** drives matching; upload/activate/rename/
  download/delete, lazy adoption of pre-library uploads, re-upload never overwrites. Activation projects
  the resume onto the profile so no matching code changes.
- **Tailored-resume library**: every built DOCX is catalogued and downloadable anytime from the Profile
  tab with a dated filename (no ~/Downloads collisions).
- **First-run seed + retention**: fresh deployments self-fill via ONE bounded, live background ingest
  (keyless sources) when empty — never a committed stale snapshot; `retention_days` keeps a rolling
  recent window past the ghost-risk threshold. `health.seeding` + banner.
- **UX**: context-aware primary button ("Get latest jobs" vs "Find profile matches") with helper
  copy; "new since last visit" pill on For You; **Deep-match top 10** with progressive next-10 batches,
  AI-verdict badges on cards + detail pane, and tiered AI re-rank (revert chip); inline profile/resume
  rename with name sync + collision auto-numbering ("name (2)") at every creation/rename point;
  **add-only AI suggestions** for interests / avoid-lists / targets / skills; toggle-knob and
  modal-focus-steal bugs fixed; text-overflow policy documented in CONTRIBUTING.md.
- **Resilience/DX**: NVIDIA→DeepSeek auto-fallback (429 circuit breaker + missing-key), `GET
  /api/health` + in-app setup banner with exact fixes, first-run onboarding, Weaviate pagination
  workaround (upstream bug), docs with mermaid diagrams, `docs/ROADMAP-CURRENT.md` living plan.

## Multi-tenant-ready core, operator console & hardening (latest round)
- **Swap seams** — single-user-perfect today, multi-tenant-ready by construction: DB (`RelationalStore`
  Protocol + `DuckDBRelationalStore` + `make_relational_store` factory, Postgres-portable SQL), file
  storage (`BlobStore` + `LocalBlobStore`), auth (`current_user_id` — the one drop-in), authz
  (`owned_profile`). Postgres / S3 / real auth become localized swaps, not rewrites.
- **Multi-tenant schema (additive; single-user behavior unchanged)**: `users` (email/display_name/
  auth_provider/`plan`/`limits_json`/`is_admin`), first-class indexed `user_id` on `user_profiles` +
  `saved_searches`, `usage_counters` ledger. Legacy rows backfill to a seeded local user.
- **Security — IDOR class closed**: an `enforce_profile_ownership` middleware guards every
  `/api/profiles/{id}/…` path (404, not 403), and `owned_profile` guards the query/body routes it can't
  see (`/api/jobs`, `/jobs/by-state`, `/match/deep`, `/match`, `attach-resume` **source**, `/search/run`)
  — a foreign profile 404s, never leaks. CORS made spec-valid + config-driven; startup stale-run reaper.
- **Dormant guard rails (OFF by default, one flag each; see `docs/pre-deployment-checklist.md`)**: rate
  limiting, upload size/type limits, request-body cap, security headers, per-account usage quotas,
  `require_auth` gate. Default behavior stays byte-identical to today.
- **Entitlements + operator console**: `resolve_limits`/`record_usage`/`check_quota` with **metering split
  from enforcement** (monitor per-account usage without capping); `/api/admin/*` (list users, grant/revoke
  premium, per-user usage + storage, deployment metrics) + `/api/users/me` + a frontend **Admin** tab
  (admin-only); `GET /api/users/me/export` + `DELETE /api/users/me/data` (right-to-access / erasure).
- **UX polish**: no outer page scrollbar (global scroll lock); fixed-height, non-resizing modals;
  single-scroll "How JobScout works"; deterministic "Tailor as" row with an always-available "Set active";
  deep-match now runs under the profile you tailor with.
- **Ops/quality**: `scripts/check.sh` one-shot gate + GitHub Actions CI, app `Dockerfile`, central logging
  config, pinned `requirements-lock.txt`.
- **Docs**: new `docs/multi-tenancy.md` (global-vs-private data-split diagram + leak table + auth/DB
  drop-in points), `docs/pre-deployment-checklist.md`, `CONTRIBUTING.md`, `docs/HANDOFF-PLAN.md`;
  `architecture.md` / `data-and-storage.md` / `api.md` / `README.md` updated.

## Key requirements
REQUIRED: `GOOGLE_API_KEY` (embeddings), `DEEPSEEK_API_KEY` (or NVIDIA). Optional: NVIDIA (free-tier
primary), Adzuna, USAJobs, Weaviate Cloud. See docs/configuration.md.

## Performance
For You: **cold 22.8s → 3.0s, warm 6.3s → 0.5–1.1s** (verdict memoization + invalidation, semantic-score
TTL cache, skill/regex memoization, eligibility fast path, facet skipping). Recommendation ceiling 500.

## Quality
- **576 backend tests passing** (incl. tenancy/IDOR, entitlements, guard-rail, admin, and seam tests);
  ruff + mypy clean; frontend tsc + vite build clean; `scripts/check.sh` runs the whole gate.
- No personal data in the repo: profiles/resumes/DuckDB/`.env` are gitignored (never committed);
  fixtures + docs use generic placeholders.
