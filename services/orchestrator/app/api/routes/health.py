from fastapi import APIRouter

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
