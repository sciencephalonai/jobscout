import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { Company, CompanyFilters, DiscoveryResult } from '../types'
import { useCompanies, useDiscoverCompanies, useRefreshCompanies } from '../api/client'
import TopNav from './TopNav'

const TIER_OPTIONS = [
  'FAANG + Top Tech', 'Mid-Size Tech', 'Startups', 'Consulting',
  'Finance & Banks', 'Fintech & Payments', 'Healthcare',
]

function H1bBadge({ on }: { on: boolean }) {
  if (!on) return null
  return (
    <span className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium bg-emerald-100 text-emerald-700">
      H-1B sponsor
    </span>
  )
}

const CAP_EXEMPT = new Set(['university', 'hospital', 'nonprofit', 'government'])
function CapExemptBadge({ employerType }: { employerType: string }) {
  if (!CAP_EXEMPT.has(employerType)) return null
  return (
    <span
      title={`Cap-exempt employer (${employerType}) — can sponsor H-1B off-lottery`}
      className="inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium bg-violet-100 text-violet-700"
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
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50">
      <TopNav />
      <header className="flex-shrink-0 border-b border-slate-200 bg-white">
        <div className="flex items-center justify-between px-6 py-3">
          <span className="text-base font-semibold text-slate-900">
            Company registry
            {companies !== undefined && (
              <span className="ml-2 text-sm font-normal text-slate-400">
                {companies.length}{rawCompanies && companies.length !== rawCompanies.length ? ` of ${rawCompanies.length}` : ''} companies
              </span>
            )}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleDiscover}
              disabled={discover.isPending}
              title="Scan your job index to find companies with verified Greenhouse / Lever / Ashby boards not yet in your watchlist"
              className="inline-flex items-center gap-2 rounded-lg border border-blue-600 px-4 py-2 text-sm font-medium text-blue-700 transition hover:bg-blue-50 disabled:opacity-50"
            >
              {discover.isPending ? '🔍 Scanning…' : '🔍 Discover new companies'}
            </button>
            <button
              type="button"
              onClick={() => refresh.mutate({ keywords: [] })}
              disabled={refresh.isPending}
              className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:opacity-50"
            >
              {refresh.isPending ? 'Starting…' : 'Refresh watchlist'}
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 px-6 py-3">
          <select
            value={filters.tier ?? ''}
            onChange={(e) => set({ tier: e.target.value || undefined })}
            className="rounded-full border border-slate-300 bg-white px-3 py-1.5 text-sm"
          >
            <option value="">All tiers</option>
            {TIER_OPTIONS.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <button
            type="button"
            onClick={() => set({ h1b_sponsor: filters.h1b_sponsor ? undefined : true })}
            className={`rounded-full border px-3 py-1.5 text-sm font-medium ${
              filters.h1b_sponsor ? 'border-blue-600 bg-blue-50 text-blue-700' : 'border-slate-300 bg-white text-slate-700'
            }`}
          >
            H-1B sponsors
          </button>
          <button
            type="button"
            onClick={() => setCapExemptOnly((v) => !v)}
            title="Universities, hospitals, nonprofits, and government — can sponsor H-1B off-lottery"
            className={`rounded-full border px-3 py-1.5 text-sm font-medium ${
              capExemptOnly ? 'border-violet-600 bg-violet-50 text-violet-700' : 'border-slate-300 bg-white text-slate-700'
            }`}
          >
            Cap-exempt only
          </button>
          <button
            type="button"
            onClick={() => set({ direct_apply_only: filters.direct_apply_only === false ? undefined : false })}
            className={`rounded-full border px-3 py-1.5 text-sm font-medium ${
              filters.direct_apply_only === false ? 'border-blue-600 bg-blue-50 text-blue-700' : 'border-slate-300 bg-white text-slate-700'
            }`}
          >
            Scrapable only
          </button>
        </div>
        {refresh.data && (
          <div className="border-t border-slate-100 bg-emerald-50 px-6 py-2 text-sm text-emerald-800">
            Refresh started for {refresh.data.companies} companies (budget {refresh.data.budget} embeds). New jobs will appear in the Jobs tab.
          </div>
        )}
      </header>

      {/* Discovery results panel */}
      {showDiscovery && (
        <div className="flex-shrink-0 border-b border-blue-200 bg-blue-50 px-6 py-4">
          <div className="flex items-center justify-between mb-3">
            <div>
              {discover.isPending && (
                <p className="text-sm font-medium text-blue-800">
                  🔍 Scanning job index and probing ATS endpoints — this takes 15–30 s…
                </p>
              )}
              {discover.isSuccess && (
                <p className="text-sm font-medium text-blue-800">
                  {discover.data.length > 0
                    ? `Found ${discover.data.length} companies with verified ATS boards — click "Add" to watch them.`
                    : 'No new companies discovered. All companies in your job index appear to already be in your watchlist, or their ATS slugs could not be guessed.'}
                </p>
              )}
              {discover.isError && (
                <p className="text-sm text-red-700">Discovery failed: {discover.error?.message}</p>
              )}
            </div>
            <button type="button" onClick={() => setShowDiscovery(false)}
              className="text-blue-400 hover:text-blue-700 text-lg leading-none ml-4">×</button>
          </div>

          {discover.isSuccess && discover.data.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm bg-white rounded-lg overflow-hidden shadow-sm">
                <thead className="text-left text-xs uppercase tracking-wide text-slate-400 bg-slate-50">
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
                            className={`rounded px-3 py-1 text-xs font-medium transition ${
                              added
                                ? 'bg-emerald-100 text-emerald-700 cursor-default'
                                : 'bg-blue-600 text-white hover:bg-blue-700'
                            }`}
                          >
                            {added ? '✓ Added' : '+ Add to watchlist'}
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

      <div className="mx-auto w-full max-w-7xl flex-1 overflow-y-auto px-6 py-4">
        {isLoading ? (
          <p className="text-sm text-slate-400">Loading registry…</p>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead className="text-left text-xs uppercase tracking-wide text-slate-400">
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
                <tr key={`${c.ats}-${c.slug}`} className="border-t border-slate-100 hover:bg-white">
                  <td className="px-3 py-2 font-medium text-slate-800">{c.name}</td>
                  <td className="px-3 py-2 text-slate-500">{c.tier}</td>
                  <td className="px-3 py-2">
                    {c.direct_apply_only ? (
                      <span className="text-xs text-slate-400">direct-apply</span>
                    ) : (
                      <span className="capitalize text-slate-600">{c.ats}</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-slate-600">{c.open_roles || '—'}</td>
                  <td className="px-3 py-2">
                    <div className="flex flex-wrap gap-1">
                      <CapExemptBadge employerType={c.employer_type} />
                      <H1bBadge on={c.known_h1b_sponsor} />
                    </div>
                  </td>
                  <td className="px-3 py-2">
                    {c.careers_url && (
                      <a href={c.careers_url} target="_blank" rel="noopener noreferrer"
                        className="text-xs font-medium text-blue-600 hover:underline">
                        {c.direct_apply_only ? 'Apply directly ↗' : 'Careers ↗'}
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {companies && companies.length === 0 && (
          <p className="py-8 text-center text-sm text-slate-400">
            No companies in the registry yet — run <code>scripts/build_company_registry.py</code>.
          </p>
        )}
      </div>
    </div>
  )
}
