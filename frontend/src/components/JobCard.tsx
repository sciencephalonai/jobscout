// One job row in the list: title/company, fit%, verdict chip, sponsorship/provenance/new-grad badges, matched-skill chips.
import {
  ArrowUpRight,
  BookmarkSimple,
  CheckCircle,
  Clock,
  EyeSlash,
  FunnelSimple,
  GraduationCap,
  Stack,
  Warning,
} from '@phosphor-icons/react'
import { formatDistanceToNow } from 'date-fns'
import type { Job, Verdict } from '../types'
import { SponsorshipBadge, EVerifyBadge } from './SponsorshipBadge'
import { useSetJobState, useCachedDeepMatch } from '../api/client'

/** Small AI second-opinion pill, shown once a deep-match result is cached for this job. */
function DeepMatchBadge({ jobId, profileId }: { jobId: string; profileId?: string | null }) {
  const deep = useCachedDeepMatch(jobId, profileId)
  if (!profileId || !deep) return null
  const styles = {
    apply: 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200',
    borderline: 'bg-amber-50 text-amber-700 ring-1 ring-amber-200',
    skip: 'bg-slate-100 text-slate-500 ring-1 ring-slate-200',
  }[deep.verdict]
  const label = { apply: 'AI ✓', borderline: 'AI caution', skip: 'AI skip' }[deep.verdict]
  return (
    <span className={`rounded-[0.35rem] px-1.5 py-0.5 text-[0.58rem] font-semibold ${styles}`}
      title={deep.summary || 'AI deep-match second opinion'}>
      {label} {Math.round(deep.score * 100)}%
    </span>
  )
}

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
    <span className={`tag ${styles[mode]}`}>
      {labels[mode]}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Posted date label
// ---------------------------------------------------------------------------

function postedLabel(postedDate: string | null, freshness: Job['freshness_kind']): string {
  if (!postedDate) return ''
  try {
    const date = new Date(postedDate)
    const rel = formatDistanceToNow(date, { addSuffix: true })
    if (freshness === 'updated') return `Updated ${rel}`
    if (freshness === 'estimated') return `Detected ${rel}`
    return `Posted ${rel}`
  } catch {
    return ''
  }
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------

export default function JobCard({ job, isSelected, onSelect, verdict, activeProfileId }: JobCardProps) {
  const postedStr = postedLabel(job.posted_date, job.freshness_kind)
  const setState = useSetJobState()

  const mark = (e: React.MouseEvent, status: 'saved' | 'applied' | 'hidden') => {
    e.stopPropagation()  // don't trigger card selection
    if (activeProfileId) setState.mutate({ profileId: activeProfileId, jobId: job.job_id, status })
  }

  const locationParts = [job.city, job.country].filter(Boolean)
  const locationDisplay = locationParts.length > 0
    ? locationParts.join(', ')
    : job.location_raw ?? null
  const companyMark = (job.company?.trim().charAt(0) || job.title.trim().charAt(0) || 'J').toUpperCase()

  return (
    <article
      onClick={() => onSelect(job.job_id)}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onSelect(job.job_id)
        }
      }}
      role="option"
      tabIndex={0}
      aria-selected={isSelected}
      className={`control-focus group relative cursor-pointer border-b border-slate-100 px-3.5 py-3 ${
        isSelected ? 'bg-signal-50/65' : 'bg-white hover:bg-[#f7f8f6]'
      }`}
    >
      {isSelected && <span className="absolute inset-y-0 left-0 w-0.5 bg-signal-500" aria-hidden="true" />}
      <div className="flex items-start gap-3">
        <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg font-mono text-[0.65rem] font-semibold ${isSelected ? 'bg-ink text-white' : 'border border-slate-200 bg-slate-50 text-slate-500'}`}>
          {companyMark}
        </div>
        <div className="min-w-0 flex-1">
          <h3 className={`text-[0.84rem] font-semibold leading-[1.35] tracking-[-0.012em] ${isSelected ? 'text-ink' : 'text-slate-800 group-hover:text-ink'}`}>
            {job.title}
          </h3>
          <p className="mt-0.5 truncate text-xs font-medium text-slate-600" title={job.company || 'Unknown employer'}>{job.company || 'Unknown employer'}</p>
          {locationDisplay && <p className="mt-0.5 truncate text-[0.68rem] text-slate-500" title={locationDisplay}>{locationDisplay}</p>}
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {verdict && (
            <span className={`rounded-[0.35rem] px-1.5 py-1 font-mono text-[0.62rem] font-semibold tabular-nums ${verdict.verdict === 'apply' ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>
              {Math.round(verdict.score * 100)}%
            </span>
          )}
          <DeepMatchBadge jobId={job.job_id} profileId={activeProfileId} />
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1">
        {verdict && <span className={`tag font-semibold ${verdict.verdict === 'apply' ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>{verdict.verdict === 'apply' ? 'Recommended' : 'Review'}</span>}
        <SponsorshipBadge job={job} />
        <RemoteModeBadge mode={job.remote_mode} />
        <span
          title={job.source_label ?? 'Source provenance'}
          className={`tag gap-1 ${
            job.source_kind === 'primary' || job.source_kind === 'government' || job.source_kind === 'curated'
              ? 'bg-ink text-white'
              : 'bg-slate-100 text-slate-500'
          }`}
        >
          {job.source_kind === 'primary' || job.source_kind === 'government' || job.source_kind === 'curated'
            ? <CheckCircle size={12} weight="fill" />
            : <FunnelSimple size={12} weight="bold" />}
          {job.source_kind === 'primary' || job.source_kind === 'government' || job.source_kind === 'curated' ? 'Direct' : 'Discovery'}
        </span>
        {job.is_recruiter_post && (
          <span
            title="Recruiter or staffing-agency post. The employer board may have a cleaner listing."
            className="tag bg-amber-100 text-amber-800"
          >
            Recruiter
          </span>
        )}
        <EVerifyBadge job={job} />
        {!!job.duplicate_count && job.duplicate_count > 0 && (
          <span
            title={`Also posted on: ${(job.also_on ?? []).join(', ')}`}
            className="tag gap-1 bg-slate-100 text-slate-500">
            <Stack size={11} />+{job.duplicate_count}
          </span>
        )}
        {job.new_grad_program && (
          <span
            title="Explicit new-grad, university, early-career, or rotational program"
            className="tag gap-1 bg-emerald-100 text-emerald-800">
            <GraduationCap size={12} />New grad
          </span>
        )}
        {job.mislabeled_entry && (
          <span
            title={`Titled entry-level but the description asks for ${job.yoe_min}+ years`}
            className="tag gap-1 bg-rose-100 text-rose-800">
            <Warning size={12} />Wants {job.yoe_min}+ yrs
          </span>
        )}
        {job.ghost_risk && job.ghost_risk !== 'low' && (
          <span
            title="Possibly stale or ghost posting. Verify it is still open before investing time."
            className="tag gap-1 bg-amber-100 text-amber-800">
            <Warning size={12} />Stale risk{typeof job.posting_age_days === 'number' ? ` · ${job.posting_age_days}d` : ''}
          </span>
        )}
      </div>

      {verdict && verdict.matched.length > 0 && (
        <div className="mt-1.5 flex min-w-0 flex-wrap gap-1">
          {verdict.matched.slice(0, 3).map((t) => (
            <span key={t} className="tag border border-emerald-100 bg-emerald-50 text-emerald-700">{t}</span>
          ))}
        </div>
      )}
      {verdict && verdict.gaps.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {verdict.gaps.slice(0, 1).map((t) => (
            <span key={t} className="tag border border-amber-100 bg-amber-50 text-amber-700">Gap: {t}</span>
          ))}
        </div>
      )}
      {verdict && verdict.reasons.length > 0 && (
        <p className="mt-1.5 line-clamp-2 text-[0.68rem] leading-4 text-slate-500">
          {verdict.reasons[0]}
        </p>
      )}

      <div className="mt-2 flex items-center justify-between gap-2">
        {postedStr ? (
          <span className="inline-flex items-center gap-1 font-mono text-[0.61rem] text-slate-400"><Clock size={11} />{postedStr}</span>
        ) : <span />}
        {activeProfileId && (
          <div className="flex items-center gap-1">
          <a href={job.url} target="_blank" rel="noopener noreferrer"
            onClick={(e) => { e.stopPropagation(); if (activeProfileId) setState.mutate({ profileId: activeProfileId, jobId: job.job_id, status: 'applied' }) }}
            className="control-focus inline-flex h-7 items-center gap-1 rounded-lg bg-ink px-2.5 text-[0.68rem] font-semibold text-white hover:bg-ink-soft">
            Apply <ArrowUpRight size={11} />
          </a>
          <button type="button" onClick={(e) => mark(e, 'saved')}
            className="control-focus flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50" aria-label="Save job"><BookmarkSimple size={13} /></button>
          <button type="button" onClick={(e) => mark(e, 'hidden')}
            className="control-focus flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 text-slate-400 hover:bg-slate-50" aria-label="Hide job"><EyeSlash size={13} /></button>
          </div>
        )}
        {job.enrichment_status === 'pending' && (
          <span className="inline-flex items-center gap-1 text-[0.64rem] text-amber-700">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
            Enriching
          </span>
        )}
        {job.enrichment_status === 'failed' && (
          <span className="text-xs text-red-400">Enrichment failed</span>
        )}
      </div>
    </article>
  )
}
