# JobScout — Handoff & Execution Plan (portable)

> **Purpose:** a self-contained doc so **any developer or agent** can pick up and finish the outstanding
> work with **no prior context**. Everything needed is here or linked.
> Last updated: 2026-07-15.

## 0. Standing rules (do not break)
- **Contributors don't auto-commit.** The maintainer opens all PRs (see CONTRIBUTING.md).
- Work in the repo root (the checkout of this repository).
- Secrets live in the gitignored `.env`; never commit keys. Personal data (`*.duckdb`, `data/resumes/`,
  `data/tailored-resumes/`) is gitignored and was verified **never committed**.
- Ship the leanest change that works; reuse existing patterns; update docs in the same change as code.

## 1. How JobScout works (1-page architecture)
JobScout aggregates jobs from many sources, screens each against a candidate's profile, and helps apply
(save, track, deep-match, tailor a resume).

- **Roles/jobs are GLOBAL.** The Weaviate `Job` collection (`backend/jobscout/store.py`) is unscoped —
  no `profile_id`/`user_id`. Every profile and every user draws from the same shared, deduplicated pool.
  A job is fetched + enriched (DeepSeek/NVIDIA) + embedded (Gemini) **once**, then serves everyone.
- **Matching is deterministic, no LLM.** `backend/jobscout/verdict.py` (`score_verdict(job, profile)`)
  applies hard gates (visa/citizenship/clearance/seniority/experience/role-type) + a weighted fit score.
  Memoized in `services/scoring_cache.py`. This is why **For You never re-bills**.
- **Two feeds** (`frontend/src/App.tsx` `JobsPage`):
  - **For You** (`recommendation_only: true`, gated on the **active profile**) = a curated shortlist of
    roles you can realistically get, best-fit first. Needs an active profile.
  - **Discover** = **all** roles, searchable/filterable, with or without a profile (profile adds fit%).
- **Profiles vs resumes:** a **profile** = a matching *lens* (target roles, skills, eligibility prefs) +
  a **resume library** (many uploads, exactly one **active** — that active resume drives matching,
  deep-match, tailoring). One user may have several profiles, but **one profile + many resumes is the
  recommended model** (see §3).
- **Deep-match** (`backend/jobscout/deep_match.py`) = an LLM "second opinion" per job, **cached** by
  `(job_id, profile_id, fingerprint)` in DuckDB so it's never re-billed unless the resume/profile/job
  changes. `POST /api/match/deep/{job_id}`.
- **Tailoring** (`backend/jobscout/tailor.py`) = a per-job audited DOCX. It already selects **only
  JD-relevant evidence**: canonical facts + presets → `_option_catalog` → an LLM selector (`chat_json`) →
  the private resume-writing toolkit builds + audits. No facts are invented. `POST /api/profiles/{id}/tailor/{job_id}`.
- **Stores:** Weaviate (jobs+vectors) + **DuckDB** (relational: profiles, resumes, tailored catalog,
  deep-match cache, job-state, users, usage_counters) behind the `RelationalStore` **Protocol seam**
  (`relational.py`) with a `make_relational_store` factory. Files go through the `BlobStore` seam
  (`blob.py`, `LocalBlobStore`). LLM providers behind `enrich.chat_json` (NVIDIA→DeepSeek failover).
- **Multi-tenancy / hosting:** `api/deps.py` is the auth drop-in (`current_user_id`), authz primitive
  (`owned_profile`), admin gate (`require_admin`); `entitlements.py` resolves per-account limits;
  `api/admin.py` is the operator console. All dormant/behind flags today.

**Read next:** `docs/architecture.md`, `docs/multi-tenancy.md`, `docs/pre-deployment-checklist.md`,
`docs/api.md`, `docs/data-and-storage.md`, `docs/user-guide.md`.

## 2. What was built (recent session)
- **Swap seams:** DB (`RelationalStore` Protocol + `DuckDBRelationalStore` + `make_relational_store`),
  file storage (`BlobStore`+`LocalBlobStore`), auth (`current_user_id`), authz (`owned_profile`).
- **Multi-tenant schema (additive):** `users` (plan/limits_json/is_admin), `user_id` columns on
  `user_profiles`/`saved_searches`, `usage_counters`. Postgres-portable SQL.
- **Correctness:** query/body **IDOR fixes** on `/api/jobs`, `/api/jobs/by-state`, `/api/match/deep`,
  `/api/match`, `/api/profiles/{id}/attach-resume/{source}`, `/api/search/run`; startup **stale-run
  reaper**; CORS made valid+config-driven.
- **Dormant guard rails (off by default):** rate limit, upload size/type, request-size, security headers,
  per-account quotas, `require_auth` gate. Enable via `docs/pre-deployment-checklist.md`.
- **Entitlements + admin:** `resolve_limits`/`record_usage`/`check_quota` (metering split from
  enforcement); `/api/admin/*` (list users, grant/revoke premium, usage, metrics) + `/api/users/me` +
  a frontend **Admin** tab (visible only to admins); `/api/users/me/export` + `DELETE /api/users/me/data`.
- **Quality:** `scripts/check.sh`, GitHub Actions CI, `Dockerfile`, logging config, `requirements-lock.txt`.
- **UI polish:** global page-scroll lock (no outer scrollbar), fixed-height modals (`Modal` `tall` prop),
  single-scroll "How JobScout works", deterministic "Tailor as" row + always-available **Set active**.
- **Tests:** ~576 backend tests green; ruff + mypy + tsc + vite clean.

## 3. Data-model truths + the recommended workflow (DECIDED)
- Roles are **for the user / everyone** (global pool). A profile only changes the *lens*, not the data.
- **Recommended: ONE profile + many resumes** (Q1 = "master resume", no auto-merge). Multiple profiles
  add friction without benefit unless target careers are genuinely divergent. Multi-profile still works.
- **Two sources of truth (intentional split):**
  - **Matching + deep-match** read your **editable profile** — the active resume's text plus your manual
    edits / AI-polish in the Profile tab (they sync into `resume_text`). Edits flow here.
  - **Tailoring** builds from the toolkit's **verified `canonical.json`** with a no-fabrication audit —
    NOT the editable profile. Edits/AI-polish do NOT change the tailored DOCX (by design). Unifying the
    two is a deferred future option (trades away the audit) — see `docs/ROADMAP-CURRENT.md`.
- Deep-match & tailoring both run under the **same chosen profile** for a job (fixed — T4 done).

## 4. Ordered tasks — STATUS: T1–T6 DONE (2026-07-15). Kept for the record + reproducibility.

### ✅ T1 — This doc.
### ✅ T2 — Consolidated to 1 profile (13 target titles, 3 resumes in its library; the 2 duplicate
profiles deleted; zero job-state loss).
### ✅ T3 — One-profile + source-of-truth docs (HelpModal + user-guide corrected: matching uses editable
profile; tailoring uses canonical facts).
### ✅ T4 — Deep-match now uses `tailorProfileId` (both AI actions use the chosen profile).
### ✅ T5 — ruff+mypy+tsc+vite green; instance live on :5173/:8001; 1 profile.
### ✅ T6 — Roadmap won't-do updated (user-level For You dropped; tailoring-unify deferred).

<details><summary>Original T2 runbook (reproduce the consolidation on another account)</summary>

### T2 — Consolidate duplicate profiles → 1 (SAFE only if the extras have **zero** saved/applied/hidden jobs)
Backend on `http://localhost:8001`. IDs (re-verify with `GET /api/profiles`): primary
`<PRIMARY_ID>` (the profile with the most resumes); merge-in `<A_ID>`, `<B_ID>` (the duplicates).
**Use the resume LIBRARY, not `attach-resume`** — `attach-resume` *overwrites* the destination's active
resume; to preserve every distinct resume, download each source file and upload it into the primary's
library. (Use FULL resume UUIDs, not truncated ids.)
```bash
BASE=http://localhost:8001; PRIMARY=8448c9ca-...; A=ec980a34-...; B=4ab02892-...
# 1. for each source profile's resume: download the file (full rid) then upload into the primary library
RID=$(curl -s "$BASE/api/profiles/$A/resumes" | python3 -c "import sys,json;print(json.load(sys.stdin)['resumes'][0]['id'])")
curl -s "$BASE/api/profiles/$A/resumes/$RID/file" -o /tmp/r.docx           # verify size + PK.. magic
curl -s -X POST "$BASE/api/profiles/$PRIMARY/resumes" -F "file=@/tmp/r.docx"
# (skip byte-identical duplicates)
# 2. union target_titles onto the primary (GET, merge+dedupe, PUT the whole profile back)
# 3. ONLY after uploads succeed, delete the redundant profiles
curl -s -X DELETE "$BASE/api/profiles/$A"; curl -s -X DELETE "$BASE/api/profiles/$B"
# 4. verify
curl -s "$BASE/api/profiles" | python3 -c "import sys,json;print(len(json.load(sys.stdin)),'profile(s)')"
```
Acceptance: exactly 1 profile; its `GET .../resumes` lists all distinct uploads; best resume active.
**Never delete before the uploads return 200.**
</details>

## 5. Run / verify commands
```bash
# Backend (from repo root — sources.yaml resolves from CWD)
.venv/bin/uvicorn backend.jobscout.api.main:app --host 127.0.0.1 --port 8001
# Frontend
cd frontend && npm run dev            # http://localhost:5173
# Full gate
bash scripts/check.sh
# Backend tests only
.venv/bin/python -m pytest backend/tests -q
```
If two uvicorns fight over :8001: `lsof -ti :8001 | xargs kill -9` then relaunch.

## 6. Deferred (Tier-3, each seam-ready — see docs/pre-deployment-checklist.md)
Real auth provider (Google/email), Postgres, S3/GCS, managed Weaviate, durable job queue, per-user
billing (Stripe), PII encryption at rest, secret manager, observability exporters, compliance
(privacy/ToS/DPA + sub-processor disclosure for DeepSeek/NVIDIA/Gemini). None block single/small-group use.

## 7. Status snapshot
- **Large-scale ready?** Single/small-group-perfect; multi-tenant-*ready* (seams in place); guard rails
  dormant; operator console live. Flip flags + build Tier-3 before public launch.
- **Security:** the 2 latent IDORs found were fixed; no known open holes; auth is a one-function drop-in.
- **Bugs:** none known (T4 fixed); ~576 backend tests green.
- **Last verified:** 2026-07-15 — ruff + mypy + tsc + vite green; :5173 + :8001 live; 1 consolidated profile.
