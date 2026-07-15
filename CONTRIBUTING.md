# Contributing to JobScout

JobScout is a multi-portal job aggregation + filtering engine: it ingests listings from structured APIs
and ATS boards, normalizes + enriches them, embeds them into Weaviate for hybrid search, and scores each
against a user profile with a deterministic verdict engine. FastAPI backend + React/Vite frontend.

## Project direction (read these to understand the plan)
- **Product spec / vision:** [`JobScout_SPEC.md`](JobScout_SPEC.md)
- **Living roadmap + status:** [`docs/ROADMAP-CURRENT.md`](docs/ROADMAP-CURRENT.md)
- **Architecture:** [`docs/architecture.md`](docs/architecture.md) · **API:** [`docs/api.md`](docs/api.md)
- **Data model + multi-tenancy:** [`docs/data-and-storage.md`](docs/data-and-storage.md),
  [`docs/multi-tenancy.md`](docs/multi-tenancy.md)
- **Before hosting for real users:** [`docs/pre-deployment-checklist.md`](docs/pre-deployment-checklist.md)

## Dev setup & commands
```bash
docker-compose up -d                 # Weaviate
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp env.example .env                  # then fill in keys (see below)
.venv/bin/uvicorn backend.jobscout.api.main:app --reload   # run from the repo ROOT (sources.yaml is CWD-relative)
cd frontend && npm install && npm run dev                  # http://localhost:5173
bash scripts/check.sh                # the one quality gate: ruff + mypy + pytest + tsc + vite
```
**Required keys:** `GOOGLE_API_KEY` (embeddings — no fallback) + `DEEPSEEK_API_KEY` **or** `NVIDIA_API_KEY`
(LLM tasks, auto-fallback both ways). Everything else is optional. Never paste a real key into source or
a shared channel; secrets live only in the gitignored `.env`.

## Code style & documentation (enforced — part of "done")
- Every new Python module gets a module docstring; every public function/class a concise, imperative
  docstring. Comments explain constraints and non-obvious *why* — never restate code.
- Every new frontend component file starts with a one-line header comment describing its role.
- Any feature or behavior change updates the matching `docs/` page (and its mermaid diagram if the flow
  changed) **in the same change**. Roadmap item statuses update in the same change that lands the work.
- **Copy & capitalization:** product chrome (nav, buttons, cards, modals, settings) uses **sentence case**
  ("Target roles"); cards that render **resume content** mirror document headings in **Title Case**
  ("Achievements & Awards"). Counts always carry a unit and are plural-safe ("1 role" / "3 roles").
- **Text overflow:** every text element picks exactly ONE behavior — never a blanket `truncate`.
  (1) **Wrap & grow** for primary user content (`min-w-0` + `[overflow-wrap:anywhere]`, no cap).
  (2) **Truncate only with a reveal** for width-constrained chrome (`truncate min-w-0` + a `title=`/link).
  (3) **nowrap** for atomic chrome (timestamps, counts, badges) with `shrink-0`. Structure must not depend
  on state — a row should look the same whether an optional element is present or not.

## Key constraints (non-negotiable)
- **Compliance:** all scraping adapters route through `CompliantHttpClient` (`adapters/base.py`) — no raw
  HTTP. Honor `robots.txt`, per-domain rate limits, no cookies/auth-bypass, unauthenticated public data
  only. High-risk sources (JobSpy) are off by default. Honor `blocklist.yaml`; on a C&D, block the domain,
  purge its rows, disable its adapter.
- **Dedup:** `job_id = sha256(company|title|city)[:16]`; on collision keep the most complete record and
  append to the `job_sources` side table.
- **Embedding consistency:** jobs and resumes must use the same embedding model; switching models means
  re-embedding the whole index — don't mix models.

## Git
Maintainers open PRs. Keep docs current so a PR can be opened at any time. Never commit `.env`, the DuckDB
files, or anything under `data/resumes/` / `data/tailored-resumes/` (all gitignored).
