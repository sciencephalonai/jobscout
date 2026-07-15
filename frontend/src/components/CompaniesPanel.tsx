// Company registry tab: watchlist with ATS/tier/H-1B/cap-exempt flags and per-company refresh.
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ArrowClockwise, ArrowSquareOut, MagnifyingGlass, Plus, X } from '@phosphor-icons/react'
import type { Company, CompanyFilters, DiscoveryResult } from '../types'
import { useCompanies, useDiscoverCompanies, useRefreshCompanies } from '../api/client'

const TIER_OPTIONS = [
  'FAANG + Top Tech', 'Mid-Size Tech', 'Startups', 'Consulting',
  'Finance & Banks', 'Fintech & Payments', 'Healthcare',
]

function H1bBadge({ on }: { on: boolean }) {
  if (!on) return null
  return (
    <span className="tag bg-emerald-100 text-emerald-800">
      H-1B sponsor
    </span>
  )
}

const CAP_EXEMPT = new Set(['university', 'hospital', 'nonprofit', 'government'])
function CapExemptBadge({ employerType }: { employerType: string }) {
  if (!CAP_EXEMPT.has(employerType)) return null
  return (
    <span
      title={`Cap-exempt employer (${employerType}); can sponsor H-1B off-lottery`}
      className="tag bg-signal-50 text-signal-800"
    >
      Cap-exempt
    </span>
  )
}

export default function CompaniesPanel() {
  const qc = useQueryClient()
  const [filters, setFilters] = useState<CompanyFilters>({ sort: 'open_roles' })
  const [capExemptOnly, setCapExemptOnly] = useState(false)
  const { data: rawCompanies, isLoading } = useCompanies(filters)
  const refresh = useRefreshCompanies()
  const discover = useDiscoverCompanies()
  const [addedSlugs, setAddedSlugs] = useState<Set<string>>(new Set())
  const [showDiscovery, setShowDiscovery] = useState(false)

  const set = (u: Partial<CompanyFilters>) => setFilters((f) => ({ ...f, ...u }))

  const handleDiscover = () => {
    setShowDiscovery(true)
    setAddedSlugs(new Set())
    discover.mutate()
  }

  const handleAddDiscovered = async (r: DiscoveryResult) => {
    const body: Partial<Company> = {
      slug: r.slug, name: r.name, ats: r.ats as Company['ats'],
      employer_type: 'for_profit', enabled: true, cap_exempt_hint: 'unknown',
    }
    await fetch('/api/companies', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    setAddedSlugs((prev) => new Set([...prev, `${r.ats}/${r.slug}`]))
    qc.invalidateQueries({ queryKey: ['companies'] })
  }

  // Client-side cap-exempt filter (280 companies — no round-trip needed).
  const companies = capExemptOnly
    ? rawCompanies?.filter((c) => CAP_EXEMPT.has(c.employer_type))
    : rawCompanies

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] min-h-0 flex-col overflow-hidden px-3 pb-3 pt-3 sm:px-4 xl:h-dvh xl:px-5 xl:pt-4">
      <header className="flex-shrink-0 border-b border-slate-200 pb-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h1 className="page-title">Company registry</h1>
            <p className="page-description">Watch verified employer boards, refresh direct ATS roles, and discover boards already present in the index.</p>
            {companies !== undefined && (
              <span className="mt-1 block font-mono text-[0.65rem] font-normal text-slate-400">
                {companies.length}{rawCompanies && companies.length !== rawCompanies.length ? ` of ${rawCompanies.length}` : ''} companies
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={handleDiscover}
              disabled={discover.isPending}
              title="Scan your job index to find companies with verified Greenhouse / Lever / Ashby boards not yet in your watchlist"
              className="control-focus inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-xs font-medium text-slate-600 hover:border-signal-200 hover:bg-signal-50 hover:text-signal-800 disabled:opacity-50"
            >
              <MagnifyingGlass size={14} />{discover.isPending ? 'Scanning…' : 'Discover boards'}
            </button>
            <button
              type="button"
              onClick={() => refresh.mutate({ keywords: [] })}
              disabled={refresh.isPending}
              className="control-focus inline-flex h-8 items-center gap-1.5 rounded-lg bg-ink px-3 text-xs font-semibold text-white hover:bg-ink-soft disabled:opacity-50"
            >
              <ArrowClockwise size={14} />{refresh.isPending ? 'Starting…' : 'Refresh watchlist'}
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <select
            value={filters.tier ?? ''}
            onChange={(e) => set({ tier: e.target.value || undefined })}
            aria-label="Filter by company tier"
            className="control-focus h-8 rounded-lg border border-slate-300 bg-white px-2.5 text-xs text-slate-700"
          >
            <option value="">All tiers</option>
            {TIER_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <button
            type="button"
            onClick={() => set({ h1b_sponsor: filters.h1b_sponsor ? undefined : true })}
            aria-pressed={!!filters.h1b_sponsor}
            className={`control-focus h-8 rounded-lg border px-2.5 text-xs font-medium ${
              filters.h1b_sponsor ? 'border-signal-400 bg-signal-50 text-signal-800' : 'border-slate-300 bg-white text-slate-700'
            }`}
          >
            H-1B sponsors
          </button>
          <button
            type="button"
            onClick={() => setCapExemptOnly((v) => !v)}
            title="Universities, hospitals, nonprofits, and government can sponsor H-1B off-lottery"
            aria-pressed={capExemptOnly}
            className={`control-focus h-8 rounded-lg border px-2.5 text-xs font-medium ${
              capExemptOnly ? 'border-signal-400 bg-signal-50 text-signal-800' : 'border-slate-300 bg-white text-slate-700'
            }`}
          >
            Cap-exempt only
          </button>
          <button
            type="button"
            onClick={() => set({ direct_apply_only: filters.direct_apply_only === false ? undefined : false })}
            aria-pressed={filters.direct_apply_only === false}
            className={`control-focus h-8 rounded-lg border px-2.5 text-xs font-medium ${
              filters.direct_apply_only === false ? 'border-signal-400 bg-signal-50 text-signal-800' : 'border-slate-300 bg-white text-slate-700'
            }`}
          >
            Scrapable only
          </button>
        </div>
        {refresh.data && (
          <div className="mt-2 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
            Refresh started for {refresh.data.companies} companies (budget {refresh.data.budget} embeds). New jobs will appear in the Jobs tab.
          </div>
        )}
      </header>

      {/* Discovery results panel */}
      {showDiscovery && (
        <div className="workspace-surface mt-3 flex-shrink-0 overflow-hidden px-4 py-3">
          <div className="flex items-center justify-between mb-3">
            <div>
              {discover.isPending && (
                <p className="text-sm font-medium text-blue-800">
                  Scanning the index and probing ATS endpoints. This usually takes 15–30 seconds.
                </p>
              )}
              {discover.isSuccess && (
                <p className="text-sm font-medium text-blue-800">
                  {discover.data.length > 0
                    ? `Found ${discover.data.length} ${discover.data.length === 1 ? 'company' : 'companies'} with verified ATS boards. Add the ones you want to watch.`
                    : 'No new companies discovered. All companies in your job index appear to already be in your watchlist, or their ATS slugs could not be guessed.'}
                </p>
              )}
              {discover.isError && (
                <p className="text-sm text-red-700">Discovery failed: {discover.error?.message}</p>
              )}
            </div>
            <button type="button" onClick={() => setShowDiscovery(false)} aria-label="Close discovered companies"
              className="control-focus ml-4 flex h-7 w-7 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-ink"><X size={15} /></button>
          </div>

          {discover.isSuccess && discover.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm bg-white rounded-lg overflow-hidden shadow-sm">
                <thead className="bg-slate-50 text-left font-mono text-[0.62rem] uppercase tracking-[0.08em] text-slate-500">
                  <tr>
                    <th className="px-3 py-2">Company</th>
                    <th className="px-3 py-2">ATS</th>
                    <th className="px-3 py-2">Open roles</th>
                    <th className="px-3 py-2">Sample role</th>
                    <th className="px-3 py-2">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {discover.data.map((r) => {
                    const key = `${r.ats}/${r.slug}`
                    const added = addedSlugs.has(key)
                    return (
                      <tr key={key} className="hover:bg-slate-50">
                        <td className="px-3 py-2 font-medium text-slate-800">{r.name}</td>
                        <td className="px-3 py-2">
                          <span className="rounded px-1.5 py-0.5 text-xs font-medium bg-slate-100 text-slate-600 capitalize">{r.ats}</span>
                        </td>
                        <td className="px-3 py-2 text-slate-600">{r.job_count}</td>
                        <td className="px-3 py-2 text-slate-500 text-xs max-w-xs truncate" title={r.sample_title ?? ''}>
                          {r.sample_title ?? '—'}
                        </td>
                        <td className="px-3 py-2">
                          <button type="button"
                            disabled={added}
                            onClick={() => handleAddDiscovered(r)}
                            className={`control-focus inline-flex h-7 items-center gap-1 rounded-lg px-2.5 text-xs font-medium ${
                              added
                                ? 'bg-emerald-100 text-emerald-700 cursor-default'
                                : 'bg-ink text-white hover:bg-ink-soft'
                            }`}
                          >
                            {added ? 'Added' : <><Plus size={12} />Add</>}
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <p className="mt-2 text-xs text-blue-700 opacity-75">
                After adding, click <strong>Refresh watchlist</strong> to fetch their open jobs.
                Workday companies (Intel, NVIDIA) must be added manually via "+ Add to watchlist" in the top bar.
              </p>
            </div>
          )}
        </div>
      )}

      <div className="mx-auto w-full max-w-7xl flex-1 overflow-auto py-3 [scrollbar-gutter:stable]">
        {isLoading ? (
          <p className="text-sm text-slate-400">Loading registry…</p>
        ) : (
          <div className="workspace-surface min-w-[46rem] overflow-hidden">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 z-10 bg-[#f7f8f6] text-left font-mono text-[0.62rem] uppercase tracking-[0.08em] text-slate-500">
              <tr>
                <th className="px-3 py-2">Company</th>
                <th className="px-3 py-2">Tier</th>
                <th className="px-3 py-2">ATS</th>
                <th className="px-3 py-2">Open roles</th>
                <th className="px-3 py-2">Signals</th>
                <th className="px-3 py-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {(companies ?? []).map((c) => (
                <tr key={`${c.ats}-${c.slug}`} className="border-t border-slate-100 hover:bg-slate-50/80">
                  <td className="px-3 py-2 font-medium text-slate-800">{c.name}</td>
                  <td className="px-3 py-2 text-slate-500">{c.tier}</td>
                  <td className="px-3 py-2">
                    {c.direct_apply_only ? (
                      <span className="text-xs text-slate-400">direct-apply</span>
                    ) : (
                      <span className="capitalize text-slate-600">{c.ats}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-slate-600">{c.open_roles || '–'}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      <CapExemptBadge employerType={c.employer_type} />
                      <H1bBadge on={c.known_h1b_sponsor} />
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    {c.careers_url && (
                      <a href={c.careers_url} target="_blank" rel="noopener noreferrer"
                        className="control-focus inline-flex items-center gap-1 rounded text-xs font-medium text-signal-700 hover:underline">
                        {c.direct_apply_only ? 'Apply directly' : 'Careers'}<ArrowSquareOut size={12} />
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
        {companies && companies.length === 0 && (
          <p className="py-8 text-center text-sm text-slate-400">
            No companies in the registry yet. Run <code>scripts/build_company_registry.py</code>.
          </p>
        )}
      </div>
    </div>
  )
}
