// Scrollable job list with pagination, lookback-window tag, and refresh indicator for the recommendation feed.
import type { Job, Verdict } from '../types'
import { CaretLeft, CaretRight, CircleNotch, MagnifyingGlass } from '@phosphor-icons/react'
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
  verdicts?: Record<string, Verdict>
  activeProfileId?: string | null
  lookbackWindow?: string | null
  recommendationOnly?: boolean
  recommendationRefreshing?: boolean
}

// ---------------------------------------------------------------------------
// Skeleton card for loading state
// ---------------------------------------------------------------------------

function SkeletonCard() {
  return (
    <div className="animate-pulse border-b border-slate-100 px-3.5 py-3">
      <div className="mb-2 h-4 w-3/4 rounded bg-slate-200" />
      <div className="mb-2 h-3 w-1/2 rounded bg-slate-100" />
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
    <div className="flex min-w-0 items-center justify-between gap-2 py-2">
      <span className="shrink-0 whitespace-nowrap font-mono text-[0.64rem] tabular-nums text-slate-400">
        {total > 0 ? `${start}–${end} / ${total.toLocaleString()}` : ''}
      </span>
      <div className="flex min-w-0 items-center gap-1.5">
        <button
          onClick={() => onPageChange(page - 1)}
          disabled={page <= 1 || isFetching}
          className="control-focus inline-flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Previous page"
          title="Previous page"
        >
          <CaretLeft size={14} />
        </button>
        <span className="min-w-11 text-center font-mono text-[0.64rem] font-semibold tabular-nums text-slate-500">{page} / {totalPages}</span>

        <button
          onClick={() => onPageChange(page + 1)}
          disabled={page >= totalPages || isFetching}
          className="control-focus inline-flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="Next page"
          title="Next page"
        >
          <CaretRight size={14} />
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
  verdicts,
  activeProfileId,
  lookbackWindow,
  recommendationOnly = false,
  recommendationRefreshing = false,
}: JobListProps) {
  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
      {/* Header */}
      <div className="flex h-11 flex-none items-center justify-between border-b border-slate-200 px-3.5">
        <h2 className="flex min-w-0 items-center gap-2 text-[0.7rem] font-medium text-slate-500">
          {isLoading ? (
            <span className="inline-block h-4 w-32 bg-slate-200 rounded animate-pulse" />
          ) : (
            <>
              <span className="font-mono font-semibold tabular-nums text-ink">{total.toLocaleString()}</span>{' '}
              {total === 1 ? 'role' : 'roles'} found
            </>
          )}
          {recommendationOnly && lookbackWindow && (
            <span className="tag hidden bg-emerald-50 text-emerald-700 sm:inline-flex">
              {lookbackWindow} window
            </span>
          )}
          {recommendationRefreshing && (
            <span className="tag hidden bg-signal-50 text-signal-700 sm:inline-flex">
              Finding newer matches…
            </span>
          )}
        </h2>
        {isFetching && !isLoading && (
          <span className="flex items-center gap-1.5 text-[0.68rem] text-signal-600">
            <CircleNotch size={13} className="animate-spin" />
            Updating
          </span>
        )}
      </div>

      <div className="job-list-scroll min-h-0 flex-1 overflow-x-hidden overflow-y-auto [scrollbar-gutter:stable]">
        {/* Loading skeletons */}
        {isLoading && (
          <div className="pb-1">
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </div>
        )}

        {/* Empty state */}
        {!isLoading && jobs.length === 0 && (
          <div className="flex min-h-64 flex-col items-center justify-center px-6 py-12 text-center">
            <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-slate-400">
              <MagnifyingGlass size={20} />
            </div>
            <h3 className="text-slate-700 font-semibold text-base mb-1">
              {recommendationOnly ? 'No profile matches yet' : 'No jobs found'}
            </h3>
            <p className="text-slate-500 text-sm max-w-xs">
              {recommendationOnly
                ? recommendationRefreshing
                  ? 'Searching enabled sources for roles that pass your complete profile criteria.'
                  : 'No qualified roles are indexed yet. JobScout will retry the profile search after its refill cooldown.'
                : total === 0 && !isFetching
                  ? 'Nothing indexed for this query yet — hit “Get latest jobs” in the sidebar to search all 20 sources, or clear some filters.'
                  : 'Try broadening your search or clearing some filters.'}
            </p>
          </div>
        )}

        {/* Job cards */}
        {!isLoading && jobs.length > 0 && (
          <div className="pb-1">
            {jobs.map((job) => (
              <JobCard
                key={job.job_id}
                job={job}
                isSelected={selectedJobId === job.job_id}
                onSelect={onJobSelect}
                verdict={verdicts?.[job.job_id]}
                activeProfileId={activeProfileId ?? null}
              />
            ))}
          </div>
        )}
      </div>

      {/* Pagination */}
      {!isLoading && total > 0 && (
        <div className="flex-none border-t border-slate-200 bg-[#fbfcfa] px-3.5">
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
