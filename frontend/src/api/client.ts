import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { Job, JobFilters, JobsResponse, SourceStatus } from '../types'

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

export function buildQueryString(filters: JobFilters): string {
  const params = new URLSearchParams()

  if (filters.q && filters.q.trim() !== '') {
    params.set('q', filters.q.trim())
  }
  // Multi-select fields: append one repeated query param per selected value
  // (e.g. ?source=adzuna&source=greenhouse). The backend OR's values within
  // each filter. Empty/undefined arrays are skipped entirely.
  const multiFields: (keyof JobFilters)[] = ['remote', 'visa', 'source', 'company_size', 'exp', 'employment_type', 'category']
  for (const field of multiFields) {
    const values = filters[field] as string[] | undefined
    if (Array.isArray(values)) {
      for (const value of values) {
        if (value !== '') params.append(field, value)
      }
    }
  }
  if (filters.date_range && filters.date_range !== '') {
    params.set('date_range', filters.date_range)
  }
  if (filters.alpha !== undefined && filters.alpha !== null) {
    params.set('alpha', String(filters.alpha))
  }
  if (filters.sort && filters.sort !== '') {
    params.set('sort', filters.sort)
  }
  if (filters.page !== undefined && filters.page !== null) {
    params.set('page', String(filters.page))
  }
  if (filters.page_size !== undefined && filters.page_size !== null) {
    params.set('page_size', String(filters.page_size))
  }

  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API error ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

/**
 * Fetch paginated, filtered job listings.
 * The query key includes the full filters object so any change triggers a refetch.
 */
export function useJobs(filters: JobFilters) {
  const qs = buildQueryString(filters)
  return useQuery<JobsResponse, Error>({
    queryKey: ['jobs', filters],
    queryFn: () => apiFetch<JobsResponse>(`/api/jobs${qs}`),
    placeholderData: (prev) => prev,
  })
}

/**
 * Fetch a single job by ID.
 */
export function useJob(jobId: string | null) {
  return useQuery<Job, Error>({
    queryKey: ['job', jobId],
    queryFn: () => apiFetch<Job>(`/api/jobs/${jobId}`),
    enabled: jobId !== null && jobId !== '',
  })
}

/**
 * Trigger an ingestion run for a given set of keywords.
 * POST /api/search/run  { keywords: string[] }  (backend iterates the list)
 */
export function useTriggerIngestion() {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, { keywords: string[] }>({
    mutationFn: (body) =>
      apiFetch('/api/search/run', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      // Invalidate jobs list so new results appear after ingestion
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/**
 * Fetch the status of all configured sources.
 */
export function useSourcesStatus() {
  return useQuery<SourceStatus[], Error>({
    queryKey: ['sources', 'status'],
    queryFn: () => apiFetch<SourceStatus[]>('/api/sources/status'),
    refetchInterval: 15_000, // poll every 15s while window is focused
  })
}
