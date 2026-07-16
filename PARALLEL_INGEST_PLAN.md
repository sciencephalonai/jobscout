# JobScout — Parallel ingestion plan (2026-07-12)

Goal: a full source sweep (and the For You auto-refill) should finish in ~3–5 minutes instead of
30–40, without breaking rate-limit compliance, the LLM/embed budget, or the single-writer databases.

## Why (what's wrong today)
`_run_ingestion` (backend/jobscout/services/ingestion_service.py:101) walks adapters **one after
another**; each adapter's network fetch AND its enrichment/embedding/writes happen inline. Workday
alone (per-job detail fetches across tenants) can take 20+ minutes, which is why the code currently
hand-orders refills to put Workday last (`:124-133`) and even re-sorts Workday tenants (`:135-150`)
— band-aids on the sequential design. Meanwhile the Simplify feed (the new entry-level supply) sits
in the queue behind it.

## Design (what stays sacred)
- **Rate limits / robots**: enforced per `CompliantHttpClient` per domain. Each fetch worker gets its
  OWN client; adapters own disjoint domains, so per-domain pacing is preserved.
- **DuckDB is single-writer, Weaviate writes + run logs + embed budget are shared** → ALL processing
  (raw_to_job → US filter → profile pre-filter → enrichment → embed → save → run logs) stays on the
  main thread, exactly as today. Only the network fetch is parallelized.
- **LLM/embed budget**: unchanged — budget checks live in the processing phase (main thread). A
  budget stop still halts processing; the only difference is some raw fetches may already have
  happened (network cost only, zero LLM cost).

## Task list (in order; full test suite after each — "don't break anything")

### Task 1 — Parallel fetch phase in `_run_ingestion`
- Split the loop into: **submit** (ThreadPoolExecutor, `max_workers=6`; each worker runs
  `list(adapter.search(keywords, location, results_wanted, since, http=own_client))` with its own
  `CompliantHttpClient`, closing it in `finally`; exceptions captured and returned, not raised) and
  **process** (`concurrent.futures.as_completed`: for each finished adapter, run the EXISTING
  per-adapter processing block unchanged — start_run/finish_run, counts, enrichment, embedding,
  saves — on the main thread).
- The refill source-ordering hack (`refill_order`, Workday-tenant sort) is kept: order now only
  decides *submission* order (still helps: fast sources finish and get processed first), and the
  Workday hospital-tenant demotion still matters within that adapter.
- Generator error semantics preserved: a worker exception is recorded on that adapter's run log
  (`error_msg`) just like today's inline `except`.
- New setting `ingest_fetch_workers: int = 6` in config.py (env-overridable; `1` = old sequential
  behavior, used by tests that assert ordering).

### Task 2 — Tests for Task 1
- `backend/tests/test_parallel_ingest.py`: fake adapters with artificial latency —
  (a) all adapters' jobs land (counts equal sequential run),
  (b) one adapter raising mid-iteration doesn't lose the others and records its error,
  (c) each worker received a DIFFERENT http client instance (no shared-session races),
  (d) with `ingest_fetch_workers=1` behavior matches today (regression guard).
- Run the FULL suite + ruff + mypy.

### Task 3 — Live verification (the actual goal: For You 10–20)
- Restart backend. Trigger the For You query (auto-refill fires) or a manual profile ingest.
- Expect: sweep completes in minutes; `by_source.simplify > 0`; For You returns ≥10 recommendable
  entry-level DS/ML/SWE roles, all US + sponsorship-viable; Simplify rows show the "Direct" badge.

### Task 4 — Small fix while verifying: run-log counts in `/api/sources/status`
- The status endpoint showed `found: None | saved: None` for every source. If the counts are already
  persisted by `finish_run` but not surfaced, wire them through; if not persisted, record
  `count_seen/count_ingested` there. Small, read-mostly fix — skip if it turns out non-trivial.

## Explicitly NOT changing
- No parallel enrichment/embedding (shared budget + provider rate limits; sequential is the safe
  and cheap part anyway).
- No new sources this round; no verdict/matching changes (locked by the precision contract + tests).
- NO git commands (owner constraint).

## Rollback
`ingest_fetch_workers=1` env var restores sequential behavior without a code revert.
