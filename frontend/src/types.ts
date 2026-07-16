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
  is_active?: boolean
  last_seen_at?: string | null
  closed_at?: string | null
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
  eligibility_evidence?: string[]
  known_h1b_sponsor?: boolean
  known_everify?: boolean
  sponsorship_likelihood?: 'likely' | 'unknown' | 'no'
  duplicate_count?: number
  also_on?: string[]
  is_recruiter_post?: boolean
  source_kind?: 'primary' | 'government' | 'curated' | 'aggregator' | 'scraper'
  source_label?: string
  freshness_kind?: 'posted' | 'updated' | 'estimated'
  // ── Early-career signals (computed by the backend) ──
  ghost_risk?: 'low' | 'medium' | 'high'
  posting_age_days?: number | null
  mislabeled_entry?: boolean
  new_grad_program?: boolean
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
  lookback_window?: string | null     // progressive freshness window actually used
  recommendation_refreshing?: boolean // profile-targeted ingestion is running
}

// Full saved profile (GET /api/profiles). Superset of ResumeProfile.
export interface Profile {
  id: string
  label: string
  skills: string[]
  interests: string[]
  target_titles: string[]
  yoe_max: number
  seniority_max: string
  needs_sponsorship: boolean
  reject_clearance: boolean
  reject_citizenship_only: boolean
  remote_preference: 'remote' | 'hybrid' | 'onsite' | 'any'
  countries: string[]
  prefer_cap_exempt: boolean
  excluded_companies: string[]
  // Deep-match steering: rendered into the deep-match LLM prompt (empty = no rules).
  avoid_role_types: string[]
  avoid_domains: string[]
  // The complete extracted resume is saved locally and is the canonical source
  // for semantic and deep matching. The original PDF/DOCX is retained separately.
  resume_text: string | null
  // Every detected heading, including custom sections, is retained in source
  // order. The complete resume_text remains the lossless canonical record.
  resume_sections: ResumeSection[]
  structured_resume: StructuredResume | null
  resume_filename: string | null
  resume_content_type: string | null
  resume_uploaded_at: string | null
  active_resume_id?: string | null
  // True when a raw-text edit left the typed structured cards behind (matching is
  // unaffected — it uses the current resume_text). Drives the Rebuild-button dot.
  structured_stale?: boolean
}

export interface ResumeSection {
  heading: string
  content: string
}

// ── Structured resume (JSON-Resume-aligned typed sections) ──
export interface EducationEntry {
  institution: string
  degree?: string | null
  field_of_study?: string | null
  gpa?: string | null
  start_date?: string | null
  end_date?: string | null
  location?: string | null
  honors: string[]
}

export interface ExperienceEntry {
  company: string
  title: string
  location?: string | null
  start_date?: string | null
  end_date?: string | null
  current: boolean
  summary?: string | null
  bullets: string[]
}

export interface ProjectEntry {
  name: string
  technologies: string[]
  url?: string | null
  github_url?: string | null
  start_date?: string | null
  end_date?: string | null
  bullets: string[]
}

export interface CertificationEntry {
  name: string
  issuer?: string | null
  date?: string | null
  credential_id?: string | null
  url?: string | null
}

export interface PublicationEntry {
  title: string
  venue?: string | null
  date?: string | null
  url?: string | null
  authors: string[]
  description?: string | null
}

export interface AchievementEntry {
  title: string
  issuer?: string | null
  date?: string | null
  description?: string | null
}

export interface SkillCategory {
  name: string
  skills: string[]
}

export interface CustomSection {
  title: string
  bullets: string[]
}

export interface StructuredResume {
  summary?: string | null
  education: EducationEntry[]
  experience: ExperienceEntry[]
  projects: ProjectEntry[]
  certifications: CertificationEntry[]
  publications: PublicationEntry[]
  achievements: AchievementEntry[]
  skill_categories: SkillCategory[]
  custom_sections: CustomSection[]
}

export interface PolishPair {
  original: string
  suggested: string
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
  recommendable: boolean
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
  profile: Profile
  jobs: Job[]
  verdicts: Record<string, Verdict>
}

// LLM "second opinion" for one job vs. the active profile (POST /api/match/deep/{job_id}).
export interface DeepMatch {
  verdict: 'apply' | 'borderline' | 'skip'
  score: number
  strengths: string[]
  gaps: string[]
  summary: string
  cached: boolean
}

export interface TailorGate {
  recommendation: 'build' | 'skip'
  rule_verdict: 'apply' | 'flag' | 'reject'
  rule_red_flags: string[]
  deep_verdict?: 'apply' | 'borderline' | 'skip'
  deep_score?: number
  deep_gaps?: string[]
  deep_summary?: string
}

export interface TailoredResumeResponse {
  built: boolean
  gate: TailorGate
  // Present only when built:
  filename?: string
  notes?: string[]
  warnings?: string[]
  provider?: 'deepseek' | 'nvidia'
  model?: string
  download_url?: string
}

// Backend wiring shown in the Settings panel (GET/PUT /api/settings).
export interface SettingsResponse {
  storage_mode: 'both' | 'cloud' | 'local'
  backend: { primary: string; mirror: string | null; dual_write: boolean }
  keys_present: { google: boolean; deepseek: boolean; nvidia: boolean; weaviate_cloud: boolean }
  llm: { provider: 'deepseek' | 'nvidia'; model: string; configured: boolean }
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
  // ── Early-career toggles ──
  exclude_ghost?: boolean          // hide likely-stale 'ghost' postings (old + still listed)
  true_entry_only?: boolean        // restrict to high-confidence entry roles (yoe_min<=2 / junior / new-grad)
  new_grad_only?: boolean          // only explicit new-grad / early-career / rotational programs
  direct_sources_only?: boolean    // official employer ATS / government boards only
  recommendation_only?: boolean    // requires profile_id; return only profile-backed recommendations
  apply_only?: boolean             // requires profile_id; hides Flag / Reject verdicts
  target_min?: number              // progressively widen freshness until this many matches exist
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
  ats: 'greenhouse' | 'lever' | 'ashby' | 'workday' | 'workable' | 'rippling' | 'recruitee' | 'smartrecruiters' | 'none'
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
  last_seen?: number | null
  last_filtered?: number | null
  last_closed?: number | null
  last_error: string | null
  total_ingested: number | null
}

export interface Stats {
  total_jobs: number
  by_source: Record<string, number>
  by_date_bucket: Record<string, number>
  embed_quota_exhausted: boolean
}
