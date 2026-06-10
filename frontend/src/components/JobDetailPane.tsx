import DOMPurify from 'dompurify'
import { formatDistanceToNow, parseISO } from 'date-fns'
import type { Job } from '../types'
import { useJob } from '../api/client'
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
    <span className={`inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium ${styles[mode]}`}>
      {labels[mode]}
    </span>
  )
}

function CompanySizeBadge({ bucket }: { bucket: string }) {
  return (
    <span className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-indigo-50 text-indigo-700">
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

export default function JobDetailPane({ jobId, onClose }: JobDetailPaneProps) {
  const { data: job, isLoading, isError, error } = useJob(jobId)

  const salary = job ? formatSalary(job.salary_min, job.salary_max, job.salary_currency) : null
  const postedStr = job?.posted_date
    ? (() => {
        try {
          const rel = formatDistanceToNow(parseISO(job.posted_date), { addSuffix: true })
          return job.posted_date_est ? `~${rel}` : rel
        } catch {
          return null
        }
      })()
    : null

  return (
    <div className="flex h-full flex-col bg-white">
      {/* Header */}
      <div className="flex-shrink-0 px-8 pt-7 pb-5 border-b border-slate-100">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            {isLoading ? (
              <>
                <div className="h-6 bg-slate-200 rounded w-64 animate-pulse mb-2" />
                <div className="h-4 bg-slate-100 rounded w-40 animate-pulse" />
              </>
            ) : job ? (
              <>
                <h1 className="text-2xl font-semibold leading-tight text-slate-900">
                  {job.title}
                </h1>
                <p className="mt-1.5 text-sm text-slate-600">
                  {[job.company, job.city, job.country].filter(Boolean).join(' · ')}
                </p>
              </>
            ) : null}
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="flex-shrink-0 rounded-full p-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
              aria-label="Close"
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>

        {/* Meta + apply row */}
        {!isLoading && job && (
          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
            <a
              href={job.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 rounded-full bg-blue-600 px-6 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 active:bg-blue-800"
            >
              Apply
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </a>
            <button
              onClick={() => navigator.clipboard.writeText(job.url)}
              title="Copy link"
              className="inline-flex items-center gap-1.5 rounded-full border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              Save link
            </button>
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
          <div className="px-8 py-6 space-y-6">
            {/* Badges */}
            <div className="flex flex-wrap gap-2">
              <SponsorshipBadge job={job} />
              <EVerifyBadge job={job} />
              <RemoteModeBadge mode={job.remote_mode} />
              {job.company_size_bucket && <CompanySizeBadge bucket={job.company_size_bucket} />}
              {job.seniority && job.seniority !== 'unknown' && (
                <span className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-slate-100 text-slate-600 capitalize">
                  {job.seniority}
                </span>
              )}
              <span className="inline-flex items-center px-2.5 py-1 rounded-md text-xs font-medium bg-slate-100 text-slate-500 capitalize">
                {job.source}
              </span>
            </div>

            {/* E-Verify rationale (STEM OPT) */}
            {job.known_everify && (
              <div className="rounded-lg border border-teal-100 bg-teal-50 px-4 py-2 text-xs text-teal-800">
                <span className="font-semibold">E-Verify employer.</span> Required for the 24-month STEM
                OPT extension. Advisory (curated list) — confirm on e-verify.gov before relying on it.
              </div>
            )}

            {/* Meta row */}
            {postedStr && (
              <div className="flex items-center gap-1.5 text-xs text-slate-500">
                <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                Posted {postedStr}
              </div>
            )}

            {/* Salary */}
            {salary && (
              <div className="flex items-center gap-2 rounded-lg border border-emerald-100 bg-emerald-50 px-4 py-3">
                <svg className="h-4 w-4 flex-shrink-0 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <span className="text-sm font-semibold text-emerald-800">{salary}</span>
                {job.salary_currency && <span className="text-xs text-emerald-600">/ year</span>}
              </div>
            )}

            {/* Work auth / restrictions */}
            {(job.work_auth_required || job.restrictions) && (
              <div className="space-y-1 rounded-lg border border-amber-100 bg-amber-50 px-4 py-3">
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
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
                  Skills
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {job.skills.map((skill) => (
                    <span
                      key={skill}
                      className="inline-flex items-center rounded-md border border-blue-100 bg-blue-50 px-2.5 py-1 text-xs font-medium text-blue-700"
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
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-500">
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
