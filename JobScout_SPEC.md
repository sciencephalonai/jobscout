# JobScout: Multi-Portal Job Aggregation and Filtering Engine

**Version:** 1.0
**Owner:** Narahara Chari Dingari
**Status:** Draft for build
**Stack philosophy:** Open source first, local-first, no paid API dependency required to run core features.

---

## 1. Problem Statement

Searching across many job portals manually is slow and repetitive. Each portal has its own UI, filters, and posting-date semantics. There is no single place to run a keyword search, normalize the results, and filter on the dimensions that actually matter: years of experience required, visa sponsorship, location/remote restrictions, and recency of the posting.

JobScout aggregates listings from multiple sources, normalizes them into one schema, enriches them with structured signals extracted via a local LLM, and exposes a fast filterable interface.

---

## 2. Goals and Non-Goals

### Goals
1. Search many job sources with one set of keywords.
2. Normalize all results to a single canonical schema.
3. Extract and filter on: years of experience, visa sponsorship status, work-authorization restrictions, remote/onsite/hybrid, salary if present.
4. Capture and filter by posted date (last 24h, last 7 days, last 2 weeks, last 3 weeks, last month, custom range).
5. Deduplicate the same role posted across multiple portals.
6. Run fully locally with open source tooling; no mandatory paid services.

### Non-Goals
1. Auto-applying to jobs (out of scope for v1).
2. Bypassing portal terms of service or anti-bot protections through aggressive scraping.
3. Resume tailoring or cover letter generation (possible v2 module).

---

## 3. Legal and Sourcing Strategy

This is the most important design constraint. Scraping major portals directly often violates their terms of service and triggers anti-bot defenses. The spec uses a tiered sourcing approach so the app stays robust and defensible.

### Tier 1: Official and semi-official structured feeds (preferred)
- **Adzuna API** (open developer API, free tier): aggregates many sources, returns structured JSON with salary, location, category.
- **USAJOBS API** (US government, free): federal jobs with structured fields.
- **Remotive API** (free, remote jobs): clean JSON, includes posting dates.
- **Arbeitnow API** (free, EU + remote): structured listings, visa-relevant fields.
- **Greenhouse and Lever public job board endpoints**: thousands of companies expose `https://boards-api.greenhouse.io/v1/boards/{company}/jobs` and `https://api.lever.co/v0/postings/{company}`. These are public JSON APIs intended for embedding. Maintain a curated list of target companies.
- **Ashby, Workable, SmartRecruiters**: similar public board APIs per company.

### Tier 2: Open source scraper libraries (use with rate limiting and respect for robots.txt)
- **python-jobspy**: open source library that pulls from LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google Jobs into a pandas DataFrame. This is the single highest-leverage dependency for breadth. Treat it as best-effort; portals change and may block.
- **JobFunnel**: open source aggregator with config-driven searches.

### Tier 3: RSS and sitemap ingestion
- Many company career pages and niche boards expose RSS or sitemap XML. Generic RSS ingester covers the long tail cheaply.

**Design rule:** the sourcing layer is plugin-based. Each source is an independent adapter implementing a common interface, so degraded or blocked sources fail gracefully without taking down the app.

---

## 4. Compliance and Sourcing Governance

The hybrid sourcing model is: use an official API when one exists, scrape under strict restrictions only when no API exists and the data is public, and use RSS/sitemap or manual upload for the long tail. Every adapter declares how it sources data and carries a risk rating, so the rules live in code and config rather than tribal knowledge.

### 4.1 Source method and risk matrix

| Source | Method | Risk | Constraints applied |
|---|---|---|---|
| Adzuna | Official API | Low | API key, honor rate limits in their terms |
| USAJOBS | Official API | Low | API key, public domain data |
| Remotive | Official API | Low | Attribution per their terms |
| Arbeitnow | Official API | Low | Public API |
| Greenhouse / Lever / Ashby / Workable | Public board API (per company) | Low | Intended for embedding; curated company list |
| The Muse | Official API | Low | API key, free tier |
| Generic RSS / sitemap | Feed parse | Low | Only published feeds; respect feed terms |
| JobSpy (LinkedIn, Indeed, Glassdoor, ZipRecruiter, Google) | Scrape (best-effort) | High | robots.txt + rate limit + no auth bypass + snippet-only storage; off by default per source, opt-in via config |
| Manual import | CSV/JSON upload | None | User-provided |

Rule of precedence: if a company or board is reachable through a Tier 1 API, the scraper adapter must not also target it. APIs always win to avoid duplicate load and unnecessary risk.

### 4.2 robots.txt enforcement (hard requirement)
Every scraping adapter must consult robots.txt before any request and must not fetch disallowed paths.

- Fetch and parse `https://{domain}/robots.txt` using `urllib.robotparser` (or `protego` for full crawl-delay and wildcard support).
- Cache the parsed rules per domain for the run; refresh at most once per 24h.
- Check `can_fetch(user_agent, url)` before every request. If disallowed, skip and log; do not fetch.
- Honor `Crawl-delay` if present; if absent, fall back to the default rate limit below.
- A scraping adapter whose target disallows the relevant paths is automatically disabled for that run, with a status surfaced in the source dashboard.

```python
from urllib.robotparser import RobotFileParser

rp = RobotFileParser()
rp.set_url(f"https://{domain}/robots.txt")
rp.read()
if not rp.can_fetch(USER_AGENT, target_url):
    log.info("robots_disallow", domain=domain, url=target_url)
    continue   # skip, never fetch
delay = rp.crawl_delay(USER_AGENT) or DEFAULT_DELAY_SECONDS
```

### 4.3 Rate limiting and politeness
- Per-domain token bucket; never global-only. Default: 1 request every 3 seconds per domain, max 1 concurrent connection per domain.
- Exponential backoff on 429/503: 2s, 8s, 32s, then mark the source degraded and stop for the run.
- Identify the client honestly with a descriptive `User-Agent` that includes a contact URL. Do not spoof browsers or rotate identities to evade blocks.
- No headless-browser fingerprint evasion, no CAPTCHA-solving services, no residential-proxy rotation to bypass blocks. If a site blocks us, we back off rather than escalate.

### 4.4 No access-control bypass (hard requirement)
- Never submit credentials, never use a logged-in session or cookies, never access pages behind a paywall or login. Only data reachable without authentication is in scope. This is the line that separates the protected-public-scraping posture from CFAA exposure.

### 4.5 Storage: facts plus snippet, link out (hard requirement)
To respect copyright in job descriptions, scraped sources store only:
- Structured facts: title, company, location, salary, posted date, remote mode (facts are not copyrightable).
- A short snippet: first 280 characters of the description, for preview only.
- The canonical `url` back to the original posting.

The full description is NOT stored for scraped (High-risk) sources; the app links out for the full text. Official-API sources may store the fuller description only to the extent the API's terms permit. LLM enrichment for scraped sources runs on the title plus snippet plus any structured API fields, not on a stored full copy. A per-source `store_full_description` flag (default false) enforces this.

### 4.6 Personal data and privacy
- Do not collect or store recruiter names, emails, phone numbers, or other personal contact data, even when present in a posting.
- Job postings are about roles, not people; keep it that way. This keeps GDPR/PIPEDA exposure minimal.

### 4.7 Takedown and revocation
- Maintain a `blocklist.yaml` of domains/companies to never source from.
- On any cease-and-desist, abuse complaint, or explicit opt-out, add the source to the blocklist, purge its stored rows, and disable its adapter. This is a one-command operation, documented in the README.

### 4.8 Compliance configuration
These defaults live in `compliance.yaml` and are enforced by the base adapter, not left to individual adapters:

```yaml
compliance:
  user_agent: "JobScoutBot/1.0 (+https://example.com/about-jobscout)"
  respect_robots_txt: true            # hard requirement, do not disable
  default_delay_seconds: 3
  max_concurrency_per_domain: 1
  backoff_seconds: [2, 8, 32]
  allow_auth_access: false            # hard requirement, do not enable
  store_full_description_for_scraped: false
  collect_personal_data: false
  blocklist_file: blocklist.yaml
scrapers:
  jobspy:
    enabled: false                    # opt-in; high-risk, off by default
    sites: [google, zip_recruiter]    # least hostile first
```

### 4.9 Enforcement point
All of the above is implemented once in `adapters/base.py` as a `CompliantHttpClient` wrapper (robots check, rate limit, backoff, UA, no-cookies). Adapters cannot make raw HTTP calls; they must go through this client. This guarantees a single adapter cannot accidentally violate policy, and adding a new scraper inherits compliance for free.

---

## 5. Architecture Overview

```
   INGESTION PIPELINE (linear ETL, NOT agentic - APScheduler)
                +---------------------------+
                |   Scheduler (APScheduler) |
                +-------------+-------------+
                              |
              +---------------v----------------+
              |       Source Adapter Layer     |
              |  Adzuna | JobSpy | Greenhouse  |
              |  Lever  | Remotive | RSS | ... |
              +---------------+----------------+
                              |  raw listings
              +---------------v----------------+
              |  Normalize + dedup hash        |
              +---------------+----------------+
                              |
              +---------------v----------------+
              |  LLM Enrichment (DeepSeek):     |
              |  YoE, visa, restrictions        |
              +---------------+----------------+
                              |
              +---------------v----------------+
              |  Embed (Google text-embed-005)  |
              +---------------+----------------+
                              |  upsert objects + vectors
        +---------------------v---------------------+
        |        Weaviate (vector DB, Docker)        |
        |  hybrid search (BM25 + vector) + native    |
        |  metadata filters (date/visa/yoe/remote)   |
        +---------------------+---------------------+
              | (relational side: run logs, dedup map -> SQLite/DuckDB)
                              |
        +---------------------+---------------------+
        |                                           |
+-------v---------+                       +---------v---------+
|  FastAPI REST   |                       |  React + Vite     |
|  filter/search  |                       |  TS + Tailwind    |
|  resume match   |                       |  TanStack Query   |
+-------+---------+                       +-------------------+
        |
+-------v-----------------------------------------+
|  AGENTIC SEARCH LAYER (optional, LangGraph)     |
|  NL intent -> filters -> search -> reflect/retry|
|  loop -> rank vs resume -> fit summaries        |
+-------------------------------------------------+
```

---

## 6. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Aligns with existing stack |
| Sourcing | python-jobspy, httpx, feedparser | Breadth plus structured APIs |
| Scheduling | APScheduler | In-process cron for the linear ETL pipeline |
| Vector + search DB | Weaviate (open source, Docker) | Hybrid BM25+vector search and metadata filters in one query; replaces FAISS + FTS |
| Relational side store | DuckDB or SQLite | Run logs, dedup `job_sources` map, analytics |
| Embeddings | Google `text-embedding-005` (default, cheapest hosted ~$0.006/1M, free dev tier), local `sentence-transformers`/BGE (offline fallback) | DeepSeek has no embeddings endpoint, so embeddings come from a separate source; cost is negligible at this scale |
| LLM enrichment | DeepSeek API (`deepseek-chat`), OpenAI-compatible client | Cheap, fast, far quicker than local Ollama for extraction |
| Agentic search (optional) | LangGraph + DeepSeek (`deepseek-chat`/`deepseek-reasoner`) | Stateful NL search with reflect/retry loop; only this layer is agentic |
| API | FastAPI + Uvicorn | Existing stack, async, auto docs |
| Frontend | React + Vite + TypeScript + Tailwind | Aligns with CPAx frontend; rich filtering UX |
| Data fetching | TanStack Query (React Query) | Caching, pagination, facet refetch |
| Queue (if scaling) | none in v1; Redis + RQ if needed | Keep v1 single-process |

---

## 7. Canonical Data Schema

All sources map into this canonical shape. It is stored as a Weaviate `Job` object (see section 11.1 for the Weaviate collection definition); the SQL below documents the logical schema and field semantics. The relational side store (SQLite/DuckDB) holds only the `job_sources` dedup map and `runs` log.

```sql
CREATE TABLE jobs (
    job_id            VARCHAR PRIMARY KEY,   -- dedup hash, see 7.1
    source            VARCHAR NOT NULL,      -- 'adzuna','jobspy_linkedin','greenhouse', etc.
    source_job_id     VARCHAR,               -- original id from source
    title             VARCHAR NOT NULL,
    company           VARCHAR,
    location_raw      VARCHAR,               -- as provided
    country           VARCHAR,
    city              VARCHAR,
    remote_mode       VARCHAR,               -- 'remote','onsite','hybrid','unknown'
    description        VARCHAR,               -- full text
    url               VARCHAR NOT NULL,
    salary_min        DOUBLE,
    salary_max        DOUBLE,
    salary_currency   VARCHAR,
    posted_date       TIMESTAMP,             -- normalized to UTC
    posted_date_est   BOOLEAN,               -- true if inferred, not exact
    ingested_at       TIMESTAMP NOT NULL,
    -- LLM-enriched structured fields
    yoe_min           INTEGER,               -- min years experience required
    yoe_max           INTEGER,
    visa_sponsorship  VARCHAR,               -- 'yes','no','unclear','not_mentioned'
    work_auth_required VARCHAR,              -- e.g. 'us_citizen','clearance','none','unclear'
    restrictions      VARCHAR,               -- free text summary of constraints
    skills            VARCHAR,               -- JSON array string
    seniority         VARCHAR,               -- 'intern','junior','mid','senior','lead','exec'
    enrichment_status VARCHAR DEFAULT 'pending', -- 'pending','done','failed'
    raw_payload       VARCHAR                -- original JSON for audit
);

CREATE INDEX idx_posted ON jobs(posted_date);
CREATE INDEX idx_source ON jobs(source);
CREATE INDEX idx_visa   ON jobs(visa_sponsorship);
```

### 7.1 Deduplication
`job_id = sha256(normalize(company) + '|' + normalize(title) + '|' + normalize(city))[:16]`

Normalization lowercases, strips punctuation and common suffixes ("Inc", "LLC"), and collapses whitespace. On collision, keep the record with the most complete fields and record all source URLs in a `job_sources` side table so the user sees "posted on 3 portals."

```sql
CREATE TABLE job_sources (
    job_id   VARCHAR,
    source   VARCHAR,
    url      VARCHAR,
    posted_date TIMESTAMP,
    PRIMARY KEY (job_id, source)
);
```

---

## 8. Source Adapter Interface

Every source implements this contract. New sources are added without touching core logic. Each adapter declares its sourcing `method` and `risk` (see section 4.1) and must route all HTTP through the shared `CompliantHttpClient` (section 4.9), which enforces robots.txt, rate limits, backoff, the honest User-Agent, and the no-cookies rule. Adapters cannot make raw requests.

```python
from typing import Protocol, Iterator, Literal
from datetime import datetime

class JobSourceAdapter(Protocol):
    name: str
    method: Literal["api", "scrape", "rss", "manual"]
    risk: Literal["none", "low", "high"]
    store_full_description: bool        # False for scraped/high-risk sources
    def search(
        self,
        keywords: list[str],
        location: str | None,
        results_wanted: int,
        since: datetime | None,         # only fetch newer than this if source supports it
        http: "CompliantHttpClient",    # the ONLY way to make requests
    ) -> Iterator[dict]:
        """Yield raw listing dicts. Must not raise on partial failure;
        log and continue. Return whatever the source provides.
        Scrape adapters that hit a robots.txt disallow skip silently."""
        ...
```

Adapters to ship in v1:
1. `AdzunaAdapter` (API key, free tier)
2. `JobSpyAdapter` (wraps python-jobspy for LinkedIn/Indeed/Glassdoor/ZipRecruiter/Google)
3. `GreenhouseAdapter` (iterates a configured company list)
4. `LeverAdapter` (iterates a configured company list)
5. `RemotiveAdapter`
6. `ArbeitnowAdapter`
7. `RSSAdapter` (generic, config-driven feed list)

Configuration lives in `sources.yaml`:

```yaml
sources:
  adzuna:
    enabled: true
    app_id: ${ADZUNA_APP_ID}
    app_key: ${ADZUNA_APP_KEY}
    countries: [us, gb, in]
  jobspy:
    enabled: true
    sites: [linkedin, indeed, zip_recruiter, google]
    hours_old: 168          # last 7 days
  greenhouse:
    enabled: true
    companies: [stripe, databricks, anthropic]
  lever:
    enabled: true
    companies: [netflix, plaid]
  rss:
    enabled: true
    feeds:
      - https://weworkremotely.com/categories/remote-programming-jobs.rss
```

---

## 9. Posted Date Handling

This is a common failure point because sources report dates inconsistently. Strategy:

1. If a source gives an exact ISO timestamp, store it, set `posted_date_est = false`.
2. If a source gives relative text ("3 days ago", "Posted today", "30+ days ago"), parse it into a UTC timestamp at ingest time, set `posted_date_est = true`. Use a small relative-date parser (`dateparser` library handles most cases).
3. If no date at all, fall back to `ingested_at` and set `posted_date_est = true`.

Date filtering API exposes presets that map to SQL ranges computed at query time:

| Preset | Condition |
|---|---|
| `24h` | `posted_date >= now() - INTERVAL 24 HOUR` |
| `7d` | `posted_date >= now() - INTERVAL 7 DAY` |
| `14d` | `posted_date >= now() - INTERVAL 14 DAY` |
| `21d` | `posted_date >= now() - INTERVAL 21 DAY` |
| `1m` | `posted_date >= now() - INTERVAL 1 MONTH` |
| `custom` | `posted_date BETWEEN :from AND :to` |

---

## 10. LLM Enrichment

Raw descriptions rarely have clean structured fields for experience and visa status. An LLM extracts them. This runs as a background job over `enrichment_status = 'pending'` rows so ingestion stays fast.

**Model:** DeepSeek `deepseek-chat` via its OpenAI-compatible endpoint (`https://api.deepseek.com`). Use the OpenAI Python SDK pointed at the DeepSeek base URL, and enable JSON output mode (`response_format={"type": "json_object"}`) so the schema below comes back as parseable JSON. DeepSeek is materially cheaper and faster than local Ollama for this volume of short extraction calls. Optional fallback to OpenAI `gpt-4o-mini` if DeepSeek is unreachable.

```python
from openai import OpenAI
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
resp = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": prompt}],
    response_format={"type": "json_object"},
    temperature=0,
)
```

**Extraction prompt (structured JSON output):**

```
You are a job-posting analyzer. Read the job description and return ONLY valid
JSON, no prose, no markdown fences, matching this schema exactly:

{
  "yoe_min": <integer or null>,
  "yoe_max": <integer or null>,
  "visa_sponsorship": "yes" | "no" | "unclear" | "not_mentioned",
  "work_auth_required": <short string or "unclear">,
  "restrictions": <one sentence summary or "">,
  "seniority": "intern"|"junior"|"mid"|"senior"|"lead"|"exec"|"unclear",
  "skills": [<up to 10 skill strings>]
}

Rules:
- yoe_min/max: extract only explicitly stated years of experience.
- visa_sponsorship: "no" only if the posting explicitly says no sponsorship or
  requires existing work authorization without sponsorship. Use "not_mentioned"
  when silent.
- Do not infer beyond the text.

JOB DESCRIPTION:
"""
{description}
"""
```

Parse defensively: strip fences, `json.loads`, on failure set `enrichment_status='failed'` and leave structured fields null so the row is still searchable by keyword and date.

**Batch sizing:** process N rows per cycle, configurable, to keep local GPU/CPU load bounded.

---

## 11. Semantic Search and Resume Matching (Weaviate)

Weaviate is the search and storage engine for job objects. It is a v1 core feature. It does three things FAISS could not do in one query: keyword search, vector search, and metadata filtering, combined natively. This removes the two-stage "FAISS top-K then SQL re-filter" awkwardness and the hand-rolled score blending.

### 11.1 Collection schema
Define one Weaviate collection, `Job`. Structured fields are stored as filterable properties; the embedding is the object vector.

```python
# weaviate-client v4
from weaviate.classes.config import Property, DataType, Configure

client.collections.create(
    name="Job",
    # Recommended: precompute vectors yourself (Vectorizer.none()) so the
    # embedding source stays swappable. Alternatively use the text2vec-google
    # module to let Weaviate embed on insert.
    vectorizer_config=Configure.Vectorizer.text2vec_google(
        model_id="text-embedding-005",
    ),
    properties=[
        Property(name="job_id", data_type=DataType.TEXT),
        Property(name="title", data_type=DataType.TEXT),
        Property(name="company", data_type=DataType.TEXT),
        Property(name="description", data_type=DataType.TEXT),
        Property(name="city", data_type=DataType.TEXT),
        Property(name="country", data_type=DataType.TEXT),
        Property(name="remote_mode", data_type=DataType.TEXT),
        Property(name="visa_sponsorship", data_type=DataType.TEXT),
        Property(name="yoe_min", data_type=DataType.INT),
        Property(name="yoe_max", data_type=DataType.INT),
        Property(name="seniority", data_type=DataType.TEXT),
        Property(name="source", data_type=DataType.TEXT),
        Property(name="posted_date", data_type=DataType.DATE),
        Property(name="salary_min", data_type=DataType.NUMBER),
        Property(name="url", data_type=DataType.TEXT),
    ],
)
```

The embedding text per job is `title + " " + company + " " + skills + " " + first 1500 chars of description`, which stays under the 2,048-token context of `text-embedding-005`. Either let the `text2vec-google` module embed on insert, or precompute with Google `text-embedding-005` (or local BGE/MiniLM offline) and pass `vector=` explicitly. Precomputing is recommended because it keeps the embedding source swappable and decoupled from Weaviate. Pick one model and keep it consistent across the whole index, since vectors from different models are not comparable; switching later means re-embedding everything.

### 11.2 Hybrid search with filters (single query)
Keyword + vector + hard filters compose in one call. `alpha` is Weaviate's built-in blend control (0 = pure keyword, 1 = pure vector).

```python
from weaviate.classes.query import Filter
import datetime as dt

res = jobs.query.hybrid(
    query="senior machine learning engineer",   # keyword + vector
    alpha=0.5,
    filters=(
        Filter.by_property("visa_sponsorship").equal("yes")
        & Filter.by_property("yoe_min").less_or_equal(8)
        & Filter.by_property("remote_mode").equal("remote")
        & Filter.by_property("posted_date").greater_than(
              dt.datetime.now(dt.UTC) - dt.timedelta(days=7))
    ),
    limit=50,
    return_metadata=["score"],
)
```

Date presets (24h/7d/14d/21d/1m/custom) map to `posted_date` filter ranges. Visa, YoE, remote, and source map to property filters. There is no separate FTS layer to maintain.

### 11.3 Resume matching flow
1. User pastes resume text or uploads a file (PDF/DOCX parsed server-side).
2. Server embeds the resume with the same embedding model used for jobs (Google `text-embedding-005` by default).
3. Use `query.near_vector` (or `near_text`) with the same property filters so semantic ranking and hard filters compose in one query.
4. Response includes Weaviate's `score`/distance per job, surfaced as a "match %" badge.
5. Per the standing preference, the resume-match endpoint returns the **top 5 results** by default (caller may request more), rather than applying a similarity cutoff threshold.

### 11.4 Why not FAISS
FAISS is a great in-process index, but it stores only vectors. Every metadata filter (date, visa, YoE) would require a parallel store and a post-filter step, and hybrid keyword+vector blending would be custom code. Weaviate folds all of that into one server and one query. The tradeoff is running a Docker container, which is acceptable for a local-first app. If you ever need a pure-embedded, zero-infra deployment, FAISS + DuckDB remains a valid fallback profile.

---

## 12. Agentic Search Layer (LangGraph, optional)

The ingestion pipeline is deliberately NOT agentic; it is linear ETL on a scheduler. LangGraph is reserved for one feature where statefulness and loops genuinely add value: a natural-language search assistant that self-corrects.

### 12.1 When this layer runs
The user types a free-form request, for example: "Find me senior ML roles, remote, that sponsor visas, posted this week, matching my background." Instead of manually setting filters, an agent interprets the request, searches, evaluates whether the result set is useful, and adjusts.

### 12.2 Graph design
State carries the parsed filters, the resume embedding, the current result set, and an attempt counter.

```
              +------------------+
              |  parse_intent    |  NL -> structured filters (LLM, JSON out)
              +--------+---------+
                       |
              +--------v---------+
              |  run_search      |  Weaviate hybrid + filters + resume vector
              +--------+---------+
                       |
              +--------v---------+
              | evaluate_results |  count + quality check
              +--------+---------+
                       |
         too few / too many?  ----yes----> +-------------------+
                       |                   | adjust_filters    |
                       | no                | loosen or tighten |
                       |                   +---------+---------+
                       |                             |
                       |       (loop back to run_search, max 3 tries)
              +--------v---------+
              | rank_and_summarize|  order by match; optional per-job fit note
              +--------+---------+
                       |
                    [ END ]
```

Conditional edge from `evaluate_results`: if `len(results) < min_wanted` and attempts < 3, route to `adjust_filters` (e.g. widen date window 7d -> 14d -> 1m, relax `yoe_min`, drop the strictest filter) then back to `run_search`. If `len(results) > max_wanted`, tighten. Otherwise proceed to `rank_and_summarize`. The retry cycle is exactly what a linear pipeline cannot express and what LangGraph handles cleanly.

### 12.3 Nodes
- `parse_intent`: DeepSeek `deepseek-chat` call (JSON mode) returning the same filter JSON used by `/api/jobs`, so the agent reuses the existing query layer rather than a parallel one. Use `deepseek-reasoner` only if intent parsing needs multi-step reasoning.
- `run_search`: calls the Weaviate hybrid query from section 11.2.
- `evaluate_results`: pure Python; checks counts against `min_wanted`/`max_wanted`.
- `adjust_filters`: deterministic relaxation/tightening rules first; LLM only if rules are exhausted.
- `rank_and_summarize`: returns top results; optionally one short fit sentence per job from DeepSeek `deepseek-chat` (off by default to control cost and latency).

### 12.4 Scope guidance
Ship the deterministic search and API (sections 11 and 13) first; it fully satisfies the original requirements without any agent. Add the LangGraph layer as an enhancement once the core is stable. Do not route ingestion or enrichment through LangGraph.

---

## 13. Search and Filter API (FastAPI)

```
GET  /api/jobs
     Query params:
       q           string   keywords (Weaviate hybrid)
       location    string
       remote      enum     remote|onsite|hybrid
       yoe_max     int      show jobs requiring <= this many years
       yoe_min     int
       visa        enum     yes|no|unclear|not_mentioned
       date_range  enum     24h|7d|14d|21d|1m|custom
       date_from   date     (custom)
       date_to     date     (custom)
       source      string   filter to one source
       alpha       float    hybrid blend, 0=keyword .. 1=vector (default 0.5)
       sort        enum     posted_desc|relevance|salary_desc
       page        int
       page_size   int
     Returns: paginated job list + facet counts

GET  /api/jobs/{job_id}          full detail incl. all source URLs
GET  /api/jobs/{job_id}/similar  Weaviate near_object neighbors
POST /api/match/resume           body: resume text or file; returns top-N jobs
                                 ranked by match score, composable with the
                                 same filters. Returns top 5 by default.
POST /api/agent/search           NL request -> LangGraph agent -> ranked jobs
POST /api/search/run             trigger an on-demand ingestion run
GET  /api/sources/status        per-source last run, count, errors, method,
                                 risk, robots.txt allow/deny, degraded flag
GET  /api/stats                  totals, by source, by date bucket
```

Filtering, keyword search, and vector ranking all execute as a single Weaviate query. Faceted counts (per source, per visa status, per date bucket) come from Weaviate `aggregate` calls so the UI shows a count on each filter.

---

## 14. Frontend (React + Vite + TypeScript + Tailwind)

Single-page app with a left filter rail, a search/command bar, and a results list. Built with Vite + TypeScript, styled with Tailwind, server state via TanStack Query.

- **Search bar:** keyword box plus a toggle for "Agent mode" (natural-language request routed to `/api/agent/search`) and a "Run new ingestion" button.
- **Resume panel:** paste text or drop a PDF/DOCX; on submit, results re-rank by match score and each card shows a match % badge.
- **Left filter rail:** date range (24h / 7d / 2 weeks / 3 weeks / 1 month / Custom date picker), visa sponsorship (multiselect with facet counts), max years experience (slider), remote mode, source (multiselect with counts), and an `alpha` slider (keyword ↔ semantic) for power users.
- **Results list:** a card per job with title, company, location, remote badge, YoE badge, visa badge (green = yes, red = no, gray = not mentioned), posted date with relative label ("2d ago"), match % when a resume is loaded, and "posted on N portals" when deduped. Click opens a detail drawer with the full description and all source apply links.
- **State/data:** TanStack Query keyed on the filter object; changing a filter refetches results and facet counts. URL-synced filter state so searches are shareable and bookmarkable.

Suggested component tree: `SearchBar`, `ResumePanel`, `FilterRail` (with `DateRangeFilter`, `VisaFilter`, `YoESlider`, `RemoteFilter`, `SourceFilter`, `AlphaSlider`), `JobList` -> `JobCard`, `JobDetailDrawer`, `SourceStatusBadge`.

v2 adds saved searches, new-match email/push alerts, and a side-by-side "compare jobs" view.

---

## 15. Scheduling and Runs

- APScheduler runs ingestion on a configurable interval (default every 6 hours) and on-demand via the API. This is the linear ETL pipeline; it is not routed through LangGraph.
- Each run: for each enabled source, call `search()` with the configured keyword sets, normalize, dedup-upsert into Weaviate (and the relational side store), mark new objects `pending` for enrichment.
- A separate enrichment job drains the pending queue: extract structured fields with DeepSeek, then embed (Google `text-embedding-005`) and write the vector to the Weaviate object.
- Run metadata (source, count, duration, errors) logged to a `runs` table (SQLite/DuckDB) for the status dashboard.

---

## 16. Project Structure

```
jobscout/
├── README.md
├── docker-compose.yml       # Weaviate (LLM + embeddings are hosted APIs)
├── pyproject.toml
├── sources.yaml
├── compliance.yaml          # robots/rate-limit/UA/storage policy (section 4.8)
├── blocklist.yaml           # domains/companies to never source (section 4.7)
├── .env.example
├── backend/
│   ├── jobscout/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── store.py             # Weaviate client, Job collection schema
│   │   ├── relational.py        # SQLite/DuckDB: runs, job_sources map
│   │   ├── models.py            # pydantic schemas
│   │   ├── normalize.py         # raw -> canonical, dedup hashing
│   │   ├── enrich.py            # DeepSeek extraction (OpenAI-compatible client)
│   │   ├── embed.py             # embedding helper (Google text-embedding-005)
│   │   ├── search.py            # Weaviate hybrid + filter query builder
│   │   ├── scheduler.py         # APScheduler (ETL only)
│   │   ├── agent/
│   │   │   ├── graph.py         # LangGraph definition (optional layer)
│   │   │   ├── nodes.py         # parse_intent, run_search, evaluate, adjust
│   │   │   └── state.py         # graph state schema
│   │   ├── adapters/
│   │   │   ├── base.py          # JobSourceAdapter protocol + CompliantHttpClient
│   │   │   ├── adzuna.py
│   │   │   ├── jobspy_adapter.py
│   │   │   ├── greenhouse.py
│   │   │   ├── lever.py
│   │   │   ├── remotive.py
│   │   │   ├── arbeitnow.py
│   │   │   └── rss.py
│   │   └── api/
│   │       └── main.py          # FastAPI app + routes
│   └── tests/
│       ├── test_normalize.py
│       ├── test_dedup.py
│       ├── test_search.py
│       └── test_adapters.py
└── frontend/                    # React + Vite + TS + Tailwind
    ├── index.html
    ├── package.json
    ├── vite.config.ts
    └── src/
        ├── main.tsx
        ├── api/client.ts        # typed fetch + TanStack Query hooks
        ├── components/
        │   ├── SearchBar.tsx
        │   ├── ResumePanel.tsx
        │   ├── FilterRail.tsx
        │   ├── JobList.tsx
        │   ├── JobCard.tsx
        │   └── JobDetailDrawer.tsx
        └── types.ts
```

---

## 17. Build Phases

**Phase 1 (core, ~1.5 weeks):** `docker-compose` with Weaviate; `Job` collection schema; the `CompliantHttpClient` (robots.txt, rate limit, backoff, UA, no-cookies) so compliance is in place from the first request; JobSpy (opt-in, off by default) + Adzuna adapters; normalization + dedup; embedding on enrich; FastAPI `/api/jobs` with Weaviate hybrid search and date/visa/YoE/remote filters; React UI with filter rail and results list. Delivers a usable, filterable aggregator with semantic search.

**Phase 2 (enrichment + resume match, ~4 days):** DeepSeek extraction for YoE/visa/restrictions; enrichment background job; `/api/match/resume` with PDF/DOCX parsing; match % badges; facet counts wired into the UI.

**Phase 3 (breadth + polish, ~1 week):** Greenhouse/Lever/Remotive/Arbeitnow/RSS adapters; APScheduler; source-status dashboard; dedup-across-sources display; URL-synced filter state.

**Phase 4 (agentic layer, optional, ~4 days):** LangGraph agent (`/api/agent/search`) with parse_intent -> search -> reflect/retry loop; "Agent mode" toggle in the UI; optional per-job fit summaries.

**Phase 5 (v2, optional):** saved searches, new-match alerts, compare view.

---

## 18. Key Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Portal blocking / ToS issues with scrapers | Hybrid model: API first, scrape only public data under restrictions (section 4); graceful per-source failure; high-risk scrapers off by default |
| robots.txt / unauthorized access exposure | `CompliantHttpClient` enforces robots.txt and bans auth/cookie use centrally; adapters cannot bypass it (sections 4.2, 4.4, 4.9) |
| Copyright in job descriptions | Store facts + 280-char snippet + link out for scraped sources; no full-text copy (section 4.5) |
| Legal/abuse complaint or C&D | `blocklist.yaml` + one-command purge-and-disable per source (section 4.7) |
| Personal data / GDPR-PIPEDA | Do not collect recruiter PII; roles not people (section 4.6) |
| Inconsistent or missing posted dates | `dateparser` + `posted_date_est` flag + fallback to ingest time |
| LLM extraction errors | Defensive JSON parse, `failed` status, row stays keyword/date searchable |
| Visa field often "not mentioned" | Distinguish "no" from "not_mentioned" so users aren't misled; surface as a distinct gray badge |
| Duplicate roles across portals | Deterministic dedup hash + `job_sources` side table |
| Enrichment throughput / cost | Batch DeepSeek calls, configurable batch size, OpenAI fallback |
| Weaviate Docker dependency | `docker-compose` ships the container; document a FAISS+DuckDB embedded fallback profile for zero-infra deploys |
| Agent loops indefinitely | LangGraph max-attempts cap (3) with deterministic relaxation rules before any LLM adjustment |

---

## 19. Environment

```
# .env.example
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
DEEPSEEK_API_KEY=                       # LLM enrichment + agent reasoning
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
GOOGLE_API_KEY=                         # embeddings (text-embedding-005)
EMBED_MODEL=text-embedding-005
OPENAI_API_KEY=                         # optional enrichment fallback (gpt-4o-mini)
WEAVIATE_URL=http://localhost:8080
ENRICH_BATCH_SIZE=20
INGEST_INTERVAL_HOURS=6
RELATIONAL_DB_PATH=./jobscout.duckdb    # runs + dedup side store
AGENT_MAX_ATTEMPTS=3
```

---

## 20. Acceptance Criteria

1. A keyword search returns normalized results from at least two live sources, ranked by Weaviate hybrid search.
2. Results are filterable by date presets (24h/7d/2 weeks/3 weeks/1 month) and a custom range, with correct facet counts.
3. Visa sponsorship and max-years-of-experience filters work and reflect LLM-extracted fields, composing with the search in a single Weaviate query.
4. A pasted resume re-ranks results by match score and returns the top 5 by default.
5. The same role appearing on multiple portals is shown once with all source links.
6. Each result links to the original posting and shows a normalized posted date.
7. Compliance is enforced centrally: scraping adapters cannot fetch a robots.txt-disallowed path, cannot send credentials/cookies, and store only facts + a snippet + link for high-risk sources. A disallowed target is auto-disabled and shown as such in the source status dashboard.
8. The app runs end to end with Weaviate self-hosted (Docker) plus low-cost hosted APIs (DeepSeek for enrichment, Google for embeddings); a fully offline profile is available via self-hosted embeddings and an optional local LLM.
9. (If Phase 4 built) A natural-language request returns relevant jobs, and the agent demonstrably loosens filters and retries when the first pass returns too few results.
