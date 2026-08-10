// Per-candidate dashboard: profile summary, every tailored resume with its
// after-tailoring humanization score + PDF/DOCX links, and the application
// pipeline funnel. Fed by GET /api/profiles/{id}/dashboard.
import { useCandidateDashboard } from '../api/client'
import type { DashboardTailoredRow } from '../types'
import { humanizationBand } from './AiRing'
import { PipelineStats } from './PipelineView'

const BAND_CLASS = {
  good: 'bg-emerald-50 text-emerald-700',
  warning: 'bg-amber-50 text-amber-700',
  serious: 'bg-rose-50 text-rose-600',
}

/** After-tailoring humanization score as a small colored pill (higher = more human). */
function HumanScore({ aiRisk }: { aiRisk: number | null }) {
  if (aiRisk === null) return <span className="text-slate-400">—</span>
  const human = Math.round(100 - aiRisk)
  const band = humanizationBand(human)
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-semibold tabular-nums ${BAND_CLASS[band]}`}
      title="Humanization score (100 − AI-risk). Higher is more human-like.">
      {human}
    </span>
  )
}

function TailoredRowItem({ row }: { row: DashboardTailoredRow }) {
  return (
    <div className="grid grid-cols-[1fr_auto_auto] items-center gap-x-3 border-b border-slate-50 px-3 py-2 text-sm last:border-0">
      <div className="min-w-0">
        <div className="truncate font-medium text-slate-800" title={row.title}>{row.title || 'Tailored resume'}</div>
        <div className="truncate text-xs text-slate-500" title={row.company}>
          {row.company}
          <span className="text-slate-400"> · {new Date(row.created_at).toLocaleDateString()}</span>
          {!row.up_to_date && <span className="text-amber-600"> · resume changed</span>}
        </div>
      </div>
      <HumanScore aiRisk={row.ai_risk_after} />
      <div className="flex shrink-0 items-center gap-2 text-xs">
        {row.pdf_download_url && (
          <a href={row.pdf_download_url} className="font-medium text-signal-700 underline underline-offset-2">PDF</a>
        )}
        <a href={row.download_url} className="font-medium text-signal-700 underline underline-offset-2">DOCX</a>
      </div>
    </div>
  )
}

export default function CandidateDashboard({ profileId }: { profileId: string | null }) {
  const { data, isLoading } = useCandidateDashboard(profileId)

  if (!profileId) {
    return (
      <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-800">
        Select a profile to see its dashboard.
      </div>
    )
  }
  if (isLoading || !data) return <p className="text-sm text-slate-400">Loading dashboard…</p>

  const tailored = [...data.tailored].sort(
    (a, b) => (a.ai_risk_after ?? 999) - (b.ai_risk_after ?? 999),
  )

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-base font-semibold text-slate-800">{data.profile.label}</h3>
        {data.profile.target_titles.length > 0 && (
          <p className="text-sm text-slate-500">
            Targeting {data.profile.target_titles.slice(0, 4).join(', ')}
          </p>
        )}
      </div>

      <section>
        <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Application pipeline</h4>
        {data.pipeline.total_applications > 0
          ? <PipelineStats a={data.pipeline} />
          : <p className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm text-slate-400">No applications tracked yet.</p>}
      </section>

      <section>
        <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Tailored resumes {tailored.length > 0 && `(${tailored.length})`}
        </h4>
        {tailored.length === 0 ? (
          <p className="rounded-xl border border-slate-200 bg-white px-3 py-3 text-sm text-slate-400">
            No tailored resumes yet. Open a job and choose <strong>Tailor</strong> to build one.
          </p>
        ) : (
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="grid grid-cols-[1fr_auto_auto] gap-x-3 border-b border-slate-100 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              <span>Role</span><span className="text-right">Human</span><span className="text-right">Files</span>
            </div>
            {tailored.map((row) => <TailoredRowItem key={row.job_id} row={row} />)}
          </div>
        )}
      </section>
    </div>
  )
}
