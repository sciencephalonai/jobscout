import { useNavigate } from 'react-router-dom'
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
    <div className="page-shell">
      <div className="mx-auto w-full max-w-6xl">
        <header className="page-header">
          <div>
            <h1 className="page-title">Saved searches</h1>
            <p className="page-description">Return to precise queries and see which ones have fresh matches since your last review.</p>
          </div>
        </header>

        <div>
        {isLoading ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : !data || data.length === 0 ? (
          <div className="workspace-surface border-dashed px-4 py-12 text-center text-sm text-slate-500">
            No saved searches yet. Set filters in Discover, then choose <strong>Save search</strong>.
          </div>
        ) : (
          <div className="workspace-surface divide-y divide-slate-100 overflow-hidden">
            {data.map((s) => (
              <div key={s.id} className="px-4 py-3 hover:bg-slate-50/70">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <h2 className="font-medium text-slate-800">{s.label}</h2>
                      {s.new_count > 0 && (
                        <span className="tag bg-rose-100 font-semibold text-rose-800">
                          {s.new_count} new
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 truncate font-mono text-[0.68rem] text-slate-500" title={describe(s.filters)}>{describe(s.filters)}</p>
                  </div>
                  <div className="flex flex-wrap gap-1.5 sm:flex-shrink-0">
                    <button type="button" onClick={() => apply(s.filters)}
                      className="control-focus h-8 rounded-lg bg-ink px-3 text-xs font-semibold text-white hover:bg-ink-soft">
                      Open results
                    </button>
                    {s.new_count > 0 && (
                      <button type="button" onClick={() => seen.mutate(s.id)}
                        className="control-focus h-8 rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-600 hover:bg-slate-50">
                        Mark seen
                      </button>
                    )}
                    <button type="button" onClick={() => del.mutate(s.id)}
                      className="control-focus h-8 rounded-lg px-2.5 text-xs font-medium text-rose-600 hover:bg-rose-50">
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
    </div>
  )
}
