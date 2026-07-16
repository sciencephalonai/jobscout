import { useEffect, useState } from 'react'
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query'
import type {
  Company, CompanyFilters, DeepMatch, DiscoveryResult, Job, JobFilters, JobsResponse, JobState,
  MatchResponse, PipelineResponse, Profile, SavedSearch, SchedulerStatus, SettingsResponse,
  SourceStatus, Stats,
  TailoredResumeResponse, PolishPair,
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
    'employer_type', 'security_clearance', 'category', 'employment_type',
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
  if (filters.exclude_ghost) params.set('exclude_ghost', 'true')
  if (filters.true_entry_only) params.set('true_entry_only', 'true')
  if (filters.new_grad_only) params.set('new_grad_only', 'true')
  if (filters.direct_sources_only) params.set('direct_sources_only', 'true')
  if (filters.recommendation_only) params.set('recommendation_only', 'true')
  if (filters.apply_only) params.set('apply_only', 'true')
  if (filters.target_min !== undefined) params.set('target_min', String(filters.target_min))
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
interface JobsQueryOptions {
  enabled?: boolean
  keepPreviousData?: boolean
  refetchInterval?: number | false
}

export function useJobs(
  filters: JobFilters,
  { enabled = true, keepPreviousData = true, refetchInterval = false }: JobsQueryOptions = {},
) {
  const qs = buildQueryString(filters)
  return useQuery<JobsResponse, Error>({
    queryKey: ['jobs', filters],
    queryFn: () => apiFetch<JobsResponse>(`/api/jobs${qs}`),
    enabled,
    placeholderData: keepPreviousData ? (prev) => prev : undefined,
    refetchInterval,
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
  return useMutation<unknown, Error, { keywords: string[]; location?: string; results_wanted?: number; profile_id?: string }>({
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['profiles'] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    },
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

/** Read the current backend wiring (storage mode, active backends, key presence). */
export function useSettings() {
  return useQuery({
    queryKey: ['settings'],
    queryFn: () => apiFetch<SettingsResponse>('/api/settings'),
  })
}

/** Update storage_mode / API keys, then reconnect the vector store. */
export function useUpdateSettings() {
  const queryClient = useQueryClient()
  return useMutation<SettingsResponse, Error, Record<string, string>>({
    mutationFn: (body) =>
      apiFetch('/api/settings', { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })
}

/**
 * LLM "deep match" — apply/borderline/skip verdict for one job vs. a profile.
 * POST /api/match/deep/{jobId}  { profile_id }
 *
 * Results are shared via the `['deep', jobId, profileId]` query cache so the
 * detail pane, the job cards, and the "Deep-match top N" batch all read one
 * source of truth. `deepMatchOnce` is the raw call the batch reuses.
 */
const deepKey = (jobId: string, profileId: string) => ['deep', jobId, profileId] as const

function deepMatchOnce(jobId: string, profileId: string): Promise<DeepMatch> {
  return apiFetch<DeepMatch>(`/api/match/deep/${jobId}`, {
    method: 'POST',
    body: JSON.stringify({ profile_id: profileId }),
  })
}

export function useDeepMatch() {
  const qc = useQueryClient()
  return useMutation<DeepMatch, Error, { jobId: string; profileId: string }>({
    mutationFn: ({ jobId, profileId }) => deepMatchOnce(jobId, profileId),
    onSuccess: (data, { jobId, profileId }) => {
      qc.setQueryData(deepKey(jobId, profileId), data)
    },
  })
}

/** Read a previously computed deep-match result from cache (never fetches). */
export function useCachedDeepMatch(jobId: string, profileId: string | null | undefined) {
  return useQuery<DeepMatch, Error>({
    queryKey: deepKey(jobId, profileId ?? ''),
    queryFn: () => Promise.reject(new Error('cache-only')),  // never runs (enabled:false)
    enabled: false,           // populated by useDeepMatch / useDeepMatchTopN only
    staleTime: Infinity,
  }).data
}

/**
 * Reactive map of cached deep-match results for a set of jobs (never fetches).
 * Single source of truth for the card badges, the "next 10" batch target, and
 * the AI re-rank — all update together as the batch/single writes land.
 */
export function useDeepResults(
  jobIds: string[], profileId: string | null | undefined,
): Record<string, DeepMatch> {
  const pid = profileId ?? ''
  const results = useQueries({
    queries: jobIds.map((id) => ({
      queryKey: deepKey(id, pid),
      queryFn: () => Promise.reject(new Error('cache-only')),
      enabled: false,
      staleTime: Infinity,
    })),
  })
  const map: Record<string, DeepMatch> = {}
  results.forEach((r, i) => {
    const d = (r as { data?: DeepMatch }).data
    if (d) map[jobIds[i]] = d
  })
  return map
}

/**
 * On load, fetch already-computed deep-match results for the visible jobs and
 * seed the shared cache — so badges/detail reappear after a reload or restart
 * with NO LLM spend. The backend returns only results whose fingerprint still
 * matches the current profile+resume, so nothing stale is shown after an edit.
 */
export function useHydrateDeepResults(jobIds: string[], profileId: string | null | undefined): void {
  const qc = useQueryClient()
  const idsKey = jobIds.join(',')
  useEffect(() => {
    if (!profileId || jobIds.length === 0) return
    let cancelled = false
    apiFetch<{ results: Record<string, DeepMatch> }>(`/api/profiles/${profileId}/deep-results`, {
      method: 'POST', body: JSON.stringify({ job_ids: jobIds }),
    })
      .then((d) => {
        if (cancelled) return
        for (const [jid, res] of Object.entries(d.results)) {
          qc.setQueryData(deepKey(jid, profileId), res)
        }
      })
      .catch(() => { /* best-effort hydrate */ })
    return () => { cancelled = true }
    // idsKey captures the job set; profileId change re-hydrates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, profileId])
}

/**
 * Deep-match the top N jobs in one click. Runs the existing single endpoint with
 * a small concurrency pool (server-side results are cached by job+profile, so
 * re-runs are ~free) and writes each result into the shared cache as it lands,
 * so cards light up progressively. Reports `{done, total}` while running.
 */
export function useDeepMatchTopN() {
  const qc = useQueryClient()
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null)
  const mutation = useMutation<void, Error, { profileId: string; jobIds: string[] }>({
    mutationFn: async ({ profileId, jobIds }) => {
      const ids = jobIds.slice(0, 10)   // bounded
      let done = 0
      setProgress({ done: 0, total: ids.length })
      const CONCURRENCY = 3
      const queue = [...ids]
      const worker = async () => {
        for (let id = queue.shift(); id !== undefined; id = queue.shift()) {
          try {
            const result = await deepMatchOnce(id, profileId)
            qc.setQueryData(deepKey(id, profileId), result)
          } catch { /* one job failing must not abort the batch */ }
          done += 1
          setProgress({ done, total: ids.length })
        }
      }
      await Promise.all(Array.from({ length: Math.min(CONCURRENCY, ids.length) }, worker))
    },
    onSettled: () => setProgress(null),
  })
  return { ...mutation, progress }
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

/** Save inline edits to the full, local canonical profile. */
export function useUpdateProfile() {
  const qc = useQueryClient()
  return useMutation<Profile, Error, Profile>({
    mutationFn: (profile) =>
      apiFetch<Profile>(`/api/profiles/${profile.id}`, {
        method: 'PUT', body: JSON.stringify(profile),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['profiles'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.invalidateQueries({ queryKey: ['pipeline'] })
      qc.removeQueries({ queryKey: ['deep'] })  // resume/prefs may have changed → old scores are stale
    },
  })
}

/** Re-extract skills, targets and experience from a profile's edited resume text. */
export function useReparseProfile() {
  const qc = useQueryClient()
  return useMutation<Profile, Error, string>({
    mutationFn: (profileId) =>
      apiFetch<Profile>(`/api/profiles/${profileId}/reparse`, { method: 'POST', body: '{}' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['profiles'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.removeQueries({ queryKey: ['deep'] })
    },
  })
}

/** Explicitly attach one locally stored source resume to a matching profile. */
export function useAttachProfileResume() {
  const qc = useQueryClient()
  return useMutation<Profile, Error, { profileId: string; sourceProfileId: string }>({
    mutationFn: ({ profileId, sourceProfileId }) =>
      apiFetch<Profile>(`/api/profiles/${profileId}/attach-resume/${sourceProfileId}`, {
        method: 'POST', body: '{}',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['profiles'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/** Deploy-readiness: key + vector-store checks with actionable fixes. */
export interface HealthProblem { key: string; message: string; fix: string }
export function useHealth() {
  return useQuery<{ embeddings_ok: boolean; llm_ok: boolean; weaviate_ok: boolean; seeding: boolean; problems: HealthProblem[] }, Error>({
    queryKey: ['health'],
    queryFn: () => apiFetch('/api/health'),
    staleTime: 60_000,
    // Poll faster while the first-run seed is running so the banner clears promptly.
    refetchInterval: (query) => (query.state.data?.seeding ? 8_000 : 120_000),
  })
}

/** Mark already-applied roles from a pasted markdown tracker table (no AI). */
export function useImportApplied() {
  const qc = useQueryClient()
  return useMutation<{ rows: number; marked_applied: number; unmatched: string[] }, Error, { profileId: string; text: string }>({
    mutationFn: ({ profileId, text }) =>
      apiFetch(`/api/profiles/${profileId}/import-applied`, {
        method: 'POST', body: JSON.stringify({ text }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.invalidateQueries({ queryKey: ['jobs-by-state'] })
      qc.invalidateQueries({ queryKey: ['pipeline'] })
    },
  })
}

// ---------------------------------------------------------------------------
// Resume library (many uploads per profile, one active for matching)
// ---------------------------------------------------------------------------

export interface ResumeRow {
  id: string
  filename: string
  content_type: string | null
  size_bytes: number
  uploaded_at: string
}
export interface ResumeLibrary { active_resume_id: string | null; resumes: ResumeRow[] }

export function useResumes(profileId: string | null) {
  return useQuery<ResumeLibrary, Error>({
    queryKey: ['resumes', profileId],
    queryFn: () => apiFetch<ResumeLibrary>(`/api/profiles/${profileId}/resumes`),
    enabled: !!profileId,
  })
}

/** Add a resume to a profile's library (multipart). First upload becomes active. */
export function useUploadResume() {
  const qc = useQueryClient()
  return useMutation<ResumeLibrary, Error, { profileId: string; file: File }>({
    mutationFn: async ({ profileId, file }) => {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`/api/profiles/${profileId}/resumes`, { method: 'POST', body: form })
      if (!res.ok) {
        const text = await res.text().catch(() => res.statusText)
        throw new Error(`Upload failed (${res.status}): ${text}`)
      }
      return res.json() as Promise<ResumeLibrary>
    },
    onSuccess: (data, { profileId }) => {
      // Write the returned library straight into the cache so the new resume
      // appears instantly — the upload runs a multi-second LLM parse, so an
      // invalidate-only refetch felt like it "needed a reload".
      qc.setQueryData(['resumes', profileId], data)
      qc.invalidateQueries({ queryKey: ['profiles'] })  // header active-resume line
      qc.invalidateQueries({ queryKey: ['jobs'] })
    },
  })
}

/** Switch which resume drives matching. */
export function useActivateResume() {
  const qc = useQueryClient()
  return useMutation<Profile, Error, { profileId: string; resumeId: string }>({
    mutationFn: ({ profileId, resumeId }) =>
      apiFetch<Profile>(`/api/profiles/${profileId}/resumes/${resumeId}/activate`, { method: 'POST', body: '{}' }),
    onSuccess: (_d, { profileId }) => {
      qc.invalidateQueries({ queryKey: ['resumes', profileId] })
      qc.invalidateQueries({ queryKey: ['profiles'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.removeQueries({ queryKey: ['deep'] })  // switching the active resume changes matching
    },
  })
}

/** Rename a resume's display label. */
export function useRenameResume() {
  const qc = useQueryClient()
  return useMutation<ResumeRow, Error, { profileId: string; resumeId: string; filename: string }>({
    mutationFn: ({ profileId, resumeId, filename }) =>
      apiFetch<ResumeRow>(`/api/profiles/${profileId}/resumes/${resumeId}`, {
        method: 'PATCH', body: JSON.stringify({ filename }),
      }),
    onSuccess: (_d, { profileId }) => {
      qc.invalidateQueries({ queryKey: ['resumes', profileId] })
      qc.invalidateQueries({ queryKey: ['profiles'] })
    },
  })
}

/** Delete a resume; if it was active the next-newest takes over. */
export function useDeleteResume() {
  const qc = useQueryClient()
  return useMutation<ResumeLibrary, Error, { profileId: string; resumeId: string }>({
    mutationFn: ({ profileId, resumeId }) =>
      apiFetch<ResumeLibrary>(`/api/profiles/${profileId}/resumes/${resumeId}`, { method: 'DELETE' }),
    onSuccess: (data, { profileId }) => {
      qc.setQueryData(['resumes', profileId], data)  // instant removal, no refetch wait
      qc.invalidateQueries({ queryKey: ['profiles'] })
      qc.invalidateQueries({ queryKey: ['jobs'] })
      qc.removeQueries({ queryKey: ['deep'] })  // active resume may have changed
    },
  })
}

// ---------------------------------------------------------------------------
// Tailored-resume library
// ---------------------------------------------------------------------------

export interface TailoredRow {
  job_id: string
  company: string
  title: string
  filename: string
  recommendation: string | null
  up_to_date: boolean
  created_at: string
  download_url: string
}

export function useTailoredResumes(profileId: string | null) {
  return useQuery<{ tailored: TailoredRow[] }, Error>({
    queryKey: ['tailored', profileId],
    queryFn: () => apiFetch<{ tailored: TailoredRow[] }>(`/api/profiles/${profileId}/tailored`),
    enabled: !!profileId,
  })
}

/** Rename a built tailored resume's download filename (auto-.docx, sibling-dedup server-side). */
export function useRenameTailored() {
  const qc = useQueryClient()
  return useMutation<{ job_id: string; filename: string }, Error, { profileId: string; jobId: string; filename: string }>({
    mutationFn: ({ profileId, jobId, filename }) =>
      apiFetch(`/api/profiles/${profileId}/tailored/${jobId}`, {
        method: 'PATCH', body: JSON.stringify({ filename }),
      }),
    onSuccess: (_d, { profileId }) => qc.invalidateQueries({ queryKey: ['tailored', profileId] }),
  })
}

export interface ProfileFit {
  profile_id: string
  label: string
  score: number
  verdict: string
  recommendable: boolean
}

/** Deterministic per-profile fit for one job (no LLM) — powers the "Tailor as" default. */
export function useProfileFits(jobId: string | null, enabled = true) {
  return useQuery<{ fits: ProfileFit[] }, Error>({
    queryKey: ['profile-fits', jobId],
    queryFn: () => apiFetch<{ fits: ProfileFit[] }>(`/api/jobs/${jobId}/profile-fits`),
    enabled: !!jobId && enabled,
  })
}

/** One-time: parse the stored resume text into typed sections (1 AI call). */
export function useStructureProfile() {
  const qc = useQueryClient()
  return useMutation<Profile, Error, string>({
    mutationFn: (profileId) =>
      apiFetch<Profile>(`/api/profiles/${profileId}/structure`, { method: 'POST', body: '{}' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['profiles'] })
      qc.removeQueries({ queryKey: ['deep'] })
    },
  })
}

/** Suggest ADD-ONLY values for one profile list field (1 AI call; read-only). */
export function useSuggestField() {
  return useMutation<{ field: string; suggestions: string[] }, Error, { profileId: string; field: string }>({
    mutationFn: ({ profileId, field }) =>
      apiFetch(`/api/profiles/${profileId}/suggest`, {
        method: 'POST', body: JSON.stringify({ field }),
      }),
  })
}

/** Suggest truthful rewrites for one entry's bullets (1 AI call; read-only). */
export function usePolishBullets() {
  return useMutation<{ bullets: PolishPair[] }, Error, { profileId: string; section: string; index: number }>({
    mutationFn: ({ profileId, section, index }) =>
      apiFetch<{ bullets: PolishPair[] }>(`/api/profiles/${profileId}/polish`, {
        method: 'POST', body: JSON.stringify({ section, index }),
      }),
  })
}

/** Build a verified, job-specific DOCX through the configured resume-writing skill. */
export function useTailorResume() {
  const qc = useQueryClient()
  return useMutation<TailoredResumeResponse, Error, { profileId: string; jobId: string; force?: boolean }>({
    mutationFn: ({ profileId, jobId, force }) =>
      apiFetch<TailoredResumeResponse>(`/api/profiles/${profileId}/tailor/${jobId}`, {
        method: 'POST', body: JSON.stringify(force ? { force: true } : {}),
      }),
    onSuccess: (data, { profileId }) => {
      if (data.built) qc.invalidateQueries({ queryKey: ['tailored', profileId] })
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

// ---------------------------------------------------------------------------
// Admin & operator monitoring (host-only; routes are require_admin-gated)
// ---------------------------------------------------------------------------

export interface AdminUser {
  id: string
  email: string | null
  display_name: string | null
  plan: string | null
  is_admin: boolean
  profile_count: number
  storage_bytes: number
  usage_30d: Record<string, number>
}

/** The calling account (drives Admin-tab visibility). */
export function useMe() {
  return useQuery<{ user_id: string; is_admin: boolean; plan: string | null }, Error>({
    queryKey: ['me'],
    queryFn: () => apiFetch('/api/users/me'),
    staleTime: 300_000,
  })
}

export function useAdminUsers() {
  return useQuery<{ users: AdminUser[] }, Error>({
    queryKey: ['admin', 'users'],
    queryFn: () => apiFetch('/api/admin/users'),
  })
}

export function useAdminMetrics() {
  return useQuery<{
    user_count: number
    usage_30d: Record<string, number>
    storage_bytes: number
    metering_enabled: boolean
    quota_enforced: boolean
  }, Error>({
    queryKey: ['admin', 'metrics'],
    queryFn: () => apiFetch('/api/admin/metrics'),
  })
}

/** Grant/revoke premium + limits: set plan / limits_json / is_admin. */
export function useUpdateUser() {
  const qc = useQueryClient()
  return useMutation<{ user: AdminUser }, Error, { userId: string; plan?: string; limits_json?: string | null; is_admin?: boolean }>({
    mutationFn: ({ userId, ...body }) =>
      apiFetch(`/api/admin/users/${userId}`, { method: 'PATCH', body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin'] }),
  })
}
