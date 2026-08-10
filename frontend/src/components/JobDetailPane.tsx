// Right-hand detail pane: full JD, verdict reasons/flags, sponsorship + provenance badges, apply/save/hide + deep-match actions.
import DOMPurify from 'dompurify'
import { formatDistanceToNow, parseISO } from 'date-fns'
import {
  ArrowSquareOut,
  CalendarBlank,
  Copy,
  CurrencyDollar,
  FileDoc,
  GraduationCap,
  MagicWand,
  Warning,
  X,
} from '@phosphor-icons/react'
import { useState } from 'react'
import type { DeepMatch, Job } from '../types'
import { useJob, useDeepMatch, useCachedDeepMatch, useTailorResume, useTailoredResumes, useTailoredMetrics, useProfiles, useProfileFits } from '../api/client'
import { JobDashboard } from './JobDashboard'
import { useActiveProfile } from '../ProfileContext'
import { SponsorshipBadge, EVerifyBadge } from './SponsorshipBadge'

interface JobDetailPaneProps {
  jobId: string
  onClose?: () => void
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatSalary(min: number | null, max: number | null, currency: string | null): string | null {
  if (min === null && max === null) return null
  const sym = currencySymbol(currency)
  const fmt = (n: number) => {
    if (n >= 1000) return `${sym}${Math.round(n / 1000)}k`
    return `${sym}${n.toLocaleString()}`
  }
  if (min !== null && max !== null) return `${fmt(min)} – ${fmt(max)}`
  if (min !== null) return `${fmt(min)}+`
  return `Up to ${fmt(max!)}`
}

function currencySymbol(currency: string | null): string {
  if (!currency) return '$'
  const map: Record<string, string> = {
    USD: '$', EUR: '€', GBP: '£', CAD: 'C$', AUD: 'A$', INR: '₹',
  }
  return map[currency.toUpperCase()] ?? `${currency} `
}

function RemoteModeBadge({ mode }: { mode: Job['remote_mode'] }) {
  const styles: Record<Job['remote_mode'], string> = {
    remote: 'bg-emerald-100 text-emerald-700',
    hybrid: 'bg-blue-100 text-blue-700',
    onsite: 'bg-slate-100 text-slate-600',
    unknown: 'bg-slate-100 text-slate-500',
  }
  const labels: Record<Job['remote_mode'], string> = {
    remote: 'Remote', hybrid: 'Hybrid', onsite: 'On-site', unknown: 'Unknown',
  }
  return (
    <span className={`tag ${styles[mode]}`}>
      {labels[mode]}
    </span>
  )
}

function CompanySizeBadge({ bucket }: { bucket: string }) {
  return (
    <span className="tag bg-signal-50 text-signal-700">
      {bucket} employees
    </span>
  )
}

// ---------------------------------------------------------------------------
// Description renderer
// ---------------------------------------------------------------------------
// Many sources (The Muse, RemoteOK, Working Nomads, Greenhouse) return the
// description as HTML; others (Adzuna, Lever) return plain text. Render HTML
// (sanitized against XSS) so tags don't leak as literal text, and fall back to
// the plain-text paragraph renderer otherwise.

const _HTML_TAG = /<\/?[a-z][\s\S]*?>/i

function DescriptionRenderer({ text }: { text: string }) {
  if (_HTML_TAG.test(text)) {
    const clean = DOMPurify.sanitize(text, { USE_PROFILES: { html: true } })
    return (
      <div
        className="job-description"
        dangerouslySetInnerHTML={{ __html: clean }}
      />
    )
  }

  const paragraphs = text.split(/\n{2,}/).filter((p) => p.trim())
  return (
    <div className="space-y-3">
      {paragraphs.map((para, idx) => {
        const lines = para.split('\n')
        const isBulletBlock = lines.every((l) => /^[\-\*\•]\s/.test(l.trim()) || l.trim() === '')
        if (isBulletBlock) {
          return (
            <ul key={idx} className="list-disc list-inside space-y-1">
              {lines
                .filter((l) => l.trim())
                .map((line, li) => (
                  <li key={li} className="text-sm text-slate-700 leading-relaxed">
                    {line.replace(/^[\-\*\•]\s+/, '')}
                  </li>
                ))}
            </ul>
          )
        }
        return (
          <p key={idx} className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
            {para}
          </p>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function PaneSkeleton() {
  return (
    <div className="p-8 space-y-4 animate-pulse">
      <div className="h-7 bg-slate-200 rounded w-3/4" />
      <div className="h-4 bg-slate-100 rounded w-1/2" />
      <div className="flex gap-2 pt-2">
        <div className="h-6 bg-slate-200 rounded w-16" />
        <div className="h-6 bg-slate-200 rounded w-14" />
        <div className="h-6 bg-slate-200 rounded w-20" />
      </div>
      <div className="space-y-2 pt-6">
        <div className="h-3 bg-slate-100 rounded w-full" />
        <div className="h-3 bg-slate-100 rounded w-5/6" />
        <div className="h-3 bg-slate-100 rounded w-4/5" />
        <div className="h-3 bg-slate-100 rounded w-full" />
        <div className="h-3 bg-slate-100 rounded w-3/4" />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main pane — rendered inline in the right column (no overlay)
// ---------------------------------------------------------------------------

function DeepMatchResult({ data }: { data: DeepMatch }) {
  const color =
    data.verdict === 'apply'
      ? 'bg-emerald-100 text-emerald-700'
      : data.verdict === 'skip'
        ? 'bg-red-100 text-red-700'
        : 'bg-amber-100 text-amber-700'
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase ${color}`}>
          {data.verdict}
        </span>
        <span className="text-slate-500">fit {data.score}/100</span>
        {data.cached && <span className="text-xs text-slate-400">(cached)</span>}
      </div>
      {data.summary && <p className="text-slate-700">{data.summary}</p>}
      {data.strengths.length > 0 && (
        <div>
          <p className="font-medium text-emerald-700">Strengths</p>
          <ul className="ml-4 list-disc text-slate-600">
            {data.strengths.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}
      {data.gaps.length > 0 && (
        <div>
          <p className="font-medium text-amber-700">Gaps</p>
          <ul className="ml-4 list-disc text-slate-600">
            {data.gaps.map((g, i) => (
              <li key={i}>{g}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function JobDetailPane({ jobId, onClose }: JobDetailPaneProps) {
  const { data: job, isLoading, isError, error } = useJob(jobId)
  const { activeProfileId, setActiveProfileId } = useActiveProfile()
  const deepMatch = useDeepMatch()
  const tailored = useTailorResume()

  // "Which profile do I tailor with?" — only matters with ≥2 profiles.
  const { data: profiles } = useProfiles()
  const multiProfile = (profiles?.length ?? 0) > 1
  const { data: fitsData } = useProfileFits(jobId, multiProfile)
  const fits = fitsData?.fits ?? []
  const bestFit = fits[0]  // sorted by score desc server-side
  // User's explicit pick wins; else best-fit for this job; else the active profile.
  const [pickedProfileId, setPickedProfileId] = useState<string | null>(null)
  const tailorProfileId = pickedProfileId ?? bestFit?.profile_id ?? activeProfileId

  // Deep-match runs under the SAME profile the user tailors with (tailorProfileId),
  // so both AI actions agree. Prefer a fresh single run; else the batch-cached result.
  const cachedDeep = useCachedDeepMatch(jobId, tailorProfileId)
  const shownDeep = deepMatch.data ?? cachedDeep

  // "Once tailored, always show it": a previously-built DOCX for this exact job,
  // scoped to the profile we'd actually tailor as.
  const { data: tailoredList } = useTailoredResumes(tailorProfileId)
  const existingTailored = tailoredList?.tailored.find((t) => t.job_id === jobId)
  // AI-reduction metrics for a previously-built resume (per-job dashboard). Skipped
  // while a fresh tailor result is on screen (that response carries its own metrics).
  const persistedMetrics = useTailoredMetrics(tailorProfileId, jobId, !!existingTailored && !tailored.data)

  const salary = job ? formatSalary(job.salary_min, job.salary_max, job.salary_currency) : null
  const postedStr = job?.posted_date
    ? (() => {
        try {
          const rel = formatDistanceToNow(parseISO(job.posted_date), { addSuffix: true })
          if (job.freshness_kind === 'updated') return `Updated ${rel}`
          if (job.freshness_kind === 'estimated') return `Detected ${rel}`
          return `Posted ${rel}`
        } catch {
          return null
        }
      })()
    : null

  return (
    <div className="flex h-full flex-col bg-white">
      {/* Header */}
      <div className="flex-shrink-0 border-b border-slate-200 px-4 py-3.5 sm:px-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            {isLoading ? (
              <>
                <div className="h-6 bg-slate-200 rounded w-64 animate-pulse mb-2" />
                <div className="h-4 bg-slate-100 rounded w-40 animate-pulse" />
              </>
            ) : job ? (
              <>
                <h1 className="max-w-4xl text-[1.08rem] font-semibold leading-[1.35] tracking-[-0.025em] text-ink">
                  {job.title}
                </h1>
                <p className="mt-1 text-xs font-medium text-slate-500">
                  {[job.company, job.city, job.country].filter(Boolean).join(' · ')}
                </p>
                {(job.new_grad_program || job.mislabeled_entry || (job.ghost_risk && job.ghost_risk !== 'low')) && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {job.new_grad_program && (
                      <span
                        title="Explicit new-grad, university, early-career, or rotational program"
                        className="tag gap-1 bg-emerald-100 text-emerald-800">
                        <GraduationCap size={12} />New-grad program
                      </span>
                    )}
                    {job.mislabeled_entry && (
                      <span
                        title={`Titled entry-level but the description asks for ${job.yoe_min}+ years`}
                        className="tag gap-1 bg-rose-100 text-rose-800">
                        <Warning size={12} />Titled junior · wants {job.yoe_min}+ yrs
                      </span>
                    )}
                    {job.ghost_risk && job.ghost_risk !== 'low' && (
                      <span
                        title="Possibly stale or ghost posting. Verify it is still open before applying."
                        className="tag gap-1 bg-amber-100 text-amber-800">
                        <Warning size={12} />Possibly stale{typeof job.posting_age_days === 'number' ? ` · posted ${job.posting_age_days}d ago` : ''}
                      </span>
                    )}
                  </div>
                )}
              </>
            ) : null}
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="control-focus flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-ink"
              aria-label="Close"
            >
              <X size={17} />
            </button>
          )}
        </div>

        {/* Meta + apply row */}
        {!isLoading && job && (
          <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
            <a
              href={job.url}
              target="_blank"
              rel="noopener noreferrer"
              className="control-focus inline-flex h-8 items-center gap-1.5 rounded-lg bg-ink px-3 text-xs font-semibold text-white hover:bg-ink-soft"
            >
              Apply
              <ArrowSquareOut size={14} />
            </a>
            <button
              onClick={() => navigator.clipboard.writeText(job.url)}
              title="Copy link"
              className="control-focus inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
            >
              <Copy size={14} />
              Save link
            </button>
            {/* Tailor: disabled once a CURRENT tailored DOCX exists; re-enabled
                (as "Re-tailor") when the resume changed since it was built. */}
            {(() => {
              const tailorDone = !!existingTailored?.up_to_date && !tailored.data
              const tailorStale = !!existingTailored && !existingTailored.up_to_date
              return (
                <button
                  onClick={() => tailorProfileId && tailored.mutate({ profileId: tailorProfileId, jobId })}
                  disabled={!tailorProfileId || tailored.isPending || tailorDone}
                  title={
                    !tailorProfileId ? 'Select a profile first'
                    : tailorDone ? 'Already tailored for your current resume — re-tailor only if you change your profile or resume'
                    : tailorStale ? 'Your resume changed since this was built — re-tailor to refresh'
                    : 'Create an audited, tailored DOCX using the active profile'
                  }
                  className="control-focus inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 text-xs font-medium text-slate-600 hover:border-signal-200 hover:bg-signal-50 hover:text-signal-800 disabled:opacity-50"
                >
                  <FileDoc size={14} />{tailored.isPending ? 'Tailoring…' : tailorDone ? 'Tailored ✓' : tailorStale ? 'Re-tailor' : 'Tailor DOCX'}
                </button>
              )
            })()}
            {/* Deep match: disabled once a CURRENT result exists; auto re-enables
                when the profile/resume changes (which clears the ['deep'] cache). */}
            <button
              onClick={() => tailorProfileId && deepMatch.mutate({ jobId, profileId: tailorProfileId })}
              disabled={!tailorProfileId || deepMatch.isPending || !!shownDeep}
              title={
                !tailorProfileId ? 'Select a profile first'
                : shownDeep ? 'Already analyzed for your current resume — re-run only if you change your profile or resume'
                : 'AI apply/skip verdict for the selected profile'
              }
              className="control-focus inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 px-2.5 text-xs font-medium text-slate-600 hover:border-signal-200 hover:bg-signal-50 hover:text-signal-800 disabled:opacity-50"
            >
              <MagicWand size={14} />{deepMatch.isPending ? 'Analyzing…' : shownDeep ? 'Deep-matched ✓' : 'Deep match'}
            </button>
          </div>
        )}
        {/* Which profile to tailor as — only shown with ≥2 profiles. Defaults to
            the best-fit profile for THIS job; override without leaving the job.
            The dropdown is tailor-scoped; "Set active" is the explicit global switch. */}
        {!isLoading && job && multiProfile && fits.length > 0 && (
          <div className="mt-2 space-y-1 text-xs">
            {/* Line 1 — controls: identical structure in every state (Set active only
                appears/disappears at the end; the select is width-capped). */}
            <div className="flex items-center gap-2">
              <label htmlFor="tailor-as" className="shrink-0 font-medium text-slate-600">Tailor as</label>
              <select
                id="tailor-as"
                value={tailorProfileId ?? ''}
                onChange={(e) => setPickedProfileId(e.target.value)}
                title={fits.find((f) => f.profile_id === tailorProfileId)?.label}
                className="control-focus w-fit max-w-[22rem] rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs font-medium text-slate-700"
              >
                {fits.map((f) => (
                  <option key={f.profile_id} value={f.profile_id}>
                    {f.label} · {Math.round(f.score * 100)}%
                  </option>
                ))}
              </select>
              {tailorProfileId && tailorProfileId !== activeProfileId && (
                <button
                  type="button"
                  onClick={() => setActiveProfileId(tailorProfileId)}
                  title="Make this the active profile everywhere (For You, badges, deep-match)"
                  className="control-focus shrink-0 font-medium text-signal-700 underline underline-offset-2"
                >
                  Set active
                </button>
              )}
            </div>
            {/* Line 2 — helper: always on its own row, never inline. */}
            <p className="text-slate-400">Tailored resumes are saved under the profile you choose.</p>
          </div>
        )}
        {/* Already-built tailored resume for this job (persists across sessions). */}
        {!isLoading && job && existingTailored && !tailored.data && !tailored.error && (
          <div className="mt-2.5 rounded-lg border border-signal-100 bg-signal-50/60 px-3 py-2.5 text-xs text-slate-700">
            <a href={existingTailored.download_url} className="font-semibold text-signal-700 underline underline-offset-2">
              Download tailored DOCX{existingTailored.filename ? ` (${existingTailored.filename})` : ''}
            </a>
            {persistedMetrics.data && (
              <a href={`${existingTailored.download_url}/pdf`} className="ml-2 font-semibold text-signal-700 underline underline-offset-2">PDF</a>
            )}
            <span className="text-slate-400"> · built {new Date(existingTailored.created_at).toLocaleDateString()}</span>
            {existingTailored.up_to_date
              ? <span className="text-slate-400"> · up to date</span>
              : <span className="text-amber-600"> · your resume changed — re-tailor to refresh</span>}
            {persistedMetrics.data && (
              <JobDashboard metrics={persistedMetrics.data.metrics} warnings={persistedMetrics.data.warnings} />
            )}
          </div>
        )}
        {!isLoading && job && (tailored.data || tailored.error) && (
          <div className="mt-2.5 rounded-lg border border-signal-100 bg-signal-50/60 px-3 py-2.5 text-xs text-slate-700" aria-live="polite">
            {tailored.error ? (
              <p className="text-rose-600">Tailored resume unavailable: {tailored.error.message}</p>
            ) : tailored.data && !tailored.data.built ? (
              /* Pre-flight gate said SKIP: show the conclusion, let the user override. */
              <div className="space-y-1.5">
                <p className="font-semibold text-rose-700">
                  Recommendation: skip this one — the pre-flight check found real walls.
                </p>
                {tailored.data.gate.deep_summary && <p>{tailored.data.gate.deep_summary}</p>}
                <ul className="list-disc space-y-0.5 pl-4">
                  {tailored.data.gate.rule_red_flags.slice(0, 4).map((flag) => <li key={flag}>{flag}</li>)}
                  {(tailored.data.gate.deep_gaps ?? []).slice(0, 3).map((gap) => <li key={gap}>Gap: {gap}</li>)}
                </ul>
                <div className="flex gap-2 pt-1">
                  <button
                    type="button"
                    onClick={() => tailorProfileId && tailored.mutate({ profileId: tailorProfileId, jobId, force: true })}
                    disabled={tailored.isPending}
                    className="control-focus rounded-lg border border-slate-300 bg-white px-2.5 py-1 font-medium text-slate-700 hover:bg-slate-50"
                  >
                    Tailor anyway
                  </button>
                  <button
                    type="button"
                    onClick={() => tailored.reset()}
                    className="control-focus rounded-lg px-2.5 py-1 font-medium text-slate-500 hover:bg-slate-100"
                  >
                    Skip
                  </button>
                </div>
              </div>
            ) : tailored.data ? (
              <div className="space-y-1.5">
                {tailored.data.gate?.recommendation === 'skip' && (
                  <p className="text-amber-700">Built on request despite a skip recommendation — double-check the walls below before applying.</p>
                )}
                <div>
                  <a href={tailored.data.download_url} className="font-semibold text-signal-700 underline underline-offset-2">Download tailored DOCX · {tailored.data.filename}</a>
                  {tailored.data.pdf_download_url && (
                    <a href={tailored.data.pdf_download_url} className="ml-2 font-semibold text-signal-700 underline underline-offset-2">PDF</a>
                  )}
                </div>
                {(tailored.data.notes ?? []).length > 0 && <p>Tailored with {tailored.data.provider}/{tailored.data.model}: {(tailored.data.notes ?? []).join(' · ')}</p>}
                {/* Audit warnings render inside the dashboard; show here only without metrics. */}
                {!tailored.data.metrics && (tailored.data.warnings ?? []).map((warning) => <p key={warning} className="text-amber-700">{warning}</p>)}
                {tailored.data.metrics && (
                  <JobDashboard metrics={tailored.data.metrics} warnings={tailored.data.warnings} />
                )}
              </div>
            ) : null}
          </div>
        )}
        {/* Deep-match result (fresh run, or a result the "Deep-match top N" batch cached) */}
        {!isLoading && job && (shownDeep || deepMatch.error) && (
          <div className="mt-2.5 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs" aria-live="polite">
            {deepMatch.error ? (
              <p className="text-red-600">Deep match failed: {deepMatch.error.message}</p>
            ) : shownDeep ? (
              <>
                <DeepMatchResult data={shownDeep} />
                <p className="mt-1.5 text-[0.68rem] text-slate-400">Re-run only needed if your resume or profile changes.</p>
              </>
            ) : null}
          </div>
        )}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        {isLoading && <PaneSkeleton />}

        {isError && (
          <div className="p-8 text-center">
            <p className="text-sm font-medium text-red-600">Failed to load job details</p>
            <p className="mt-1 text-xs text-slate-500">{error?.message}</p>
          </div>
        )}

        {!isLoading && job && (
          <div className="space-y-4 px-5 py-4 sm:px-6">
            {/* Badges */}
            <div className="flex flex-wrap gap-1">
              <SponsorshipBadge job={job} />
              <EVerifyBadge job={job} />
              <RemoteModeBadge mode={job.remote_mode} />
              {job.company_size_bucket && <CompanySizeBadge bucket={job.company_size_bucket} />}
              {job.seniority && job.seniority !== 'unknown' && (
                <span className="tag bg-slate-100 capitalize text-slate-600">
                  {job.seniority}
                </span>
              )}
              <span
                title={job.source_label ?? 'Source provenance'}
                className={`tag ${
                  job.source_kind === 'primary' || job.source_kind === 'government' || job.source_kind === 'curated'
                    ? 'bg-ink text-white'
                    : 'bg-slate-100 text-slate-500'
                }`}
              >
                {job.source_label ?? job.source}
              </span>
            </div>

            {/* E-Verify rationale (STEM OPT) */}
            {job.known_everify && (
              <div className="rounded-lg border border-teal-100 bg-teal-50 px-3 py-2 text-xs text-teal-800">
                <span className="font-semibold">E-Verify employer.</span> Required for the 24-month STEM
                OPT extension. Advisory list; confirm on e-verify.gov before relying on it.
              </div>
            )}

            {job.eligibility_evidence && job.eligibility_evidence.length > 0 && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-relaxed text-amber-900">
                <p className="font-semibold">Eligibility evidence from the posting</p>
                <ul className="mt-1 list-disc space-y-1 pl-4">
                  {job.eligibility_evidence.map((evidence) => <li key={evidence}>{evidence}</li>)}
                </ul>
              </div>
            )}

            {/* Meta row */}
            {postedStr && (
              <div className="flex items-center gap-1.5 font-mono text-[0.68rem] text-slate-500">
                <CalendarBlank size={14} />
                {postedStr}
              </div>
            )}

            {/* Salary */}
            {salary && (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2">
                <CurrencyDollar size={15} className="shrink-0 text-emerald-600" />
                <span className="text-xs font-semibold text-emerald-800">{salary}</span>
                {job.salary_currency && <span className="text-xs text-emerald-600">/ year</span>}
              </div>
            )}

            {/* Work auth / restrictions */}
            {(job.work_auth_required || job.restrictions) && (
              <div className="space-y-1 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2.5">
                {job.work_auth_required && (
                  <p className="text-xs text-amber-800">
                    <span className="font-semibold">Work auth:</span> {job.work_auth_required}
                  </p>
                )}
                {job.restrictions && (
                  <p className="text-xs text-amber-800">
                    <span className="font-semibold">Restrictions:</span> {job.restrictions}
                  </p>
                )}
              </div>
            )}

            {/* Skills */}
            {job.skills.length > 0 && (
              <div>
                <h3 className="mb-2 font-mono text-[0.64rem] font-semibold uppercase tracking-[0.1em] text-slate-500">
                  Skills
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {job.skills.map((skill) => (
                    <span
                      key={skill}
                      className="tag border border-signal-100 bg-signal-50 text-signal-800"
                    >
                      {skill}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="border-t border-slate-100" />

            {/* Description */}
            {job.description ? (
              <div>
                <h3 className="mb-3 font-mono text-[0.64rem] font-semibold uppercase tracking-[0.1em] text-slate-500">
                  About the job
                </h3>
                <DescriptionRenderer text={job.description} />
              </div>
            ) : (
              <p className="text-sm italic text-slate-400">No description available.</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
