"""Acquisition helpers for policy-guarded HTTP fetching."""

from services.orchestrator.app.acquisition.http_client import (
    AcquisitionPolicyError,
    HostResolver,
    HttpAcquisitionClient,
    HttpFetchResult,
    SocketHostResolver,
)
from services.orchestrator.app.acquisition.smoke import SmokeAcquisitionClient

__all__ = [
    "AcquisitionPolicyError",
    "HostResolver",
    "HttpAcquisitionClient",
    "HttpFetchResult",
    "SocketHostResolver",
    "SmokeAcquisitionClient",
]
