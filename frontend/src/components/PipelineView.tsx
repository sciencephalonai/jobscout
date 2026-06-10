import { useActiveProfile } from '../ProfileContext'
import { usePipeline, useSetJobState } from '../api/client'
import { PIPELINE_STAGES, type JobState } from '../types'
import { SponsorshipBadge } from './SponsorshipBadge'

function daysAgo(iso: string): string {
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
  return d <= 0 ? 'today' : d === 1 ? '1 day ago' : `${d} days ago`
}

/** Application tracker: jobs you're pursuing, grouped by stage, with a per-job
 * stage selector + a "applied N days ago" follow-up hint. */
export default function PipelineView() {
  const { activeProfileId } = useActiveProfile()
  const { data, isLoading } = usePipeline(activeProfileId)
  const setState = useSetJobState()

  if (!activeProfileId) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-6 text-center text-sm text-amber-800">
        Select a profile in the top bar to track applications.
      </div>
    )
  }
  if (isLoading) return <p className="text-sm text-slate-400">Loading…</p>
  if (!data || data.jobs.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-400">
        Nothing in your pipeline yet. Mark a job <strong>Apply</strong> on the Jobs tab to start tracking.
      </div>
    )
  }

  // Group jobs by stage.
  const byStage: Record<string, typeof data.jobs> = {}
  for (const j of data.jobs) {
    const st = data.stages[j.job_id]?.stage ?? 'applied'
    ;(byStage[st] ??= []).push(j)
  }

  return (
    <div className="space-y-6">
      {PIPELINE_STAGES.map(({ key, label }) => {
        const jobs = byStage[key] ?? []
        if (jobs.length === 0) return null
        return (
          <section key={key}>
            <h3 className="mb-2 text-sm font-semibold text-slate-700">{label} ({jobs.length})</h3>
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              {jobs.map((j) => {
                const meta = data.stages[j.job_id]
                return (
                  <div key={j.job_id} className="border-b border-slate-100 px-4 py-3 last:border-0">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <a href={j.url} target="_blank" rel="noopener noreferrer"
                          className="font-medium text-slate-800 hover:text-blue-600">{j.title}</a>
                        <p className="text-sm text-slate-500">
                          {j.company}{meta ? ` · ${daysAgo(meta.updated_at)}` : ''}
                        </p>
                      </div>
                      <div className="flex flex-shrink-0 items-center gap-2">
                        <SponsorshipBadge job={j} />
                        <select
                          value={key}
                          onChange={(e) => setState.mutate({
                            profileId: activeProfileId, jobId: j.job_id,
                            status: e.target.value as JobState,
                          })}
                          className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs"
                          title="Move to a different stage"
                        >
                          {PIPELINE_STAGES.map((s) => (
                            <option key={s.key} value={s.key}>{s.label}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                    {meta?.note && <p className="mt-1 text-xs text-slate-500">📝 {meta.note}</p>}
                  </div>
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
}
