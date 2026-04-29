from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status

from packages.observability import render_metrics
from services.orchestrator.app.research_quality import QUALITY_DIAGNOSTIC_FIELDS
from services.orchestrator.app.settings import get_settings

router = APIRouter(tags=["system"])
APP_VERSION = "0.1.0"


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


@router.get("/versionz")
def versionz() -> dict[str, Any]:
    git_commit = _git_commit_hash()
    return {
        "service": get_settings().app_name,
        "app_version": APP_VERSION,
        "git_commit": git_commit,
        "git_commit_available": git_commit is not None,
        "research_quality_diagnostics_fields": list(QUALITY_DIAGNOSTIC_FIELDS),
        "research_quality_diagnostics_enabled": True,
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


def _git_commit_hash() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[5],
            check=True,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit or None
