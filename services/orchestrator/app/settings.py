from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="deepresearch-orchestrator", validation_alias="APP_NAME")
    app_env: str = Field(default="development", validation_alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", validation_alias="APP_HOST")
    app_port: int = Field(default=8000, validation_alias="APP_PORT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_format: str = Field(default="json", validation_alias="LOG_FORMAT")
    database_url: str = Field(default="sqlite:///./data/dev.db", validation_alias="DATABASE_URL")
    search_provider: str = Field(default="searxng", validation_alias="SEARCH_PROVIDER")
    searxng_base_url: str = Field(
        default="http://127.0.0.1:8080",
        validation_alias="SEARXNG_BASE_URL",
    )
    searxng_timeout_seconds: float = Field(
        default=10.0,
        validation_alias="SEARXNG_TIMEOUT_SECONDS",
    )
    search_max_results_per_query: int = Field(
        default=10,
        validation_alias="SEARCH_MAX_RESULTS_PER_QUERY",
    )
    query_expansion_max_domains: int = Field(
        default=3,
        validation_alias="QUERY_EXPANSION_MAX_DOMAINS",
    )
    acquisition_timeout_seconds: float = Field(
        default=10.0,
        validation_alias="ACQUISITION_TIMEOUT_SECONDS",
    )
    acquisition_max_redirects: int = Field(
        default=3,
        validation_alias="ACQUISITION_MAX_REDIRECTS",
    )
    acquisition_max_response_bytes: int = Field(
        default=1_048_576,
        validation_alias="ACQUISITION_MAX_RESPONSE_BYTES",
    )
    acquisition_max_candidates_per_request: int = Field(
        default=5,
        validation_alias="ACQUISITION_MAX_CANDIDATES_PER_REQUEST",
    )
    acquisition_user_agent: str = Field(
        default="deepresearch-orchestrator/0.1",
        validation_alias="ACQUISITION_USER_AGENT",
    )
    snapshot_storage_backend: str = Field(
        default="filesystem",
        validation_alias="SNAPSHOT_STORAGE_BACKEND",
    )
    snapshot_storage_root: str = Field(
        default="./data/snapshots",
        validation_alias="SNAPSHOT_STORAGE_ROOT",
    )
    minio_endpoint: str = Field(default="", validation_alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="", validation_alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="", validation_alias="MINIO_SECRET_KEY")
    minio_secure: bool = Field(default=False, validation_alias="MINIO_SECURE")
    minio_region: str | None = Field(default=None, validation_alias="MINIO_REGION")
    snapshot_storage_bucket: str = Field(
        default="snapshots",
        validation_alias="SNAPSHOT_STORAGE_BUCKET",
    )
    report_storage_bucket: str = Field(
        default="reports",
        validation_alias="REPORT_STORAGE_BUCKET",
    )
    index_backend: str = Field(
        default="opensearch",
        validation_alias="INDEX_BACKEND",
    )
    opensearch_base_url: str = Field(
        default="http://127.0.0.1:9200",
        validation_alias="OPENSEARCH_BASE_URL",
    )
    opensearch_index_name: str = Field(
        default="source-chunks-v1",
        validation_alias="OPENSEARCH_INDEX_NAME",
    )
    opensearch_username: str = Field(default="", validation_alias="OPENSEARCH_USERNAME")
    opensearch_password: str = Field(default="", validation_alias="OPENSEARCH_PASSWORD")
    opensearch_verify_tls: bool = Field(default=True, validation_alias="OPENSEARCH_VERIFY_TLS")
    opensearch_ca_bundle_path: str | None = Field(
        default=None,
        validation_alias="OPENSEARCH_CA_BUNDLE_PATH",
    )
    opensearch_timeout_seconds: float = Field(
        default=10.0,
        validation_alias="OPENSEARCH_TIMEOUT_SECONDS",
    )
    opensearch_validate_connectivity_on_startup: bool = Field(
        default=False,
        validation_alias="OPENSEARCH_VALIDATE_CONNECTIVITY_ON_STARTUP",
    )
    indexing_max_chunks_per_request: int = Field(
        default=20,
        validation_alias="INDEXING_MAX_CHUNKS_PER_REQUEST",
    )
    retrieval_max_results_per_request: int = Field(
        default=20,
        validation_alias="RETRIEVAL_MAX_RESULTS_PER_REQUEST",
    )
    claim_drafting_max_candidates_per_request: int = Field(
        default=5,
        validation_alias="CLAIM_DRAFTING_MAX_CANDIDATES_PER_REQUEST",
    )
    claim_verification_max_claims_per_request: int = Field(
        default=5,
        validation_alias="CLAIM_VERIFICATION_MAX_CLAIMS_PER_REQUEST",
    )
    metrics_enabled: bool = Field(default=True, validation_alias="METRICS_ENABLED")


@lru_cache
def get_settings() -> Settings:
    return Settings()
