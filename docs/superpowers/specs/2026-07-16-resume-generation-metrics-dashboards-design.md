# Resume generation (LaTeX) + AI-reduction metrics + dashboards — design

- **Date:** 2026-07-16
- **Status:** ✅ Implemented (2026-07-16). See `docs/ROADMAP-CURRENT.md` Round 6.
  Realization note: the LLM emits a structured JSON content plan (not raw LaTeX); a deterministic
  escaped renderer injects it into a fixed template — the strongest form of "constrain the template
  surface the model may edit" (§10), removing compile-fragility and making the audit tractable.
- **Source of ideas:** the personal resume-tailoring toolkit at
  `/Users/ndingari/Dropbox/Resume/Resume - Data/` (LaTeX→PDF/DOCX pipeline,
  `code/metrics_advanced.py` AI-text-detection suite, `code/report_helpers.py` HTML dashboard).

## 1. Goal

Bring three capabilities into JobScout, driven by the resumes it already tailors per
`(candidate, job)`:

1. **Replace the resume generator** with a LaTeX→PDF/DOCX engine (the DOCX-only Node
   builder becomes a config-selectable fallback).
2. **AI-reduction scoring** — a lightweight port of the AI-text-detection metric suite,
   computing a before/after "humanization" bundle on each tailored resume.
3. **Two native React dashboards** — per-job and per-candidate — visualizing the metrics
   with the `dataviz` design system.

## 2. Confirmed decisions

| Question | Decision |
|---|---|
| Core goal | Replace the generator **and** add metrics + dashboards. |
| Candidate model | **Multi-candidate**: each JobScout profile is a candidate; source content is its `structured_resume` (preferred) or `resume_text`. No Dingari-specific `master_content.md`. |
| Dashboard delivery | **Native React panels** (not reused HTML / not iframes), fed by metrics JSON. |
| Metric-suite weight | **Lightweight**: no torch/GPT-2, no sentence-transformers. `nltk`/`textstat` optional with graceful degradation. |
| Generator strategy | **Approach A** — LaTeX engine is the default; Node builder reachable via `settings.tailor_engine="node"`. No silent auto-fallback. |
| Truthfulness | Keep the no-fabrication stance. LLM writes LaTeX **constrained to canonical facts**, then an audit runs. |
| Personas | **Dropped.** Each profile is its own candidate. |
| Audit hardness | **Warn-only.** Ungrounded claims surface as warnings; a build is never blocked by the audit. |
| System toolchain | xelatex (TeX Live 2025) + pandoc 3.7 confirmed present on the dev machine. |

## 3. Architecture

Three layers, each independently shippable. Build order: **metrics → generator → dashboards**.

### 3.1 Modules

| Module | New/changed | Role | Depends on |
|---|---|---|---|
| `backend/jobscout/resume_metrics.py` | new | Lightweight metric suite. Pure functions, no LLM. | stdlib; optional `nltk`, `textstat` |
| `backend/jobscout/latex_tailor.py` | new | LaTeX engine: `build_latex_resume(job, profile)`. | `enrich.chat_json`, `resume_metrics`, xelatex, pandoc |
| `backend/jobscout/resume_templates/resume_template.tex` | new | Candidate-agnostic LaTeX template (de-personalized port). | — |
| `backend/jobscout/tailor.py` | changed | `build_tailored_resume()` dispatches on `settings.tailor_engine`. Shared gate + record shape. | `latex_tailor` |
| `backend/jobscout/models.py` | changed | `TailoredResumeRecord` gains metric/PDF/engine fields. | — |
| `backend/jobscout/relational.py` | changed | `tailored_resumes` gets additive columns. | — |
| `backend/jobscout/api/main.py` | changed | New PDF/metrics/dashboard routes. | — |
| `frontend/src/components/CandidateDashboard.tsx` | new | Per-candidate dashboard (Profile tab). | dataviz, `PipelineStats` |
| `frontend/src/components/JobDashboard.tsx` | new | Per-job dashboard (JobDetailPane). | dataviz |

### 3.2 Data model — `TailoredResumeRecord` additions

- `engine: Literal["latex","node"] = "latex"`
- `pdf_filename: str = ""` — sibling `.pdf` next to the `.docx`
- `metrics_json: str = ""` — serialized `{before, after, risk_before, risk_after, deltas}`
- `ai_risk_after: float | None = None` — denormalized for cheap dashboard sorting

DuckDB `tailored_resumes` gains `engine`, `pdf_filename`, `metrics_json`, `ai_risk_after`
via additive `ALTER TABLE … ADD COLUMN` (matching the existing migration style in
`relational.py`).

### 3.3 API

All new routes live under `/api/profiles/{id}/…`, so `enforce_profile_ownership`
guards them automatically (no new IDOR surface).

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/profiles/{id}/tailor/{job_id}` | Unchanged contract; runs the LaTeX engine, returns `metrics` + `pdf_download_url`. |
| GET | `/api/profiles/{id}/tailored/{job_id}/pdf` | Serve the tailored PDF. |
| GET | `/api/profiles/{id}/tailored/{job_id}/metrics` | Metrics bundle for the per-job dashboard. |
| GET | `/api/profiles/{id}/dashboard` | Candidate roll-up: profile summary + tailored resumes (with `ai_risk_after`) + pipeline-analytics funnel. |

The per-job dashboard composes from existing job/verdict endpoints + the metrics route;
no new job endpoint.

## 4. LaTeX generation engine (`latex_tailor.py`)

`build_latex_resume(job, profile) -> TailoredBuild` flow:

1. **Gate** — reuse `resume_tailoring_gate` (US-role / no-citizenship-wall) before any tokens.
2. **Assemble candidate source** — `profile.structured_resume` (preferred) or `resume_text`
   is the canonical fact set (the per-candidate `master_content` equivalent).
3. **LLM writes LaTeX** — one call via `enrich.chat_json` (inherits NVIDIA→DeepSeek
   failover). Prompt fills `resume_template.tex`, selects/rewrites bullets **only from
   canonical facts**, obeys anti-AI-tell rules (lead with outcome, no em-dashes, periods
   on bullets).
4. **Compile** — `xelatex` → PDF (primary); `pandoc` .tex → DOCX (restyled). Both through
   the existing `_run_checked` subprocess helper with timeout, in a temp build dir.
5. **Audit (warn-only)** — extract atomic claims (employers, titles, date ranges, numeric
   metrics, credentials) and verify each is grounded in the canonical source. Ungrounded
   claims → `warnings`. **Never blocks.** Pure-function, unit-testable.
6. **Metrics** — `resume_metrics.delta(before=active_resume_text, after=tailored_text)`;
   bundle persisted on the record.
7. **Persist** — PDF + DOCX under `tailored_resume_storage_dir/{profile}/`, metrics on the
   record.

**Failure handling:** any step raising → `TailoringError` with the xelatex log tail,
surfaced in the per-job dashboard. The Node engine is used **only** when
`settings.tailor_engine="node"`, never as a silent auto-fallback (silent fallback would
hide a broken template).

## 5. Metrics service (`resume_metrics.py`)

Lightweight port — families needing no torch/sentence-transformers:

- **Kept:** burstiness / sentence-length variance (perplexity proxy), readability (textstat,
  optional), lexical richness (MTLD / Yule's K / Maas), char-trigram entropy,
  sentence-structure, function-vs-content word ratios, repetition/diversity (non-embedding),
  AI-buzzword density, composite AI-risk rollup.
- **Dropped:** sentence-transformer embedding family, GPT-2 LM perplexity.
- **Graceful degradation:** optional `textstat`/`nltk`; a missing dep degrades that one
  family to `null`, never crashes.

Public surface (pure, unit-testable):

- `compute_metrics(text) -> dict`
- `ai_risk(bundle) -> {score, band, drivers}`  (band ∈ good/warning/serious)
- `delta(before, after) -> list[{metric, before, after, better}]`

## 6. Dashboards (native React, `dataviz`)

- **Per-candidate** — new **Dashboard** card in the Profile tab: candidate header (active
  resume, target roles), a table of every tailored resume with its **AI-risk-after** ring +
  build date + PDF/DOCX links (sortable by AI-risk), and the pipeline-analytics funnel
  (reuse `PipelineStats`).
- **Per-job** — in `JobDetailPane` for the active candidate: the tailored resume for this
  job, its **AI-risk ring**, the **before→after delta table**, audit warnings, and
  PDF/DOCX/tailor actions. No tailor yet → "Tailor for this candidate" CTA.

Rings encode a single score (magnitude, not category) → no categorical palette to validate;
risk bands use the dataviz status ramp (good/warning/serious) with icon+label, never
color-alone.

## 7. Testing

- Pure-function unit tests for every metric family + `ai_risk` + `delta` (fixed strings,
  deterministic).
- Audit unit tests: grounded vs ungrounded claims → correct warnings; never raises.
- LaTeX-build integration test **gated on `shutil.which("xelatex")`** so TeX-less CI skips it.
- API smoke for the new routes via the fake-store fixtures.
- Frontend `tsc --noEmit` + `vite build`.

## 8. Docs (definition of done, enforced by repo CLAUDE.md)

Update in the same change: `docs/api.md` (new routes), `docs/architecture.md` (tailoring
engine + metrics flow, mermaid), `docs/configuration.md` (`tailor_engine`, template dir,
optional metric deps), `docs/user-guide.md` (dashboards), `docs/ROADMAP-CURRENT.md` (new
round), `PR_DESCRIPTION.md`.

## 9. Out of scope / YAGNI

- torch / GPT-2 perplexity, sentence-transformer embedding metrics.
- Persona system.
- Cover-letter generation (template may be ported later; not built now).
- Reusing the raw `report_helpers.py` HTML (dashboards are native React instead).
- Auto-fallback between engines (explicit setting only).

## 10. Open risks

- **LLM-authored LaTeX can fail to compile** (bad macros). Mitigation: constrain the
  template surface the model may edit; retry once with the xelatex error fed back; else
  `TailoringError`.
- **Audit is heuristic.** Warn-only by decision, so false negatives don't block; they're
  visible in the dashboard for human review.
- **Per-resume latency** rises (xelatex+pandoc+metrics). Acceptable: tailoring is an
  explicit, on-demand, per-job action.
