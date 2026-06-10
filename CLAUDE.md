# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

JobScout is a multi-portal job aggregation and filtering engine. It ingests listings from structured APIs (Adzuna, USAJOBS, Remotive, Greenhouse, Lever) and optional scraper libraries (python-jobspy), normalizes them into a canonical schema, enriches them via DeepSeek LLM extraction, embeds them into Weaviate for hybrid search, and exposes a FastAPI + React frontend. Full spec: `JobScout_SPEC.md`.

> The spec describes the full target system. This `## Project Overview` and `## Stack` describe that target. **What is actually built today** is narrower — see `## Implementation Status` before assuming a component exists.

## Implementation Status

This repo is at roughly **Phase 3** (enrichment, resume matching, most adapters, source-status dashboard) **plus a user-profile + deterministic verdict/scoring layer**. The remaining gaps (do not assume they exist):

| Area | State | Notes |
|---|---|---|
| `CompliantHttpClient` + adapter protocol | ✅ Built | `adapters/base.py` |
| Adapters | ✅ 18 built | Adzuna, Remotive, Arbeitnow, Jobicy, RemoteOK, WorkingNomads, TheMuse, Greenhouse, Lever, **Ashby**, **Workable**, **Workday** (CXS POST API), **Rippling**, **Recruitee**, **SmartRecruiters**, **RSS**, **JobrightAI**, JobSpy. Workable/Workday/Rippling/RSS/Recruitee/SmartRecruiters now **enabled** (smoke-tested). JobSpy + JobrightAI off by default (runtime-togglable). USAJOBS deliberately skipped (federal roles need citizenship). **Per-company ATS title filter uses `keyword_title_match` (ANY keyword) — fixed a multi-keyword "joined-phrase" bug that returned nothing.** |
| Normalization + dedup hash | ✅ Built | `normalize.py` |
| Weaviate store + hybrid search + facets/stats | ✅ Built | `store.py`, `search.py` |
| DuckDB `runs` + `job_sources` | ✅ Built | `relational.py` |
| FastAPI endpoints | ✅ Built | see `## API Endpoints` below |
| React filter UI | ✅ Built | `frontend/` |
| **DeepSeek enrichment** | ✅ Built | `enrich.py` extracts `yoe_min/max`, `visa_sponsorship`, `skills`, `seniority`, `company_size_bucket` via DeepSeek, synchronously during ingest. `enrichment_status` set to `done`/`failed`/`pending` accordingly |
| Enrichment: clearance / employer_type / cap_exempt / recruiter-flag | ✅ Built | `enrich.py` extracts `security_clearance`, `citizenship_required`, `employer_type`; `cap_exempt` derived via `derive_cap_exempt()`; `is_recruiter_post` heuristic. Curated adapters stamp `employer_type` from config (wins over LLM) |
| **User profiles + verdict/scoring** | ✅ Built | `UserProfile` + `user_job_state` in DuckDB; `verdict.py` pure Apply/Flag/Reject engine; `/api/jobs?profile_id=` attaches verdicts, excludes applied/hidden, sorts cap-exempt-first; profile CRUD endpoints |
| **Resume matching** | ✅ Built | `POST /api/match` — embeds resume via `embed_query()`, `near_vector` with profile eligibility filters + verdicts |
| **Progressive lookback windowing** | ✅ Built | `/api/jobs?target_min=` widens 6h→12h→18h→24h; hourly presets added to `DATE_PRESETS` |
| **Service layer** | ✅ Built | `services/source_config.py` (sources.yaml load/merge/adapter construction), `services/query_service.py` (dedup, date-range, resume match, semantic scoring, saved-search counts), `services/ingestion_service.py` (ingestion/enrichment/watchlist-refresh). `api/main.py` is thin (routes → services → repositories). `RelationalStore` serializes its single DuckDB connection with a re-entrant lock (background ingestion thread + request handlers). |
| **Cap-exempt sources** | ✅ Built | Curated Greenhouse nonprofits (mozilla/khanacademy/givewell) + `scripts/probe_workday.py` verifies cap-exempt Workday tenants (universities/AMCs/nonprofit-research) from `data/workday_cap_exempt_seeds.txt` → `sources.discovered.yaml`. `type` stamps cap-exempt; tenant `name` stamps the employer. **`services/registry.py` projects these into the company registry at startup so the Companies tab shows them + "Get companies" refreshes them (incl Workday via `region`/`site` columns).** |
| **Saved searches + pipeline** | ✅ Built | `saved_searches` + new-since-last-visit counts; application-status pipeline (applied/oa/interview/offer/rejected) with notes. |
| **APScheduler** | ✅ Built (off by default) | `scheduler.py` daily auto-refresh; `settings.scheduler_enabled` gates it. Ingestion is on-demand (`POST /api/search/run`) otherwise. |
| **Weaviate backup** | ✅ Built | `backup.py` + `scripts/export_weaviate.py`/`import_weaviate.py` export the index **with vectors** (`include_vector=True`) to `data/weaviate_export.jsonl.gz` and restore it — **$0, no embedding calls**. Import refuses on a vector-dimension mismatch (no mixing models). Opt-in `EXPORT_AFTER_INGEST` (default off) re-exports after each ingest. |
| **LangGraph agentic search** | ❌ Not built | No `agent/` directory (optional Phase 4) |

A `README.md` (setup/run guide) exists at the repo root.

## Stack

| Layer | Choice |
|---|---|
| Backend language | Python 3.11+ |
| Search + vector DB | Weaviate (Docker), hybrid BM25 + vector |
| Relational side store | DuckDB (run logs, `job_sources` dedup map) |
| LLM enrichment | DeepSeek `deepseek-chat` via OpenAI-compatible client |
| Embeddings | Google Gemini API via `google-generativeai`. **Use a `gemini-embedding-*` model** (e.g. `gemini-embedding-001`, 3072-dim) — the spec's `text-embedding-005` is a *Vertex* model name **not served by the Gemini API** and will 404. Set `EMBED_MODEL` accordingly. Local BGE/MiniLM fallback is planned, not built. |
| Scheduling | APScheduler (linear ETL only — not agentic) |
| Agentic search | LangGraph (optional Phase 4 feature) |
| API | FastAPI + Uvicorn |
| Frontend | React + Vite + TypeScript + Tailwind + TanStack Query |

## Development Commands

```bash
# Start Weaviate
docker-compose up -d

# Install backend
pip install -e ".[dev]"

# Run backend — MUST be launched from the repo root.
# sources.yaml and blocklist.yaml are loaded relative to the process CWD.
uvicorn backend.jobscout.api.main:app --reload

# Run tests
pytest backend/tests/

# Run a single test (tests are class-based — use Class::method)
pytest backend/tests/test_normalize.py::TestComputeJobId::test_deterministic -s

# Install frontend deps
cd frontend && npm install

# Run frontend dev server
cd frontend && npm run dev

# Lint / type check
ruff check backend/
mypy backend/

# Build frontend (also runs tsc typecheck)
cd frontend && npm run build

# Trigger on-demand ingestion (via API) — a JSON body is required
curl -X POST http://localhost:8000/api/search/run \
  -H 'Content-Type: application/json' \
  -d '{"keywords": ["software engineer"], "location": "remote", "results_wanted": 50}'
```

Copy `.env.example` to `.env` and fill in keys before running. `GOOGLE_API_KEY` is needed for embeddings (every ingest call embeds). `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` enable the Adzuna adapter. `DEEPSEEK_API_KEY` drives enrichment (`enrich.py`, OpenAI-compatible client) — extracting YoE/visa/skills/seniority/clearance/employer_type during ingest.

Note: the `${ADZUNA_APP_ID}`-style placeholders in `sources.yaml` are **not** expanded — `_load_sources_cfg` is a plain `yaml.safe_load`. Credentials come from `.env` via `jobscout.config.settings`; `sources.yaml` only toggles `enabled` and per-source options.

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/jobs` | Hybrid search + filters + pagination (the main query endpoint) |
| GET | `/api/jobs/{job_id}` | Fetch one job by canonical id |
| POST | `/api/search/run` | Kick off on-demand ingestion (FastAPI `BackgroundTasks`, returns `RunLog` stubs immediately) |
| GET | `/api/sources/status` | Per-source last-run info (from DuckDB) |
| GET | `/api/stats` | Aggregate counts: total, by source, by date bucket (Weaviate `aggregate`) |

## Architecture

### Ingestion pipeline (linear ETL, NOT agentic)

**As implemented today** (`services/ingestion_service.py::_run_ingestion`): `POST /api/search/run` schedules a FastAPI `BackgroundTasks` job → iterates enabled adapters → `normalize.py` (canonical schema + dedup hash) → **`enrich.py` (DeepSeek) extracts YoE/visa/skills/seniority/clearance/employer_type synchronously** → `embed_job(...)` embeds with the Gemini model (`gemini-embedding-001`) including the enriched skills → `WeaviateStore.upsert(job, vector)` → `RelationalStore.upsert_job_source(...)`. Each adapter run is bracketed by `start_run`/`finish_run` in the DuckDB `runs` table; `enrichment_status` is set `done`/`failed`. Idempotent: an already-enriched job in Weaviate is reused (no repeat DeepSeek call).

**Optional daily refresh:** `scheduler.py` (APScheduler) can trigger `_refresh_watchlist` on a daily cadence — **off by default** (`settings.scheduler_enabled`). Enrichment is synchronous-at-ingest (not a separate worker); `embed.py` docstrings describe an earlier target flow.

### Source adapter layer (`adapters/`)
All adapters implement `JobSourceAdapter` protocol (`adapters/base.py`). All HTTP goes through `CompliantHttpClient` — adapters cannot make raw requests. `CompliantHttpClient` enforces robots.txt, per-domain rate limiting (default 1 req/3s), exponential backoff on 429/503, honest User-Agent, and no-cookies. Adding a new source inherits compliance automatically.

### Search and storage (`store.py`, `search.py`)
One Weaviate `Job` collection holds job objects + vectors. All queries (keyword, vector, metadata filters) compose in a single Weaviate `hybrid()` call — no two-stage FAISS+SQL re-filter. `alpha` parameter (0–1) blends BM25 vs vector ranking. Date presets map to `posted_date` filter ranges. Faceted counts come from Weaviate `aggregate` calls.

### Resume matching (✅ built)
`POST /api/match` (text) and `POST /api/match/upload` (PDF/DOCX/TXT/JSON) embed the resume with the same model as jobs and run `near_vector` with the profile's eligibility filters, returning ranked matches with verdicts. Upload also parses the resume into a saved `UserProfile` (`resume.py`, DeepSeek) that drives verdicts/sorting. UI: the Profile tab's resume-drop (`ResumeMatchPanel`).

### Agentic search (NOT yet built — optional Phase 4, `agent/`)
Planned LangGraph graph: `parse_intent` → `run_search` → `evaluate_results` → `adjust_filters` (loop, max 3 attempts) → `rank_and_summarize`. Only this layer would use LangGraph. Ingestion and enrichment stay linear. No `agent/` directory exists yet.

## Key Constraints

**Compliance (non-negotiable):**
- All scraping adapters route through `CompliantHttpClient`. Never allow raw HTTP in an adapter.
- Check `robots.txt` before every request; skip and log if disallowed.
- No credentials, cookies, or auth-bypass. Only unauthenticated public data.
- For `risk="high"` scraped sources: store only structured facts + 280-char snippet + URL, not the full description (`store_full_description=False`).
- `blocklist.yaml` must be honored. On a C&D, add the domain, purge its rows, disable its adapter.
- High-risk sources (JobSpy) are **off by default** in `compliance.yaml`; require explicit opt-in.

**Deduplication:**
`job_id = sha256(normalize(company) + '|' + normalize(title) + '|' + normalize(city))[:16]`. On collision, keep the most complete record and append to the `job_sources` side table.

**Embedding consistency:**
Vectors for jobs and resumes must use the same model. Switching models requires re-embedding the entire index — don't mix models.

**Date handling:**
Relative dates ("3 days ago") are parsed at ingest with `dateparser` and stored with `posted_date_est=True`. Missing dates fall back to `ingested_at`.

## Configuration Files

| File | Purpose |
|---|---|
| `sources.yaml` | Per-source enable/disable, API keys refs, company lists |
| `compliance.yaml` | robots.txt enforcement, rate limits, User-Agent, storage policy |
| `blocklist.yaml` | Domains/companies to never source from |
| `.env` | API keys and runtime config |
| `docker-compose.yml` | Weaviate container |

## Build Phases (from spec)
1. **Phase 1** — Weaviate schema, `CompliantHttpClient`, JobSpy + Adzuna adapters, normalization, FastAPI `/api/jobs`, React filter UI
2. **Phase 2** — DeepSeek enrichment, resume match endpoint, match % badges
3. **Phase 3** — Remaining adapters (Greenhouse/Lever/Remotive/Arbeitnow/RSS), APScheduler, source-status dashboard
4. **Phase 4** — LangGraph agentic search (optional)
