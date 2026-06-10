import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { useActiveProfile } from '../ProfileContext'
import {
  useProfiles, useTriggerIngestion, useRefreshCompanies, useScheduler, useSetScheduler,
  useSourceOverrides, useSetSourceOverride, useSavedSearches, useStats,
} from '../api/client'
import AddToWatchlistModal from './AddToWatchlistModal'

const TABS = [
  { to: '/', label: 'Jobs' },
  { to: '/my-jobs', label: 'My Jobs' },
  { to: '/saved', label: 'Saved' },
  { to: '/companies', label: 'Companies' },
  { to: '/profile', label: 'Profile' },
]

function SettingsMenu() {
  const [open, setOpen] = useState(false)
  const { data: sched } = useScheduler()
  const setSched = useSetScheduler()
  const { data: overrides } = useSourceOverrides()
  const setOverride = useSetSourceOverride()
  const jobspyOn = !!overrides?.jobspy
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)}
        className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50">
        Settings
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-80 rounded-xl border border-slate-200 bg-white p-4 shadow-lg space-y-4">
          <div>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-700">Daily auto-refresh</span>
              <button
                type="button"
                onClick={() => setSched.mutate(!sched?.enabled)}
                className={`rounded-full px-3 py-1 text-xs font-semibold ${
                  sched?.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'
                }`}
              >
                {sched?.enabled ? 'ON' : 'OFF'}
              </button>
            </div>
            <p className="mt-2 text-xs text-slate-400">
              Off by default. A daily crawl can exhaust the Gemini free embedding tier
              (1,000/day). Use "Get latest jobs" manually, or turn this on once you have a
              paid embedding tier. {sched?.next_run ? `Next run: ${new Date(sched.next_run).toLocaleString()}` : ''}
            </p>
          </div>

          <div className="border-t border-slate-100 pt-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-700">High-risk scraper (JobSpy)</span>
              <button
                type="button"
                onClick={() => setOverride.mutate({ jobspy: !jobspyOn })}
                className={`rounded-full px-3 py-1 text-xs font-semibold ${
                  jobspyOn ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-500'
                }`}
              >
                {jobspyOn ? 'ON' : 'OFF'}
              </button>
            </div>
            <p className="mt-2 text-xs text-amber-600">
              Scrapes Indeed &amp; Glassdoor. Higher volume but compliance-sensitive — use sparingly.
              Off by default; resets off when the backend restarts. Then click "Get latest jobs".
            </p>
          </div>
        </div>
      )}
    </div>
  )
}

export default function TopNav() {
  const { pathname } = useLocation()
  const { activeProfileId, setActiveProfileId } = useActiveProfile()
  const { data: profiles } = useProfiles()
  const ingest = useTriggerIngestion()
  const refreshCompanies = useRefreshCompanies()
  const queryClient = useQueryClient()
  const { data: saved } = useSavedSearches()
  const totalNew = (saved ?? []).reduce((n, s) => n + (s.new_count || 0), 0)

  const active = profiles?.find((p) => p.id === activeProfileId)

  // Ingestion runs in the background, so re-pull the jobs list a few times after
  // triggering — newly enriched + embedded jobs land over the next ~minute.
  const scheduleRefreshes = () => {
    for (const ms of [10_000, 30_000, 60_000]) {
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ['jobs'] }), ms)
    }
  }

  const getLatest = () => {
    const keywords = active?.target_titles?.length
      ? active.target_titles
      : ['software engineer', 'data engineer', 'data scientist']
    // Fetch deep (already-indexed jobs are skipped for free, so this surfaces
    // unseen jobs to actually use the embed budget). The budget still caps the run.
    ingest.mutate({ keywords, results_wanted: 250 }, { onSuccess: scheduleRefreshes })
  }

  const getCompanies = () =>
    refreshCompanies.mutate({ keywords: [] }, { onSuccess: scheduleRefreshes })

  const [showAddModal, setShowAddModal] = useState(false)
  const busy = ingest.isPending || refreshCompanies.isPending
  const getLatestRan = ingest.isSuccess
  const watchlistRan = refreshCompanies.isSuccess

  // Embedding-quota signal (polled every 15s). It's set the moment a run hits the
  // Gemini 429 and cleared on the next successful embed — so a run with quota
  // headroom shows the green "started" message first and only flips to amber if it
  // actually exhausts the quota; if the quota is already gone it shows amber up
  // front; once it recovers the banner clears itself. Covers BOTH ingest buttons.
  const { data: stats } = useStats()
  const quotaExhausted = stats?.embed_quota_exhausted ?? false

  return (
    <header className="flex-shrink-0 border-b border-slate-200 bg-white">
      <div className="flex flex-wrap items-center gap-4 px-6 py-3">
        <span className="text-lg font-semibold tracking-tight text-slate-900">JobScout</span>
        <nav className="flex flex-wrap gap-1 text-sm">
          {TABS.map((t) => {
            const activeTab = t.to === '/' ? pathname === '/' : pathname.startsWith(t.to)
            return (
              <Link key={t.to} to={t.to}
                className={`rounded-full px-3 py-1.5 font-medium ${
                  activeTab ? 'bg-blue-50 text-blue-700' : 'text-slate-500 hover:bg-slate-100'
                }`}>
                {t.label}
              </Link>
            )
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {/* Saved-search "new for me" bell */}
          <Link to="/saved" title="New matches in your saved searches"
            className="relative rounded-lg border border-slate-300 px-2.5 py-1.5 text-sm text-slate-600 hover:bg-slate-50">
            🔔
            {totalNew > 0 && (
              <span className="absolute -right-1.5 -top-1.5 rounded-full bg-rose-600 px-1.5 py-0.5 text-[10px] font-bold leading-none text-white">
                {totalNew > 99 ? '99+' : totalNew}
              </span>
            )}
          </Link>
          {/* Active-profile selector */}
          <select
            value={activeProfileId ?? ''}
            onChange={(e) => setActiveProfileId(e.target.value || null)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm"
            title="Active profile drives verdicts, sorting, and your shortlist/applied lists"
          >
            <option value="">No profile</option>
            {(profiles ?? []).map((p) => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={getLatest}
            disabled={busy}
            className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {ingest.isPending ? 'Fetching…' : 'Get latest jobs'}
          </button>
          <button
            type="button"
            onClick={getCompanies}
            disabled={busy}
            title="Re-check all watched companies for new job postings (no keyword filter — gets ALL open roles)"
            className="rounded-lg border border-blue-600 px-3 py-1.5 text-sm font-medium text-blue-700 hover:bg-blue-50 disabled:opacity-50"
          >
            {refreshCompanies.isPending ? 'Fetching…' : 'Refresh watchlist'}
          </button>
          <button
            type="button"
            onClick={() => setShowAddModal(true)}
            title="Add a company to your watchlist by specifying its ATS. 'Refresh watchlist' will then fetch its jobs."
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-50"
          >
            + Add to watchlist
          </button>
          <SettingsMenu />
        </div>
      </div>
      {quotaExhausted ? (
        <div className="border-t border-amber-200 bg-amber-50 px-6 py-1.5 text-xs text-amber-900">
          <span className="font-semibold">⚠ Embedding quota reached.</span>{' '}
          New jobs can't be saved right now (Gemini free tier = 1,000 embeds/day) — they'll resume
          after the daily reset. Your existing jobs are unaffected. Applies to both "Get latest jobs"
          and "Refresh watchlist".
        </div>
      ) : getLatestRan ? (
        <div className="border-t border-slate-100 bg-emerald-50 px-6 py-1.5 text-xs text-emerald-800">
          <span className="font-semibold">Searching job boards</span> for your keywords — new jobs are
          enriched + embedded and appear here automatically in ~1 min.
          Already-indexed jobs are skipped (free). Track per-source progress on the Sources tab.
        </div>
      ) : watchlistRan ? (
        <div className="border-t border-slate-100 bg-emerald-50 px-6 py-1.5 text-xs text-emerald-800">
          <span className="font-semibold">Checking your watched companies</span> for new postings — no
          keyword filter, fetches ALL open roles from each company. New jobs appear here in ~1 min.
          Already-indexed jobs are skipped (free).
        </div>
      ) : null}
      {showAddModal && <AddToWatchlistModal onClose={() => setShowAddModal(false)} />}
    </header>
  )
}
