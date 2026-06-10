export interface Job {
  job_id: string
  source: string
  title: string
  company: string | null
  location_raw: string | null
  city: string | null
  country: string | null
  remote_mode: 'remote' | 'onsite' | 'hybrid' | 'unknown'
  description: string | null
  url: string
  salary_min: number | null
  salary_max: number | null
  salary_currency: string | null
  posted_date: string | null // ISO datetime string
  posted_date_est: boolean
  ingested_at: string
  yoe_min: number | null
  yoe_max: number | null
  visa_sponsorship: 'yes' | 'no' | 'unclear' | 'not_mentioned'
  work_auth_required: string | null
  restrictions: string | null
  skills: string[]
  seniority: string
  enrichment_status: 'pending' | 'done' | 'failed'
  company_size_bucket?: string | null
  // ── Sponsorship intelligence (from the backend) ──
  employer_type?: 'university' | 'hospital' | 'nonprofit' | 'government' | 'for_profit' | 'unclear'
  cap_exempt?: 'yes' | 'likely' | 'no' | 'unknown'
  citizenship_required?: boolean
  known_h1b_sponsor?: boolean
  known_everify?: boolean
  sponsorship_likelihood?: 'likely' | 'unknown' | 'no'
  duplicate_count?: number
  also_on?: string[]
  is_recruiter_post?: boolean
}

export interface JobsResponse {
  jobs: Job[]
  total: number
  page: number
  page_size: number
  facets: {
    visa_sponsorship?: Record<string, number>
    remote_mode?: Record<string, number>
    source?: Record<string, number>
    company_size?: Record<string, number>
    category?: Record<string, number>
  }
  verdicts?: Record<string, Verdict>  // present when a profile_id is supplied
}

// Full saved profile (GET /api/profiles). Superset of ResumeProfile.
export interface Profile {
  id: string
  label: string
  skills: string[]
  target_titles: string[]
  yoe_max: number
  seniority_max: string
  needs_sponsorship: boolean
}

export type JobState =
  | 'applied' | 'saved' | 'seen' | 'hidden'
  | 'oa' | 'interview' | 'offer' | 'rejected'

export const PIPELINE_STAGES: { key: JobState; label: string }[] = [
  { key: 'applied', label: 'Applied' },
  { key: 'oa', label: 'OA' },
  { key: 'interview', label: 'Interview' },
  { key: 'offer', label: 'Offer' },
  { key: 'rejected', label: 'Rejected' },
]

export interface PipelineResponse {
  jobs: Job[]
  stages: Record<string, { stage: JobState; note: string | null; updated_at: string }>
}

export interface SavedSearch {
  id: string
  label: string
  filters: JobFilters
  profile_id: string | null
  created_at: string
  last_checked_at: string
  new_count: number
}

export interface SchedulerStatus {
  enabled: boolean
  hour: number
  embed_daily_budget: number
  next_run: string | null
}

export interface Verdict {
  job_id: string
  verdict: 'apply' | 'flag' | 'reject'
  score: number
  reasons: string[]
  red_flags: string[]
  matched: string[]
  gaps: string[]
  cap_exempt: string
}

export interface ResumeProfile {
  id: string
  label: string
  skills: string[]
  target_titles: string[]
  yoe_max: number
  seniority_max: string
  needs_sponsorship: boolean
}

export interface MatchResponse {
  profile: ResumeProfile
  jobs: Job[]
  verdicts: Record<string, Verdict>
}

export interface JobFilters {
  q?: string
  remote?: string[]
  exp?: string[]
  visa?: string[]
  date_range?: string
  source?: string[]
  company_size?: string[]
  category?: string[]
  employment_type?: string[]       // full_time|contract|part_time|internship|temporary
  // ── Sponsorship toggles ──
  cap_exempt?: string[]            // e.g. ['yes','likely'] for the cap-exempt toggle
  exclude_no_sponsorship?: boolean // hide explicit no + citizenship-required
  h1b_sponsor?: boolean            // only proven DoL H-1B filers
  everify?: boolean                // only known E-Verify employers (STEM OPT gate)
  employer_type?: string[]         // university|hospital|nonprofit|government|for_profit
  security_clearance?: string[]    // required|preferred|none|unclear
  exclude_recruiter?: boolean      // hide staffing-agency / aggregator reposts
  profile_id?: string              // attach verdicts + cap-exempt sort + exclusions
  alpha?: number
  sort?: string
  page?: number
  page_size?: number
}

export interface DiscoveryResult {
  name: string
  ats: string
  slug: string
  job_count: number
  sample_title: string | null
}

export interface Company {
  slug: string
  ats: 'greenhouse' | 'lever' | 'ashby' | 'workday' | 'workable' | 'rippling' | 'none'
  name: string
  careers_url: string | null
  tier: string
  employer_type: string
  size_bucket: string | null
  known_h1b_sponsor: boolean
  cap_exempt_hint: string
  open_roles: number
  last_checked: string | null
  enabled: boolean
  direct_apply_only: boolean
}

export interface CompanyFilters {
  tier?: string
  ats?: string
  h1b_sponsor?: boolean
  enabled?: boolean
  direct_apply_only?: boolean
  sort?: string
}

export interface SourceStatus {
  source: string
  last_run_at: string | null
  last_run_status: string | null
  last_ingested: number | null
  last_failed: number | null
  last_error: string | null
  total_ingested: number | null
}

export interface Stats {
  total_jobs: number
  by_source: Record<string, number>
  by_date_bucket: Record<string, number>
  embed_quota_exhausted: boolean
}
