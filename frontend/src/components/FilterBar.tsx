import { useState, useRef, useEffect } from 'react'
import type { JobFilters, JobsResponse } from '../types'

interface FilterBarProps {
  filters: JobFilters
  facets: JobsResponse['facets'] | undefined
  sourceOptions: string[]
  onFilterChange: (updates: Partial<JobFilters>) => void
  onClearFilters: () => void
}

// ---------------------------------------------------------------------------
// Option definitions
// ---------------------------------------------------------------------------

const DATE_OPTIONS = [
  { label: 'Past 24 hours', value: '24h' },
  { label: 'Past week', value: '7d' },
  { label: 'Past 2 weeks', value: '14d' },
  { label: 'Past 3 weeks', value: '21d' },
  { label: 'Past month', value: '1m' },
]

const REMOTE_OPTIONS = [
  { label: 'Remote', value: 'remote' },
  { label: 'Hybrid', value: 'hybrid' },
  { label: 'On-site', value: 'onsite' },
]

const COMPANY_SIZE_OPTIONS = [
  { label: '1–50', value: '1-50' },
  { label: '51–200', value: '51-200' },
  { label: '201–500', value: '201-500' },
  { label: '501–1,000', value: '501-1000' },
  { label: '1,001–5,000', value: '1001-5000' },
  { label: '5,000+', value: '5000+' },
]

const EXPERIENCE_OPTIONS = [
  { label: 'Entry (0–2 yrs)', value: 'entry' },
  { label: 'Mid (3–5)', value: 'mid' },
  { label: 'Senior (6–10)', value: 'senior' },
  { label: 'Lead (10+)', value: 'lead' },
]

const EMPLOYER_TYPE_OPTIONS = [
  { label: 'University', value: 'university' },
  { label: 'Hospital', value: 'hospital' },
  { label: 'Nonprofit', value: 'nonprofit' },
  { label: 'Government', value: 'government' },
  { label: 'For-profit', value: 'for_profit' },
]

const CLEARANCE_OPTIONS = [
  { label: 'None required', value: 'none' },
  { label: 'Preferred', value: 'preferred' },
  { label: 'Required', value: 'required' },
  { label: 'Unclear', value: 'unclear' },
]

const CATEGORY_OPTIONS = [
  { label: 'Software Eng', value: 'software_eng' },
  { label: 'Data / ML / AI', value: 'data_ml_ai' },
  { label: 'DevOps / Infra', value: 'devops_infra' },
  { label: 'Security', value: 'security' },
  { label: 'Product', value: 'product_mgmt' },
  { label: 'Design / UX', value: 'design_ux' },
  { label: 'Management', value: 'management' },
  { label: 'Other', value: 'other' },
]

const EMPLOYMENT_TYPE_OPTIONS = [
  { label: 'Full-time', value: 'full_time' },
  { label: 'Contract', value: 'contract' },
  { label: 'Part-time', value: 'part_time' },
  { label: 'Internship', value: 'internship' },
  { label: 'Temporary', value: 'temporary' },
]

// ---------------------------------------------------------------------------
// Dropdown pill — a single-select popover styled as a LinkedIn-like pill
// ---------------------------------------------------------------------------

interface DropdownPillProps {
  label: string
  value: string | undefined
  options: { label: string; value: string }[]
  onSelect: (value: string | undefined) => void
}

function DropdownPill({ label, value, options, onSelect }: DropdownPillProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const active = value !== undefined && value !== ''
  const selectedLabel = active
    ? options.find((o) => o.value === value)?.label ?? label
    : label

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-medium transition ${
          active
            ? 'border-blue-600 bg-blue-50 text-blue-700'
            : 'border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50'
        }`}
      >
        <span>{selectedLabel}</span>
        <svg
          className={`h-4 w-4 transition-transform ${open ? 'rotate-180' : ''} ${
            active ? 'text-blue-600' : 'text-slate-400'
          }`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 z-30 mt-2 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg">
          <button
            type="button"
            onClick={() => {
              onSelect(undefined)
              setOpen(false)
            }}
            className={`flex w-full items-center px-4 py-2 text-left text-sm transition hover:bg-slate-50 ${
              !active ? 'font-medium text-blue-700' : 'text-slate-600'
            }`}
          >
            Any
          </button>
          {options.map((opt) => {
            const isSel = opt.value === value
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  onSelect(isSel ? undefined : opt.value)
                  setOpen(false)
                }}
                className={`flex w-full items-center justify-between px-4 py-2 text-left text-sm transition hover:bg-slate-50 ${
                  isSel ? 'font-medium text-blue-700' : 'text-slate-700'
                }`}
              >
                <span>{opt.label}</span>
                {isSel && (
                  <svg className="h-4 w-4 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Multi-select pill — a checkbox popover styled to match DropdownPill
// ---------------------------------------------------------------------------

interface MultiSelectPillProps {
  label: string
  values: string[] | undefined
  options: { label: string; value: string }[]
  onChange: (values: string[] | undefined) => void
}

function MultiSelectPill({ label, values, options, onChange }: MultiSelectPillProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const selected = values ?? []
  const count = selected.length
  const active = count > 0

  const toggle = (value: string) => {
    const next = selected.includes(value)
      ? selected.filter((v) => v !== value)
      : [...selected, value]
    onChange(next.length > 0 ? next : undefined)
  }

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-medium transition ${
          active
            ? 'border-blue-600 bg-blue-50 text-blue-700'
            : 'border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50'
        }`}
      >
        <span>{label}</span>
        {active && (
          <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-blue-600 px-1.5 text-xs font-semibold text-white">
            {count}
          </span>
        )}
        <svg
          className={`h-4 w-4 transition-transform ${open ? 'rotate-180' : ''} ${
            active ? 'text-blue-600' : 'text-slate-400'
          }`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 z-30 mt-2 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg">
          {active && (
            <button
              type="button"
              onClick={() => onChange(undefined)}
              className="flex w-full items-center px-4 py-2 text-left text-sm text-slate-500 transition hover:bg-slate-50"
            >
              Clear
            </button>
          )}
          {options.map((opt) => {
            const isSel = selected.includes(opt.value)
            return (
              <button
                key={opt.value}
                type="button"
                onClick={() => toggle(opt.value)}
                className={`flex w-full items-center gap-2.5 px-4 py-2 text-left text-sm transition hover:bg-slate-50 ${
                  isSel ? 'font-medium text-blue-700' : 'text-slate-700'
                }`}
              >
                <span
                  className={`flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition ${
                    isSel ? 'border-blue-600 bg-blue-600' : 'border-slate-300 bg-white'
                  }`}
                >
                  {isSel && (
                    <svg className="h-3 w-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </span>
                <span>{opt.label}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sponsorship pill — three INDEPENDENT toggles mapping to different filters.
// A binary "Sponsors visa = yes" is the wrong model: ~96% of postings never
// mention visa, so requiring "yes" hides almost everything. Instead:
//   • Hide no-sponsorship  → exclude_no_sponsorship (default ON for visa users)
//   • Likely sponsor       → cap_exempt = [yes, likely]
//   • Proven H-1B sponsor  → h1b_sponsor
// ---------------------------------------------------------------------------

interface SponsorshipPillProps {
  filters: JobFilters
  onChange: (updates: Partial<JobFilters>) => void
}

function SponsorshipPill({ filters, onChange }: SponsorshipPillProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const capOn = (filters.cap_exempt?.length ?? 0) > 0
  const toggles = [
    {
      label: 'Hide no-sponsorship',
      hint: 'Drops jobs that explicitly refuse sponsorship or require citizenship',
      on: !!filters.exclude_no_sponsorship,
      set: () => onChange({ exclude_no_sponsorship: !filters.exclude_no_sponsorship }),
    },
    {
      label: 'Likely sponsor (cap-exempt)',
      hint: 'University / nonprofit / government — can sponsor H-1B off-lottery',
      on: capOn,
      set: () => onChange({ cap_exempt: capOn ? undefined : ['yes', 'likely'] }),
    },
    {
      label: 'Proven H-1B sponsor',
      hint: 'Company appears in the public DoL H-1B filer list',
      on: !!filters.h1b_sponsor,
      set: () => onChange({ h1b_sponsor: !filters.h1b_sponsor }),
    },
    {
      label: 'E-Verify employer',
      hint: 'Known E-Verify participant — required for the 24-month STEM OPT extension',
      on: !!filters.everify,
      set: () => onChange({ everify: !filters.everify }),
    },
  ]
  const count = toggles.filter((t) => t.on).length
  const active = count > 0

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-medium transition ${
          active
            ? 'border-blue-600 bg-blue-50 text-blue-700'
            : 'border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50'
        }`}
      >
        <span>Work authorization</span>
        {active && (
          <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-blue-600 px-1.5 text-xs font-semibold text-white">
            {count}
          </span>
        )}
        <svg
          className={`h-4 w-4 transition-transform ${open ? 'rotate-180' : ''} ${
            active ? 'text-blue-600' : 'text-slate-400'
          }`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="absolute left-0 z-30 mt-2 w-72 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg">
          {toggles.map((t) => (
            <button
              key={t.label}
              type="button"
              onClick={t.set}
              className={`flex w-full items-start gap-2.5 px-4 py-2.5 text-left text-sm transition hover:bg-slate-50 ${
                t.on ? 'font-medium text-blue-700' : 'text-slate-700'
              }`}
            >
              <span
                className={`mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition ${
                  t.on ? 'border-blue-600 bg-blue-600' : 'border-slate-300 bg-white'
                }`}
              >
                {t.on && (
                  <svg className="h-3 w-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </span>
              <span className="flex flex-col">
                <span>{t.label}</span>
                <span className="text-xs font-normal text-slate-400">{t.hint}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main bar
// ---------------------------------------------------------------------------

export default function FilterBar({
  filters,
  facets,
  sourceOptions,
  onFilterChange,
  onClearFilters,
}: FilterBarProps) {
  // Prefer facet keys (live counts) for source options; fall back to passed-in list.
  const facetSources = facets?.source ? Object.keys(facets.source) : []
  const sources = (facetSources.length > 0 ? facetSources : sourceOptions)
    .slice()
    .sort()

  const sourceOpts = sources.map((s) => ({
    label: s.charAt(0).toUpperCase() + s.slice(1),
    value: s,
  }))

  const hasActiveFilters =
    (filters.category?.length ?? 0) > 0 ||
    (filters.employment_type?.length ?? 0) > 0 ||
    !!filters.date_range ||
    (filters.remote?.length ?? 0) > 0 ||
    (filters.source?.length ?? 0) > 0 ||
    (filters.company_size?.length ?? 0) > 0 ||
    (filters.exp?.length ?? 0) > 0 ||
    !!filters.exclude_no_sponsorship ||
    (filters.cap_exempt?.length ?? 0) > 0 ||
    !!filters.h1b_sponsor ||
    !!filters.everify ||
    (filters.employer_type?.length ?? 0) > 0 ||
    (filters.security_clearance?.some(v => v !== 'none') ?? false) ||
    !!filters.exclude_recruiter

  return (
    <div className="flex flex-wrap items-center gap-2 px-6 py-3">
      <DropdownPill
        label="Date posted"
        value={filters.date_range}
        options={DATE_OPTIONS}
        onSelect={(v) => onFilterChange({ date_range: v })}
      />
      <MultiSelectPill
        label="Remote"
        values={filters.remote}
        options={REMOTE_OPTIONS}
        onChange={(v) => onFilterChange({ remote: v })}
      />
      <MultiSelectPill
        label="Source"
        values={filters.source}
        options={sourceOpts}
        onChange={(v) => onFilterChange({ source: v })}
      />
      <MultiSelectPill
        label="Experience"
        values={filters.exp}
        options={EXPERIENCE_OPTIONS}
        onChange={(v) => onFilterChange({ exp: v })}
      />
      <MultiSelectPill
        label="Category"
        values={filters.category}
        options={CATEGORY_OPTIONS}
        onChange={(v) => onFilterChange({ category: v })}
      />
      <MultiSelectPill
        label="Job type"
        values={filters.employment_type}
        options={EMPLOYMENT_TYPE_OPTIONS}
        onChange={(v) => onFilterChange({ employment_type: v })}
      />
      <SponsorshipPill filters={filters} onChange={onFilterChange} />
      <MultiSelectPill
        label="Employer type"
        values={filters.employer_type}
        options={EMPLOYER_TYPE_OPTIONS}
        onChange={(v) => onFilterChange({ employer_type: v })}
      />
      <MultiSelectPill
        label="Clearance"
        values={filters.security_clearance}
        options={CLEARANCE_OPTIONS}
        onChange={(v) => onFilterChange({ security_clearance: v })}
      />
      <MultiSelectPill
        label="Company size"
        values={filters.company_size}
        options={COMPANY_SIZE_OPTIONS}
        onChange={(v) => onFilterChange({ company_size: v })}
      />
      <button
        type="button"
        title="Hide staffing-agency and aggregator reposts — prefer direct employer listings"
        onClick={() => onFilterChange({ exclude_recruiter: filters.exclude_recruiter ? undefined : true })}
        className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium transition ${
          filters.exclude_recruiter
            ? 'border-amber-500 bg-amber-50 text-amber-700'
            : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'
        }`}
      >
        {filters.exclude_recruiter ? '✕ ' : ''}Hide recruiters
      </button>

      {hasActiveFilters && (
        <button
          type="button"
          onClick={onClearFilters}
          className="ml-1 inline-flex items-center gap-1 rounded-full px-3 py-1.5 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
        >
          <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
          Clear filters
        </button>
      )}
    </div>
  )
}
