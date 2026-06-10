import { SponsorshipBadge } from './SponsorshipBadge'
import { useActiveProfile } from '../ProfileContext'
import { useJobsByState, useSetJobState } from '../api/client'
import type { JobState, Verdict, Job } from '../types'

function Chips({ items, cls, label }: { items: string[]; cls: string; label: string }) {
  if (!items || items.length === 0) return null
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      <span className="text-xs font-medium text-slate-400">{label}</span>
      {items.map((t) => (
        <span key={t} className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ${cls}`}>{t}</span>
      ))}
    </div>
  )
}

function Row({ job, verdict, profileId }: { job: Job; verdict?: Verdict; profileId: string }) {
  const setState = useSetJobState()
  return (
    <div className="border-b border-slate-100 px-4 py-3 hover:bg-white">
      <div className="flex items-start justify-between gap-3">
        <div>
          <a href={job.url} target="_blank" rel="noopener noreferrer"
            className="font-medium text-slate-800 hover:text-blue-600">{job.title}</a>
          <p className="text-sm text-slate-500">{job.company}{job.location_raw ? ` · ${job.location_raw}` : ''}</p>
        </div>
        <div className="flex flex-shrink-0 items-center gap-2">
          {verdict && (
            <span className="rounded-full bg-blue-50 px-2 py-0.5 text-xs font-semibold text-blue-700">
              fit {Math.round(verdict.score * 100)}%
            </span>
          )}
          <SponsorshipBadge job={job} />
          <button type="button"
            onClick={() => setState.mutate({ profileId, jobId: job.job_id, status: 'applied' })}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
            Mark applied
          </button>
        </div>
      </div>
      {verdict && (
        <>
          <Chips items={verdict.matched} cls="bg-emerald-100 text-emerald-700" label="matches" />
          <Chips items={verdict.gaps} cls="bg-amber-100 text-amber-700" label="gaps" />
        </>
      )}
    </div>
  )
}

/**
 * The list body for a given job-state — no header/nav, so it can be embedded
 * (e.g. inside the My Jobs page behind a Shortlist|Applied toggle).
 */
export function StateList({ status }: { status: JobState }) {
  const { activeProfileId } = useActiveProfile()
  const { data, isLoading } = useJobsByState(activeProfileId, status)

  if (!activeProfileId) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-6 text-center text-sm text-amber-800">
        Select a profile in the top bar to use this view. (Drop a resume in the Profile tab to create one.)
      </div>
    )
  }
  if (isLoading) return <p className="text-sm text-slate-400">Loading…</p>
  if (!data || data.jobs.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-400">
        Nothing here yet. Mark jobs as “{status}” from the Jobs tab.
      </div>
    )
  }
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
      {data.jobs.map((j) => (
        <Row key={j.job_id} job={j} verdict={data.verdicts?.[j.job_id]} profileId={activeProfileId} />
      ))}
    </div>
  )
}
