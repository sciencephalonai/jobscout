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

const VISA_OPTIONS = [
  { label: 'Sponsors visa', value: 'yes' },
  { label: 'No sponsorship', value: 'no' },
]

const EMPLOYMENT_TYPE_OPTIONS = [
  { label: 'Full-time', value: 'full_time' },
  { label: 'Part-time', value: 'part_time' },
  { label: 'Contract', value: 'contract' },
  { label: 'Internship', value: 'internship' },
  { label: 'Temporary', value: 'temporary' },
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

// ---------------------------------------------------------------------------
// Dropdown pill — a single-select popover styled as a LinkedIn-like pill
// ---------------------------------------------------------------------------

interface DropdownPillProps {
  label: string
  value: string | undefined
  options: { label: string; value: string }[]
  onSelect: (value: string | undefined) => void
  required?: boolean
}

function DropdownPill({ label, value, options, onSelect, required = false }: DropdownPillProps) {
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
          {!required && (
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
          )}
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
    (filters.date_range !== undefined && filters.date_range !== '1m') ||
    (filters.remote?.length ?? 0) > 0 ||
    (filters.source?.length ?? 0) > 0 ||
    (filters.company_size?.length ?? 0) > 0 ||
    (filters.exp?.length ?? 0) > 0 ||
    (filters.employment_type?.length ?? 0) > 0 ||
    (filters.category?.length ?? 0) > 0 ||
    (filters.visa?.length ?? 0) > 0

  return (
    <div className="flex flex-wrap items-center gap-2 px-6 py-3">
      <DropdownPill
        label="Date posted"
        value={filters.date_range}
        options={DATE_OPTIONS}
        onSelect={(v) => onFilterChange({ date_range: v })}
        required
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
        label="Job type"
        values={filters.employment_type}
        options={EMPLOYMENT_TYPE_OPTIONS}
        onChange={(v) => onFilterChange({ employment_type: v })}
      />
      <MultiSelectPill
        label="Category"
        values={filters.category}
        options={CATEGORY_OPTIONS}
        onChange={(v) => onFilterChange({ category: v })}
      />
      <MultiSelectPill
        label="Visa"
        values={filters.visa}
        options={VISA_OPTIONS}
        onChange={(v) => onFilterChange({ visa: v })}
      />
      <MultiSelectPill
        label="Company size"
        values={filters.company_size}
        options={COMPANY_SIZE_OPTIONS}
        onChange={(v) => onFilterChange({ company_size: v })}
      />

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
