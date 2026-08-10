// A single-score donut ring for AI-reduction metrics. Magnitude, not category:
// one arc, colored by status band (good/warning/serious) with a text label so
// identity is never color-alone (dataviz accessibility rule).
import type { MetricBand } from '../types'

const BAND_COLOR: Record<MetricBand, string> = {
  good: '#059669',      // emerald-600
  warning: '#d97706',   // amber-600
  serious: '#e11d48',   // rose-600
}
const BAND_LABEL: Record<MetricBand, string> = {
  good: 'Human-like',
  warning: 'Some AI tells',
  serious: 'Reads AI-generated',
}

/** Band for a humanization score (higher = more human). Mirrors ai_risk() bands. */
export function humanizationBand(humanization: number): MetricBand {
  const risk = 100 - humanization
  return risk < 35 ? 'good' : risk < 60 ? 'warning' : 'serious'
}

export function AiRing({
  value, label, band, size = 88,
}: { value: number; label: string; band: MetricBand; size?: number }) {
  const stroke = 9
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const pct = Math.max(0, Math.min(100, value))
  const color = BAND_COLOR[band]
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img"
        aria-label={`${label}: ${Math.round(pct)} out of 100, ${BAND_LABEL[band]}`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e2e8f0" strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={stroke}
          strokeLinecap="round" strokeDasharray={c}
          strokeDashoffset={c * (1 - pct / 100)}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
        <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle"
          className="fill-slate-800" style={{ fontSize: size * 0.26, fontWeight: 700 }}>
          {Math.round(pct)}
        </text>
      </svg>
      <div className="text-center">
        <div className="text-xs font-medium text-slate-600">{label}</div>
        <div className="text-[11px]" style={{ color }}>{BAND_LABEL[band]}</div>
      </div>
    </div>
  )
}
