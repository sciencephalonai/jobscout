import { useState, useEffect, useRef, useCallback } from 'react'
import { MagnifyingGlass, X } from '@phosphor-icons/react'
import type { JobFilters } from '../types'
import { useActiveProfile } from '../ProfileContext'

interface SearchBarProps {
  filters: JobFilters
  onFilterChange: (updates: Partial<JobFilters>) => void
}

const SORT_OPTIONS = [
  { value: 'match', label: 'Best Match' },
  { value: 'relevance', label: 'Most Relevant' },
  { value: 'posted_desc', label: 'Newest First' },
  { value: 'salary_desc', label: 'Highest Salary' },
]

export default function SearchBar({ filters, onFilterChange }: SearchBarProps) {
  const { activeProfileId } = useActiveProfile()
  const [inputValue, setInputValue] = useState(filters.q ?? '')
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Sync input value if external filter changes (e.g., clear filters)
  useEffect(() => {
    setInputValue(filters.q ?? '')
  }, [filters.q])

  const handleSearchChange = useCallback(
    (value: string) => {
      setInputValue(value)
      if (debounceRef.current) clearTimeout(debounceRef.current)
      debounceRef.current = setTimeout(() => {
        onFilterChange({ q: value })
      }, 500)
    },
    [onFilterChange],
  )

  const handleSearchSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault()
      if (debounceRef.current) clearTimeout(debounceRef.current)
      onFilterChange({ q: inputValue })
    },
    [inputValue, onFilterChange],
  )

  const handleSortChange = useCallback(
    (e: React.ChangeEvent<HTMLSelectElement>) => {
      onFilterChange({ sort: e.target.value })
    },
    [onFilterChange],
  )

  return (
    <form onSubmit={handleSearchSubmit} className="grid grid-cols-[minmax(0,1fr)_8.5rem] items-center gap-1.5 sm:grid-cols-[minmax(0,1fr)_9.5rem]">
      {/* Search input */}
      <div className="relative flex-1">
        <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none">
          <MagnifyingGlass size={15} className="text-slate-400" />
        </div>
        <label htmlFor="job-search" className="sr-only">Search job titles, skills, or companies</label>
        <input
          id="job-search"
          type="text"
          value={inputValue}
          onChange={(e) => handleSearchChange(e.target.value)}
          placeholder="Search job titles, skills, companies…"
          className="control-focus h-8 w-full rounded-lg border border-slate-200 bg-[#f7f8f6] py-1 pl-8 pr-8 text-xs text-ink placeholder-slate-400 outline-none focus:border-signal-300 focus:bg-white"
        />
        {inputValue && (
          <button
            type="button"
            onClick={() => {
              setInputValue('')
              onFilterChange({ q: '' })
            }}
            className="control-focus absolute inset-y-0 right-2 flex items-center rounded-md px-1 text-slate-400 hover:text-ink"
            aria-label="Clear search"
          >
            <X size={14} />
          </button>
        )}
      </div>

      {/* Sort dropdown */}
      <select
        aria-label="Sort jobs"
        value={filters.sort ?? 'relevance'}
        onChange={handleSortChange}
        className="control-focus h-8 min-w-0 cursor-pointer rounded-lg border border-slate-200 bg-[#f7f8f6] px-2 text-[0.7rem] font-medium text-slate-700 outline-none focus:border-signal-300 focus:bg-white"
      >
        {SORT_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      {filters.sort === 'match' && !activeProfileId && (
        <span className="col-span-2 text-[0.68rem] text-amber-700">
          Pick a profile (top bar) to rank by match
        </span>
      )}
    </form>
  )
}
