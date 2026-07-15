# Configuration

Three config surfaces: `.env` (secrets + runtime), `sources.yaml` (which sources/companies), and a few
YAML policy files. Everything is read relative to the **repo root** — run the backend from there.

---

## `.env` (copy from `env.example`)

| Key | Purpose | Notes |
|---|---|---|
| `GOOGLE_API_KEY` | Gemini embeddings | Required for ingest + text search. Use a `gemini-embedding-*` model. |
| `EMBED_MODEL` | Embedding model | Default `gemini-embedding-001` (3072-dim). The spec's `text-embedding-005` is Vertex-only and 404s on the Gemini API. |
| `LLM_PROVIDER` | Active chat provider | `deepseek` (default) or `nvidia`. Choose it in Data & backend settings or `.env`. |
| `DEEPSEEK_API_KEY` | DeepSeek enrichment + resume parse | Used when `LLM_PROVIDER=deepseek`. |
| `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` | DeepSeek client | Defaults provided. |
| `NVIDIA_API_KEY` | NVIDIA NIM enrichment + resume parse | Used when `LLM_PROVIDER=nvidia`; never commit a real key. |
| `NVIDIA_BASE_URL` / `NVIDIA_MODEL` | NVIDIA OpenAI-compatible client | Defaults to `https://integrate.api.nvidia.com/v1` and `z-ai/glm-5.2`. |
| `WEAVIATE_CLUSTER_URL` + `WEAVIATE_API_KEY` | Weaviate Cloud | When both set, connects to the cloud cluster; otherwise uses `WEAVIATE_URL` (local Docker). |
| `WEAVIATE_URL` | Local Weaviate | Default `http://localhost:8080`. |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | Adzuna source | Optional. |
| `RELATIONAL_DB_PATH` | DuckDB file | Default `./jobscout.duckdb`. |
| `RESUME_WRITER_DIR` | Private resume-writing toolkit | Its canonical facts, presets, single DOCX builder, and auditor power tailored resumes. Default points to the supplied local skill. |
| `TAILORED_RESUME_STORAGE_DIR` | Generated DOCX storage | Default `./data/tailored-resumes`; local-only and gitignored. |
| `EMBED_DAILY_BUDGET` | Max embeds per ingest/refresh run | Default `500`. The Gemini free tier is 1,000 embeds/day **shared** with search + resume-match, so this reserves ~half the day's quota for searching. |
| `SCHEDULER_ENABLED` | Daily auto-refresh | Default `false`. Toggle at runtime via Settings or `POST /api/scheduler`. |
| `SCHEDULER_HOUR` | Hour to run the daily refresh | Default `6`. |
| `SEED_ON_FIRST_RUN` | One-time first-run job seed | Default `true`. When the index is empty, an embedding key is set, and there's no `seeded_at` marker, a bounded background ingest fills the app so it isn't empty on first open. |
| `SEED_JOB_COUNT` | Jobs to fetch in the first-run seed | Default `150`. Kept well under `EMBED_DAILY_BUDGET` so the seed can't eat the day's quota. |
| `RETENTION_DAYS` | Rolling freshness window | Default `60`. At the end of each ingest, jobs older than this are purged so the index stays a recent window. Deliberately `> GHOST_STALE_DAYS` (45) so a job is ghost-flagged before removal. `0` disables retention. |
| `RESUME_STORAGE_DIR` | Uploaded resume files | Default `./data/resumes`; local-only and gitignored. Library uploads live at `{profile_id}/{resume_id}.{ext}`. |

> The app reads **`.env`** (with the leading dot). `env.example` is the template.

---

## The daily scheduler

```mermaid
flowchart LR
    OFF["scheduler_enabled = false<br/>(default)"] -->|"Settings toggle ON<br/>or POST /api/scheduler"| ON["daily cron at SCHEDULER_HOUR"]
    ON -->|"each run"| R["_refresh_watchlist<br/>(new jobs only, capped at EMBED_DAILY_BUDGET)"]
    ON -->|"toggle OFF"| OFF
```

**Fallback (textual):** the scheduler is **off by default**. Turn it on in the UI (Settings) or
`POST /api/scheduler {"enabled": true}`; it then runs `_refresh_watchlist` once a day at
`SCHEDULER_HOUR`, ingesting only new jobs and stopping at `EMBED_DAILY_BUDGET` embeds. Turn it off the
same way. Both the manual **Get latest jobs** button and **Refresh watchlist** work regardless.

**Why off by default:** a daily crawl of many companies can exceed the free Gemini embedding tier
(1,000/day). Run manually, or enable the scheduler after moving to a paid tier / local embeddings.

---

## `sources.yaml`

Per-source `enabled` flag + curated company/account/tenant lists. See
[sources.md](sources.md) for the `{token, type}` entry shape and discovery scripts. Auto-discovered
companies live in `sources.discovered.yaml` (generated; merged into `sources.yaml` at load, deduped).

`data/company_targets.yaml` is the curated Companies-tab list. A row with a verified ATS must also have
a matching entry in `sources.yaml`; rows with `ats: none` are direct-apply-only links and never enter an
ingestion run. All configured sources and targets are projected into DuckDB idempotently at startup.

## Policy files

| File | Purpose |
|---|---|
| `compliance.yaml` | robots.txt enforcement, per-domain rate limit, User-Agent, `collect_personal_data: false`. |
| `blocklist.yaml` | Domains/companies to never source from. |
| `docker-compose.yml` | Local Weaviate container. |

## Embedding consistency (important)
Jobs and resumes must be embedded with the **same** model. Changing `EMBED_MODEL` requires re-embedding
the whole index — don't mix models in one Weaviate collection.

## Chat provider scope

Set `LLM_PROVIDER=nvidia` and configure `NVIDIA_API_KEY` to use NVIDIA NIM for all chat tasks: job
enrichment, resume parsing, deep matching, and tailored-resume planning. DeepSeek and NVIDIA are
selectable alternatives; only the active one receives a request. Job collection and resume **embeddings**
remain on Gemini because they require a common embedding model, while direct ATS/API fetching does not
need an LLM at all.

## Embedding budget & quota (`EMBED_DAILY_BUDGET`)
Every **new** job is embedded once (Gemini) before it can be stored — the free tier allows **1,000
embeds/day**. **Both** ingest buttons use it: "Get latest jobs" (`_run_ingestion`) and "Get companies"
(`_refresh_watchlist`) each embed via Gemini + enrich via the selected LLM provider. `EMBED_DAILY_BUDGET` (default 500) caps
embeds **per run for both**, so a single click can't exhaust the whole day's quota; both also stop cleanly
on a 429 (no crash).

**Already-indexed jobs are skipped** (no re-embed — vectors are deterministic), so the budget is spent only
on *new* jobs and a run can add **up to `EMBED_DAILY_BUDGET` new jobs** rather than re-embedding what you
already have. How many it actually adds is bounded by how many *unseen* matching postings the fetch surfaces
— "Get latest jobs" requests `results_wanted=250` per source to backfill toward the budget; raise it to go
deeper, lower it for lighter/faster runs. Once you've caught up with the boards, runs taper to genuine new
arrivals.

The current quota state is exposed as **`embed_quota_exhausted`** on `GET /api/stats` — set the moment an
embed hits the provider 429, **cleared on the next successful embed** (so it auto-recovers after the daily
reset). The UI reads it for a single, self-clearing banner: a run with quota headroom shows "Fetch
started…" first and only flips to the amber *"Embedding quota reached — resumes after the daily reset"*
warning if it actually exhausts the quota; if the quota is already gone it shows amber up front. Existing
jobs are unaffected; already-indexed jobs are skipped before embedding, so re-running costs nothing. The
permanent fix for the 1,000/day ceiling is a paid Gemini tier or a local embedding model (planned).

## Weaviate backup (`EXPORT_AFTER_INGEST`)
`EXPORT_AFTER_INGEST` (default `false`): when `true`, a Weaviate backup
(`scripts/export_weaviate.py` → `data/weaviate_export.jsonl.gz`) runs automatically at the end of each
ingest so the local export stays fresh. It's a pure $0 download (no embedding). Leave it off to back up
on demand instead (`python scripts/export_weaviate.py`). Restore with `scripts/import_weaviate.py`. See
`docs/data-and-storage.md`.


## Required vs optional keys (canonical)

| Key | Status | Used for | Fallback |
|---|---|---|---|
| `GOOGLE_API_KEY` | **REQUIRED** | All embeddings (indexing, search, resume similarity) | None — the vector index is dimension-locked to Gemini; DeepSeek has no embeddings API |
| `DEEPSEEK_API_KEY` | **REQUIRED** (unless NVIDIA set) | Enrichment, deep match, resume parsing/structuring, bullet polish, tailoring | — |
| `NVIDIA_API_KEY` | Optional | Free-tier primary for the same LLM tasks | Auto-falls back to DeepSeek on 429 (15-min circuit breaker) or when the key is absent |
| `ADZUNA_APP_ID/KEY` | Optional | Adzuna source | Source self-skips |
| `USAJOBS_API_KEY/EMAIL` | Optional | USAJobs source (opt-in in sources.yaml) | Source self-skips |
| `WEAVIATE_CLUSTER_URL/API_KEY` | Optional | Cloud mirror of the vector store | Local Docker Weaviate |

`GET /api/health` reports readiness with fix instructions; the UI shows the same as a banner.
