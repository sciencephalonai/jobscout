# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

JobScout is a multi-portal job aggregation and filtering engine. It ingests listings from structured APIs (Adzuna, USAJOBS, Remotive, Greenhouse, Lever) and optional scraper libraries (python-jobspy), normalizes them into a canonical schema, enriches them via DeepSeek LLM extraction, embeds them into Weaviate for hybrid search, and exposes a FastAPI + React frontend. Full spec: `JobScout_SPEC.md`.

> The spec describes the full target system. This `## Project Overview` and `## Stack` describe that target. **What is actually built today** is narrower — see `## Implementation Status` before assuming a component exists.

## Implementation Status

This repo is now well past the original "Phase 1". Enrichment, scheduling, and most Phase 3 adapters are built. Use this table — not the spec's phase plan — as the source of truth:

| Area | State | Notes |
|---|---|---|
| `CompliantHttpClient` + adapter protocol | ✅ Built | `adapters/base.py` |
| Adapters | ✅ ~15 built | adzuna, remotive, arbeitnow, jobicy, jobrightai, remoteok, workingnomads, themuse, greenhouse, lever, ashby, workable, smartrecruiters, recruitee, jobspy. `rss` is in `sources.yaml` (disabled) with **no adapter code**. |
| Normalization + dedup hash + category derivation + US filter | ✅ Built | `normalize.py` (`raw_to_job`, `compute_job_id`, `derive_category`, `is_us_job`) |
| Weaviate store (local **and** Weaviate Cloud) + hybrid search + facets/stats | ✅ Built | `store.py`, `search.py` |
| DuckDB `runs` + `job_sources` | ✅ Built | `relational.py` |
| FastAPI endpoints | ✅ Built | see `## API Endpoints` below |
| React filter UI | ✅ Built | `frontend/` |
| **DeepSeek enrichment** | ✅ **Built** | `enrich.py::extract_enrichment`. Runs **synchronously at ingest** (YoE / visa / skills / seniority / company-size / employment-type) and also via the decoupled `POST /api/enrich/run` endpoint. `enrichment_status` ∈ `done`/`failed`/`pending`. |
| **APScheduler** | ✅ Built | `BackgroundScheduler` + `CronTrigger` started in the FastAPI `lifespan`; cron from `settings.ingest_schedule` (default `0 8 * * *`). |
| **Resume matching** | ❌ Not built | No endpoint, no `near_vector` call |
| **LangGraph agentic search** | ❌ Not built | No `agent/` directory |

There is also **no `README.md`** despite `pyproject.toml` referencing it.

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
# One-shot local dev: clears stale uvicorn (DuckDB single-writer lock), starts
# backend on :8000 and frontend on :5173, polls for health. Logs in /tmp.
scripts/run.sh          # start both    scripts/stop.sh  # stop both
scripts/health.sh       # check status

# Start Weaviate
docker-compose up -d

# Install backend
pip install -e ".[dev]"

# Run backend manually — MUST be launched from the repo root.
# sources.yaml and blocklist.yaml are loaded relative to the process CWD.
uvicorn backend.jobscout.api.main:app --reload

# Backfill the `category` property on existing Weaviate objects after adding
# the category feature (pages the whole collection, updates category only)
python scripts/backfill_categories.py

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

Copy `.env.example` to `.env` and fill in keys before running. `GOOGLE_API_KEY` is needed for embeddings (every ingest call embeds) — set `EMBED_MODEL=gemini-embedding-001`. `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` enable the Adzuna adapter. `DEEPSEEK_API_KEY` now drives enrichment — without it, enrichment is skipped and jobs land `enrichment_status="pending"`. `INGEST_SCHEDULE`/`INGEST_KEYWORDS`/`INGEST_RESULTS_WANTED` configure the APScheduler cron. Set `WEAVIATE_CLUSTER_URL`+`WEAVIATE_API_KEY` to use Weaviate Cloud instead of the local Docker instance.

Note: the `${ADZUNA_APP_ID}`-style placeholders in `sources.yaml` are **not** expanded — `_load_sources_cfg` is a plain `yaml.safe_load`. Credentials come from `.env` via `jobscout.config.settings`; `sources.yaml` only toggles `enabled` and per-source options.

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/jobs` | Hybrid search + filters + pagination (the main query endpoint) |
| GET | `/api/jobs/{job_id}` | Fetch one job by canonical id |
| POST | `/api/search/run` | Kick off on-demand ingestion (FastAPI `BackgroundTasks`, returns `RunLog` stubs immediately) |
| POST | `/api/enrich/run` | Re-run DeepSeek enrichment over `pending`/`failed` jobs (`{"limit": 50}`) |
| POST | `/api/purge/old` | Delete jobs older than 30 days from the index |
| GET | `/api/sources/status` | Per-source last-run info (from DuckDB) |
| GET | `/api/stats` | Aggregate counts: total, by source, by date bucket (Weaviate `aggregate`) |

`GET /api/jobs` filters (all repeatable where noted): `q`, `location`, `remote`, `visa`, `exp` (entry/mid/senior/lead), `source`, `company_size`, `employment_type`, `category` (software_eng/data_ml_ai/devops_infra/security/product_mgmt/design_ux/management/other), `date_range` (24h/7d/14d/21d/1m/custom + `date_from`/`date_to`), `alpha` (hybrid blend), `sort` (relevance/posted_desc/salary_desc), `page`/`page_size`. With no date filter, a **1-month floor** is enforced so stale jobs never surface.

## Architecture

### Ingestion pipeline (linear ETL, NOT agentic)

**As implemented today** (`api/main.py::_run_ingestion`), per job, in order:
1. **Trigger** — three paths feed the *same* `_run_ingestion`: manual `POST /api/search/run`, the APScheduler cron job (`_scheduled_ingest`), and **auto-fetch** (a `GET /api/jobs` search returning `< AUTOFETCH_MIN_RESULTS` quietly kicks a background ingest for that query, deduped via `_autofetch_inflight`, capped at `AUTOFETCH_MAX_INFLIGHT`).
2. `normalize.raw_to_job` → canonical schema + dedup hash + `derive_category`.
3. **US-only gate** — `is_us_job(...)` drops non-US postings *before* the paid enrich/embed steps. **30-day age gate** — jobs with a reliable `posted_date` older than 30 days are skipped.
4. **Per-company flood cap** — at most `MAX_JOBS_PER_COMPANY_PER_RUN` (15) jobs kept per company per run.
5. **Enrichment (idempotent)** — if the job already exists in Weaviate with `enrichment_status="done"`, reuse its enriched fields and skip the DeepSeek call. Otherwise, if `DEEPSEEK_API_KEY` is set and there's a description, call `enrich.extract_enrichment` (YoE / visa / skills / seniority / company-size / employment-type). A hard failure marks `enrichment_status="failed"` instead of storing blank fields.
6. **Company size** — exact value from `sources.yaml` company config wins; otherwise a per-run cache; otherwise the LLM estimate.
7. **Location aggregation** — the same `job_id` posted in multiple cities is collapsed into one record whose `locations` is the union across this run and what's already stored.
8. `embed_job(...)` embeds (title/company/skills/description) with the Gemini embedding model (see Stack note — set `EMBED_MODEL=gemini-embedding-001`, **not** the config default `text-embedding-005`, which 404s) → `WeaviateStore.upsert(job, vector)` → `RelationalStore.upsert_job_source(...)`.

Each adapter run is bracketed by `start_run`/`finish_run` in the DuckDB `runs` table.

**Decoupled enrichment & purge:** `POST /api/enrich/run` re-runs DeepSeek over jobs currently `pending`/`failed` (recovery after an outage). `POST /api/purge/old` deletes jobs older than 30 days. `embed.py`'s docstrings still describe an older target flow (enrich → embed in a separate worker); the real flow is the single in-loop sequence above.

### Source adapter layer (`adapters/`)
All adapters implement `JobSourceAdapter` protocol (`adapters/base.py`). All HTTP goes through `CompliantHttpClient` — adapters cannot make raw requests. `CompliantHttpClient` enforces robots.txt, per-domain rate limiting (default 1 req/3s), exponential backoff on 429/503, honest User-Agent, and no-cookies. Adding a new source inherits compliance automatically.

### Search and storage (`store.py`, `search.py`)
One Weaviate `Job` collection holds job objects + vectors. All queries (keyword, vector, metadata filters) compose in a single Weaviate `hybrid()` call — no two-stage FAISS+SQL re-filter. `alpha` parameter (0–1) blends BM25 vs vector ranking. Date presets map to `posted_date` filter ranges. Faceted counts come from Weaviate `aggregate` calls.

### Resume matching (NOT yet built)
Planned: resume text/PDF is embedded with the same model as jobs, then passed to `near_vector` with the same property filters, returning top 5. No endpoint or `near_vector` call exists yet.

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
