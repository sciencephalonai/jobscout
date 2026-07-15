# Current roadmap (living checklist — update status as work lands)

> Purpose: survives any assistant-session loss. Historical plans: `../IMPROVEMENT_PLAN.md`,
> `../PARALLEL_INGEST_PLAN.md`. Update the Status column in the same change that lands the work.

## In-flight round (structured profile + UX + health) — 2026-07-13

| # | Item | Status |
|---|------|--------|
| 1 | Backend structured resume: typed models (JSON-Resume-aligned), `parse_structured_resume`, `compose_resume_text_from_structured`, PUT sync, `POST /profiles/{id}/structure`, `POST /profiles/{id}/polish`, tests | ✅ done |
| 2 | Frontend structured section cards: Education grouped per school, Experience per role (dates+duration+bullets), Projects (tech pills + links), Certifications, Skills as per-category pills, custom sections; master-detail edit modals | ✅ done |
| 3 | AI bullet polish with per-bullet diff + accept/reject in Experience/Projects editors (1 LLM call per use) | ✅ done |
| 4 | First-run UX: For You onboarding hero (3 steps + upload CTA), Discover empty-DB guidance | ✅ done |
| 5 | LLM key auto-fallback (`LLM_PROVIDER=nvidia` with no NVIDIA key → DeepSeek) + `GET /api/health` + AppShell banner with exact fix instructions + run.sh preflight wording | ✅ done |
| 6 | Docs pass: user-guide (structured editing, polish, onboarding, tailor gate), architecture (structured data flow), configuration/README key matrix, api.md new endpoints | ✅ done |
| 7 | CONTRIBUTING.md: enforced documentation & code-style rules | ✅ done |

## Round 2 (taxonomy, performance, intuitiveness, backlog) — 2026-07-13

| # | Item | Status |
|---|------|--------|
| 1 | Resume taxonomy: first-class **Publications** + **Achievements** entries (papers/DOI vs awards vs licenses vs custom), routing rules in the parse prompt, new cards + editors | ✅ done |
| 2 | **Latency**: verdict memo cache (+ invalidation on ingest), semantic-score TTL cache, `_same_skill`/`canonicalize`/`_plain_text` memoization, eligibility whole-text fast path, facet skip on candidate window → **cold 22.8s → 3.0s, warm 6.3s → 0.5-1.1s** | ✅ done |
| 3 | Matching accuracy: umbrella-skill implications (computer vision/RL/transfer learning/ETL/MLOps…), 18 new aliases | ✅ done |
| 4 | Intuitiveness: "How JobScout works" help modal (loop, tabs, badges, buttons, privacy), Discover-without-profile guidance strip | ✅ done |
| 5 | Backlog: applied-jobs importer (markdown table → mark applied), recommendation ceiling 200 → 500, +10 live-probed sponsor-heavy boards | ✅ done |
| 6 | **LLM failover for ALL tasks** (was enrichment-only): shared `chat_json` — deep match, resume parse/structure, polish, tailoring now fall back NVIDIA→DeepSeek on 429/missing key | ✅ done |
| 7 | Deferred: "new since last visit" pill, auto deep-match top-N (opt-in), precomputed feeds | → resolved in Round 3 (pill ✅ shipped; auto deep-match + precomputed feeds ❌ deliberate won't-do) |

## Round 3 (resume library, seed, UX polish, backlog) — 2026-07-13

| # | Item | Status |
|---|------|--------|
| 1 | **Action clarity**: sidebar primary button explains itself — "Get latest jobs" (no profile, generic keywords) vs "Find profile matches" (targeted + gated; fixed label so a long profile name never overflows the sidebar), with helper copy in sidebar, mobile drawer, and Help modal | ✅ done |
| 2 | **Text-overflow policy**: 3-way rule (wrap & grow / truncate-with-reveal / nowrap) in `CONTRIBUTING.md`; section headers wrap instead of clipping; `Bullets` no longer caps the user's own resume; `title=` reveals added to legitimate chrome truncations | ✅ done |
| 3 | **Resume library**: many resumes per profile, one Active drives matching. `resumes` table + `ResumeRecord`; upload/list/activate/rename/download/delete endpoints; lazy adoption of pre-library uploads; Profile "Resumes" card; 6 tests | ✅ done |
| 4 | **Tailored-resume library**: `tailored_resumes` catalog upserted on each build; `GET …/tailored` list; dated download filename (no ~/Downloads collisions); Profile "Tailored resumes" card | ✅ done |
| 5 | **First-run seed (live, not committed)**: bounded one-time background ingest over keyless sources when the index is empty + embedding key present + unseeded; `seeded_at` marker; `health.seeding` + AppShell banner; 8 tests | ✅ done |
| 6 | **Retention**: `prune_stale_jobs` at end of each ingest (reuses `purge_older_than`), `retention_days=60` (> `GHOST_STALE_DAYS=45`, so a job is ghost-flagged before removal) — freshness stays a live window | ✅ done |
| 7 | **New since last visit**: For You pill counting jobs ingested after the last visit (localStorage — local single-user tool, no backend write per view); toggles a "new only" view | ✅ done |
| 8 | Docs + diagrams (this round): resume-library lifecycle, tailoring gate, first-run seed; api/data-and-storage/user-guide/architecture updates | ✅ done |
| 9 | **Deep-match top N button** (For You): one click batches the existing single deep-match over the top 10 with a 3-wide client pool; results cached by `['deep', job, profile]` and shown as AI-verdict badges on cards + in the detail pane. Reuses server-side deep cache (re-runs free). No new backend route | ✅ done |
| 10 | **Progressive batches + AI re-rank** (For You): each click analyzes the next 10 un-analyzed matches (button shows how many are left) and **tiered-re-ranks** the whole analyzed set (apply → borderline → un-analyzed → skip, AI score within tiers) on completion; "Ranked by AI ✕" chip reverts to server order. `useDeepResults` (`useQueries`) is the reactive single source of truth. Badges cross into Discover; trigger stays For-You-only. Client-only | ✅ done |
| 11 | **Rename reliability + name sync + audit fixes**: profile rename pins the viewed profile (label re-sort could flip the panel to a duplicate-label look-alike → "rename didn't work"); Enter+blur double-commit guarded; rename errors surfaced inline; default-ON "Also rename active resume" checkbox syncs names (extension preserved); resume download restores the real suffix after a display rename; profile delete removes its `data/resumes/{id}/` dir; GET→PUT round trips can no longer recompose (corrupt) the lossless `resume_text` | ✅ done |
| 12 | **Toggle fix + auto-dedupe + AI suggest**: toggle knobs anchored (`left-0` missing in ToggleRow + FilterBar → knob rendered past the track); every name-creation/rename point auto-suffixes collisions ("name (2)") with an inline "saved as" note; `POST /profiles/{id}/suggest` — add-only AI suggestions for interests / avoid role types / avoid domains / target roles / skills, surfaced as accept-chips in the tag editors. Bullet polish stays whole-entry with per-bullet accept (per-bullet buttons = N AI calls + clutter — considered, rejected) | ✅ done |
| 13 | **Tailoring 429 → shared failover**: the resume planner (`tailor.py`) was the ONE LLM call still hitting the provider directly (no 429 failover/circuit breaker); routed through `enrich.chat_json` so it inherits NVIDIA→DeepSeek fallback like every other task. Also: profile-switcher redesigned as a proper anchored dropdown control (Build 11 regression). Requires a second provider key for the fallback to engage | ✅ done |
| 14 | **Per-job deep/tailor correctness + persistence**: fixed detail-pane cross-job state leak (`key={jobId}` — was showing the last job's deep/tailor result for EVERY job); deep-match now **persisted** to DuckDB (`deep_match_cache`, fingerprint-keyed) so a score survives restart, is never re-billed, and auto-clears when the resume/profile changes; `POST /deep-results` rehydrates badges on load with no spend; profile mutations drop the `['deep']` cache; detail pane surfaces an already-built tailored DOCX ("Download · built {date}"). One profile ↔ multiple resumes confirmed as the model (public signup would add a user-account layer — future) | ✅ done |
| 15 | **Deep/tailor detail-pane polish**: Deep-match & Tailor buttons **disable when a current result exists** ("Deep-matched ✓" / "Tailored ✓") and auto-re-enable ("Re-tailor") when the resume/profile changes (`TailoredResumeRecord` gains `filename` + `fingerprint`; `/tailored` returns `up_to_date`); tailored download line shows the filename; deep result shows a "re-run only if your resume changes" hint; resume uploads/deletes now `setQueryData` for **instant** UI (no reload — the upload's LLM parse made invalidate-only feel stale); HelpModal explains profile vs resume vs tailored | ✅ done |

## Round 4 (tailored-filename, which-profile-to-tailor, multi-tenancy seam) — 2026-07-14

| # | Item | Status |
|---|------|--------|
| 1 | **Tailored filename = single source of truth + editable** (Build 19): download now reads the STORED `record.filename` (was recomputing + ignoring renames); new `PATCH /profiles/{id}/tailored/{job_id}` (auto-`.docx`, sibling-dedup); inline rename in the Tailored resumes card (pencil → input → one-commit guard + "saved as" note). Architecture kept: profiles → resumes (one active) → tailored per (profile, job) | ✅ done |
| 2 | **"Which profile do I tailor with?"** (Build 20): new `GET /jobs/{job_id}/profile-fits` (deterministic `score_verdict`, **no LLM**) → each profile's fit %; detail pane gains a **"Tailor as [Profile ▾]"** selector defaulting to the **best-fit** profile for THIS job (hidden with <2 profiles), routing both the tailor action and the already-tailored lookup through the choice; a subtle **better-fit nudge** ("X fits this job better · Set active") when another profile beats the active one by ≥10 pts. HelpModal answers it | ✅ done |
| 3 | **Multi-tenancy seam — leak-proof by construction, no auth yet** (Build 21): `user_id` on `UserProfile`/`SavedSearch` (legacy empty → local user, stamped on upsert); `api/deps.py` (`current_user_id` = the ONE auth drop-in, `effective_owner`, `require_admin`); a single `enforce_profile_ownership` HTTP middleware guards all 24 profile-scoped routes with **404 (not 403)** so the whole IDOR class is impossible; list endpoints + saved-search seen/delete scoped to the caller; global-write routes (`PUT /settings`, `/maintenance/*`, `/scheduler`, `/sources/overrides`) admin-gated (open locally, 403 when hosting). New `docs/multi-tenancy.md` (data split diagram, leak table, Postgres/quota notes). 14 tenancy tests | ✅ done |

### Deliberate won't-do (with reasons)
- **User-level "For You" (union across all profiles)** — ❌ dropped. The recommended model is **one
  profile + many resumes**, so a cross-profile union feed is unnecessary. Multi-profile capability stays
  in the code; For You remains scoped to the active profile, Discover shows all roles.
- **Unify tailoring → the editable profile sections** — ⏸ deferred (not won't-do). Today matching/
  deep-match read the editable profile; tailoring builds from the toolkit's verified `canonical.json` with
  a no-fabrication audit. Making tailoring read the editable (AI-polishable) profile would trade away that
  audit, so it's a future decision, not a current change.
- **Committed job seed ("ship the past 7 days")** — ❌. Jobs are the most perishable data in the app; a
  committed snapshot is stale on the next clone (the exact "ghost risk" the product warns about) and
  saves no setup cost (every job still needs a Gemini embedding). The live first-run seed + retention
  keep the window fresh instead. Zero-quota local dev/demo is served by the existing
  `jobscout.backup` export/import (a real index with vectors), so no separate dev-seed fixture was added.
- **Auto deep-match of For You top N** — shipped as a **manual button** instead (Round 3 #9). Auto-on-
  every-load silently bills LLM quota per page view; a button gives the same "analyze my top matches in
  one click" value while leaving the *when* to the user. Not built as an always-on toggle.
- **Precomputed feeds / raising the ceiling** — ❌ unnecessary. For You is 0.5–3s with a 500 ceiling; a
  precompute layer would add cache-invalidation complexity for no user-visible gain.
- **Handshake / Wellfound / BuiltIn sources** — ❌ not viable (school SSO / anti-bot / no public API).
  The compliant lever is more curated ATS boards (10 added last round).
- **`users` table + OAuth now** — ❌ deferred (YAGNI). Without an identity provider nothing reads a users
  table, so it's speculative. The tenancy *seam* (Round 4 #3) makes leakage impossible today; the users
  table + provider get built together when login is wired — `docs/multi-tenancy.md` marks the exact spot.
- **Postgres migration** — ❌ not now. DuckDB (embedded, single-writer) is right for the single local
  user; a real multi-user deployment moves the relational store to Postgres. Documented, not built.

## Key requirements (confirmed 2026-07-13)
- **REQUIRED**: `DEEPSEEK_API_KEY` (all LLM tasks) + `GOOGLE_API_KEY` (embeddings — no DeepSeek
  fallback exists; the vector index is dimension-locked to Gemini).
- **OPTIONAL**: `NVIDIA_API_KEY` (free-tier LLM primary; auto-falls back to DeepSeek on 429 or
  missing key), Adzuna, USAJobs, Weaviate Cloud.

## Backlog (agreed, not scheduled)
- Applied-jobs importer / auto-hide; daily new-matches digest; auto deep-match of For You top N;
  more sources (Handshake/BuiltIn/Wellfound); semantic-score caching if For You latency regresses;
  raise the 200-recommendation ceiling via precomputed feeds; entry drag-reorder in structured
  editors; PR to github.com/sciencephalonai/jobscout when owner says go (never via assistant).
