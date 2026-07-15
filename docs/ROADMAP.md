# JobScout — Roadmap & Backlog

Developer-facing roadmap. Priorities: **P0** = land now, **P1** = next, **P2/P3** = incremental.
**Not everything here is needed now — ship one item per PR**, and keep `main` green
(`pytest -q`, `ruff`, `mypy`).

## Project status & how to run
**Run locally** (from the repo root, Python ≥3.11):
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"   # first time only
(cd frontend && npm install)                                                    # first time only
cp env.example .env   # then fill GOOGLE_API_KEY / DEEPSEEK_API_KEY (+ ADZUNA_*); keep WEAVIATE_URL=http://localhost:8080
docker compose up -d  # local Weaviate on :8080
bash scripts/run.sh   # backend :8001 + frontend :5173  →  open http://localhost:5173
```
Stop with `bash scripts/stop.sh`; health-check with `bash scripts/health.sh`.

> **Gotcha — one backend at a time.** DuckDB is single-writer: a second backend hitting `jobscout.duckdb`
> fails with `Could not set lock on file`. Always launch via `scripts/run.sh` (it clears stale uvicorn and
> uses **:8001**, which the Vite proxy expects). Do **not** run `uvicorn …:8000` manually — wrong port for
> the proxy, and it collides with the running instance.

- **Build state:** local Weaviate, ~1,500 jobs indexed; backend `:8001`, frontend `:5173`.
- **Uncommitted work:** P0 fixes + P1 deep-match + early-career #1–#3 are implemented in the working tree but
  **not yet committed** (the local clone has pull-only access to the upstream, so contributing would go
  fork → branch → PR — deferred pending a repository-access decision).

## P0 — Pending fixes already in the working tree (commit these first)

| Change | File | Why |
|---|---|---|
| Visa stopword filter fix | `backend/jobscout/search.py` | `visa_sponsorship.not_equal("no")` returned HTTP 500 because `"no"` is a Weaviate stopword ("only stopwords provided"). Rewritten as the positive complement `equal(yes/unclear/not_mentioned)`. "Hide no-sponsorship" now works. |
| Vite proxy port | `frontend/vite.config.ts` | Proxy `:8000 → :8001` to match `run.sh`/`health.sh` (the repo already defaults the backend to 8001; the committed `vite.config.ts` lagged at 8000 → blank UI / "API error 500"). |
| Weaviate image bump | `docker-compose.yml` | `1.24.1 → 1.27.0`; `weaviate-client` ≥4.22 requires server ≥1.27. |
| Ignore key backups | `.gitignore` | Add `.env.*.original`. |
| Bulk company prober | `scripts/probe_companies_bulk.py` (new) | Imported from an older snapshot; complements `discover_companies.py`. |
| Workday enterprise seeds | `data/workday_enterprise_seeds.txt` (new) | Extra Workday tenants; feed via `scripts/probe_workday.py`. |

> Note: `ashby` was briefly disabled then **reverted to enabled** — it was *slow, not broken* (ingested 138
> jobs incl. Ramp). **Do not disable a whole adapter for slowness**; the HTTP client already has a 30s
> timeout + backoff (`adapters/base.py`). If one specific board misbehaves, blocklist that board only.

## P1 — Deep LLM match ("second opinion" verdict) — ✅ DONE
- **What:** on-demand DeepSeek verdict (apply / borderline / skip + score + strengths + gaps + summary) per
  job, layered over the deterministic `verdict.py`. Ported from an older snapshot and improved.
- **Shipped:** `backend/jobscout/deep_match.py` (`compute_deep_match`); `POST /api/match/deep/{job_id}`
  (body `{profile_id}`) in `api/main.py`; "✨ Deep match" button + result panel in
  `frontend/src/components/JobDetailPane.tsx` (+ `useDeepMatch` in `api/client.ts`, `DeepMatch` in
  `types.ts`); tests in `backend/tests/test_deep_match.py`.
- **Improvements over the original:** (1) DeepSeek call runs off the event loop via `run_in_threadpool`
  (no async-server stall); (2) bounded in-process cache keyed by `(job_id, profile_id)` returns repeats with
  `cached: true` (no re-billing). Graceful `borderline` fallback on any LLM error (never 500s).
- **Deferred:** persisting verdicts to DuckDB across restarts (in-process cache suffices for now).

## P2 — New source adapters (visa-relevant coverage)
None of these exist yet. Each is an isolated adapter following `backend/jobscout/adapters/base.py`
(`CompliantHttpClient`, `keyword_title_match`), registered in `services/source_config.py` + `sources.yaml`,
smoke-tested via `scripts/smoke_adapters.py`, and unit-tested per `backend/tests/test_new_adapters.py`.
- **Cap-exempt-dense (best for the F-1 → H-1B angle):** NSF & NIH job boards; USAJobs (research/data roles
  only — federal, so flag citizenship).
- **Startup-dense:** Wellfound, Built In, YC "Work at a Startup", Handshake, Simplify.jobs.
- **Effort:** ~½–1 day each. **Accept:** appears in `/api/stats` `by_source`; titles keyword-filtered;
  US-only filter respected.

## P2 — Local embedding fallback (remove Gemini quota ceiling)
- **What:** optional local BGE/MiniLM embeddings (sentence-transformers) so ingest/search don't depend on
  the Gemini free tier (1,000/day, shared with search + resume-match). Planned, not built.
- **Where:** `backend/jobscout/embed.py` — add a backend selectable via an `EMBED_BACKEND` env var; keep
  Gemini as default. Vector dimension must match the Weaviate collection (recreate the collection if the
  dim changes).
- **Effort:** medium. **Accept:** ingest works with `GOOGLE_API_KEY` unset; search + resume-match still work.

## P3 — Agentic search layer (LangGraph, optional Phase 4)
- **What:** `parse_intent → run_search → evaluate_results → adjust_filters (≤3 loops) → rank_and_summarize`.
  `deep_match.py` slots into the rank/evaluate step. Ingestion and enrichment stay linear.
- **Where:** new `backend/jobscout/agent/` package. **Effort:** large.
- **Accept:** a natural-language query yields a ranked shortlist; retries capped at 3.

## P3 — Resume keyword-gap analysis
- **What:** count a JD keyword as a real match only when the canonical resume supports it; surface
  unsupported keywords as explicit **gaps** (from the screening spec). Extends the embedding match in
  `resume.py`.
- **Effort:** medium. **Accept:** match output lists matched-skills vs gap-skills per job.

## P3 — Markdown shortlist export
- **What:** export the current filtered shortlist as a dated markdown table (append to a file).
- **Where:** `api/main.py` endpoint + an export button in the UI. **Effort:** small.

## Early-career / new-grad track (2026 pain points)
Targeted at fresh graduates (bachelor's/master's) and early-career seekers, especially F-1/OPT
international students. Grounded in current research (see Sources).

1. **Ghost / stale-job risk — ✅ DONE.** ~1 in 3 listings are stale/fake (2026). Each job carries a
   computed `ghost_risk` (low/medium/high) + `posting_age_days` (age-based: `medium` >30d, `high` >45d;
   unknown/estimated dates stay `low` to avoid false alarms), an amber "⚠ Possibly stale" badge, and a
   **"Hide likely-stale"** filter (`exclude_ghost` → drops `posted_date` older than 45d). `models.py`,
   `search.py`, `api/main.py`, `JobCard`/`JobDetailPane`/`FilterBar`; tests in `test_ghost_entry.py`.
2. **True-entry-level filter + mislabeled badge — ✅ DONE.** Two parts:
   - **"True entry-level"** filter (`true_entry_only`) — restricts to high-confidence entry roles:
     `yoe_min ≤ 2`, OR junior/intern when YoE is unknown, OR explicit new-grad programs; **excludes
     senior/staff/principal/lead/manager+ titles** even when they list a low `yoe_min` (new-grad programs
     override). Keys off `yoe_min` because `yoe_max` is almost always unenriched. (Live: 1496 → ~326.)
   - **Mislabeled badge** — computed `mislabeled_entry` (entry title/seniority + `yoe_min ≥ 3`) → rose
     "⚠ Titled junior · wants N+ yrs" badge for the rare title/requirement contradiction.
   Note: an earlier version mis-implemented the filter as "drop the contradiction" (matched ~0 → no-op);
   redefined to actually restrict to entry-level.
3. **New-grad / early-career program detector — ✅ DONE.** Stored, indexed boolean `new_grad_program`
   detected at ingest from title+description (`normalize.detect_new_grad_program`, no LLM); a 🎓 "New-grad
   program" badge + a **"New-grad programs"** filter (`new_grad_only`). Existing jobs backfilled in place via
   `scripts/backfill_new_grad.py` (17/1496 flagged on first pass — e.g. NVIDIA "New College Grad 2026").
   Files: `normalize.py`, `models.py`, `store.py` (+`_migrate_collection`), `search.py`, `api/main.py`,
   `JobCard`/`JobDetailPane`/`FilterBar`; tests in `test_new_grad.py`.
4. **Resume keyword-gap (ATS optimization) — ✅ ALREADY BUILT.** `verdict.py` `_skill_score` returns
   matched vs gap JD skills with the truthfulness rule (a keyword counts only if the resume supports it);
   `resume.py` extracts only resume-backed skills; JobCard renders matched/gap chips.
5. **STEM-OPT / E-Verify + cap-exempt weighting — ✅ ALREADY BUILT.** `EVerifyBadge` shows "required for the
   24-month STEM OPT extension"; cap-exempt is weighted in `verdict.priority_key`/`match_key`/`score`.

6. **Missing ATS adapters (less-contested boards) — planned.** Jobs on company ATS boards are far less
   contested than LinkedIn. We cover Ashby/Greenhouse/Lever/Workday/SmartRecruiters/Workable; **missing:
   iCIMS (`careers.icims.com`), Jobvite (`jobs.jobvite.com`), BambooHR (`jobs.bamboohr.com`), JazzHR
   (`apply.jazz.co`)** — add adapters following `adapters/base.py`. *(High value for the "not on LinkedIn"
   edge; each ~½–1 day.)*

### User tip (not a feature)
Find these boards manually with a search-engine dork: `site:jobs.ashbyhq.com ("data scientist" OR "ML
engineer")` — swap the host for any ATS above; boolean works. Startup roles often aren't posted publicly —
the a16z Speedrun talent network is a good external referral channel. (Guidance only; not integrable.)

### Sources (2026)
- Ghost jobs ~1 in 3: https://jobstrack.io/blog/ghost-jobs-2026 ·
  https://www.itpro.com/business/careers-and-training/ai-resume-screening-recruiter-chatbots-and-ghost-jobs-are-causing-havoc-for-struggling-entry-level-workers
- Entry-level paradox / AI screening: https://www.artech.com/blog/entry-level-tech-jobs-2026-challenges-solutions/
- OPT / STEM-OPT / H-1B ($100k fee, cap-exempt, E-Verify): https://www.internationalstudent.com/immigration/opt-stem-opt-h1b-2026/ ·
  https://h1bvisajobs.com/stem-opt-extension-2026-rules-strategies/

## Configurable backends — Settings panel — ✅ DONE (Phase 1)
A **⚙ Backends** Settings modal lets the user switch the vector store and enter API keys on the spot:
- **storage_mode** = `both` | `cloud` | `local`. **`both`** dual-writes each job to **local + cloud**
  (cloud best-effort, non-blocking) so switching never loses data; **`cloud`** auto-degrades to local if the
  cluster is unreachable; **`local`** is local-only. Auto-selects local when no cloud creds.
- `store.py` `WeaviateStore` holds a local (canonical) client + optional cloud **mirror**; `upsert`/
  `upsert_many` fan out best-effort; cloud-primary connect failure **degrades to local** (no fatal startup).
- API: `GET/PUT /api/settings` (key **presence** only is returned; secrets are written server-side to the
  gitignored `.env`, never to the browser) → reconnects the store immediately. `EVerifyBadge`-style health
  shown in the modal. **DuckDB data (company list, profiles, saved searches) is never affected by switching.**
- **Phase 2 (planned):** local BGE/MiniLM embeddings (`EMBED_BACKEND=local`, dim-locked → separate
  `Job_local` collection) for full Gemini-quota independence.

## Already built — do NOT rebuild
Progressive 6h→12h→18h→24h lookback ladder (`search.py` `PROGRESSIVE_LADDER`); applied-job dedup + shortlist
(My Jobs / Pipeline tracker, replaces a manual `applied_jobs.md`); cap-exempt-first source ordering;
recruiter/repost flag; the "visa not-mentioned ≠ reject" model; 20 source adapters; DeepSeek enrichment;
resume embedding match.

## Suggested sequence
P0 (commit now) → ~~P1 deep_match~~ (done) → P2 a couple of cap-exempt adapters (NSF/NIH/USAJobs) → P2 local
embeddings → P3 as desired. **One PR per item.**
