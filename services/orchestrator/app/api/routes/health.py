from fastapi import APIRouter, HTTPException, Response, status

from packages.observability import render_metrics
from services.orchestrator.app.settings import get_settings

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz() -> dict[str, str]:
    settings = get_settings()
    return {
        "environment": settings.app_env,
        "service": settings.app_name,
        "status": "ready",
    }


@router.get("/metrics")
def metrics() -> Response:
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="metrics endpoint is disabled",
        )
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)
