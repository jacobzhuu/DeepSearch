from fastapi import FastAPI

from services.orchestrator.app.api.routes.health import router as health_router
from services.orchestrator.app.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version="0.1.0",
    )
    application.include_router(health_router)
    return application


app = create_app()
