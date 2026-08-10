# API reference

FastAPI app at `http://localhost:8000`. Interactive docs (Swagger) at `/docs`. All endpoints return
JSON. This is the local, unauthenticated API for the single-user tool — but every profile-scoped route
is already ownership-guarded (a non-owner gets **404**, never 403) and the global-write routes are
admin-gated, so adding real login later can't leak data. See [multi-tenancy.md](multi-tenancy.md).

## Jobs & search

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/jobs` | Main search + filter. `recommendation_only=true` requires `profile_id` and returns only jobs that pass target-role/profession, experience, seniority, skill/resume evidence, location, work-authorization, specialty, and known work-mode gates. Junior profiles additionally require stated compatible experience or an explicit junior/Level I/associate/new-grad signal. `target_min` progressively widens personalized retrieval through one month without adding unrelated filler. A sparse/stale recommendation request starts a deduplicated, cooldown- and quota-bounded profile refill and reports `recommendation_refreshing=true`. Fit/verdict rank before optional cap-exempt preference. |
| GET | `/api/jobs/by-state` | Jobs a profile marked with `status` (`applied`\|`saved`\|`seen`\|`hidden`), newest first, verdict-scored. Params: `profile_id` (required), `status`. Powers Shortlist/Applied. |
| GET | `/api/jobs/{job_id}` | One job by canonical id. |
| GET | `/api/jobs/{job_id}/profile-fits` | Score this job against each of the caller's profiles (deterministic `score_verdict`, **no LLM/embedding**) → `{fits:[{profile_id,label,score,verdict,recommendable}]}` sorted by score desc. Powers the detail pane's "Tailor as [best-fit profile]" default. 404 on unknown job; `[]` with no profiles. |
| POST | `/api/search/run` | Trigger on-demand ingestion ("Get latest jobs"). Body: `{keywords[], location?, results_wanted?}`. Returns `RunLog` stubs; work runs in the background. |

## Resume matching

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/match` | Resume **text** → matched jobs. Body: `{resume_text, profile_id?, limit?}`. |
| POST | `/api/match/upload` | Resume **file** (multipart `file` + `limit`) → extract → parse → **save profile** → matched jobs. Returns `{profile, jobs, verdicts}`. A label collision auto-suffixes ("name (2)") so duplicate uploads never create indistinguishable profiles. |
| POST | `/api/match/deep/{job_id}` | LLM "second opinion" for one job vs a profile. Body `{profile_id}` → `{verdict, score, strengths, gaps, summary, cached}`. **Persisted** to DuckDB by (job, profile, fingerprint) — survives restart, never re-billed; a changed resume/profile flips the fingerprint so a stale score is recomputed. |
| POST | `/api/profiles/{id}/deep-results` | Rehydrate already-computed deep results without spending. Body `{job_ids:[…]}` → `{results:{job_id:DeepMatch}}`, returning only rows whose stored fingerprint matches the CURRENT profile+resume (so nothing stale is shown after an edit). |

## Profiles & job state

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/profiles` | List all saved profiles. |
| GET | `/api/profiles/{id}` | One profile. |
| POST | `/api/profiles` | Create/update a profile (JSON body = `UserProfile`). |
| PUT | `/api/profiles/{id}` | Save edits to the full canonical profile and editable resume text. |
| GET | `/api/profiles/{id}/resume` | Download the active profile's original local PDF/DOCX/TXT upload. |
| POST | `/api/profiles/{id}/reparse` | Rebuild skills, target roles, experience, and sponsorship from saved resume text. |
| POST | `/api/profiles/{id}/attach-resume/{source_id}` | Copy another profile's saved resume (text + file) onto a metadata-only profile without losing its preferences. |
| POST | `/api/profiles/{id}/suggest` | ADD-ONLY AI suggestions for one list field. Body `{field}` (interests, avoid_role_types, avoid_domains, target_titles, skills) → `{suggestions[]}` filtered against current values. 1 LLM call; read-only — the UI adds accepted items via the normal PUT. |
| POST | `/api/profiles/{id}/tailor/{job_id}` | Apply the hard JD gate, then build + audit a tailored resume. The default **LaTeX engine** (`settings.tailor_engine="latex"`) has the LLM write a canonical-constrained content plan from the profile's own resume, renders it to **PDF + DOCX** (xelatex/pandoc), runs a warn-only fabrication audit, and scores before/after **AI-reduction metrics**. Returns `download_url`, `pdf_download_url`, `engine`, and the `metrics` bundle. (`tailor_engine="node"` keeps the legacy private DOCX toolkit.) Records the build in the tailored catalog. |
| GET | `/api/profiles/{id}/tailored/{job_id}` | Download the audited tailored DOCX. Filename is the **stored** name (user-editable, source of truth); legacy rows fall back to a computed dated name. |
| GET | `/api/profiles/{id}/tailored/{job_id}/pdf` | Download the LaTeX-engine PDF for a tailored resume (404 if none was built). |
| GET | `/api/profiles/{id}/tailored/{job_id}/metrics` | The AI-reduction metric bundle `{before, after, delta, ai_risk_*, humanization_*}` for one tailored resume (powers the per-job dashboard). 404 when the resume has no metrics. |
| GET | `/api/profiles/{id}/dashboard` | Per-candidate dashboard: `{profile, tailored[], pipeline}` — profile summary, every tailored resume with its `ai_risk_after` + PDF/DOCX links + `up_to_date`, and the pipeline-analytics funnel. |
| PATCH | `/api/profiles/{id}/tailored/{job_id}` | Rename a built tailored resume's download filename. Body `{filename}` → trimmed, forced `.docx`, sibling-collisions auto-suffixed ("name (2).docx"); returns the saved name. |
| DELETE | `/api/profiles/{id}` | Delete a profile (+ its job-state, resume-library, and tailored-catalog rows). |

### Resume library (many uploads per profile, one active for matching)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/profiles/{id}/resumes` | `{active_resume_id, resumes[]}` (metadata only), newest first. Lazily adopts a pre-library single upload as record 0. |
| POST | `/api/profiles/{id}/resumes` | Add a resume (multipart `file`) → extract + parse → store. First upload auto-activates. Same-name uploads auto-suffix ("resume (2).docx"). Returns the updated library. |
| POST | `/api/profiles/{id}/resumes/{rid}/activate` | Make this resume active — projects its text/sections/structured/skills/targets onto the profile and re-scores matching. |
| PATCH | `/api/profiles/{id}/resumes/{rid}` | Rename a resume's display label. Body `{filename}`. Sibling-name collisions auto-suffix ("name (2)"); the saved name is returned so the UI can show what happened. |
| GET | `/api/profiles/{id}/resumes/{rid}/file` | Download that resume's original file. |
| DELETE | `/api/profiles/{id}/resumes/{rid}` | Delete a resume; if active, the next-newest is promoted. |
| GET | `/api/profiles/{id}/tailored` | List built tailored resumes (company, title, `filename`, recommendation, date, `download_url`), newest first. Each row carries `up_to_date` — false when the profile's resume changed since the build (fingerprint mismatch), so the UI can prompt a re-tailor. |
| POST | `/api/profiles/{id}/job-state` | Mark a job. Body: `{job_id, status, note?}`. Status ∈ triage (`saved`\|`seen`\|`hidden`) or pipeline (`applied`\|`oa`\|`interview`\|`offer`\|`rejected`). |
| GET | `/api/profiles/{id}/pipeline` | Application tracker: `{jobs, stages:{job_id:{stage,note,updated_at}}, analytics}`. `analytics` is a funnel rollup — `total_applications`, `by_stage`, `responded`, `response_rate`/`screening_rate`/`interview_rate`/`offer_rate` (0–1), and `by_source[]` (per-provenance apps/replied/offers). Computed over ALL pipeline rows, including jobs that have aged out of the index. |

## Companies (registry)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/companies` | List/filter the registry. Params: `tier`, `ats`, `size`, `h1b_sponsor`, `enabled`, `direct_apply_only`, `sort`. |
| POST | `/api/companies` | Add/update a company (JSON body = `Company`). |
| POST | `/api/companies/refresh` | Incremental refresh of enabled companies (new jobs only, budget-capped). Body: `{keywords?, budget?}`. |
| POST | `/api/companies/discover` | Probe candidate company slugs across ATS providers; returns verified boards to add. |
| POST | `/api/companies/validate` | Validate one company's ATS + slug before adding it to the watchlist. |

## Operations

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/stats` | Aggregate counts: total jobs, by source, by date bucket. |
| GET | `/api/sources/status` | Per-source last-run info. |
| POST | `/api/enrich/run` | Re-enrich pending/failed jobs. Body: `{limit?}`. |
| GET | `/api/scheduler` | Daily auto-refresh status `{enabled, hour, embed_daily_budget, next_run}`. |
| POST | `/api/scheduler` | Enable/disable the daily scheduler at runtime. Body: `{enabled}`. **Off by default.** |
| GET | `/api/sources/overrides` | Runtime source toggles (e.g. JobSpy). |
| POST | `/api/sources/overrides` | Toggle a high-risk source. Body: `{jobspy: true}`. |
| POST | `/api/maintenance/purge` | Delete jobs older than `{days}` (explicit cleanup). Automatic retention also prunes jobs older than `RETENTION_DAYS` after each ingest. |
| POST | `/api/maintenance/backfill-lever-descriptions` | One-off: fetch full descriptions for Lever jobs ingested before detail-fetching existed. |
| GET | `/api/saved-searches` | List saved searches, each with a live `new_count` (matches ingested since last seen). |
| POST | `/api/saved-searches` | Save current query+filters. Body: `{label, filters, profile_id?}`. |
| POST | `/api/saved-searches/{id}/seen` | Mark seen (resets `new_count`). |
| DELETE | `/api/saved-searches/{id}` | Delete a saved search. |

## Account data lifecycle

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/users/me/export` | Export the calling user's data (profiles + their resumes/tailored/pipeline + saved searches). Right-to-access / local backup; scoped to `current_user_id`. |
| DELETE | `/api/users/me/data` | Delete ALL of the calling user's profiles (+ resumes, tailored files, job-state) and saved searches. Right-to-erasure / clean reset. |

## Admin & operator monitoring

All `/api/admin/*` routes are behind `require_admin` (open to the local operator while `single_user_mode`;
`users.is_admin` once hosting). Per-user usage is populated when `usage_metering_enabled` is on.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/users` | Every account with `plan`, `is_admin`, `profile_count`, `storage_bytes`, and 30-day per-metric usage. |
| PATCH | `/api/admin/users/{id}` | Grant/revoke premium + limits: set `plan` / `limits_json` / `is_admin`. Reflected immediately by `resolve_limits`. |
| GET | `/api/admin/users/{id}/usage` | Per-metric usage rollups (today / 7-day / 30-day) for one account. |
| GET | `/api/admin/metrics` | Deployment aggregates: user count, total per-metric usage (LLM/tailor/deep-match/requests), total storage, metering/enforcement flags. |

## Notes
- **Route ordering:** `/api/jobs/by-state` is declared before `/api/jobs/{job_id}` so the literal path
  wins over the path param.
- **Admin-gated (global-write) routes:** `PUT /api/settings`, `POST /api/scheduler`,
  `POST /api/sources/overrides`, and `POST /api/maintenance/*` write the server `.env`, toggle shared
  scrapers, or mutate/purge the global index. They are open while `single_user_mode` (local admin) and
  return **403** once hosting is enabled. See [multi-tenancy.md](multi-tenancy.md).
- **Cost:** `/api/search/run`, profile recommendation refills, `/api/companies/refresh`, and `/api/match/upload` consume DeepSeek +
  Gemini calls (1 each per new job / per resume). Mind the Gemini free-tier 1,000 embeds/day.


## Recent endpoints

- `GET /api/health` — `{embeddings_ok, llm_ok, llm_provider_effective, weaviate_ok, seeding, problems[]}`;
  each problem carries `message` + `fix`. `seeding=true` while the one-time first-run job seed runs.
- `POST /api/profiles/{id}/structure` — parse stored resume text into `structured_resume`
  (typed education/experience/projects/certifications/skill_categories/custom_sections). 1 LLM call.
- `POST /api/profiles/{id}/polish` — body `{section, index}`; returns
  `{bullets: [{original, suggested}]}` (truthfulness-constrained rewrites; read-only). 1 LLM call.
- `POST /api/profiles/{id}/tailor/{job_id}` — now gated: returns `{built: false, gate}` when the
  verdict/deep-match conclusion is *skip*, unless body `{"force": true}`.
