# API reference

FastAPI app at `http://localhost:8000`. Interactive docs (Swagger) at `/docs`. All endpoints return
JSON. This is the local, unauthenticated API for the single-user tool.

## Jobs & search

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/jobs` | Main search + filter. Params: `q`, `remote[]`, `visa[]`, `exp[]`, `date_range`, `source[]`, `company_size[]`, `cap_exempt[]`, `employer_type[]`, `security_clearance[]`, `exclude_no_sponsorship`, `h1b_sponsor`, `profile_id`, `target_min`, `alpha`, `sort`, `page`, `page_size`. With `profile_id`, response includes `verdicts` (fit + matched/gaps), cap-exempt-first sort, applied/hidden excluded. |
| GET | `/api/jobs/by-state` | Jobs a profile marked with `status` (`applied`\|`saved`\|`seen`\|`hidden`), newest first, verdict-scored. Params: `profile_id` (required), `status`. Powers Shortlist/Applied. |
| GET | `/api/jobs/{job_id}` | One job by canonical id. |
| POST | `/api/search/run` | Trigger on-demand ingestion ("Get latest jobs"). Body: `{keywords[], location?, results_wanted?}`. Returns `RunLog` stubs; work runs in the background. |

## Resume matching

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/match` | Resume **text** → matched jobs. Body: `{resume_text, profile_id?, limit?}`. |
| POST | `/api/match/upload` | Resume **file** (multipart `file` + `limit`) → extract → parse → **save profile** → matched jobs. Returns `{profile, jobs, verdicts}` with matched/gap keywords. |

## Profiles & job state

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/profiles` | List all saved profiles. |
| GET | `/api/profiles/{id}` | One profile. |
| POST | `/api/profiles` | Create/update a profile (JSON body = `UserProfile`). |
| DELETE | `/api/profiles/{id}` | Delete a profile (+ its job-state rows). |
| POST | `/api/profiles/{id}/job-state` | Mark a job. Body: `{job_id, status, note?}`. Status ∈ triage (`saved`\|`seen`\|`hidden`) or pipeline (`applied`\|`oa`\|`interview`\|`offer`\|`rejected`). |
| GET | `/api/profiles/{id}/pipeline` | Application tracker: `{jobs, stages:{job_id:{stage,note,updated_at}}}`. |

## Companies (registry)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/companies` | List/filter the registry. Params: `tier`, `ats`, `size`, `h1b_sponsor`, `enabled`, `direct_apply_only`, `sort`. |
| POST | `/api/companies` | Add/update a company (JSON body = `Company`). |
| POST | `/api/companies/refresh` | Incremental refresh of enabled companies (new jobs only, budget-capped). Body: `{keywords?, budget?}`. |

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
| POST | `/api/maintenance/purge` | Delete jobs older than `{days}` (explicit cleanup). |
| GET | `/api/saved-searches` | List saved searches, each with a live `new_count` (matches ingested since last seen). |
| POST | `/api/saved-searches` | Save current query+filters. Body: `{label, filters, profile_id?}`. |
| POST | `/api/saved-searches/{id}/seen` | Mark seen (resets `new_count`). |
| DELETE | `/api/saved-searches/{id}` | Delete a saved search. |

## Notes
- **Route ordering:** `/api/jobs/by-state` is declared before `/api/jobs/{job_id}` so the literal path
  wins over the path param.
- **Cost:** `/api/search/run`, `/api/companies/refresh`, and `/api/match/upload` consume DeepSeek +
  Gemini calls (1 each per new job / per resume). Mind the Gemini free-tier 1,000 embeds/day.
