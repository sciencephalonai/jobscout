from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    google_api_key: str = ""
    embed_model: str = "gemini-embedding-001"
    openai_api_key: str = ""
    weaviate_url: str = "http://localhost:8080"
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
    relational_db_path: str = "./jobscout.duckdb"
    agent_max_attempts: int = 3
    # When true, an on-demand Weaviate backup (jobscout.backup.export_index) runs
    # at the end of each ingest so the local export file stays fresh. Off by
    # default — opt-in; the export is a pure $0 download (no embedding), and data
    # only changes on ingest.
    export_after_ingest: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
