import { useState, useCallback, useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import type { JobFilters } from './types'
import { useJobs, useSourcesStatus, useCreateSavedSearch } from './api/client'
import { ProfileProvider, useActiveProfile } from './ProfileContext'
import FilterBar from './components/FilterBar'
import SearchBar from './components/SearchBar'
import JobList from './components/JobList'
import JobDetailPane from './components/JobDetailPane'
import CompaniesPanel from './components/CompaniesPanel'
import TopNav from './components/TopNav'
import MyJobsPanel from './components/MyJobsPanel'
import ProfilePanel from './components/ProfilePanel'
import SavedSearchesPanel from './components/SavedSearchesPanel'

const APPLY_KEY = 'jobscout.applyFilters'

const DEFAULT_FILTERS: JobFilters = {
  page: 1,
  page_size: 20,
  sort: 'posted_desc',
  // Semantic-leaning search (mostly meaning-based, slight keyword weighting so
  // exact title matches still surface). No user-facing control — see SearchBar.
  alpha: 0.75,
  // Default ON for visa-needing users: hide only the jobs that explicitly refuse
  // sponsorship / require citizenship. Keeps the ~96% that say nothing, so the
  // list is never empty. The user can turn this off in the Sponsorship filter.
  exclude_no_sponsorship: true,
  security_clearance: ['none'],
}

function JobsPage() {
  const [filters, setFilters] = useState<JobFilters>(DEFAULT_FILTERS)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)
  const { activeProfileId } = useActiveProfile()
  const createSaved = useCreateSavedSearch()

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

  // Attach the active profile so the list carries verdicts + cap-exempt sort +
  // applied/hidden exclusions. Re-derived whenever the selection changes.
  const effectiveFilters: JobFilters = {
    ...filters,
    profile_id: activeProfileId ?? undefined,
  }
  const { data, isLoading, isFetching, isError, error } = useJobs(effectiveFilters)
  const { data: sources } = useSourcesStatus()

  const jobs = data?.jobs ?? []

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
  }, [])

  const handleClearFilters = useCallback(() => {
    setFilters(DEFAULT_FILTERS)
  }, [])

  const sourceOptions = (sources ?? []).map((s) => s.source)

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-50">
      <TopNav />
      <div className="flex-shrink-0 border-b border-slate-200 bg-white">
        <div className="px-6 py-3">
          <div className="flex max-w-3xl items-center gap-2">
            <div className="flex-1"><SearchBar filters={filters} onFilterChange={handleFilterChange} /></div>
            <button type="button" onClick={onSaveSearch} disabled={createSaved.isPending}
              title="Save this query + filters; get a badge when new matches arrive"
              className="flex-shrink-0 rounded-lg border border-amber-300 px-3 py-2 text-sm font-medium text-amber-700 hover:bg-amber-50 disabled:opacity-50">
              {createSaved.isSuccess ? '★ Saved' : '★ Save search'}
            </button>
          </div>
        </div>
        {/* Filter bar */}
        <div className="border-t border-slate-100">
          <FilterBar
            filters={filters}
            facets={data?.facets}
            sourceOptions={sourceOptions}
            onFilterChange={handleFilterChange}
            onClearFilters={handleClearFilters}
          />
        </div>
      </div>

      {/* Error banner */}
      {isError && (
        <div className="mx-6 mt-4 flex-shrink-0 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <span className="font-medium">Error loading jobs:</span>{' '}
          {error?.message ?? 'Unknown error'}
        </div>
      )}

      {/* Two-pane body */}
      <div className="mx-auto flex w-full max-w-7xl flex-1 gap-4 overflow-hidden px-6 py-4">
        {/* Left: scrollable job list */}
        <div className="flex w-full max-w-md flex-shrink-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="scrollbar-thin flex-1 overflow-y-auto">
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
            />
          </div>
        </div>

        {/* Right: inline detail pane */}
        <div className="flex flex-1 flex-col overflow-hidden rounded-xl border border-slate-200 bg-white">
          {selectedJobId ? (
            <JobDetailPane jobId={selectedJobId} />
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-center text-slate-400">
              <svg className="mb-3 h-12 w-12" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
              </svg>
              <p className="text-sm">
                {isLoading ? 'Loading jobs…' : 'Select a job to see the details'}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <ProfileProvider>
      <Routes>
        <Route path="/" element={<JobsPage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/my-jobs" element={<MyJobsPanel />} />
        <Route path="/saved" element={<SavedSearchesPanel />} />
        <Route path="/companies" element={<CompaniesPanel />} />
        <Route path="/profile" element={<ProfilePanel />} />
        {/* Back-compat redirects from the old 6-tab layout */}
        <Route path="/shortlist" element={<Navigate to="/my-jobs" replace />} />
        <Route path="/applied" element={<Navigate to="/my-jobs" replace />} />
        <Route path="/match" element={<Navigate to="/profile" replace />} />
        <Route path="/profiles" element={<Navigate to="/profile" replace />} />
      </Routes>
    </ProfileProvider>
  )
}
