import { formatDistanceToNow } from 'date-fns'
import type { Job } from '../types'

interface JobCardProps {
  job: Job
  isSelected: boolean
  onSelect: (jobId: string) => void
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
// Display locations: prefer the aggregated `locations` array, else fall back
// to city/country, else `location_raw`. Deduped, empties dropped.
// ---------------------------------------------------------------------------

function displayLocations(job: Job): string[] {
  const raw =
    job.locations && job.locations.length > 0
      ? job.locations
      : [[job.city, job.country].filter(Boolean).join(', '), job.location_raw]
  const seen = new Set<string>()
  const out: string[] = []
  for (const loc of raw) {
    const trimmed = (loc ?? '').trim()
    if (trimmed && !seen.has(trimmed)) {
      seen.add(trimmed)
      out.push(trimmed)
    }
  }
  return out
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

export default function JobCard({ job, isSelected, onSelect }: JobCardProps) {
  const postedStr = postedLabel(job.posted_date, job.posted_date_est)

  const locations = displayLocations(job)
  const locationDisplay = locations[0] ?? null
  const extraLocations = locations.length - 1

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
        <p className="mt-0.5 text-xs text-slate-500">
          {locationDisplay}
          {extraLocations > 0 && (
            <span className="ml-1.5 text-slate-400">+{extraLocations} more</span>
          )}
        </p>
      )}

      {/* Badges */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <RemoteModeBadge mode={job.remote_mode} />
        {job.company_size_bucket && (
          <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-700">
            {job.company_size_bucket}
          </span>
        )}
        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-slate-100 text-slate-500 capitalize">
          {job.source}
        </span>
      </div>

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
