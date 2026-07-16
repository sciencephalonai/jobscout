// Filter pill row for the jobs list: date/remote/experience/sponsorship pills, direct-sources + recommendation toggles, advanced filter drawer.
import { useState, useRef, useEffect } from 'react'
import { CaretDown, Check, SlidersHorizontal, X } from '@phosphor-icons/react'
import type { JobFilters, JobsResponse } from '../types'

interface FilterBarProps {
  filters: JobFilters
  facets: JobsResponse['facets'] | undefined
  sourceOptions: string[]
  hasActiveProfile: boolean
  recommendationLocked?: boolean
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

const SOURCE_LABELS: Record<string, string> = {
  jobspy: 'JobSpy',
  remoteok: 'Remote OK',
  rss: 'RSS feeds',
  smartrecruiters: 'SmartRecruiters',
  themuse: 'The Muse',
  workingnomads: 'Working Nomads',
}

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
        aria-expanded={open}
        className={`control-focus inline-flex h-7 items-center gap-1 rounded-lg border px-2.5 text-[0.7rem] font-medium ${
          active
            ? 'border-signal-400 bg-signal-50 text-signal-800'
            : 'border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50'
        }`}
      >
        <span>{selectedLabel}</span>
        <CaretDown size={13} className={`${open ? 'rotate-180' : ''} ${active ? 'text-signal-600' : 'text-slate-400'}`} />
      </button>

      {open && (
        <div className="popover-enter absolute left-0 z-30 mt-1.5 w-52 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-xl">
          <button
            type="button"
            onClick={() => {
              onSelect(undefined)
              setOpen(false)
            }}
            className={`flex w-full items-center px-3 py-1.5 text-left text-xs transition hover:bg-slate-50 ${
              !active ? 'font-medium text-signal-700' : 'text-slate-600'
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
                className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-xs transition hover:bg-slate-50 ${
                  isSel ? 'font-medium text-signal-700' : 'text-slate-700'
                }`}
              >
                <span>{opt.label}</span>
                {isSel && <Check size={14} weight="bold" className="text-signal-600" />}
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
  variant?: 'compact' | 'field'
}

function MultiSelectPill({ label, values, options, onChange, variant = 'compact' }: MultiSelectPillProps) {
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
        aria-expanded={open}
        className={`${variant === 'field' ? 'flex h-8 w-full justify-between rounded-lg px-2.5 text-xs' : 'inline-flex h-7 rounded-lg px-2.5 text-[0.7rem]'} control-focus items-center gap-1 border font-medium ${
          active
            ? 'border-signal-400 bg-signal-50 text-signal-800'
            : 'border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50'
        }`}
      >
        <span>{label}</span>
        {active && (
          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-[0.3rem] bg-signal-600 px-1 font-mono text-[9px] font-semibold text-white">
            {count}
          </span>
        )}
        <CaretDown size={13} className={`${open ? 'rotate-180' : ''} ${active ? 'text-signal-600' : 'text-slate-400'}`} />
      </button>

      {open && (
        <div className="popover-enter absolute left-0 z-30 mt-1.5 max-h-72 w-52 overflow-y-auto rounded-lg border border-slate-200 bg-white py-1 shadow-xl">
          {active && (
            <button
              type="button"
              onClick={() => onChange(undefined)}
              className="flex w-full items-center px-3 py-1.5 text-left text-xs text-slate-500 transition hover:bg-slate-50"
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
                aria-pressed={isSel}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition hover:bg-slate-50 ${
                  isSel ? 'font-medium text-signal-700' : 'text-slate-700'
                }`}
              >
                <span
                  className={`flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center rounded-[0.22rem] border ${
                    isSel ? 'border-signal-500 bg-signal-500' : 'border-slate-300 bg-white'
                  }`}
                >
                  {isSel && <Check size={10} weight="bold" className="text-white" />}
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
        aria-expanded={open}
        className={`control-focus inline-flex h-7 items-center gap-1 rounded-lg border px-2.5 text-[0.7rem] font-medium ${
          active
            ? 'border-signal-400 bg-signal-50 text-signal-800'
            : 'border-slate-300 bg-white text-slate-700 hover:border-slate-400 hover:bg-slate-50'
        }`}
      >
        <span>Work authorization</span>
        {active && (
          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-[0.3rem] bg-signal-600 px-1 font-mono text-[9px] font-semibold text-white">
            {count}
          </span>
        )}
        <CaretDown size={13} className={`${open ? 'rotate-180' : ''} ${active ? 'text-signal-600' : 'text-slate-400'}`} />
      </button>

      {open && (
        <div className="popover-enter absolute left-0 z-30 mt-1.5 w-72 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-xl">
          {toggles.map((t) => (
            <button
              key={t.label}
              type="button"
              onClick={t.set}
              aria-pressed={t.on}
              className={`flex w-full items-start gap-2 px-3 py-2 text-left text-xs transition hover:bg-slate-50 ${
                t.on ? 'font-medium text-signal-700' : 'text-slate-700'
              }`}
            >
              <span
                className={`mt-0.5 flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center rounded-[0.22rem] border ${
                  t.on ? 'border-signal-500 bg-signal-500' : 'border-slate-300 bg-white'
                }`}
              >
                {t.on && <Check size={10} weight="bold" className="text-white" />}
              </span>
              <span className="flex flex-col">
                <span>{t.label}</span>
                <span className="text-[0.68rem] font-normal leading-4 text-slate-400">{t.hint}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

interface AdvancedToggleProps {
  label: string
  hint: string
  active: boolean
  onClick: () => void
}

function AdvancedToggle({ label, hint, active, onClick }: AdvancedToggleProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="control-focus group flex min-h-12 w-full items-center justify-between gap-3 rounded-md px-2 py-1.5 text-left hover:bg-slate-50"
    >
      <span className="min-w-0 flex-1">
        <span className={`block text-xs font-semibold leading-4 ${active ? 'text-signal-800' : 'text-slate-700'}`}>{label}</span>
        <span className="mt-0.5 block text-[0.65rem] font-normal leading-4 text-slate-400">{hint}</span>
      </span>
      <span className={`relative h-4 w-7 shrink-0 rounded-full ${active ? 'bg-signal-500' : 'bg-slate-200 group-hover:bg-slate-300'}`} aria-hidden="true">
        {/* left-0 anchors the knob (see ToggleRow) — without it translate-x overshoots the track */}
        <span className={`absolute left-0 top-0.5 h-3 w-3 rounded-full bg-white shadow-sm ${active ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
      </span>
    </button>
  )
}

// ---------------------------------------------------------------------------
// Main bar
// ---------------------------------------------------------------------------

export default function FilterBar({
  filters,
  facets,
  sourceOptions,
  hasActiveProfile,
  recommendationLocked = false,
  onFilterChange,
  onClearFilters,
}: FilterBarProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const advancedRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!advancedOpen) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (advancedRef.current && !advancedRef.current.contains(event.target as Node)) {
        setAdvancedOpen(false)
      }
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setAdvancedOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [advancedOpen])
  // Facets reflect the current query. Once a source is selected, the response
  // may contain only that source. Keep the stable source catalog in the menu so
  // users can add a second source without clearing the first one.
  const facetSources = facets?.source ? Object.keys(facets.source) : []
  const sources = Array.from(new Set([
    ...sourceOptions,
    ...facetSources,
    ...(filters.source ?? []),
  ])).sort()

  const sourceOpts = sources.map((s) => ({
    label: SOURCE_LABELS[s] ?? s.charAt(0).toUpperCase() + s.slice(1),
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
    !!filters.exclude_recruiter ||
    !!filters.exclude_ghost ||
    !!filters.true_entry_only ||
    !!filters.new_grad_only ||
    !!filters.direct_sources_only ||
    !!filters.recommendation_only ||
    !!filters.apply_only

  const advancedFilterCount =
    Number((filters.source?.length ?? 0) > 0) +
    Number((filters.category?.length ?? 0) > 0) +
    Number((filters.employment_type?.length ?? 0) > 0) +
    Number((filters.employer_type?.length ?? 0) > 0) +
    Number((filters.company_size?.length ?? 0) > 0) +
    Number((filters.security_clearance?.filter((value) => value !== 'none').length ?? 0) > 0) +
    Number(!!filters.exclude_recruiter) +
    Number(!!filters.exclude_ghost) +
    Number(!!filters.true_entry_only) +
    Number(!!filters.new_grad_only)

  const clearAdvancedFilters = () => onFilterChange({
    source: undefined,
    category: undefined,
    employment_type: undefined,
    employer_type: undefined,
    security_clearance: undefined,
    company_size: undefined,
    exclude_recruiter: undefined,
    exclude_ghost: undefined,
    true_entry_only: undefined,
    new_grad_only: undefined,
  })

  return (
    <div ref={advancedRef} className="relative">
      <div className="flex flex-wrap items-center gap-1.5">
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
        label="Experience"
        values={filters.exp}
        options={EXPERIENCE_OPTIONS}
        onChange={(v) => onFilterChange({ exp: v })}
      />
      <SponsorshipPill filters={filters} onChange={onFilterChange} />
      <button
        type="button"
        title={recommendationLocked
          ? 'For You is locked to official employer ATS and government boards.'
          : 'Only official employer ATS and government boards. Keeps direct application links and removes discovery sources.'}
        onClick={() => {
          if (recommendationLocked) return
          onFilterChange({ direct_sources_only: filters.direct_sources_only ? undefined : true })
        }}
        disabled={recommendationLocked}
        className={`control-focus inline-flex h-7 items-center gap-1 rounded-lg border px-2.5 text-[0.7rem] font-medium ${
          filters.direct_sources_only
            ? 'border-signal-400 bg-signal-50 text-signal-800'
            : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50'
        }`}
      >
        {filters.direct_sources_only && <Check size={12} weight="bold" />}Direct sources
      </button>
      <button
        type="button"
        title={recommendationLocked
          ? 'For You always shows profile-backed recommendations from direct sources.'
          : 'Show only roles supported by the active profile, prioritizing direct employer sources.'}
        onClick={() => {
          if (recommendationLocked) return
          onFilterChange({
            recommendation_only: filters.recommendation_only ? undefined : true,
            target_min: filters.recommendation_only ? undefined : 5,
            direct_sources_only: filters.recommendation_only ? undefined : true,
            exclude_ghost: filters.recommendation_only ? undefined : true,
            date_range: undefined,
          })
        }}
        disabled={!hasActiveProfile || recommendationLocked}
        className={`control-focus inline-flex h-7 items-center gap-1 rounded-lg border px-2.5 text-[0.7rem] font-medium ${
          filters.recommendation_only
            ? 'border-signal-600 bg-signal-600 text-white'
            : 'border-slate-300 bg-white text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-45'
        }`}
      >
        {filters.recommendation_only && <Check size={12} weight="bold" />}Profile recommendations
      </button>
      <button
        type="button"
        onClick={() => setAdvancedOpen((open) => !open)}
        aria-expanded={advancedOpen}
        aria-controls="advanced-filter-panel"
        className={`control-focus inline-flex h-7 items-center gap-1.5 rounded-lg border px-2.5 text-[0.7rem] font-medium ${
          advancedOpen || advancedFilterCount
            ? 'border-signal-400 bg-signal-50 text-signal-800'
            : 'border-slate-300 bg-white text-slate-600 hover:border-slate-400 hover:bg-slate-50'
        }`}
      >
        <SlidersHorizontal size={14} />
        <span>More filters</span>
        {advancedFilterCount > 0 && <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-[0.3rem] bg-signal-600 px-1 font-mono text-[9px] font-semibold text-white">{advancedFilterCount}</span>}
        <CaretDown size={12} className={`${advancedOpen ? 'rotate-180' : ''} text-signal-500`} />
      </button>

      {hasActiveFilters && (
        <button
          type="button"
          onClick={onClearFilters}
          aria-label="Clear filters"
          title="Clear filters"
          className="control-focus ml-auto inline-flex h-7 w-7 items-center justify-center gap-1 rounded-lg text-[0.7rem] font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-700 sm:w-auto sm:px-2"
        >
          <X size={14} />
          <span className="hidden sm:inline">Clear filters</span>
        </button>
      )}
      </div>

      {advancedOpen && (
        <>
          <button type="button" aria-label="Close more filters" onClick={() => setAdvancedOpen(false)} className="fixed inset-0 z-30 bg-ink/30 backdrop-blur-[1px] sm:hidden" />
          <div
            id="advanced-filter-panel"
            role="dialog"
            aria-labelledby="advanced-filter-title"
            className="popover-enter fixed inset-x-2 bottom-2 z-40 max-h-[calc(100dvh-5rem)] overflow-y-auto rounded-xl border border-slate-200 bg-white shadow-[0_24px_64px_-20px_rgba(21,26,31,0.42)] sm:absolute sm:inset-x-auto sm:bottom-auto sm:right-0 sm:top-full sm:mt-2 sm:w-[min(46rem,calc(100vw-4rem))]"
          >
            <div className="h-0.5 rounded-t-xl bg-signal-500" aria-hidden="true" />
            <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-4 py-3">
              <div>
                <p id="advanced-filter-title" className="text-sm font-semibold tracking-[-0.015em] text-ink">More filters</p>
                <p className="mt-0.5 text-[0.68rem] leading-4 text-slate-400">Narrow by role, employer, source, and screening quality.</p>
              </div>
              <button type="button" onClick={() => setAdvancedOpen(false)} aria-label="Close more filters" className="control-focus flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-ink">
                <X size={15} />
              </button>
            </div>

            <div className="grid md:grid-cols-3 md:divide-x md:divide-slate-200">
              <section className="px-4 py-3">
                <p className="text-xs font-semibold text-slate-700">Role</p>
                <p className="mb-2 text-[0.65rem] leading-4 text-slate-400">Function, contract, and eligibility restrictions.</p>
                <div className="space-y-1.5">
                  <MultiSelectPill variant="field" label="Category" values={filters.category} options={CATEGORY_OPTIONS} onChange={(v) => onFilterChange({ category: v })} />
                  <MultiSelectPill variant="field" label="Job type" values={filters.employment_type} options={EMPLOYMENT_TYPE_OPTIONS} onChange={(v) => onFilterChange({ employment_type: v })} />
                  <MultiSelectPill variant="field" label="Clearance" values={filters.security_clearance} options={CLEARANCE_OPTIONS} onChange={(v) => onFilterChange({ security_clearance: v })} />
                </div>
              </section>

              <section className="border-t border-slate-200 px-4 py-3 md:border-t-0">
                <p className="text-xs font-semibold text-slate-700">Employer & source</p>
                <p className="mb-2 text-[0.65rem] leading-4 text-slate-400">Where the role came from and who is hiring.</p>
                <div className="space-y-1.5">
                  <MultiSelectPill variant="field" label="Source" values={filters.source} options={sourceOpts} onChange={(v) => onFilterChange({ source: v })} />
                  <MultiSelectPill variant="field" label="Employer type" values={filters.employer_type} options={EMPLOYER_TYPE_OPTIONS} onChange={(v) => onFilterChange({ employer_type: v })} />
                  <MultiSelectPill variant="field" label="Company size" values={filters.company_size} options={COMPANY_SIZE_OPTIONS} onChange={(v) => onFilterChange({ company_size: v })} />
                </div>
              </section>

              <section className="border-t border-slate-200 px-4 py-3 md:border-t-0">
                <p className="text-xs font-semibold text-slate-700">Screening quality</p>
                <p className="mb-1 text-[0.65rem] leading-4 text-slate-400">Remove noise before it reaches the shortlist.</p>
                <div className="divide-y divide-slate-100">
                  <AdvancedToggle label="Hide recruiters" hint="Prefer employer postings." active={!!filters.exclude_recruiter} onClick={() => onFilterChange({ exclude_recruiter: filters.exclude_recruiter ? undefined : true })} />
                  <AdvancedToggle label="Hide likely stale" hint="Remove ghost-risk listings." active={!!filters.exclude_ghost} onClick={() => onFilterChange({ exclude_ghost: filters.exclude_ghost ? undefined : true })} />
                  <AdvancedToggle label="True entry-level" hint="Keep high-confidence 0–2 year roles." active={!!filters.true_entry_only} onClick={() => onFilterChange({ true_entry_only: filters.true_entry_only ? undefined : true })} />
                  <AdvancedToggle label="New-grad programs" hint="Explicit early-career tracks." active={!!filters.new_grad_only} onClick={() => onFilterChange({ new_grad_only: filters.new_grad_only ? undefined : true })} />
                </div>
              </section>
            </div>

            <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50/70 px-4 py-2.5">
              <span className="font-mono text-[0.62rem] text-slate-400">{advancedFilterCount || 'No'} advanced {advancedFilterCount === 1 ? 'filter' : 'filters'} active</span>
              <div className="flex items-center gap-1.5">
                {advancedFilterCount > 0 && <button type="button" onClick={clearAdvancedFilters} className="control-focus h-8 rounded-lg px-2.5 text-xs font-medium text-slate-500 hover:bg-white hover:text-ink">Reset</button>}
                <button type="button" onClick={() => setAdvancedOpen(false)} className="control-focus h-8 rounded-lg bg-ink px-3 text-xs font-semibold text-white hover:bg-ink-soft">Done</button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
