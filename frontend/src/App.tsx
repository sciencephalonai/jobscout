// Root app: routes + the Jobs page (Discover and the locked For You recommendation view).
import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { Routes, Route, Navigate, Link } from 'react-router-dom'
import { BookmarkSimple, ClipboardText, MagicWand, Sparkle, UserCircle } from '@phosphor-icons/react'
import type { JobFilters } from './types'
import { useJobs, useSourcesStatus, useCreateSavedSearch, useDeepMatchTopN, useDeepResults, useHydrateDeepResults } from './api/client'
import { ProfileProvider, useActiveProfile } from './ProfileContext'
import FilterBar from './components/FilterBar'
import SearchBar from './components/SearchBar'
import JobList from './components/JobList'
import JobDetailPane from './components/JobDetailPane'
import AdminPage from './components/AdminPage'
import CompaniesPanel from './components/CompaniesPanel'
import MyJobsPanel from './components/MyJobsPanel'
import ProfilePanel from './components/ProfilePanel'
import SavedSearchesPanel from './components/SavedSearchesPanel'
import AppShell from './components/AppShell'

const APPLY_KEY = 'jobscout.applyFilters'

/**
 * "New since your last visit" for the For You feed. Local-only by design — this
 * is a single-user local tool, so the last-visit timestamp lives in localStorage
 * (no backend write per page view). Returns the cutoff captured on THIS visit,
 * then stamps now so the next visit compares fresh.
 */
function useLastSeen(profileId: string | null | undefined): string | null {
  const cutoffRef = useRef<{ id: string | null; value: string | null }>({ id: null, value: null })
  const id = profileId ?? null
  if (cutoffRef.current.id !== id) {
    const key = id ? `jobscout.foryouSeen:${id}` : null
    cutoffRef.current = { id, value: key ? localStorage.getItem(key) : null }
    if (key) localStorage.setItem(key, new Date().toISOString())
  }
  return cutoffRef.current.value
}

const DEFAULT_FILTERS: JobFilters = {
  page: 1,
  page_size: 20,
  sort: 'posted_desc',
  // Semantic-leaning search (mostly meaning-based, slight keyword weighting so
  // exact title matches still surface). No user-facing control — see SearchBar.
  alpha: 0.75,
}

// "For You": a profile-backed recommendation feed. Retrieval stays on direct
// employer sources and the backend progressively widens until useful matches exist.
const FOR_YOU_FILTERS: JobFilters = {
  ...DEFAULT_FILTERS,
  sort: 'match',
  recommendation_only: true,
  target_min: 5,
  direct_sources_only: true,
  exclude_ghost: true,
}

function JobsPage({ forYou = false }: { forYou?: boolean }) {
  const [filters, setFilters] = useState<JobFilters>(forYou ? FOR_YOU_FILTERS : DEFAULT_FILTERS)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false)
  const [noProfileTipDismissed, setNoProfileTipDismissed] = useState(false)
  const [showOnlyNew, setShowOnlyNew] = useState(false)
  const { activeProfileId } = useActiveProfile()
  const createSaved = useCreateSavedSearch()
  const deepTopN = useDeepMatchTopN()
  const lastSeen = useLastSeen(forYou ? activeProfileId : null)

  // Apply a saved search handed off from the Saved tab (via localStorage), once.
  useEffect(() => {
    const raw = localStorage.getItem(APPLY_KEY)
    if (raw) {
      localStorage.removeItem(APPLY_KEY)
      try { setFilters({ ...DEFAULT_FILTERS, ...JSON.parse(raw), page: 1 }) } catch { /* ignore */ }
    }
  }, [])

  const onSaveSearch = () => {
    const label = window.prompt('Name this saved search (you\'ll get a "new matches" badge):')
    if (label && label.trim()) {
      createSaved.mutate({ label: label.trim(), filters, profile_id: activeProfileId ?? null })
    }
  }

  // Attach the active profile so the list carries verdicts + exclusions. For You
  // is never allowed to degrade to a generic feed: without a profile, the jobs
  // query is disabled and the page presents an explicit profile-required state.
  const missingProfile = forYou && !activeProfileId
  const effectiveFilters: JobFilters = {
    ...filters,
    ...(forYou ? {
      recommendation_only: true,
      apply_only: undefined,
      // Best match by default, but respect the user's sort choice (e.g.
      // "Newest") — ordering only; qualification gates are unchanged.
      sort: filters.sort ?? 'match',
      target_min: 5,
      direct_sources_only: true,
      exclude_recruiter: undefined,
    } : {}),
    profile_id: activeProfileId ?? undefined,
  }
  const { data, isLoading, isFetching, isError, error } = useJobs(effectiveFilters, {
    enabled: !missingProfile,
    // Keep the previous list on screen while a filter/sort change refetches —
    // a cold spinner on every pill click reads as "slow" even when it isn't.
    keepPreviousData: true,
    // For You can launch a budget-capped profile refill in the backend. Poll so
    // newly ingested matches appear without a manual reload on any viewport.
    refetchInterval: forYou && !missingProfile ? 60_000 : false,
  })
  const { data: sources } = useSourcesStatus()

  // A disabled query can still have cached data for its key. Mask it explicitly
  // so a previous profile or generic result can never appear in the required state.
  const allJobs = missingProfile ? [] : (data?.jobs ?? [])
  // "New since last visit": jobs ingested after the cutoff captured on mount.
  // Client-side over the loaded shortlist (For You is a bounded feed).
  const newCount = useMemo(
    () => (lastSeen ? allJobs.filter((j) => j.ingested_at > lastSeen).length : 0),
    [allJobs, lastSeen],
  )
  const filteredJobs = showOnlyNew && lastSeen ? allJobs.filter((j) => j.ingested_at > lastSeen) : allJobs

  // Rehydrate persisted deep-match results for the visible jobs (no LLM spend) so
  // badges survive reloads/restarts; the backend filters out stale-fingerprint rows.
  useHydrateDeepResults(allJobs.map((j) => j.job_id), activeProfileId)
  // Deep-match: reactive map of cached AI verdicts for the loaded jobs (drives the
  // badges, the "next 10" batch target, and the tiered re-rank — one source of truth).
  const deepResults = useDeepResults(forYou ? allJobs.map((j) => j.job_id) : [], activeProfileId)
  const analyzedCount = Object.keys(deepResults).length
  const unanalyzed = useMemo(
    () => filteredJobs.filter((j) => !deepResults[j.job_id]),
    [filteredJobs, deepResults],
  )
  // Auto-rank by AI once any results exist; the "✕" chip reverts to server order.
  const [aiRankOverride, setAiRankOverride] = useState<boolean | null>(null)
  const aiRanked = (aiRankOverride ?? analyzedCount > 0)
  const jobs = useMemo(() => {
    if (!forYou || !aiRanked || deepTopN.isPending) return filteredJobs  // don't reorder mid-batch
    const tier = (id: string) => {
      const d = deepResults[id]
      if (!d) return 2
      return d.verdict === 'apply' ? 0 : d.verdict === 'borderline' ? 1 : 3
    }
    return [...filteredJobs].sort((a, b) => {
      const ta = tier(a.job_id), tb = tier(b.job_id)
      if (ta !== tb) return ta - tb
      return (deepResults[b.job_id]?.score ?? 0) - (deepResults[a.job_id]?.score ?? 0)
    })
  }, [forYou, aiRanked, deepTopN.isPending, filteredJobs, deepResults])

  // Auto-select the first job whenever the list changes and the current
  // selection is no longer present (or nothing is selected yet).
  useEffect(() => {
    if (jobs.length === 0) {
      setSelectedJobId(null)
      return
    }
    const stillPresent = selectedJobId && jobs.some((j) => j.job_id === selectedJobId)
    if (!stillPresent) {
      setSelectedJobId(jobs[0].job_id)
    }
  }, [jobs, selectedJobId])

  const handleFilterChange = useCallback((updates: Partial<JobFilters>) => {
    setFilters((prev) => ({
      ...prev,
      ...updates,
      // Reset to page 1 when any filter changes except pagination itself
      page: updates.page !== undefined ? updates.page : 1,
    }))
  }, [])

  const handleJobSelect = useCallback((jobId: string) => {
    setSelectedJobId(jobId)
    if (window.matchMedia('(max-width: 1023px)').matches) setMobileDetailOpen(true)
  }, [])

  const handleClearFilters = useCallback(() => {
    setFilters(forYou ? FOR_YOU_FILTERS : DEFAULT_FILTERS)
  }, [forYou])

  const sourceOptions = (sources ?? []).map((s) => s.source)

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] min-h-0 flex-col overflow-hidden px-2.5 pb-2.5 pt-2.5 sm:px-3 xl:h-dvh xl:px-3.5 xl:pt-3">
      <section className="workspace-surface mb-2 flex-shrink-0 overflow-visible px-3.5 py-2.5">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-[1.02rem] font-semibold tracking-[-0.03em] text-ink">{forYou ? 'For You' : 'Discover'}</h1>
              {forYou && <span className="tag bg-signal-50 text-signal-700">Profile-backed</span>}
            </div>
            <p className="mt-0.5 hidden text-[0.7rem] text-slate-400 md:block">
              {forYou
                ? 'Only current roles that clear your profile, experience, and work-authorization gates.'
                : 'Search official ATS boards and discovery sources from one evidence-first workspace.'}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {forYou && !missingProfile && allJobs.length > 0 && (() => {
              const batch = unanalyzed.slice(0, 10).map((j) => j.job_id)
              const left = unanalyzed.length
              const done = left === 0
              const label = deepTopN.progress
                ? `Analyzing ${deepTopN.progress.done}/${deepTopN.progress.total}…`
                : done ? 'All top matches analyzed'
                : analyzedCount === 0 ? `Deep-match top ${batch.length}`
                : `Deep-match next ${batch.length} · ${left} left`
              return (
                <button type="button"
                  onClick={() => activeProfileId && batch.length && deepTopN.mutate({ profileId: activeProfileId, jobIds: batch })}
                  disabled={deepTopN.isPending || !activeProfileId || done}
                  title={done ? 'Every loaded match has an AI second opinion' : `AI second opinion on the next ${batch.length} matches (up to ${batch.length} AI calls; already-analyzed jobs are free)`}
                  className="control-focus inline-flex h-8 items-center gap-1.5 rounded-lg border border-signal-200 bg-signal-50 px-2.5 text-xs font-semibold text-signal-700 hover:bg-signal-100 disabled:opacity-50">
                  <MagicWand size={14} weight="fill" />
                  <span className="hidden sm:inline">{label}</span>
                </button>
              )
            })()}
            {forYou && analyzedCount > 0 && (
              <button type="button" onClick={() => setAiRankOverride(!aiRanked)}
                title={aiRanked ? 'Revert to the normal match/newest order' : 'Re-order the feed by the AI verdicts'}
                className={`control-focus inline-flex h-8 items-center gap-1 rounded-lg border px-2 text-[0.7rem] font-medium ${aiRanked ? 'border-signal-300 bg-signal-600 text-white' : 'border-slate-200 bg-white text-slate-500 hover:bg-slate-50'}`}>
                {aiRanked ? <>Ranked by AI <span aria-hidden>✕</span></> : 'Rank by AI'}
              </button>
            )}
            {!missingProfile && <button type="button" onClick={onSaveSearch} disabled={createSaved.isPending}
              title="Save this query + filters; get a badge when new matches arrive"
              className="control-focus inline-flex h-8 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-600 hover:border-slate-300 hover:bg-slate-50 disabled:opacity-50">
              <BookmarkSimple size={14} weight={createSaved.isSuccess ? 'fill' : 'regular'} className={createSaved.isSuccess ? 'text-signal-500' : ''} />
              <span className="hidden sm:inline">{createSaved.isSuccess ? 'Saved' : 'Save search'}</span>
            </button>}
          </div>
        </div>
        {!missingProfile && <><div className="mt-2 flex items-center gap-2">
          <div className="min-w-0 flex-1"><SearchBar filters={filters} onFilterChange={handleFilterChange} /></div>
        </div>
        <div className="mt-2 border-t border-slate-100 pt-2">
          <FilterBar
            filters={filters}
            facets={data?.facets}
            sourceOptions={sourceOptions}
            hasActiveProfile={!!activeProfileId}
            recommendationLocked={forYou}
            onFilterChange={handleFilterChange}
            onClearFilters={handleClearFilters}
          />
          {forYou && (newCount > 0 || showOnlyNew) && (
            <button type="button" onClick={() => setShowOnlyNew((v) => !v)}
              title="Roles added since you last opened For You"
              className={`control-focus mt-2 inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.7rem] font-semibold ${showOnlyNew ? 'border-signal-300 bg-signal-600 text-white' : 'border-signal-200 bg-signal-50 text-signal-700 hover:bg-signal-100'}`}>
              <Sparkle size={12} weight="fill" />
              {showOnlyNew ? 'Showing new only — clear' : `${newCount} new since last visit`}
            </button>
          )}
        </div></>}
      </section>

      {/* Discover without a profile: explain what they're missing (dismissible). */}
      {!forYou && !activeProfileId && !noProfileTipDismissed && (
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-signal-100 bg-signal-50/70 px-3 py-2 text-xs text-signal-900">
          <span>
            You're browsing every indexed job. <b>Add a profile</b> to get fit scores, eligibility
            screening (visa / seniority / role type) and the curated For You feed.
          </span>
          <span className="flex items-center gap-1.5">
            <Link to="/profile" role="button" className="control-focus rounded-lg bg-signal-600 px-2.5 py-1 font-semibold text-white hover:bg-signal-700">
              Upload resume
            </Link>
            <button type="button" onClick={() => setNoProfileTipDismissed(true)} className="control-focus rounded-lg px-2 py-1 font-medium text-signal-700 hover:bg-signal-100">
              Not now
            </button>
          </span>
        </div>
      )}

      {/* Error banner */}
      {!missingProfile && isError && (
        <div className="mb-2 flex-shrink-0 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          <span className="font-medium">Error loading jobs:</span>{' '}
          {error?.message ?? 'Unknown error'}
        </div>
      )}

      {missingProfile ? (
        /* First-run onboarding: explain the whole loop in three steps. */
        <div className="workspace-surface flex min-h-0 flex-1 items-center justify-center overflow-y-auto px-6 py-8 text-center">
          <div className="max-w-2xl">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl border border-signal-100 bg-signal-50 text-signal-600">
              <UserCircle size={25} />
            </div>
            <h2 className="mt-4 text-lg font-semibold tracking-[-0.02em] text-slate-900">Get your personal job feed in three steps</h2>
            <div className="mt-5 grid gap-3 text-left sm:grid-cols-3">
              {[
                { n: '1', title: 'Upload your resume', body: 'JobScout builds a profile from it: target roles, skills, experience, and your work-authorization needs — all editable.' },
                { n: '2', title: 'We fetch & screen', body: '20 job sources are searched with your target roles; every posting is checked against visa, seniority, experience, and role-fit gates.' },
                { n: '3', title: 'Review your matches', body: 'For You shows only roles you can actually get. Discover keeps everything searchable when you want to browse wider.' },
              ].map((step) => (
                <div key={step.n} className="rounded-xl border border-slate-200 bg-white p-3.5">
                  <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-ink font-mono text-[0.7rem] font-bold text-white">{step.n}</span>
                  <p className="mt-2 text-sm font-semibold text-ink">{step.title}</p>
                  <p className="mt-1 text-xs leading-relaxed text-slate-500">{step.body}</p>
                </div>
              ))}
            </div>
            <Link to="/profile" role="button" className="control-focus mt-6 inline-flex rounded-lg bg-signal-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-signal-700">
              Upload your resume →
            </Link>
            <p className="mt-2 text-xs text-slate-400">Already made a profile? Pick it under “Matching profile” in the sidebar.</p>
          </div>
        </div>
      ) : <>{/* Two-pane body */}
      <div className="workspace-surface flex min-h-0 flex-1 overflow-hidden">
        {/* Left: scrollable job list */}
        <div className="flex min-w-0 w-full flex-shrink-0 flex-col overflow-hidden lg:w-[23.5rem] lg:border-r lg:border-slate-200">
          <JobList
            jobs={jobs}
            total={data?.total ?? 0}
            page={filters.page ?? 1}
            pageSize={filters.page_size ?? 20}
            isLoading={isLoading}
            isFetching={isFetching}
            selectedJobId={selectedJobId}
            onJobSelect={handleJobSelect}
            onPageChange={(page) => handleFilterChange({ page })}
            verdicts={data?.verdicts}
            activeProfileId={activeProfileId}
            lookbackWindow={data?.lookback_window}
            recommendationOnly={!!effectiveFilters.recommendation_only}
            recommendationRefreshing={!!data?.recommendation_refreshing}
          />
        </div>

        {/* Right: inline detail pane */}
        <div className="hidden min-w-0 flex-1 flex-col overflow-hidden bg-white lg:flex">
          {selectedJobId ? (
            // key: remount per job so deep-match / tailor mutation state never
            // leaks across jobs (was showing the last job's result for every job).
            <JobDetailPane key={selectedJobId} jobId={selectedJobId} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-center text-slate-400">
              <ClipboardText size={34} className="mb-3" />
              <p className="text-sm">
                {isLoading ? 'Loading jobs…' : 'Select a job to see the details'}
              </p>
            </div>
          )}
        </div>
      </div>

      {mobileDetailOpen && selectedJobId && (
        <div className="fixed inset-0 z-50 bg-ink/40 p-2 backdrop-blur-[2px] sm:p-4 lg:hidden" role="dialog" aria-modal="true" aria-label="Job details">
          <div className="h-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-2xl">
            <JobDetailPane key={selectedJobId} jobId={selectedJobId} onClose={() => setMobileDetailOpen(false)} />
          </div>
        </div>
      )}</>}
    </div>
  )
}

export default function App() {
  return (
    <ProfileProvider>
      <AppShell>
        <Routes>
          <Route path="/" element={<JobsPage key="discover" />} />
          <Route path="/jobs" element={<JobsPage key="discover" />} />
          <Route path="/for-you" element={<JobsPage key="foryou" forYou />} />
          <Route path="/my-jobs" element={<MyJobsPanel />} />
          <Route path="/saved" element={<SavedSearchesPanel />} />
          <Route path="/companies" element={<CompaniesPanel />} />
          <Route path="/profile" element={<ProfilePanel />} />
          <Route path="/admin" element={<AdminPage />} />
          {/* Back-compat redirects from the old 6-tab layout */}
          <Route path="/shortlist" element={<Navigate to="/my-jobs" replace />} />
          <Route path="/applied" element={<Navigate to="/my-jobs" replace />} />
          <Route path="/match" element={<Navigate to="/profile" replace />} />
          <Route path="/profiles" element={<Navigate to="/profile" replace />} />
        </Routes>
      </AppShell>
    </ProfileProvider>
  )
}
