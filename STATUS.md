# JobScout — Honest Status Audit

> **Update (ingest efficiency — stop re-embedding duplicates):** "500 budget but index only grew +4" was a
> real bug: `_run_ingestion` ("Get latest jobs") reused enrichment for already-indexed jobs but **still
> re-embedded + re-upserted them**, burning the daily Gemini embed budget on the ~1,290 jobs you already
> have before reaching any new one. Fixed: it now **skips already-indexed enriched jobs entirely** (`continue`,
> mirroring `_refresh_watchlist`) — embeds are spent only on genuinely-new (or not-yet-`done`) jobs.
> Re-embedding was pure waste (deterministic vectors). Also raised "Get latest jobs" `results_wanted` 50→250
> so a run surfaces enough *unseen* jobs to actually use the budget (backfill). Net: a run now adds **up to
> ~500 new jobs** (ceiling = the budget, bounded by available unseen postings), not +4. Tests:
> existing→0 embeds (trip-wire), new→embedded. 305 tests, mypy 0, ruff clean, build green. (Live demo waits
> for the Gemini quota reset; today's is spent.)

> **Update (boot-resilient Weaviate connect):** A backend restart died with "Application startup failed"
> because `WeaviateStore.__init__` ran a synchronous gRPC health check that **transiently timed out** →
> nothing on :8000 → the Vite proxy 500'd → UI showed *"Error loading jobs: API error 500"* (a manual
> retry connected fine). Hardened `store.py`: connect now uses **`skip_init_checks=True`** + a 30s init
> timeout and **retries 3×** (2s backoff) before raising the existing friendly error. A transient/slow
> Weaviate no longer kills startup; a genuinely-down cluster still fails with a clear message after the
> retries. Queries are unchanged (skip_init_checks only skips the boot ping). Tests in
> `test_store_connect.py`. (Full offline independence remains the separate, deferred local-Weaviate track.)

> **Update (unified, self-clearing quota banner — both buttons):** Replaced the prior stale
> `sources/status.last_error` scrape with an app-level embedding-quota signal: `embed.py` sets a flag on a
> real 429 and **clears it on the next successful embed** (auto-recovers after the daily reset), surfaced as
> **`embed_quota_exhausted` on `/api/stats`**. Also made **"Get companies" (`_refresh_watchlist`)
> quota-aware** — it uses Gemini+DeepSeek just like "Get latest jobs" but previously **crashed silently** on
> a 429; now it stops cleanly. The UI banner (`TopNav` via new `useStats`, polled 15s) is now action-tied:
> quota-with-headroom → green "Fetch started" first, flips to amber only if a run actually 429s; quota
> already gone → amber up front; recovers → banner clears itself. Covers **both** buttons. Browser-verified
> live (flag flipped True within 15s of an ingest 429; amber banner shows, 0 console errors). 300 tests,
> mypy 0, ruff clean, build green.

> **Update (quota error now visible in the UI):** The backend recorded the embedding-quota stop in
> `/api/sources/status.last_error`, but the UI never showed it — and worse, "Get latest jobs" always
> displayed a green "fetch started, jobs will appear" banner even when the quota was exhausted and nothing
> could save (→ "looks broken"). Fixed in the frontend: `TopNav` now reads the already-polled
> `useSourcesStatus` (every 15s) and shows an **amber "⚠ Embedding quota reached — resumes after the daily
> reset; existing jobs unaffected"** banner, **suppressing the misleading green one** when a quota/budget
> stop is present. Also fixed the stale `SourceStatus` TS type (was `error_message`/`status`; real API is
> `last_error`/`last_run_status`/`last_ingested`). Browser-verified live (banner renders from the active
> greenhouse quota error, 0 console errors); `npm run build` green. Auto-clears after a clean run.

> **Update (quota-aware ingestion — "flat job count" fix):** Diagnosed the "jobs not saving / count stuck
> at 1,289" report: **not a save bug** — the Gemini free-tier embed quota (1,000/day) was exhausted, so new
> jobs couldn't be embedded → couldn't be stored (run logs showed every source `ingested=0, failed=N`).
> Found + fixed two real robustness gaps that made it silent: (1) `_run_ingestion` ("Get latest jobs") had
> **no embed-budget guard** (only "Get companies" did) → one run could blow the whole day's quota — now both
> cap at `settings.embed_daily_budget`; (2) the 429 was swallowed as a generic failure → now `embed.py`
> raises a typed `EmbeddingQuotaError`, ingestion **stops cleanly** and records a clear reason
> (`last_error` in `/api/sources/status`: "quota exhausted, resets daily" / "embed budget reached"). Tests
> in `test_ingest_quota.py`. The permanent fix for the 1,000/day ceiling = paid Gemini tier or the deferred
> local-embedding backend. **Immediate: the data isn't lost — new jobs ingest again after the daily quota
> reset.**

> **Update (Weaviate backup):** Jobs live only in Weaviate (cloud), so the Dropbox/folder copy didn't
> contain them. Added `scripts/export_weaviate.py` / `import_weaviate.py` (+ `backend/jobscout/backup.py`)
> that export the index **with vectors** (`include_vector=True`) to `data/weaviate_export.jsonl.gz` and
> restore it — **zero embedding calls, $0** (pure download, not a re-embed). Import refuses on a
> vector-dimension mismatch (can't mix embedding models). Opt-in `EXPORT_AFTER_INGEST` (default off)
> re-exports after each ingest. Verified live: exported **1,289 jobs / 3072-dim / 42 MB** with a trip-wire
> proving 0 embed calls; round-trip + guard unit-tested. 296 tests, mypy 0, ruff clean. (Local-embedding /
> key-free track deliberately deferred.)

> **Update (onboarding audit):** Verified a fresh user can run the tool (`env.example` complete, Weaviate
> `Job` collection auto-creates, local-Docker Weaviate fallback, deps resolve). Fixed: declared the
> previously-transitive-only **`pyyaml`** dependency; corrected README/doc drift — **5 tabs** (added the
> **Saved** searches tab), top bar now lists **Get companies** + the 🔔 bell, **18 adapters** (was 19),
> **293 tests** (was ~245), and a **First run** note (index starts empty → "Get latest jobs"; optional
> for-profit registry seeding). Flagged (not changed): `.env` + the `jobscout_BACKUP_*` folders contain
> real API keys — scrub before sharing.

> **Update ("Get companies" now covers cap-exempt):** Previously "Get latest jobs" pulled cap-exempt but
> "Get companies" (the watchlist refresh) only touched the 258 **for-profit** greenhouse/lever/ashby
> registry — zero cap-exempt, no Workday. Now: added `region`/`site` columns to the `companies` registry
> (for Workday's tenant connection); new `services/registry.py::register_cap_exempt_companies` projects the
> curated cap-exempt employers (Greenhouse/Workable nonprofits + verified Workday universities/AMCs) from
> `sources.yaml`(+discovered) into the registry at startup (idempotent; also `scripts/sync_cap_exempt_
> registry.py`); `_REFRESH_ADAPTER` + `_refresh_watchlist` gained Workday/Workable support (Workday rows
> carry region/site/name). So the **Companies tab now shows cap-exempt employers (violet "Cap-exempt"
> badge)** and **"Get companies" refreshes them, including Workday**. `sources.yaml` stays the single
> source of truth; the registry is a synced projection, so a `probe_workday.py` addition flows in on next
> boot. 293 tests (+5), mypy 0, ruff clean, build green.

> **Update (stale-data purge + more tenants + doc accuracy):** Purged the 11 pre-fix blank-company Workday
> jobs via `scripts/purge_workday_unnamed.py` (dry-run by default, `--yes` to delete; scoped to
> `source=workday` + null company — Weaviate's `is_none` matches the empty strings). They were duplicate
> orphans created because `company` is part of `job_id`'s hash, so stamping the tenant name minted new
> records. Added **7 more verified cap-exempt Workday tenants** (Penn State, Ohio State, Cleveland Clinic,
> Keck/USC, Kansas Health, Roswell Park, + uasys) — prober verified **18/20** (minnstate 422, MGB thin).
> **Doc accuracy pass:** de-staled `jobscout/CLAUDE.md` — it still claimed "Resume matching NOT yet built",
> "enrichment is unbuilt", "APScheduler ❌ Not built", "14 adapters / Workday enabled:false", all shipped
> long ago; now reflects 18 adapters + service layer + APScheduler(off-by-default) + cap-exempt sources +
> the keyword fix + the concurrency lock. Only LangGraph agentic search remains genuinely unbuilt.

> **Update (Workday cap-exempt prober):** Added `scripts/probe_workday.py` — a verifier for cap-exempt
> Workday tenants (universities / academic medical centers / nonprofit research, the H-1B cap-exempt
> classes that the slug-based `discover_companies.py` can't reach). It parses career-site URLs
> (`parse_workday_url`) from `data/workday_cap_exempt_seeds.txt`, probes the public CXS API via the
> shipping `WorkdayAdapter` (robots + rate-limit), reports live tenants with keyword-relevant roles, and
> `--write-sources` merges verified ones into `sources.discovered.yaml` (`_merge_discovered` now merges
> `tenants`). Seeded 13 real URLs (WebSearch-sourced) → **11 verified** → ingested → **cap-exempt 19 → 32**,
> incl. real on-target roles: UMD *ML Application Engineer* / *Graphics Software Engineer*, UW–Madison
> *Data Engineer* / *Data Scientist*, Cornell *Applied ML* — browser-confirmed, 0 console errors. Two
> adapter fixes shipped with it: (1) Workday's `searchText` near-phrase-matches, so multi-keyword ingest
> returned almost nothing — now queries **per-keyword + dedups + title-filters** (consistent with the
> other ATS); (2) Workday listings omit the employer, so jobs rendered blank — now each tenant carries a
> display `name` stamped as the company (also fixes the 4 existing tenants). 288 tests, mypy 0, ruff clean.
> Known leftover: ~11 pre-fix Workday jobs with blank company linger (company is part of the dedup hash, so
> named re-ingests are new records); a one-time purge needs an explicit OK (it was correctly blocked as an
> unrequested mass-delete). Backup: `jobscout_BACKUP_20260608-1008`.

> **Update (live UI acceptance — browser-confirmed):** Drove the real app (Playwright) through the core
> flows: Jobs list (881 jobs, sponsor/E-Verify/cap-exempt badges, dedup "+N posting" chip, detail panel,
> pagination), the cap-exempt filter (→ 13 real roles: mozilla Senior Data Engineer / Staff Data
> Scientist, khanacademy Data Analyst, Cornell, Braven, Univ of Colorado — across greenhouse/workday/
> workable/jobicy), and resume-drop → parse → **12 matched roles** + profile created. **0 console errors**
> on every page. Caught + fixed one real (minor) UI bug: the resume-upload mutation didn't invalidate the
> `['profiles']` React-Query cache, so a newly created profile didn't appear in the dropdown/list until a
> reload — fixed in `client.ts::useMatchResume` (verified live: profile now appears immediately). 279
> tests, mypy 0, ruff clean, frontend build green.

> **Update (service layer complete + concurrency fix):** Finished the `api/main.py` decomposition — the
> third service module `services/ingestion_service.py` (ingestion/enrichment/watchlist-refresh background
> jobs) was extracted via a reliable AST line-span carve. **`api/main.py` is now 808 LOC (from 1492)**;
> the service layer is complete: `source_config` + `query_service` + `ingestion_service`, with `main.py` =
> routes + app wiring. While live-smoke-testing, found + fixed a **pre-existing DuckDB concurrency bug**:
> the single `RelationalStore` connection is shared by the background ingestion thread and request
> handlers, and DuckDB connections aren't thread-safe → a concurrent write corrupted a reader's cursor
> (`KeyError` 500 on `/api/companies/refresh` when an ingest was mid-flight). Fixed with a re-entrant lock
> wrapping every public `RelationalStore` method (`_synchronized_methods` class decorator); verified with a
> 2-writer/3-reader stress test (0 errors) and live (concurrent ingest + refresh → all 200). 279 tests,
> mypy 0, ruff clean, build green. Backups: `jobscout_BACKUP_20260608-0401` (+ earlier).

> **Update (keyword-trap fix + cap-exempt curation):** Found & fixed a real ingestion bug — the 8
> per-company ATS adapters (greenhouse/lever/ashby/workable/recruitee/smartrecruiters/rippling/rss)
> filtered titles against the **joined** keyword phrase (`" ".join(keywords)`), so any *multi*-keyword
> search matched **nothing** (only single-keyword worked). Added `adapters.base.keyword_title_match`
> (ANY-keyword, case-insensitive) + tests; all 8 adapters now use it. This unblocks cap-exempt ingestion
> *and* improves every ATS source's yield. Cap-exempt boards curated + verified live (mozilla 39,
> khanacademy 27, givewell 14; dead `thebridgespangroup` removed; nonprofits moved FIRST in the
> greenhouse list so bounded ingests reach them first). `discover_companies.py` gained `--seeds`. Added an
> **ingestion smoke test** (fake adapter/enrich/embed). 279 tests, mypy 0, ruff clean. Backups:
> `jobscout_BACKUP_20260607-{1548,2224}`.

> **Update (service layer + ingest):** Decomposed `api/main.py` **1492 → 1195 LOC** by extracting two
> service modules — `services/source_config.py` (sources.yaml load/merge/override + adapter construction
> + source order/authority) and `services/query_service.py` (repost dedup, date-range parsing, resume
> match, semantic scoring, saved-search counting; `_match_resume_to_jobs` made stateless). Repointed
> `main.py` + tests + the conftest monkeypatch targets. Service layer established (routers → **services**
> → repositories); **274 tests green (incl. live smoke), mypy 0, ruff clean.** **Remaining (queued):**
> `ingestion_service` extraction (largest + least test-covered → deferred) + the multi-file router split.
> **Bounded ingest run** executed: aggregators ingested (adzuna +14) but the newly-enabled Workday/
> nonprofit boards returned 0 for the keywords used, so cap-exempt stayed at 3 — those boards need
> verified slugs / different queries (curated candidates 404-skip). Fresh backup:
> `jobscout_BACKUP_20260607-1548`.

> **Update (features + test net):** Added a real **API test net** (`backend/tests/conftest.py` fake
> Weaviate + in-memory DuckDB; `test_api_smoke.py`; route-registration test) — 274 tests, mypy clean.
> Shipped: **near-duplicate repost collapse** (`/api/jobs?dedupe=`, "+N more postings" chip),
> **application pipeline** (applied→OA→interview→offer→rejected + notes; `/api/profiles/{id}/pipeline`;
> Pipeline tab), **cap-exempt source expansion** (Workday university/hospital tenants enabled + nonprofit
> Greenhouse slugs + `data/cap_exempt_seeds.txt`). A **snapshot backup** (`jobscout_BACKUP_*`) is the
> rollback (no git, per user). **Still deferred:** the `api/main.py` router/service decomposition — now
> *safe to do* (the test net exists); queued as the next focused task.

> **Update (quality pass):** Architecture audit done — app follows Adapter + Repository patterns,
> Pydantic schemas, pydantic-settings, pure testable core. Shipped: **Saved Searches + "new since last
> visit" alerts** (pull→push: `saved_searches` table, `/api/saved-searches` CRUD with live `new_count`,
> `ingested_after` filter, UI Saved tab + 🔔 badge + "★ Save search"); **mypy 312 → 0** in
> `backend/jobscout` (pragmatic `[tool.mypy]` config + core type fixes); route-registration safety-net
> test. **Deferred (deliberately):** decomposing the 1358-LOC `api/main.py` into routers+services — it's
> a behavior-preserving refactor best done in a focused session *after* adding API integration tests, to
> honor "nothing breaks". The service layer is the one remaining structural debt.

> **Update (Dropbox parity pass):** Ported the last Dropbox-only bits so our version fully supersedes the
> Dropbox snapshot: `fix_mojibake`+`ftfy` (unicode repair) + `normalize_title`/`normalize_employment_type`
> (better dedup) in `normalize.py`; `store.purge_older_than` + `POST /api/maintenance/purge`; Workable
> `?details=true` restored; ~12 curated US company slugs added to `sources.yaml` (Workable/Recruitee/Ashby
> enabled). JobSpy (Indeed/Glassdoor) kept but behind a **default-off Settings toggle**
> (`/api/sources/overrides`). Also fixed the Work-authorization toggles to **OR** positive sponsorship
> signals. **Our tree is now a strict superset of the Dropbox copy on every axis.**

> **Update (prior session):** Shipped the shortlisting workflow + UX gaps + full docs.
> New backend: `/api/jobs/by-state` (Shortlist/Applied), `/api/scheduler` (daily auto-refresh, **OFF by
> default**), `relational.get_job_state_ids`. New frontend tabs: **Shortlist, Applied, Profiles**, an
> **active-profile selector**, per-job **Apply/Save/Hide** + verdict/matched/gap chips, a **Get latest
> jobs** button, and a **Settings** scheduler toggle. New docs: rewritten `README.md` + `docs/`
> (architecture, user-guide, data-and-storage, api, sources, configuration) with Mermaid + textual
> fallbacks. Resume upload (`/api/match/upload`), sponsorship intelligence, company registry, and the 19
> adapters from prior sessions remain in place.

---


_Last verified: 2026-06-04, via live API calls (`scripts/smoke_adapters.py`) — not assumptions._

This answers: **how much of this tool actually works, and how much is done correctly?** Confidence
levels are explicit. "Verified live" = I hit the real API this session and saw real jobs. "Tested" =
unit tests pass. A hard lesson is recorded below: **passing unit tests are NOT proof an adapter works**
— two adapters (Workable, Workday) had green tests written against *fabricated* response shapes and
were silently broken against the real APIs until live verification caught it.

## Sources / adapters

| Adapter | Type | Live status (this session) | Notes |
|---|---|---|---|
| **Greenhouse** | ATS (per-company) | ✅ Verified live | givewell→4, anthropic→369 jobs |
| **Lever** | ATS (per-company) | ✅ Verified live | spotify→6+ US jobs |
| **Ashby** | ATS (per-company) | ✅ Verified live | ramp→111 jobs (newly built) |
| **Rippling** | ATS (per-company) | ✅ Verified live | tavernresearch→16 jobs |
| **Workable** | ATS (per-company) | ✅ Verified live (after bug fix) | braven→60 jobs. **Was broken** — adapter assumed a nested `location` dict; real API uses top-level `country`/`city` + `locations[]`. Fixed. |
| **Workday** | ATS (per-company, POST) | ✅ Verified live (after bug fix) | cornell→158 jobs. **Was broken** — bare campus `locationsText` ("Ithaca (Main Campus)") has no US token → all dropped. Now stamps `country: us` for curated US tenants. |
| **Remotive** | Aggregator | ✅ Verified live | US remote tech |
| **RemoteOK** | Aggregator | ✅ Verified live | |
| **WorkingNomads** | Aggregator | ✅ Verified live | |
| **TheMuse** | Aggregator | ✅ Verified live | |
| **Jobicy** | Aggregator | ✅ Verified live | 20 raw → 7 US-eligible |
| **Arbeitnow** | Aggregator | ⚠️ Works but **EU-only** | 20 raw → **0 US-eligible**. Low value for a US-only product. |
| **RSS (HigherEdJobs)** | RSS | ❌ Dead-end for us | HigherEdJobs serves an **empty feed to our honest `JobScoutBot` UA** (0 entries); won't spoof UA (compliance). Adapter code works on feeds that serve all clients. |
| **Adzuna** | Aggregator (keyed) | ⏳ Not tested here | Needs `ADZUNA_APP_ID/KEY`; not exercised this session |
| **JobSpy** | Scraper | ⏳ Off by default | High-risk; `enabled: false` |

**Bottom line on sourcing:** 11 of 14 adapters are live-verified returning real US jobs. RSS is a
dead-end as configured, Arbeitnow is EU-only, Adzuna/JobSpy untested this session.

## Company discovery (new)

| Piece | Status |
|---|---|
| `scripts/discover_companies.py` | ✅ Working — probes Greenhouse/Lever/Ashby (Workable/Rippling opt-in; Workable 429s on bulk) from a seed list, ranks by relevant-open-roles, writes `data/discovered_companies.csv` |
| `data/company_seeds.txt` | ✅ ~390 candidate startup slugs (extensible) |
| **Run result** | ✅ **168 verified actively-hiring companies** (90 Ashby, 68 Greenhouse, 10 Lever); 48 are "small" (≤10 open roles) — the best CEO/founder reach-out targets |
| Hit rate | ~45% of well-known slugs resolve to a live public board with relevant roles |

**Important honesty:** discovery yields a **company reach-out list** (name, ATS, careers URL, open-role
counts, sample role). It does **NOT** find or store any person's contact info (CEO/recruiter email) —
that is a manual LinkedIn step (PII boundary, `compliance.yaml collect_personal_data: false`).
"Small company" is approximated by open-role count at discovery time; true headcount is refined by the
DeepSeek `company_size` enrichment once a company is ingested.

## Backend pipeline — VERIFIED END-TO-END THIS SESSION ✅

Ran a real bounded ingest (14 Ramp/Ashby jobs) → DeepSeek enrichment → Gemini embeddings → Weaviate
Cloud → `exp=entry` query. **The full pipeline works.** (Earlier worry that the Gemini key was invalid
was WRONG — Google AI Studio now issues `AQ.…`-format keys, and it embeds fine at 3072 dims.)

| Component | Status |
|---|---|
| Normalization + dedup (`normalize.py`) | ✅ Tested + exercised live |
| `CompliantHttpClient` (robots/rate-limit/backoff, +POST) | ✅ Tested + exercised live |
| Verdict engine (`verdict.py`, Apply/Flag/Reject) | ✅ Unit-tested (pure function, high coverage) |
| User profiles + DuckDB state | ✅ Built + tested |
| **Embeddings (Gemini `gemini-embedding-001`)** | ✅ **Verified live** — 3072-dim vectors |
| **DeepSeek enrichment** | ✅ **Verified live** — correctly pulled yoe_min/max, seniority, visa (e.g. Security Engineer→yoe 5/senior, Mobile Engineer→yoe 1/mid) |
| **Weaviate Cloud store + search** | ✅ **Verified live** — upsert + filtered query worked |
| **True 0–2yr (`exp=entry`) YoE filtering** | ✅ **Works** — enriched senior roles correctly excluded |

**Bug found + fixed this session:** `store.py::_props_to_job` used `props.get(key, default)`, but
Weaviate returns objects migrated before the new enrichment fields existed with the key PRESENT and
value `None`, so the default never applied → Pydantic crashed reading them back. This broke `/api/jobs`
for any pre-existing job in the Weaviate Cloud index. Fixed with `props.get(key) or default`.

**Operational limit found live (important):** the Gemini embedding key is on the **free tier =
1,000 embeddings/day**. A ~300-job ingest hit the daily cap partway (error:
`embed_content_free_tier_requests, limit: 1000`). Every ingested job embeds once; a text **search**
also embeds the query. So: bulk ingest and text search are quota-bound. Mitigations — upgrade the
Gemini tier, or build the planned local BGE/MiniLM embedding fallback (CLAUDE.md). **Filter-only
browsing** (`/api/jobs?exp=entry&source=ashby&...` with no `q`) does NOT embed, so the UI works fine
within quota; only free-text search is blocked once the daily cap is hit.

**Behavior worth knowing (not a bug):** the `exp=entry` band is `yoe_min IS NULL OR yoe_min ≤ 2` — it
deliberately **includes un-enriched (null-YoE) jobs** so genuine entry roles aren't missed. Consequence:
on an index with many un-enriched jobs, `exp=entry` is noisy (a "Principal Data Scientist" with null YoE
will appear). It is precise for *enriched* jobs. Mitigation: enrich the index (`POST /api/enrich/run`)
or add `enrichment_status=done` to the query.

## What "working" means at each layer (so expectations are calibrated)
1. **Adapters** find jobs by **title keyword** + curated company slugs. No YoE awareness.
2. **Enrichment** (DeepSeek) reads each description → `yoe_min/max`, visa, skills, clearance, employer_type.
3. **Search/verdict** then filters `exp=entry` (`yoe_min ≤ 2`), sorts cap-exempt-first, excludes applied.
   Layers 2–3 require the backend + a valid Gemini key, which is the one open blocker.

## Tests
- 24 adapter-focused tests + full suite (~182) pass. **Caveat now corrected:** Workable/Workday tests
  were rewritten to the real API shapes after the live bugs; the lesson is to seed unit tests from
  captured real responses, which the smoke script makes easy to do.
