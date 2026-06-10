import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type {
  Company, CompanyFilters, DiscoveryResult, Job, JobFilters, JobsResponse, JobState,
  MatchResponse, PipelineResponse, Profile, SavedSearch, SchedulerStatus, SourceStatus, Stats,
} from '../types'

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
  const multiFields: (keyof JobFilters)[] = [
    'remote', 'visa', 'source', 'company_size', 'exp', 'cap_exempt',
    'employer_type', 'security_clearance',
  ]
  for (const field of multiFields) {
    const values = filters[field] as string[] | undefined
    if (Array.isArray(values)) {
      for (const value of values) {
        if (value !== '') params.append(field, value)
      }
    }
  }
  // Boolean sponsorship toggles — only send when true.
  if (filters.exclude_no_sponsorship) params.set('exclude_no_sponsorship', 'true')
  if (filters.h1b_sponsor) params.set('h1b_sponsor', 'true')
  if (filters.everify) params.set('everify', 'true')
  if (filters.exclude_recruiter) params.set('exclude_recruiter', 'true')
  if (filters.profile_id) params.set('profile_id', filters.profile_id)
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
 * Trigger an ingestion run ("Get latest jobs").
 * POST /api/search/run  { keywords, location?, results_wanted? }
 */
export function useTriggerIngestion() {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, { keywords: string[]; location?: string; results_wanted?: number }>({
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
 * Drop a resume file → saved profile + matched jobs (multipart upload).
 */
export function useMatchResume() {
  const queryClient = useQueryClient()
  return useMutation<MatchResponse, Error, { file: File; limit?: number }>({
    mutationFn: async ({ file, limit = 10 }) => {
      const form = new FormData()
      form.append('file', file)
      form.append('limit', String(limit))
      const res = await fetch('/api/match/upload', { method: 'POST', body: form })
      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText)
        throw new Error(`Upload failed (${res.status}): ${text}`)
      }
      return res.json() as Promise<MatchResponse>
    },
    // Uploading a resume creates a saved profile — refresh the profile list +
    // the active-profile dropdown so the new profile appears without a reload.
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] })
    },
  })
}

/**
 * Delete a saved profile by id.
 */
export function useDeleteProfile() {
  const queryClient = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (profileId) =>
      apiFetch(`/api/profiles/${profileId}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['jobs'] }),
  })
}

/**
 * Fetch the company registry, filtered.
 */
export function useCompanies(filters: CompanyFilters) {
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== '') params.set(k, String(v))
  }
  const qs = params.toString()
  return useQuery<Company[], Error>({
    queryKey: ['companies', filters],
    queryFn: () => apiFetch<Company[]>(`/api/companies${qs ? `?${qs}` : ''}`),
  })
}

/**
 * Discover new companies (probe Greenhouse/Lever/Ashby for companies seen in
 * the job index but not yet in the watchlist). Returns verified candidates.
 * Can take 15-30s — run on demand only.
 */
export function useDiscoverCompanies() {
  return useMutation<DiscoveryResult[], Error, void>({
    mutationFn: () =>
      apiFetch<DiscoveryResult[]>('/api/companies/discover', {
        method: 'POST', body: '{}',
      }),
  })
}

/**
 * Trigger an incremental refresh of the enabled watchlist.
 */
export function useRefreshCompanies() {
  const queryClient = useQueryClient()
  return useMutation<{ status: string; companies: number; budget: number }, Error, { keywords?: string[]; budget?: number }>({
    mutationFn: (body) =>
      apiFetch('/api/companies/refresh', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['companies'] })
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

export function useStats() {
  return useQuery<Stats, Error>({
    queryKey: ['stats'],
    queryFn: () => apiFetch<Stats>('/api/stats'),
    refetchInterval: 15_000, // poll so the embed-quota banner appears/clears live
  })
}

// ---------------------------------------------------------------------------
// Profiles, job-state, shortlist/applied, ingestion, scheduler
// ---------------------------------------------------------------------------

/** List all saved profiles (for the active-profile selector + Profiles tab). */
export function useProfiles() {
  return useQuery<Profile[], Error>({
    queryKey: ['profiles'],
    queryFn: () => apiFetch<Profile[]>('/api/profiles'),
  })
}

/** Delete a saved profile by id. */
export function useDeleteProfileById() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (id) => apiFetch(`/api/profiles/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['profiles'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/** Mark a job applied/saved/seen/hidden or a pipeline stage (+ optional note). */
export function useSetJobState() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, { profileId: string; jobId: string; status: JobState; note?: string }>({
    mutationFn: ({ profileId, jobId, status, note }) =>
      apiFetch(`/api/profiles/${profileId}/job-state`, {
        method: 'POST',
        body: JSON.stringify({ job_id: jobId, status, note }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.invalidateQueries({ queryKey: ['jobs-by-state'] })
      qc.invalidateQueries({ queryKey: ['pipeline'] })
    },
  })
}

/** The application pipeline (applied→oa→interview→offer→rejected) for a profile. */
export function usePipeline(profileId: string | null) {
  return useQuery<PipelineResponse, Error>({
    queryKey: ['pipeline', profileId],
    queryFn: () => apiFetch<PipelineResponse>(`/api/profiles/${profileId}/pipeline`),
    enabled: !!profileId,
  })
}

/** Jobs a profile marked with a given state (Shortlist = saved, Applied = applied). */
export function useJobsByState(profileId: string | null, status: JobState) {
  return useQuery<JobsResponse, Error>({
    queryKey: ['jobs-by-state', profileId, status],
    queryFn: () =>
      apiFetch<JobsResponse>(`/api/jobs/by-state?profile_id=${profileId}&status=${status}`),
    enabled: !!profileId,
  })
}

/** Read the daily auto-refresh scheduler status. */
export function useScheduler() {
  return useQuery<SchedulerStatus, Error>({
    queryKey: ['scheduler'],
    queryFn: () => apiFetch<SchedulerStatus>('/api/scheduler'),
  })
}

/** Enable/disable the daily scheduler. */
export function useSetScheduler() {
  const qc = useQueryClient()
  return useMutation<SchedulerStatus, Error, boolean>({
    mutationFn: (enabled) =>
      apiFetch<SchedulerStatus>('/api/scheduler', {
        method: 'POST',
        body: JSON.stringify({ enabled }),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['scheduler'] }),
  })
}

/** Read runtime source overrides (e.g. the high-risk JobSpy scraper). */
export function useSourceOverrides() {
  return useQuery<Record<string, boolean>, Error>({
    queryKey: ['source-overrides'],
    queryFn: () => apiFetch<Record<string, boolean>>('/api/sources/overrides'),
  })
}

/** Toggle a high-risk source on/off at runtime (e.g. {jobspy: true}). */
export function useSetSourceOverride() {
  const qc = useQueryClient()
  return useMutation<Record<string, boolean>, Error, Record<string, boolean>>({
    mutationFn: (body) =>
      apiFetch<Record<string, boolean>>('/api/sources/overrides', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['source-overrides'] }),
  })
}

// ---------------------------------------------------------------------------
// Saved searches ("new since last visit" alerts)
// ---------------------------------------------------------------------------

/** List saved searches, each with a live new_count. Polls so the bell stays fresh. */
export function useSavedSearches() {
  return useQuery<SavedSearch[], Error>({
    queryKey: ['saved-searches'],
    queryFn: () => apiFetch<SavedSearch[]>('/api/saved-searches'),
    refetchInterval: 60_000,
  })
}

/** Save the current query+filters under a label. */
export function useCreateSavedSearch() {
  const qc = useQueryClient()
  return useMutation<SavedSearch, Error, { label: string; filters: JobFilters; profile_id?: string | null }>({
    mutationFn: (body) =>
      apiFetch<SavedSearch>('/api/saved-searches', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['saved-searches'] }),
  })
}

/** Mark a saved search seen (resets its new_count). */
export function useMarkSavedSeen() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (id) => apiFetch(`/api/saved-searches/${id}/seen`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['saved-searches'] }),
  })
}

/** Delete a saved search. */
export function useDeleteSavedSearch() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (id) => apiFetch(`/api/saved-searches/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['saved-searches'] }),
  })
}
