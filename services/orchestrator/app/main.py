from __future__ import annotations

import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from packages.observability import configure_logging, get_logger, observe_http_request
from services.orchestrator.app.api.routes.acquisition import router as acquisition_router
from services.orchestrator.app.api.routes.claims import router as claims_router
from services.orchestrator.app.api.routes.debug_pipeline import router as debug_pipeline_router
from services.orchestrator.app.api.routes.health import APP_VERSION
from services.orchestrator.app.api.routes.health import router as health_router
from services.orchestrator.app.api.routes.indexing import router as indexing_router
from services.orchestrator.app.api.routes.parsing import router as parsing_router
from services.orchestrator.app.api.routes.pipeline import router as pipeline_router
from services.orchestrator.app.api.routes.reporting import router as reporting_router
from services.orchestrator.app.api.routes.research_tasks import router as research_tasks_router
from services.orchestrator.app.api.routes.search_discovery import router as search_discovery_router
from services.orchestrator.app.indexing import build_chunk_index_backend
from services.orchestrator.app.settings import Settings, get_settings
from services.orchestrator.app.storage import SnapshotObjectStore, build_snapshot_object_store

logger = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.log_format.strip().lower() != "json":
        raise RuntimeError(f"unsupported log format: {settings.log_format}")
    configure_logging(settings.log_level)

    snapshot_object_store = _build_startup_object_store(settings)
    snapshot_object_store.validate_configuration()

    chunk_index_backend = build_chunk_index_backend(
        backend=settings.index_backend,
        opensearch_base_url=settings.opensearch_base_url,
        opensearch_index_name=settings.opensearch_index_name,
        opensearch_username=settings.opensearch_username,
        opensearch_password=settings.opensearch_password,
        opensearch_verify_tls=settings.opensearch_verify_tls,
        opensearch_ca_bundle_path=settings.opensearch_ca_bundle_path,
        opensearch_timeout_seconds=settings.opensearch_timeout_seconds,
        opensearch_validate_connectivity=settings.opensearch_validate_connectivity_on_startup,
    )
    chunk_index_backend.validate_configuration()

    application = FastAPI(
        title=settings.app_name,
        version=APP_VERSION,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # For development, allow all origins.
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _install_http_observability_middleware(application)

    application.include_router(health_router)
    application.include_router(research_tasks_router)
    application.include_router(search_discovery_router)
    application.include_router(acquisition_router)
    application.include_router(parsing_router)
    application.include_router(indexing_router)
    application.include_router(claims_router)
    application.include_router(reporting_router)
    application.include_router(pipeline_router)
    application.include_router(debug_pipeline_router)

    logger.info(
        "app.startup.validated",
        extra={
            "app_env": settings.app_env,
            "snapshot_storage_backend": settings.snapshot_storage_backend,
            "index_backend": settings.index_backend,
            "opensearch_validate_connectivity_on_startup": (
                settings.opensearch_validate_connectivity_on_startup
            ),
            "llm": settings.llm_safe_summary(),
        },
    )
    return application


def _build_startup_object_store(settings: Settings) -> SnapshotObjectStore:
    return build_snapshot_object_store(
        backend=settings.snapshot_storage_backend,
        root_directory=settings.snapshot_storage_root,
        minio_endpoint=settings.minio_endpoint,
        minio_access_key=settings.minio_access_key,
        minio_secret_key=settings.minio_secret_key,
        minio_secure=settings.minio_secure,
        minio_region=settings.minio_region,
        required_buckets=[
            settings.snapshot_storage_bucket,
            settings.report_storage_bucket,
        ],
    )


def _install_http_observability_middleware(application: FastAPI) -> None:
    @application.middleware("http")
    async def _http_observability_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id") or str(uuid4())
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_seconds = time.perf_counter() - started_at
            observe_http_request(
                method=request.method,
                path=request.url.path,
                status_code=500,
                duration_seconds=duration_seconds,
            )
            logger.exception(
                "http.request.failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_seconds * 1000, 3),
                },
            )
            raise

        duration_seconds = time.perf_counter() - started_at
        response.headers["x-request-id"] = request_id
        observe_http_request(
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_seconds=duration_seconds,
        )
        logger.info(
            "http.request.completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_seconds * 1000, 3),
            },
        )
        return response


app = create_app()
