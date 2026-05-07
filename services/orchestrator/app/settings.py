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
    yacy_base_url: str = Field(default="http://127.0.0.1:8090", validation_alias="YACY_BASE_URL")
    yacy_timeout_seconds: float = Field(default=10.0, validation_alias="YACY_TIMEOUT_SECONDS")
    yacy_resource: str = Field(default="local", validation_alias="YACY_RESOURCE")
    yacy_verify: str = Field(default="false", validation_alias="YACY_VERIFY")
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
    acquisition_target_successful_snapshots: int = Field(
        default=2,
        validation_alias="ACQUISITION_TARGET_SUCCESSFUL_SNAPSHOTS",
    )
    acquisition_min_answer_sources: int = Field(
        default=3,
        validation_alias="ACQUISITION_MIN_ANSWER_SOURCES",
    )
    acquisition_max_supplemental_sources: int = Field(
        default=3,
        validation_alias="ACQUISITION_MAX_SUPPLEMENTAL_SOURCES",
    )
    research_gap_max_rounds: int = Field(
        default=2,
        validation_alias="RESEARCH_GAP_MAX_ROUNDS",
    )
    research_gap_max_queries_per_round: int = Field(
        default=4,
        validation_alias="RESEARCH_GAP_MAX_QUERIES_PER_ROUND",
    )
    research_worker_poll_interval_seconds: float = Field(
        default=2.0,
        validation_alias="RESEARCH_WORKER_POLL_INTERVAL_SECONDS",
    )
    research_worker_batch_size: int = Field(
        default=1,
        validation_alias="RESEARCH_WORKER_BATCH_SIZE",
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
    llm_enabled: bool = Field(default=False, validation_alias="LLM_ENABLED")
    llm_provider: str = Field(default="noop", validation_alias="LLM_PROVIDER")
    llm_model: str = Field(default="", validation_alias="LLM_MODEL")
    llm_api_key: str = Field(default="", validation_alias="LLM_API_KEY", repr=False)
    llm_base_url: str = Field(default="", validation_alias="LLM_BASE_URL")
    llm_timeout_seconds: float = Field(default=30.0, validation_alias="LLM_TIMEOUT_SECONDS")
    llm_max_retries: int = Field(default=1, validation_alias="LLM_MAX_RETRIES")
    llm_max_output_tokens: int = Field(
        default=1200,
        validation_alias="LLM_MAX_OUTPUT_TOKENS",
    )
    llm_report_writer_enabled: bool = Field(
        default=False,
        validation_alias="LLM_REPORT_WRITER_ENABLED",
    )
    llm_source_judge_enabled: bool = Field(
        default=False,
        validation_alias="LLM_SOURCE_JUDGE_ENABLED",
    )
    llm_source_judge_active_rerank: bool = Field(
        default=False,
        validation_alias="LLM_SOURCE_JUDGE_ACTIVE_RERANK",
    )
    llm_source_judge_max_candidates: int = Field(
        default=5,
        validation_alias="LLM_SOURCE_JUDGE_MAX_CANDIDATES",
    )
    llm_query_rewriter_enabled: bool = Field(
        default=False,
        validation_alias="LLM_QUERY_REWRITER_ENABLED",
    )
    llm_query_rewriter_max_queries: int = Field(
        default=8,
        validation_alias="LLM_QUERY_REWRITER_MAX_QUERIES",
    )
    llm_evidence_reranker_enabled: bool = Field(
        default=False,
        validation_alias="LLM_EVIDENCE_RERANKER_ENABLED",
    )
    llm_evidence_reranker_max_chunks: int = Field(
        default=40,
        validation_alias="LLM_EVIDENCE_RERANKER_MAX_CHUNKS",
    )
    llm_claim_reviewer_enabled: bool = Field(
        default=False,
        validation_alias="LLM_CLAIM_REVIEWER_ENABLED",
    )
    llm_claim_reviewer_max_claims: int = Field(
        default=12,
        validation_alias="LLM_CLAIM_REVIEWER_MAX_CLAIMS",
    )
    llm_assistance_input_max_chars: int = Field(
        default=24_000,
        validation_alias="LLM_ASSISTANCE_INPUT_MAX_CHARS",
    )
    llm_report_max_output_tokens: int = Field(
        default=2400,
        validation_alias="LLM_REPORT_MAX_OUTPUT_TOKENS",
    )
    report_include_ledger_debug_appendix: bool = Field(
        default=False,
        validation_alias="REPORT_INCLUDE_LEDGER_DEBUG_APPENDIX",
    )
    research_planner_enabled: bool = Field(
        default=False,
        validation_alias="RESEARCH_PLANNER_ENABLED",
    )
    research_planner_max_subquestions: int = Field(
        default=5,
        validation_alias="RESEARCH_PLANNER_MAX_SUBQUESTIONS",
    )
    research_planner_max_search_queries: int = Field(
        default=8,
        validation_alias="RESEARCH_PLANNER_MAX_SEARCH_QUERIES",
    )
    metrics_enabled: bool = Field(default=True, validation_alias="METRICS_ENABLED")

    def llm_safe_summary(self) -> dict[str, object]:
        normalized_provider = self.llm_provider.strip().lower() or "noop"
        normalized_base_url = self.llm_base_url.strip()
        return {
            "llm_enabled": self.llm_enabled,
            "llm_provider": normalized_provider,
            "llm_model": self.llm_model.strip(),
            "llm_base_url_configured": bool(normalized_base_url),
            "llm_api_key_present": bool(self.llm_api_key.strip()),
            "llm_timeout_seconds": self.llm_timeout_seconds,
            "llm_max_retries": self.llm_max_retries,
            "llm_max_output_tokens": self.llm_max_output_tokens,
            "llm_report_writer_enabled": self.llm_report_writer_enabled,
            "llm_report_max_output_tokens": self.llm_report_max_output_tokens,
            "llm_source_judge_enabled": self.llm_source_judge_enabled,
            "llm_source_judge_active_rerank": self.llm_source_judge_active_rerank,
            "llm_source_judge_max_candidates": self.llm_source_judge_max_candidates,
            "llm_query_rewriter_enabled": self.llm_query_rewriter_enabled,
            "llm_query_rewriter_max_queries": self.llm_query_rewriter_max_queries,
            "llm_evidence_reranker_enabled": self.llm_evidence_reranker_enabled,
            "llm_evidence_reranker_max_chunks": self.llm_evidence_reranker_max_chunks,
            "llm_claim_reviewer_enabled": self.llm_claim_reviewer_enabled,
            "llm_claim_reviewer_max_claims": self.llm_claim_reviewer_max_claims,
            "llm_assistance_input_max_chars": self.llm_assistance_input_max_chars,
            "research_planner_enabled": self.research_planner_enabled,
            "research_planner_max_subquestions": self.research_planner_max_subquestions,
            "research_planner_max_search_queries": self.research_planner_max_search_queries,
            "report_include_ledger_debug_appendix": self.report_include_ledger_debug_appendix,
            "research_gap_max_rounds": self.research_gap_max_rounds,
            "research_gap_max_queries_per_round": self.research_gap_max_queries_per_round,
            "acquisition_min_answer_sources": self.acquisition_min_answer_sources,
            "acquisition_max_supplemental_sources": self.acquisition_max_supplemental_sources,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
