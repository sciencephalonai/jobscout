import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { Company } from '../types'

const ATS_OPTIONS = [
  { value: 'ashby',           label: 'Ashby',           hint: 'e.g. anthropic, vercel, linear' },
  { value: 'greenhouse',      label: 'Greenhouse',       hint: 'e.g. mozilla, khanacademy' },
  { value: 'lever',           label: 'Lever',            hint: 'e.g. openai, stripe' },
  { value: 'workday',         label: 'Workday',          hint: 'e.g. nvidia  (from nvidia.wd5.myworkdayjobs.com)' },
  { value: 'workable',        label: 'Workable',         hint: 'e.g. company-slug' },
  { value: 'rippling',        label: 'Rippling',         hint: 'e.g. company-slug' },
  { value: 'smartrecruiters', label: 'SmartRecruiters',  hint: 'e.g. CompanyName' },
  { value: 'recruitee',       label: 'Recruitee',        hint: 'e.g. company-slug' },
]

const EMPLOYER_TYPES = [
  { value: 'for_profit',  label: 'For-profit' },
  { value: 'university',  label: 'University (cap-exempt)' },
  { value: 'hospital',    label: 'Hospital / AMC (cap-exempt)' },
  { value: 'nonprofit',   label: 'Nonprofit (cap-exempt)' },
  { value: 'government',  label: 'Government (cap-exempt)' },
]

const CAP_EXEMPT_TYPES = new Set(['university', 'hospital', 'nonprofit', 'government'])

type ValidationState =
  | { status: 'idle' }
  | { status: 'checking' }
  | { status: 'valid'; jobCount: number; sampleTitle: string | null }
  | { status: 'invalid'; error: string }

interface Props {
  onClose: () => void
}

export default function AddToWatchlistModal({ onClose }: Props) {
  const qc = useQueryClient()
  const [name, setName]               = useState('')
  const [ats, setAts]                 = useState('ashby')
  const [slug, setSlug]               = useState('')
  const [employerType, setEmployerType] = useState('for_profit')
  const [region, setRegion]           = useState('wd1')
  const [site, setSite]               = useState('External')
  const [validation, setValidation]   = useState<ValidationState>({ status: 'idle' })
  const [addStatus, setAddStatus]     = useState<'idle' | 'adding' | 'done' | 'error'>('idle')
  const [addError, setAddError]       = useState('')

  const hintText = ATS_OPTIONS.find((o) => o.value === ats)?.hint ?? ''
  const canValidate = !!slug.trim()
  const isValidated = validation.status === 'valid'

  // Reset validation whenever the form changes
  const resetValidation = () => setValidation({ status: 'idle' })

  const handleValidate = async () => {
    if (!slug.trim()) return
    setValidation({ status: 'checking' })
    try {
      const res = await fetch('/api/companies/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ats, slug: slug.trim(), region: region.trim(), site: site.trim() }),
      })
      const data = await res.json()
      if (data.valid) {
        setValidation({ status: 'valid', jobCount: data.job_count, sampleTitle: data.sample_title })
      } else {
        setValidation({ status: 'invalid', error: data.error || 'Could not find jobs at this ATS slug.' })
      }
    } catch (e) {
      setValidation({ status: 'invalid', error: e instanceof Error ? e.message : 'Network error' })
    }
  }

  const handleAdd = async () => {
    if (!name.trim() || !slug.trim() || !isValidated) return
    setAddStatus('adding')
    setAddError('')
    try {
      const body: Partial<Company> = {
        slug: slug.trim(),
        name: name.trim(),
        ats: ats as Company['ats'],
        employer_type: employerType,
        enabled: true,
        cap_exempt_hint: CAP_EXEMPT_TYPES.has(employerType) ? 'likely' : 'unknown',
        ...(ats === 'workday' ? { region: region.trim() || 'wd1', site: site.trim() || 'External' } : {}),
      }
      const res = await fetch('/api/companies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(await res.text().catch(() => res.statusText))
      setAddStatus('done')
      qc.invalidateQueries({ queryKey: ['companies'] })
      setTimeout(onClose, 1400)
    } catch (e) {
      setAddStatus('error')
      setAddError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>

        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-slate-900">Add company to watchlist</h2>
          <button type="button" onClick={onClose} className="text-slate-400 hover:text-slate-600 text-xl leading-none">×</button>
        </div>

        <p className="mb-4 text-xs text-slate-500 leading-relaxed">
          Enter the company's ATS info, then <strong>Validate</strong> to confirm it works, then <strong>Add</strong>.
          After adding, click <strong>Refresh watchlist</strong> to fetch their open jobs.
        </p>

        <div className="space-y-3">

          {/* Name */}
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">Company name</label>
            <input type="text" value={name} onChange={(e) => setName(e.target.value)}
              placeholder="e.g. NVIDIA" autoFocus
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* ATS + Employer type */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">ATS type</label>
              <select value={ats} onChange={(e) => { setAts(e.target.value); resetValidation() }}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {ATS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Employer type</label>
              <select value={employerType} onChange={(e) => setEmployerType(e.target.value)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {EMPLOYER_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
          </div>

          {/* Slug */}
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">
              {ats === 'workday' ? 'Workday tenant slug' : 'ATS slug / token'}
            </label>
            <input type="text" value={slug}
              onChange={(e) => { setSlug(e.target.value); resetValidation() }}
              placeholder={hintText}
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Workday region + site */}
          {ats === 'workday' && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Region</label>
                <input type="text" value={region}
                  onChange={(e) => { setRegion(e.target.value); resetValidation() }}
                  placeholder="wd1, wd5, wd3…"
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Career site path</label>
                <input type="text" value={site}
                  onChange={(e) => { setSite(e.target.value); resetValidation() }}
                  placeholder="External"
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </div>
            </div>
          )}
        </div>

        {/* Validation result */}
        {validation.status === 'checking' && (
          <div className="mt-3 text-xs text-slate-500 bg-slate-50 rounded px-3 py-2">
            Probing {ats} endpoint…
          </div>
        )}
        {validation.status === 'valid' && (
          <div className="mt-3 text-xs text-emerald-700 bg-emerald-50 rounded px-3 py-2">
            ✓ Found <strong>{validation.jobCount} open roles</strong>
            {validation.sampleTitle ? ` — e.g. "${validation.sampleTitle}"` : ''}.
            Enter a name above and click Add.
          </div>
        )}
        {validation.status === 'invalid' && (
          <div className="mt-3 text-xs text-red-600 bg-red-50 rounded px-3 py-2">
            ✗ {validation.error}
          </div>
        )}
        {addStatus === 'error' && (
          <div className="mt-3 text-xs text-red-600 bg-red-50 rounded px-3 py-2">{addError}</div>
        )}
        {addStatus === 'done' && (
          <div className="mt-3 text-xs text-emerald-700 bg-emerald-50 rounded px-3 py-2">
            ✓ Added to watchlist — click <strong>Refresh watchlist</strong> to fetch their jobs.
          </div>
        )}

        {/* Action buttons */}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onClose}
            className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
          >
            Cancel
          </button>

          {/* Step 1: Validate */}
          {!isValidated && (
            <button type="button" onClick={handleValidate}
              disabled={!canValidate || validation.status === 'checking'}
              className="rounded-lg bg-slate-700 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {validation.status === 'checking' ? 'Checking…' : 'Validate'}
            </button>
          )}

          {/* Step 2: Add (only after validation passes) */}
          {isValidated && (
            <button type="button" onClick={handleAdd}
              disabled={!name.trim() || addStatus === 'adding' || addStatus === 'done'}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {addStatus === 'adding' ? 'Adding…' : 'Add to watchlist'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
