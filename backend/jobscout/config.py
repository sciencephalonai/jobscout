from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    google_api_key: str = ""
    embed_model: str = "text-embedding-005"
    openai_api_key: str = ""
    weaviate_url: str = "http://localhost:8080"
    # Weaviate Cloud (WCD). When both are set, the store connects to the cloud
    # cluster instead of a local instance. cluster_url is the REST endpoint
    # (with or without an https:// scheme).
    weaviate_cluster_url: str = ""
    weaviate_api_key: str = ""
    enrich_batch_size: int = 20
    ingest_interval_hours: int = 6
    # Scheduler: cron expression for automatic ingestion (default: 8 AM daily)
    ingest_schedule: str = "0 8 * * *"
    # Comma-separated keywords passed to adapters that require a search term
    ingest_keywords: str = "software engineer,data scientist,product manager,devops engineer"
    # Maximum new results requested per keyword per run
    ingest_results_wanted: int = 50
    relational_db_path: str = "./jobscout.duckdb"
    agent_max_attempts: int = 3

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
