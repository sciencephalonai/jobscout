import { formatDistanceToNow } from 'date-fns'
import type { Job, Verdict } from '../types'
import { SponsorshipBadge, EVerifyBadge } from './SponsorshipBadge'
import { useSetJobState } from '../api/client'

interface JobCardProps {
  job: Job
  isSelected: boolean
  onSelect: (jobId: string) => void
  verdict?: Verdict
  activeProfileId?: string | null
}

// ---------------------------------------------------------------------------
// Badge helpers
// ---------------------------------------------------------------------------

function RemoteModeBadge({ mode }: { mode: Job['remote_mode'] }) {
  if (mode === 'unknown') return null
  const styles: Record<Exclude<Job['remote_mode'], 'unknown'>, string> = {
    remote: 'bg-emerald-100 text-emerald-700',
    hybrid: 'bg-blue-100 text-blue-700',
    onsite: 'bg-slate-100 text-slate-600',
  }
  const labels: Record<Exclude<Job['remote_mode'], 'unknown'>, string> = {
    remote: 'Remote',
    hybrid: 'Hybrid',
    onsite: 'On-site',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${styles[mode]}`}>
      {labels[mode]}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Posted date label
// ---------------------------------------------------------------------------

function postedLabel(postedDate: string | null, isEst: boolean): string {
  if (!postedDate) return ''
  try {
    const date = new Date(postedDate)
    const rel = formatDistanceToNow(date, { addSuffix: true })
    return isEst ? `~${rel}` : rel
  } catch {
    return ''
  }
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

export default function JobCard({ job, isSelected, onSelect, verdict, activeProfileId }: JobCardProps) {
  const postedStr = postedLabel(job.posted_date, job.posted_date_est)
  const setState = useSetJobState()

  const mark = (e: React.MouseEvent, status: 'saved' | 'applied' | 'hidden') => {
    e.stopPropagation()  // don't trigger card selection
    if (activeProfileId) setState.mutate({ profileId: activeProfileId, jobId: job.job_id, status })
  }

  const locationParts = [job.city, job.country].filter(Boolean)
  const locationDisplay = locationParts.length > 0
    ? locationParts.join(', ')
    : job.location_raw ?? null

  return (
    <article
      onClick={() => onSelect(job.job_id)}
      className={`group cursor-pointer border-l-2 px-4 py-3 transition-colors ${
        isSelected
          ? 'border-l-blue-600 bg-blue-50/60'
          : 'border-l-transparent hover:bg-slate-50'
      }`}
    >
      {/* Title */}
      <h3
        className={`text-[15px] font-semibold leading-snug ${
          isSelected ? 'text-blue-700' : 'text-slate-900 group-hover:text-blue-700'
        } transition-colors`}
      >
        {job.title}
      </h3>

      {/* Company */}
      {job.company && (
        <p className="mt-0.5 text-sm text-slate-700">{job.company}</p>
      )}

      {/* Location */}
      {locationDisplay && (
        <p className="mt-0.5 text-xs text-slate-500">{locationDisplay}</p>
      )}

      {/* Badges */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {verdict && (
          <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${
            verdict.verdict === 'apply' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'
          }`}>
            {verdict.verdict === 'apply' ? 'Apply' : 'Flag'} · {Math.round(verdict.score * 100)}%
          </span>
        )}
        <SponsorshipBadge job={job} />
        <EVerifyBadge job={job} />
        <RemoteModeBadge mode={job.remote_mode} />
        {job.company_size_bucket && (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-700">
            {job.company_size_bucket}
          </span>
        )}
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-500 capitalize">
          {job.source}
        </span>
        {job.is_recruiter_post && (
          <span
            title="Recruiter / staffing-agency post — the direct employer's board may have a cleaner listing"
            className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-700"
          >
            Recruiter
          </span>
        )}
        {!!job.duplicate_count && job.duplicate_count > 0 && (
          <span
            title={`Also posted on: ${(job.also_on ?? []).join(', ')}`}
            className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-violet-50 text-violet-700">
            ＋{job.duplicate_count} more posting{job.duplicate_count > 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Matched / gap keyword chips (when a profile is active) */}
      {verdict && verdict.matched.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1">
          {verdict.matched.slice(0, 6).map((t) => (
            <span key={t} className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium bg-emerald-100 text-emerald-700">{t}</span>
          ))}
        </div>
      )}
      {verdict && verdict.gaps.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {verdict.gaps.slice(0, 5).map((t) => (
            <span key={t} className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium bg-amber-100 text-amber-700">{t}</span>
          ))}
        </div>
      )}

      {/* Apply / Save / Hide — only when a profile is active */}
      {activeProfileId && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          <a href={job.url} target="_blank" rel="noopener noreferrer"
            onClick={(e) => { e.stopPropagation(); if (activeProfileId) setState.mutate({ profileId: activeProfileId, jobId: job.job_id, status: 'applied' }) }}
            className="rounded border border-emerald-300 px-2 py-0.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50">
            Apply ↗
          </a>
          <button type="button" onClick={(e) => mark(e, 'saved')}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs font-medium text-slate-600 hover:bg-slate-50">Save</button>
          <button type="button" onClick={(e) => mark(e, 'hidden')}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs font-medium text-slate-400 hover:bg-slate-50">Hide</button>
        </div>
      )}

      {/* Footer: posted date + enrichment status */}
      <div className="mt-2 flex items-center justify-between">
        {postedStr ? (
          <span className="text-xs text-slate-400">{postedStr}</span>
        ) : (
          <span />
        )}
        {job.enrichment_status === 'pending' && (
          <span className="inline-flex items-center gap-1 text-xs text-amber-600">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
            Enriching…
          </span>
        )}
        {job.enrichment_status === 'failed' && (
          <span className="text-xs text-red-400">Enrichment failed</span>
        )}
      </div>
    </article>
  )
}
