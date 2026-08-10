// Per-job AI-reduction dashboard: humanization before→after rings, the top
// metric improvements from tailoring, and any audit warnings. Rendered in the
// job detail pane for a tailored resume. Stat tiles + rings, not a chart.
import type { MetricDeltaRow, ResumeMetrics } from '../types'
import { AiRing, humanizationBand } from './AiRing'

const METRIC_LABEL: Record<string, string> = {
  humanization_score: 'Humanization',
  ai_buzzword_density: 'AI-buzzword density',
  structural_burstiness: 'Sentence burstiness',
  sent_len_cv: 'Sentence-length variance',
  distinct_2: 'Bigram diversity',
  mtld: 'Lexical diversity (MTLD)',
  trigram_rep_rate: 'Trigram repetition',
  flesch_reading_ease: 'Readability',
}

function label(metric: string): string {
  return METRIC_LABEL[metric] ?? metric.replace(/_/g, ' ')
}

function fmt(n: number | null): string {
  if (n === null) return '—'
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

/** Rows the tailoring moved most, better first, then worse — for a compact "what changed" list. */
function topChanges(delta: MetricDeltaRow[]): MetricDeltaRow[] {
  const scored = delta.filter((r) => r.direction !== 'neutral' && r.metric !== 'ai_risk_score')
  const rank = (r: MetricDeltaRow) => (r.direction === 'better' ? 0 : 1)
  return scored
    .sort((a, b) => rank(a) - rank(b) || Math.abs(b.delta ?? 0) - Math.abs(a.delta ?? 0))
    .slice(0, 6)
}

export function JobDashboard({ metrics, warnings }: { metrics: ResumeMetrics; warnings?: string[] }) {
  const hb = metrics.humanization_before
  const ha = metrics.humanization_after
  const changes = topChanges(metrics.delta)
  const improved = metrics.delta.filter((r) => r.direction === 'better').length

  return (
    <div className="mt-2 space-y-3 rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center justify-between gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">AI-reduction dashboard</h4>
        <span className="text-[11px] text-slate-400">{improved} metrics more human</span>
      </div>

      <div className="flex flex-wrap items-center gap-5">
        {hb !== null && <AiRing value={hb} band={humanizationBand(hb)} label="Before" />}
        {ha !== null && <AiRing value={ha} band={humanizationBand(ha)} label="After" />}
        <div className="min-w-0 flex-1 text-xs text-slate-500">
          <p>Humanization = 100 − the composite AI-risk score; higher is more human-like.
            The tailored resume is scored against your current resume.</p>
        </div>
      </div>

      {changes.length > 0 && (
        <div className="overflow-hidden rounded-lg border border-slate-100">
          <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-3 border-b border-slate-100 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            <span>Metric</span><span className="text-right">Before</span>
            <span className="text-right">After</span><span className="text-right">Δ</span>
          </div>
          {changes.map((r) => (
            <div key={`${r.family}-${r.metric}`}
              className="grid grid-cols-[1fr_auto_auto_auto] items-center gap-x-3 border-b border-slate-50 px-3 py-1.5 text-xs last:border-0">
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate text-slate-700" title={`${r.family} · ${r.metric}`}>{label(r.metric)}</span>
                <span className={`shrink-0 rounded px-1 text-[10px] font-medium ${
                  r.direction === 'better' ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-600'
                }`}>{r.direction === 'better' ? '↑ better' : '↓ worse'}</span>
              </span>
              <span className="text-right tabular-nums text-slate-500">{fmt(r.before)}</span>
              <span className="text-right tabular-nums text-slate-700">{fmt(r.after)}</span>
              <span className={`text-right tabular-nums ${r.direction === 'better' ? 'text-emerald-600' : 'text-rose-500'}`}>{fmt(r.delta)}</span>
            </div>
          ))}
        </div>
      )}

      {warnings && warnings.length > 0 && (
        <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">
          <p className="mb-0.5 font-medium">Audit — confirm before applying:</p>
          <ul className="list-disc space-y-0.5 pl-4">
            {warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        </div>
      )}
    </div>
  )
}
