import type { Job } from '../types'

/**
 * Visa-sponsorship likelihood badge, derived by the backend from multiple
 * signals (explicit visa field + cap-exempt employer type + citizenship
 * requirement + public DoL H-1B filer history). Advisory, not a guarantee.
 *
 * - likely  → green  (explicit yes, proven H-1B filer, or cap-exempt employer)
 * - unknown → gray   (JD says nothing — the ~96% case; surfaced, not hidden)
 * - no      → red    (explicit refusal or citizenship/clearance required)
 */
export function SponsorshipBadge({ job }: { job: Job }) {
  const likelihood = job.sponsorship_likelihood
  if (!likelihood) return null

  const config: Record<NonNullable<Job['sponsorship_likelihood']>, { cls: string; label: string }> = {
    likely: { cls: 'bg-emerald-100 text-emerald-700', label: 'Likely sponsor' },
    unknown: { cls: 'bg-slate-100 text-slate-500', label: 'Sponsorship unknown' },
    no: { cls: 'bg-rose-100 text-rose-700', label: 'No sponsorship' },
  }
  const { cls, label } = config[likelihood]

  // Why it's "likely" — surfaced as a tooltip for transparency.
  let why = ''
  if (likelihood === 'likely') {
    if (job.known_h1b_sponsor) why = 'Company has filed H-1B petitions before'
    else if (job.cap_exempt === 'yes' || job.cap_exempt === 'likely')
      why = `Cap-exempt employer (${job.employer_type ?? 'nonprofit/university'}) — can sponsor off-lottery`
    else if (job.visa_sponsorship === 'yes') why = 'Posting explicitly offers sponsorship'
  } else if (likelihood === 'no') {
    why = job.citizenship_required ? 'Requires US citizenship / clearance' : 'Posting states no sponsorship'
  } else {
    why = 'Posting does not mention sponsorship'
  }

  return (
    <span
      title={why}
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}`}
    >
      {label}
    </span>
  )
}

/**
 * E-Verify badge — shown only when the company is a KNOWN E-Verify participant.
 * Kept separate from sponsorship because it's a different legal mechanism: the
 * 24-month STEM OPT extension requires the employer to be enrolled in E-Verify.
 * Curated + advisory: absence of the badge means "unknown", never "not enrolled".
 */
export function EVerifyBadge({ job }: { job: Job }) {
  if (!job.known_everify) return null
  return (
    <span
      title="Known E-Verify employer — required for the 24-month STEM OPT extension. Verify on e-verify.gov."
      className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-teal-100 text-teal-700"
    >
      E-Verify
    </span>
  )
}
