# JobScout

A local, visa-aware job aggregation + matching tool. It pulls jobs from many sources (ATS boards +
aggregators), enriches each with an LLM (years-of-experience, visa/sponsorship signals, skills,
cap-exempt employer type), embeds them for semantic search, and helps you **find fitting roles, judge
sponsorship likelihood, and shortlist what to apply to** — including dropping your resume to get ranked
matches.

> Single-user / local tool. No login, no multi-tenant accounts. Your data lives on your machine
> (DuckDB) and in your own Weaviate Cloud cluster.

Full docs: **[`docs/`](docs/)** — start with [docs/user-guide.md](docs/user-guide.md) and
[docs/architecture.md](docs/architecture.md).

**Project direction & contributing:** the vision is in [`JobScout_SPEC.md`](JobScout_SPEC.md), the living
plan in [`docs/ROADMAP-CURRENT.md`](docs/ROADMAP-CURRENT.md), the hosting path in
[`docs/pre-deployment-checklist.md`](docs/pre-deployment-checklist.md), and dev standards in
[`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## What it does (at a glance)

```
            ┌──────────── sources (18 adapters) ────────────┐
            │  ATS boards: Greenhouse, Lever, Ashby, Workday, │
            │  Workable, Rippling, Recruitee, SmartRecruiters │
            │  Aggregators: Adzuna, Remotive, RemoteOK, ...   │
            └───────────────────────┬────────────────────────┘
                                     ▼
   normalize → US filter → DeepSeek enrich → Gemini embed → store
                                     ▼
              Weaviate Cloud (jobs + vectors) + DuckDB (state)
                                     ▼
        FastAPI  ───────────────►  React UI (Jobs / Shortlist /
                                    Applied / Companies / Match / Profiles)
```

The same diagram with rendering + a fuller pipeline lives in
[docs/architecture.md](docs/architecture.md) (Mermaid + text fallback).

---

## Quickstart

Three processes: **Weaviate** (or Weaviate Cloud), the **FastAPI backend**, and the **React frontend**.
Run everything from the repo root.

```bash
# 0. Prereqs: Python 3.11+, Node.js, and Docker (only if running Weaviate locally).

# 1. Python env + deps
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Config — copy the example and fill in keys
cp env.example .env        # the app reads ".env" (with the dot)

# 3. Vector DB: either Weaviate Cloud (set WEAVIATE_CLUSTER_URL + WEAVIATE_API_KEY in .env)
#    or local Docker:
docker-compose up -d

# 4. Backend (from repo root — sources.yaml is resolved from the CWD)
uvicorn backend.jobscout.api.main:app --reload      # http://localhost:8000  (docs at /docs)

# 5. Frontend (second terminal)
cd frontend && npm install && npm run dev            # http://localhost:5173
```

### Required keys (`.env`)
- `GOOGLE_API_KEY` — Gemini embeddings (`gemini-embedding-001`). Needed for ingest + text search.
- `DEEPSEEK_API_KEY` — job/resume enrichment when `LLM_PROVIDER=deepseek` (default).
- `NVIDIA_API_KEY` — optional NVIDIA NIM alternative. Set `LLM_PROVIDER=nvidia` and choose the model in Data & backend settings (or set `NVIDIA_MODEL` in `.env`).
- `WEAVIATE_CLUSTER_URL` + `WEAVIATE_API_KEY` — Weaviate Cloud (or use local Docker).
- `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` — optional, enables the Adzuna source.

Details: [docs/configuration.md](docs/configuration.md).

### First run

The index starts **empty** — open the UI and click **Get latest jobs** (top bar) to ingest from the
curated boards, including the cap-exempt Workday universities + nonprofit boards. Give it a minute (each
new job is enriched + embedded). Every configured public ATS board and every curated target in
`data/company_targets.yaml` is idempotently synced into the **Companies** tab at startup. Targets without
a supported public ATS remain safe direct-apply links and are never scraped.

---

## The UI, tab by tab

The UI has one consistent workspace shell across its main pages.

| Tab | What it's for | How to use it |
|---|---|---|
| **For You** | Strict profile-backed recommendations. | Requires an active profile and returns only roles that pass target-profession, experience, seniority, resume/skill evidence, location, authorization, specialty, and work-mode gates. It widens recency without unrelated filler and automatically runs a cooldown- and quota-bounded profile refill when results are sparse/stale. |
| **Discover** | The broad search + filter view (two-pane: list + detail). | Type a query and apply date, remote, source, experience, work-authorization, employer-type, clearance, and company-size filters. With a profile, verdict/fit quality ranks before optional cap-exempt preference. |
| **My Jobs** | Your shortlist + application tracker (one page, toggle). | **Shortlist** = jobs you **Saved**; **Pipeline** = jobs you marked **Applied / OA / Interview / Offer / Rejected** (with notes; applied jobs drop out of the main Jobs list). Replaces a manual `applied_jobs.md`. |
| **Saved** | Saved searches + "new since last visit" alerts. | Save the current query+filters (★ on the Jobs tab); this tab lists each saved search with a live **new-matches** count. The **🔔 bell** in the top bar badges when there are new matches. |
| **Companies** | The company registry (employers, ATS, tier, H-1B / **cap-exempt** flags). | Filter by tier / H-1B / scrapable. **Get companies** (top bar) / **Refresh watchlist** pulls *new* jobs only from verified public ATS boards. Bespoke/Oracle/company-hosted targets stay visible as direct careers links without being scraped. |
| **Profile** | Drop your resume + manage a resume library + profiles (one page). | Drag a PDF/DOCX/TXT/JSON → extracts text, builds a saved profile, lists matches with matched (green) / gap (amber) chips. **Resumes** card: keep several resumes, pick one active for matching, rename/download/delete. **Tailored resumes** card: re-download any built DOCX. Below: every saved profile with **Set active** / **Delete**. |

**Top bar (every tab):** active-profile selector, **Get latest jobs** (runs ingestion on demand),
**Get companies** (refreshes the company watchlist), the **🔔** saved-search bell, and **Settings**
(the daily auto-refresh toggle — off by default).

**E-Verify badge:** jobs at known E-Verify employers get a teal **E-Verify** chip — important because the
24-month STEM OPT extension requires the employer to be enrolled. Advisory (curated list); confirm on
e-verify.gov. Filter to them via Work authorization → *E-Verify employer*.

Full walkthrough with a sequence diagram: [docs/user-guide.md](docs/user-guide.md).

---

**How fetching works:** all enabled sources are fetched **in parallel** (one worker + one compliant
HTTP client per source; per-domain rate limits and robots.txt still respected — `INGEST_FETCH_WORKERS`,
default 6), while enrichment/embedding/writes stay on a single stream. The **For You** feed keeps
widening its freshness window (24h → 7d → 1m) until it has ~25 qualified recommendations (display cap
50), and you can pin an exact window with the *Date posted* pill.

## Keeping jobs fresh

- **First run:** an empty index self-fills once via a bounded background seed (keyless sources) so the
  app isn't empty on first open — never a committed snapshot. `RETENTION_DAYS` keeps a rolling recent
  window. See [docs/data-and-storage.md](docs/data-and-storage.md).
- **For You:** a sparse/stale qualified feed automatically starts a profile-targeted refill at most once
  per six hours for the same profile evidence; the UI polls until new matches land. A "new since last
  visit" pill surfaces roles indexed since you last opened the feed.
- **Manual:** click **Find profile matches / Get latest jobs** or **Refresh watchlist** for an
  immediate run (the primary button's label + helper text explain which you're getting).
- **Daily auto-refresh:** built but **OFF by default** (Settings → toggle). On the free Gemini tier
  (1,000 embeds/day) a daily crawl can exhaust quota, so the manual button is the safe default. Turn
  the scheduler on once you have a paid embedding tier. See [docs/configuration.md](docs/configuration.md).

---

## Where your data lives

- **Jobs + vectors** → Weaviate (`Job` collection). This lives in Weaviate (cloud or local Docker), **not**
  in the project folder — so a folder/Dropbox copy does **not** contain your jobs. Back them up with the
  export script below.
- **Profiles, applied/saved/hidden state, run logs, company registry, source dedup** → `jobscout.duckdb`
  (a single DuckDB file at the repo root).
- **Resumes** are parsed in memory; the extracted text + profile are saved in the `user_profiles` table.
  No personal contact info is scraped or stored. Delete a profile anytime (Profiles or Match tab).

Exact tables + retention: [docs/data-and-storage.md](docs/data-and-storage.md).

## Hosting it for more than yourself

JobScout runs today as a single local user, but the core is shaped for multi-user hosting **without a
rewrite**: the database (DuckDB→Postgres), file storage (local→S3), authentication, and authorization are
each behind a single swap seam, and per-account limits/quotas resolve through one entitlements function.

- **Operator console** — the host monitors accounts and grants/revokes premium via `/api/admin/*` and the
  **Admin** tab (visible to `is_admin`). Per-account LLM/tailor/deep-match usage, storage, and traffic
  populate once `usage_metering_enabled` is on. See [docs/multi-tenancy.md](docs/multi-tenancy.md).
- **Guard rails** (rate limiting, upload/body-size caps, security headers, per-account quotas, auth
  enforcement) are **implemented but off by default** — flip them before exposing the app to untrusted
  users. The single source of truth is **[docs/pre-deployment-checklist.md](docs/pre-deployment-checklist.md)**
  (part A: flags to flip; part B: Postgres/S3/auth/queue/billing/compliance to build).

### Back up your jobs (Weaviate)

```bash
python scripts/export_weaviate.py     # → data/weaviate_export.jsonl.gz  (jobs + vectors)
python scripts/import_weaviate.py      # restore from that file
```

The export includes each job's **already-computed vector**, so a restore writes them back with **no
embedding calls — $0, no Gemini quota** (it's a file download, not a re-embed). The file rides along in
your folder/Dropbox copy. To refresh it automatically after every ingest, set `EXPORT_AFTER_INGEST=true`
in `.env` (off by default). Details: [docs/data-and-storage.md](docs/data-and-storage.md).

---

## Development

```bash
pytest backend/tests/           # 296 tests
ruff check backend/ scripts/    # lint
mypy backend/                   # type check
cd frontend && npm run build    # tsc + vite build
```

Useful scripts (`scripts/`): `discover_companies.py` (find new ATS boards), `probe_workday.py` (verify
cap-exempt Workday tenants), `build_company_registry.py` (seed the registry), `ingest_discovered.py`
(bounded enriched ingest), `export_weaviate.py` / `import_weaviate.py` (back up + restore the Weaviate
index, vectors included, $0), `smoke_adapters.py` (live-test adapters), `restamp_sponsors.py` (backfill
H-1B flags). See [docs/sources.md](docs/sources.md).

---

## Docs index
- [docs/user-guide.md](docs/user-guide.md) — end-to-end how-to.
- [docs/architecture.md](docs/architecture.md) — system + pipeline diagrams.
- [docs/data-and-storage.md](docs/data-and-storage.md) — where everything is stored.
- [docs/api.md](docs/api.md) — every REST endpoint.
- [docs/sources.md](docs/sources.md) — the 18 adapters + discovery + compliance.
- [docs/configuration.md](docs/configuration.md) — `.env`, `sources.yaml`, scheduler, budgets.
