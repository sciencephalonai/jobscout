"""Application settings, loaded from the environment / `.env`.

Every knob is documented in `env.example`. Settings are read once at import
time into the module-level ``settings`` singleton; tests monkeypatch fields
on that object directly.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    # USAJobs (data.usajobs.gov) — free API key from developer.usajobs.gov. The
    # email is sent as the required User-Agent header. Both must be set for the
    # opt-in USAJobs adapter to run.
    usajobs_api_key: str = ""
    usajobs_email: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    # Structured enrichment, resume parsing, and on-demand deep matching use an
    # OpenAI-compatible chat endpoint. NVIDIA is an alternate provider, not a
    # second mandatory dependency, so only one provider is active at a time.
    llm_provider: str = "deepseek"
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "z-ai/glm-5.2"
    google_api_key: str = ""
    embed_model: str = "gemini-embedding-001"
    weaviate_url: str = "http://localhost:8080"
    # Vector-store write target: "both" (local always + cloud best-effort mirror),
    # "cloud" (cloud primary, auto-degrades to local if unreachable), or "local".
    # Effective mode also depends on whether cloud creds are present + reachable.
    storage_mode: str = "both"
    # Weaviate Cloud (WCD). When both are set, the store connects to the cloud
    # cluster instead of a local instance. cluster_url is the REST endpoint
    # (with or without an https:// scheme).
    weaviate_cluster_url: str = ""
    weaviate_api_key: str = ""
    enrich_batch_size: int = 20
    ingest_interval_hours: int = 6
    # Daily auto-refresh scheduler. OFF by default — a daily crawl can exhaust the
    # Gemini free embedding tier (1,000/day). Enable via /api/scheduler or env once a
    # paid tier / local embeddings remove the ceiling.
    scheduler_enabled: bool = False
    scheduler_hour: int = 6   # local hour to run the daily refresh when enabled
    # Max embeddings spent per watchlist-refresh run. Keeps headroom under the
    # Gemini free tier (1,000 embeds/day) so a single big board can't exhaust it.
    embed_daily_budget: int = 500
    # Parallel source-fetch workers for ingestion (1 = sequential legacy behavior).
    ingest_fetch_workers: int = 6
    # First-run seed: when the index is empty and an embedding key is present, run
    # ONE bounded background ingest over the fastest keyless sources so a fresh
    # deployment isn't empty. Runs once (a persisted marker prevents repeats).
    # Bounded well under embed_daily_budget so it can't eat the day's quota.
    seed_on_first_run: bool = True
    seed_job_count: int = 150
    # Freshness is kept live, not frozen: at the end of each ingest, jobs whose
    # posting is older than this are purged (reusing WeaviateStore.purge_older_than),
    # so the index is a rolling recent window instead of accumulating stale postings.
    # Deliberately > GHOST_STALE_DAYS (45): a job first gets a visible "ghost risk"
    # badge, then a grace period, and is only removed once it is well past useful —
    # this respects the "flag it, let the user decide" design instead of pre-empting it.
    # 0 disables retention entirely.
    retention_days: int = 60
    relational_db_path: str = "./jobscout.duckdb"
    # DB seam: which RelationalStore backend make_relational_store builds. Only
    # "duckdb" exists today; a "postgres" backend is a future drop-in (the store
    # SQL is already portable). See docs/multi-tenancy.md.
    relational_backend: str = "duckdb"
    # Blob seam: which BlobStore backend stores original resume/tailored files.
    # "local" = local disk (today); "s3"/"gcs" are future drop-ins for multi-instance.
    blob_backend: str = "local"
    # Original resume files stay on this local machine. Parsed, editable text
    # remains in the DuckDB profile JSON so matching never depends on a PDF.
    resume_storage_dir: str = "./data/resumes"
    # Path to the user's private resume-writing toolkit (canonical facts,
    # presets, DOCX builder, audit gate). Per-user — set RESUME_WRITER_DIR in
    # .env; blank disables resume tailoring.
    resume_writer_dir: str = ""
    # Optional earlier toolkit whose truthfulness/keyword-gap rules feed the
    # planner prompt. Blank = skipped.
    resume_writer_legacy_dir: str = ""
    tailored_resume_storage_dir: str = "./data/tailored-resumes"
    # Optional path that exposes the toolkit's `docx` Node dependency. If blank,
    # the service also checks the toolkit and the local user's node_modules.
    resume_writer_node_path: str = ""
    agent_max_attempts: int = 3
    # When true, an on-demand Weaviate backup (jobscout.backup.export_index) runs
    # at the end of each ingest so the local export file stays fresh. Off by
    # default — opt-in; the export is a pure $0 download (no embedding), and data
    # only changes on ingest.
    export_after_ingest: bool = False

    # Multi-tenancy seam. Today JobScout is a single local user who owns everything;
    # every profile/saved-search is stamped with `local_user_id`, and the ONE place
    # that decides "who is calling" is api/deps.current_user_id (returns this).
    # When `single_user_mode` is True the dangerous global routes (settings, maintenance,
    # scheduler, source overrides) stay open (local admin). Flip it False + replace
    # current_user_id's body with a real session/JWT lookup to host multiple users
    # without any private-data leakage. See docs/multi-tenancy.md.
    local_user_id: str = "local"
    single_user_mode: bool = True
    # CORS: allowed browser origins. Default "*" is fine for local/trusted-group use;
    # lock to an explicit list before deployment (see docs/pre-deployment-checklist.md).
    # Wildcard + credentials is invalid per the CORS spec, so credentials are only
    # enabled when a concrete allowlist is set (handled in api/main.py).
    cors_allow_origins: list[str] = ["*"]
    # ── Dormant guard rails (Tier 2) ─────────────────────────────────────────────
    # All OFF by default so single/small-group behavior is byte-identical to today.
    # Flip these before exposing the app to untrusted users — see
    # docs/pre-deployment-checklist.md. Per-account overrides ride on top via
    # entitlements.resolve_limits (users.plan/limits_json).
    rate_limit_enabled: bool = False          # per-IP request throttling
    security_headers_enabled: bool = False    # X-Frame-Options/CSP/etc.
    hsts_enabled: bool = False                 # HSTS header (only meaningful over HTTPS)
    upload_limits_enabled: bool = False       # enforce max_upload_mb + type allowlist
    max_request_mb: float | None = None       # global request-body cap (None = off)
    usage_metering_enabled: bool = False      # RECORD per-account usage (for monitoring) without capping
    quota_enforced: bool = False              # ENFORCE per-account usage caps (implies recording)
    require_auth: bool = False                # 401 without a valid session (gate only)
    # Default limit VALUES (used by entitlements.resolve_limits; None = unlimited).
    # These are the GLOBAL defaults; a user's plan/limits_json can override per account.
    rate_limit_per_min: int | None = 120
    max_upload_mb: float | None = 10.0
    upload_allowed_types: list[str] = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword", "text/plain", "application/json", "text/markdown",
    ]
    tailor_per_day: int | None = None
    deep_match_per_day: int | None = None
    llm_spend_per_day: int | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
