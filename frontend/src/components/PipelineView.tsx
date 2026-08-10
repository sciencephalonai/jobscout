import { useActiveProfile } from '../ProfileContext'
import { usePipeline, useSetJobState } from '../api/client'
import { PIPELINE_STAGES, type JobState, type PipelineAnalytics } from '../types'
import { SponsorshipBadge } from './SponsorshipBadge'

function daysAgo(iso: string): string {
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
  return d <= 0 ? 'today' : d === 1 ? '1 day ago' : `${d} days ago`
}

const pct = (x: number): string => `${Math.round(x * 100)}%`
const DIRECT_KINDS = new Set(['primary', 'government', 'curated'])

/** Funnel rollup: KPI tiles (applications, response/interview/offer rate) plus a
 * per-source conversion table. Stat tiles, not a chart — headline magnitudes with
 * no axes, so values wear slate text tokens (identity is the label, not color). */
export function PipelineStats({ a }: { a: PipelineAnalytics }) {
  if (a.total_applications === 0) return null
  const tiles = [
    { label: a.total_applications === 1 ? 'Application' : 'Applications',
      value: String(a.total_applications),
      sub: `across ${a.by_source.length} source${a.by_source.length === 1 ? '' : 's'}` },
    { label: 'Response rate', value: pct(a.response_rate),
      sub: `${a.responded} of ${a.total_applications} replied` },
    { label: 'Interview rate', value: pct(a.interview_rate),
      sub: 'reached interview' },
    { label: 'Offer rate', value: pct(a.offer_rate),
      sub: `${a.by_stage.offer} offer${a.by_stage.offer === 1 ? '' : 's'}` },
  ]
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {tiles.map((t) => (
          <div key={t.label} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
            <div className="text-2xl font-semibold leading-tight text-slate-800">{t.value}</div>
            <div className="text-xs font-medium text-slate-600">{t.label}</div>
            <div className="mt-0.5 text-[11px] text-slate-400">{t.sub}</div>
          </div>
        ))}
      </div>
      {a.by_source.length > 1 && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-3 border-b border-slate-100 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            <span>Source</span>
            <span className="text-right">Apps</span>
            <span className="text-right">Replied</span>
            <span className="text-right">Offers</span>
          </div>
          {a.by_source.map((s) => (
            <div key={s.source} className="grid grid-cols-[1fr_auto_auto_auto] items-center gap-x-3 border-b border-slate-50 px-3 py-1.5 text-sm last:border-0">
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate text-slate-700" title={s.source}>{s.source}</span>
                <span
                  className={`shrink-0 rounded px-1 py-px text-[10px] font-medium ${
                    DIRECT_KINDS.has(s.source_kind)
                      ? 'bg-emerald-50 text-emerald-700'
                      : 'bg-slate-100 text-slate-500'
                  }`}
                >
                  {DIRECT_KINDS.has(s.source_kind) ? 'Direct' : 'Discovery'}
                </span>
              </span>
              <span className="text-right tabular-nums text-slate-600">{s.applications}</span>
              <span className="text-right tabular-nums text-slate-600">{s.responded}</span>
              <span className="text-right tabular-nums text-slate-600">{s.offers}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
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
      {data.analytics && <PipelineStats a={data.analytics} />}
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
