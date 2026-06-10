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
- `DEEPSEEK_API_KEY` — job/resume enrichment (`deepseek-chat`).
- `WEAVIATE_CLUSTER_URL` + `WEAVIATE_API_KEY` — Weaviate Cloud (or use local Docker).
- `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` — optional, enables the Adzuna source.

Details: [docs/configuration.md](docs/configuration.md).

### First run

The index starts **empty** — open the UI and click **Get latest jobs** (top bar) to ingest from the
curated boards, including the cap-exempt Workday universities + nonprofit boards. Give it a minute (each
new job is enriched + embedded). Cap-exempt employers are auto-synced into the **Companies** tab at
startup. To also populate the broader *for-profit* company watchlist (optional), run
`python scripts/discover_companies.py --write-sources` then `python scripts/build_company_registry.py`.

---

## The UI, tab by tab

The UI is **5 tabs** with one consistent top bar on every page.

| Tab | What it's for | How to use it |
|---|---|---|
| **Jobs** | The main search + filter view (two-pane: list + detail). | Type a query, apply filters (date, remote, source, experience, **Work authorization** [hide-no-sponsorship / cap-exempt / H-1B / **E-Verify**], **employer type**, **clearance**, company size). Pick an **active profile** (top bar) to get fit verdicts, matched/gap chips, cap-exempt-first sorting, and **Apply / Save / Hide** buttons on each job. Badges include sponsorship likelihood and **E-Verify**. |
| **My Jobs** | Your shortlist + application tracker (one page, toggle). | **Shortlist** = jobs you **Saved**; **Pipeline** = jobs you marked **Applied / OA / Interview / Offer / Rejected** (with notes; applied jobs drop out of the main Jobs list). Replaces a manual `applied_jobs.md`. |
| **Saved** | Saved searches + "new since last visit" alerts. | Save the current query+filters (★ on the Jobs tab); this tab lists each saved search with a live **new-matches** count. The **🔔 bell** in the top bar badges when there are new matches. |
| **Companies** | The company registry (employers, ATS, tier, H-1B / **cap-exempt** flags). | Filter by tier / H-1B / scrapable. **Get companies** (top bar) / **Refresh watchlist** pulls *new* jobs from enabled companies — now including the **cap-exempt** universities/nonprofits auto-synced into the registry (violet *Cap-exempt* badge). Direct-apply-only firms show a careers link. |
| **Profile** | Drop your resume + manage profiles (one page). | Drag a PDF/DOCX/TXT/JSON → extracts text, builds a saved profile, lists matches with matched (green) / gap (amber) chips. Below: every saved profile with **Set active** / **Delete**. |

**Top bar (every tab):** active-profile selector, **Get latest jobs** (runs ingestion on demand),
**Get companies** (refreshes the company watchlist), the **🔔** saved-search bell, and **Settings**
(the daily auto-refresh toggle — off by default).

**E-Verify badge:** jobs at known E-Verify employers get a teal **E-Verify** chip — important because the
24-month STEM OPT extension requires the employer to be enrolled. Advisory (curated list); confirm on
e-verify.gov. Filter to them via Work authorization → *E-Verify employer*.

Full walkthrough with a sequence diagram: [docs/user-guide.md](docs/user-guide.md).

---

## Keeping jobs fresh

- **Manual (default):** click **Get latest jobs** (top bar) or **Refresh watchlist** (Companies).
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
