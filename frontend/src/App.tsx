import { useState, useCallback, useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import type { JobFilters } from './types'
import { useJobs, useSourcesStatus } from './api/client'
import FilterBar from './components/FilterBar'
import SearchBar from './components/SearchBar'
import JobList from './components/JobList'
import JobDetailPane from './components/JobDetailPane'

const DEFAULT_FILTERS: JobFilters = {
  page: 1,
  page_size: 20,
  sort: 'relevance',
  // Semantic-leaning search (mostly meaning-based, slight keyword weighting so
  // exact title matches still surface). No user-facing control — see SearchBar.
  alpha: 0.75,
  date_range: '1m',
}

function JobsPage() {
  const [filters, setFilters] = useState<JobFilters>(DEFAULT_FILTERS)
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null)

  const { data, isLoading, isFetching, isError, error } = useJobs(filters)
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
      {/* Top bar: logo + search */}
      <header className="flex-shrink-0 border-b border-slate-200 bg-white">
        <div className="flex items-center gap-6 px-6 py-3">
          <div className="flex flex-shrink-0 items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-600">
              <svg className="h-4 w-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <span className="text-lg font-semibold tracking-tight text-slate-900">JobScout</span>
          </div>
          <div className="max-w-2xl flex-1">
            <SearchBar filters={filters} onFilterChange={handleFilterChange} />
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
      </header>

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
    <Routes>
      <Route path="/" element={<JobsPage />} />
      <Route path="/jobs" element={<JobsPage />} />
    </Routes>
  )
}
