import type { Job } from '../types'
import JobCard from './JobCard'

interface JobListProps {
  jobs: Job[]
  total: number
  page: number
  pageSize: number
  isLoading: boolean
  isFetching: boolean
  selectedJobId: string | null
  onJobSelect: (jobId: string) => void
  onPageChange: (page: number) => void
}

// ---------------------------------------------------------------------------
// Skeleton card for loading state
// ---------------------------------------------------------------------------

function SkeletonCard() {
  return (
    <div className="border-l-2 border-l-transparent px-4 py-3 animate-pulse">
      <div className="h-4 bg-slate-200 rounded w-3/4 mb-2" />
      <div className="h-3 bg-slate-100 rounded w-1/2 mb-2" />
      <div className="flex gap-1.5">
        <div className="h-5 bg-slate-100 rounded w-16" />
        <div className="h-5 bg-slate-100 rounded w-14" />
      </div>
      <div className="mt-2 h-3 bg-slate-100 rounded w-16" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Pagination controls
// ---------------------------------------------------------------------------

interface PaginationProps {
  page: number
  pageSize: number
  total: number
  isFetching: boolean
  onPageChange: (page: number) => void
}

function Pagination({ page, pageSize, total, isFetching, onPageChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const start = (page - 1) * pageSize + 1
  const end = Math.min(page * pageSize, total)

  if (totalPages <= 1) return null

  return (
    <div className="flex items-center justify-between pt-4 pb-2">
      <span className="text-sm text-slate-500">
        {total > 0 ? `${start}–${end} of ${total.toLocaleString()}` : ''}
      </span>
      <div className="flex items-center gap-2">
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1 || isFetching}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          Prev
        </button>

        {/* Page number pills */}
        <div className="flex items-center gap-1">
          {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
            let pageNum: number
            if (totalPages <= 7) {
              pageNum = i + 1
            } else if (page <= 4) {
              pageNum = i + 1
            } else if (page >= totalPages - 3) {
              pageNum = totalPages - 6 + i
            } else {
              pageNum = page - 3 + i
            }

            return (
              <button
                key={pageNum}
                onClick={() => onPageChange(pageNum)}
                disabled={isFetching}
                className={`w-8 h-8 rounded-lg text-sm font-medium transition ${
                  pageNum === page
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-600 hover:bg-slate-100 disabled:opacity-50'
                }`}
              >
                {pageNum}
              </button>
            )
          })}
        </div>

        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages || isFetching}
          className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition"
        >
          Next
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main list
// ---------------------------------------------------------------------------

export default function JobList({
  jobs,
  total,
  page,
  pageSize,
  isLoading,
  isFetching,
  selectedJobId,
  onJobSelect,
  onPageChange,
}: JobListProps) {
  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
        <h2 className="text-sm font-medium text-slate-600">
          {isLoading ? (
            <span className="inline-block h-4 w-32 bg-slate-200 rounded animate-pulse" />
          ) : (
            <>
              <span className="font-semibold text-slate-900">{total.toLocaleString()}</span>{' '}
              {total === 1 ? 'job' : 'jobs'} found
            </>
          )}
        </h2>
        {isFetching && !isLoading && (
          <span className="flex items-center gap-1.5 text-xs text-blue-600">
            <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Updating…
          </span>
        )}
      </div>

      {/* Loading skeletons */}
      {isLoading && (
        <div className="divide-y divide-slate-100">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      )}

      {/* Empty state */}
      {!isLoading && jobs.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <div className="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center mb-4">
            <svg
              className="w-8 h-8 text-slate-400"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M9.172 16.172a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
          </div>
          <h3 className="text-slate-700 font-semibold text-base mb-1">No jobs found</h3>
          <p className="text-slate-500 text-sm max-w-xs">
            Try broadening your search or clearing some filters.
          </p>
        </div>
      )}

      {/* Job cards */}
      {!isLoading && jobs.length > 0 && (
        <div className="divide-y divide-slate-100">
          {jobs.map((job) => (
            <JobCard
              key={job.job_id}
              job={job}
              isSelected={selectedJobId === job.job_id}
              onSelect={onJobSelect}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {!isLoading && total > 0 && (
        <div className="px-4">
          <Pagination
            page={page}
            pageSize={pageSize}
            total={total}
            isFetching={isFetching}
            onPageChange={onPageChange}
          />
        </div>
      )}
    </div>
  )
}
