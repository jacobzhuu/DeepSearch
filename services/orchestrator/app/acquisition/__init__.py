"""Acquisition helpers for policy-guarded HTTP fetching."""

from services.orchestrator.app.acquisition.http_client import (
    AcquisitionPolicyError,
    HostResolver,
    HttpAcquisitionClient,
    HttpFetchResult,
    SocketHostResolver,
)

__all__ = [
    "AcquisitionPolicyError",
    "HostResolver",
    "HttpAcquisitionClient",
    "HttpFetchResult",
    "SocketHostResolver",
]
