# JobScout — "For You should give me 10–20 applicable jobs" plan (2026-07-12)

## What we HAVE today (simple terms)

**The matching engine is now correct, but starving.**

- **Collection**: 20 sources — 8 aggregators (Remotive, Adzuna, Himalayas…) + per-company ATS boards
  (Greenhouse/Lever/Ashby/Workday/… — the `companies` lists in sources.yaml) + opt-in USAJobs.
  US-only enforced at ingest; foreign leaks (Vietnam NVIDIA, "2 Locations", ISO-code cities) fixed and purged.
- **Matching**: every job is scored against your profile on ALL criteria — target titles, skills,
  experience (YoE), seniority, interests, work-mode, sponsorship, clearance, citizenship, category.
  Senior/Staff/Principal → rejected. Nurses/Medical Assistants → rejected (role-category gate).
  The old nurse-wall was a half-finished deploy + cap-exempt-first sorting; both fixed.
- **For You tab**: shows only "recommendable" jobs = fit ≥ 60% AND no fit-mismatch of any kind
  (only allowed caveat: sponsorship not stated at a cap-exempt/known-sponsor employer).
  When the feed is sparse it ALREADY auto-triggers a background profile-targeted ingest (your
  "always ingest for For You" — yes, and it's automatic, with a cooldown).
- **The problem**: precision is high but the corpus (~3.4k jobs) contains almost no *entry-level*
  DS/ML/SWE postings — generic keywords like "data scientist" pull mostly senior roles that the
  gates correctly reject. Result: 3 recommendations. Also the For You query walks a 7-window
  ladder and takes ~110s.

## What we are going to CHANGE (step by step)

### Step 1 — New source: SimplifyJobs New-Grad feed (the big supply fix)
- New adapter `backend/jobscout/adapters/simplify.py` reading
  `https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json`
  (probed live: 17,386 listings, 2,091 active+visible, 508 data/ML-titled; fields: title,
  company_name, locations[], url, sponsorship, date_posted (epoch), active, is_visible, category, degrees).
- Adapter rules:
  - keep only `active && is_visible`; drop `sponsorship in {"U.S. Citizenship is Required", "Does Not Offer Sponsorship"}`
  - map: locations[0] → location (joined "; " like Workday), epoch → posted_date, company_url → company site
  - stamp `new_grad_program=True` (these are curated new-grad roles — this is what lets them pass
    the "experience not stated" gate honestly)
  - honor `keyword_title_match` + `results_wanted`; non-US locations dropped downstream by `is_us_job`.
- Wire into `adapters/__init__.py`, `services/source_config.py` (`_SOURCE_ORDER`, authority=1),
  `sources.yaml` (`simplify: enabled: true`). Offline tests with a fake payload.

### Step 2 — Let curated new-grad roles through the evidence gate (small precision tweak)
- `verdict.py`: the "job has insufficient extracted skill evidence" block currently blocks any job
  without a description (Simplify listings have none). Change: when `job.new_grad_program` is true
  AND the title strongly matches a target role (title score ≥ 0.75) AND all eligibility gates pass,
  thin skill evidence keeps the caveat ("verify requirements on the posting") but does NOT block
  recommendation. Rationale: an eligible, title-matching, curated new-grad role is exactly what a
  0–2 yr candidate should see; skills can be verified on click.

### Step 3 — Entry-flavored refill keywords
- The sparse-feed auto-ingest currently searches raw target titles ("data scientist" → senior-heavy).
  Append entry terms (["new grad", "early career", "2026 graduate"]) to the refill keyword list in
  `api/main.py` (the `_profile_autofetch_and_clear` call) so title matching also captures
  "New Grad Software Engineer 2026"-style postings across all sources.

### Step 4 — For You latency (110s → ~40s)
- `search.py`: trim `RECOMMENDATION_LADDER` from 7 windows (6h…1m) to `["24h", "7d", "1m"]`.
  Entry-level supply is scarce; scanning 6h/12h/18h rungs almost never fills the target and each
  rung re-scores a full candidate window.

### Step 5 — Run it end-to-end
- Restart backend, trigger one profile-targeted ingest (Simplify + entry keywords). New jobs pass
  the profile pre-filter BEFORE enrichment (no LLM budget wasted on nurses/seniors).
- Measure `GET /api/jobs?recommendation_only=true&…&target_min=10` → expect **10–25 recommendations**.

### Blocker to clear first
- Weaviate (the vector DB in Docker) is currently unresponsive and the Docker CLI hangs —
  **Docker Desktop needs a restart** before any live verification. Code + tests proceed regardless.

## What we WILL HAVE afterwards (simple terms)
- For You: **10–20+ real, eligible, entry-level roles per visit** — every one passes: US-only,
  sponsorship-viable, junior-level, DS/ML/SWE role type, skill/background fit — sorted best-match first.
- A steady pipe of curated new-grad roles (the exact segment you compete best in), refreshed
  automatically whenever the feed runs low.
- Faster For You loads.
- Same LLM budget discipline: filtering happens before enrichment; deep-match LLM stays on-demand.

## Not doing (this round)
- Auto deep-match of the For You page (LLM spend; add later if wanted).
- More per-company boards / Handshake / LinkedIn scraping.
- Fixing the 110s further via semantic-score caching (only if the ladder trim isn't enough).
