export interface Job {
  job_id: string
  source: string
  title: string
  company: string | null
  location_raw: string | null
  city: string | null
  country: string | null
  locations?: string[]
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
  employment_type?: string
  category?: string
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
}

export interface JobFilters {
  q?: string
  remote?: string[]
  exp?: string[]
  visa?: string[]
  date_range?: string
  source?: string[]
  company_size?: string[]
  employment_type?: string[]
  category?: string[]
  alpha?: number
  sort?: string
  page?: number
  page_size?: number
}

export interface SourceStatus {
  source: string
  enabled: boolean
  last_run: string | null
  jobs_scraped: number
  status: 'idle' | 'running' | 'error'
  error_message: string | null
}
