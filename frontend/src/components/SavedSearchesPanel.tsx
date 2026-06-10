import { useNavigate } from 'react-router-dom'
import TopNav from './TopNav'
import {
  useSavedSearches, useMarkSavedSeen, useDeleteSavedSearch,
} from '../api/client'
import type { JobFilters } from '../types'

const APPLY_KEY = 'jobscout.applyFilters'

function describe(f: JobFilters): string {
  const bits: string[] = []
  if (f.q) bits.push(`"${f.q}"`)
  if (f.exp?.length) bits.push(f.exp.join('/'))
  if (f.remote?.length) bits.push(f.remote.join('/'))
  if (f.date_range) bits.push(f.date_range)
  if (f.everify) bits.push('e-verify')
  if (f.h1b_sponsor) bits.push('h1b')
  if (f.cap_exempt?.length) bits.push('cap-exempt')
  return bits.length ? bits.join(' · ') : 'all jobs'
}

export default function SavedSearchesPanel() {
  const { data, isLoading } = useSavedSearches()
  const seen = useMarkSavedSeen()
  const del = useDeleteSavedSearch()
  const navigate = useNavigate()

  const apply = (f: JobFilters) => {
    localStorage.setItem(APPLY_KEY, JSON.stringify(f))
    navigate('/')
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50">
      <TopNav />
      <div className="mx-auto w-full max-w-4xl flex-1 overflow-y-auto px-6 py-6">
        <h1 className="text-lg font-semibold text-slate-900">Saved searches</h1>
        <p className="mb-4 text-sm text-slate-500">
          Pin a query + filters and JobScout tracks how many <strong>new</strong> matches have arrived
          since you last looked (pull → push). Click one to apply it, "Mark seen" to reset the badge.
        </p>

        {isLoading ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : !data || data.length === 0 ? (
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-8 text-center text-sm text-slate-400">
            No saved searches yet. On the Jobs tab, set your filters and click <strong>★ Save search</strong>.
          </div>
        ) : (
          <div className="space-y-3">
            {data.map((s) => (
              <div key={s.id} className="rounded-xl border border-slate-200 bg-white p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="font-medium text-slate-800">{s.label}</h2>
                      {s.new_count > 0 && (
                        <span className="rounded-full bg-rose-100 px-2 py-0.5 text-xs font-semibold text-rose-700">
                          {s.new_count} new
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500">{describe(s.filters)}</p>
                  </div>
                  <div className="flex flex-shrink-0 gap-2">
                    <button type="button" onClick={() => apply(s.filters)}
                      className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700">
                      Apply
                    </button>
                    {s.new_count > 0 && (
                      <button type="button" onClick={() => seen.mutate(s.id)}
                        className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50">
                        Mark seen
                      </button>
                    )}
                    <button type="button" onClick={() => del.mutate(s.id)}
                      className="rounded-lg border border-rose-300 px-3 py-1.5 text-xs font-medium text-rose-600 hover:bg-rose-50">
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
