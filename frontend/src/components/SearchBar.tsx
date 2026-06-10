import { useState, useEffect, useRef, useCallback } from 'react'
import type { JobFilters } from '../types'

interface SearchBarProps {
  filters: JobFilters
  onFilterChange: (updates: Partial<JobFilters>) => void
}

const SORT_OPTIONS = [
  { value: 'relevance', label: 'Most Relevant' },
  { value: 'posted_desc', label: 'Newest First' },
  { value: 'salary_desc', label: 'Highest Salary' },
]

export default function SearchBar({ filters, onFilterChange }: SearchBarProps) {
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
    <form onSubmit={handleSearchSubmit} className="flex items-center gap-3">
      {/* Search input */}
      <div className="relative flex-1">
        <div className="absolute inset-y-0 left-3 flex items-center pointer-events-none">
          <svg
            className="w-4 h-4 text-slate-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
        </div>
        <input
          type="text"
          value={inputValue}
          onChange={(e) => handleSearchChange(e.target.value)}
          placeholder="Search job titles, skills, companies…"
          className="w-full pl-10 pr-4 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition"
        />
        {inputValue && (
          <button
            type="button"
            onClick={() => {
              setInputValue('')
              onFilterChange({ q: '' })
            }}
            className="absolute inset-y-0 right-3 flex items-center text-slate-400 hover:text-slate-600"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>

      {/* Sort dropdown */}
      <select
        value={filters.sort ?? 'relevance'}
        onChange={handleSortChange}
        className="px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-lg text-sm text-slate-700 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent cursor-pointer"
      >
        {SORT_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </form>
  )
}
