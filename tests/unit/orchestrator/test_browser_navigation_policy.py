from __future__ import annotations

import pytest
from tests.unit.orchestrator.test_acquisition_service import StaticResolver

from services.orchestrator.app.acquisition.browser_navigation_policy import (
    validate_browser_subresource_url,
)
from services.orchestrator.app.acquisition.http_client import (
    AcquisitionPolicyError,
    HttpAcquisitionClient,
)


def test_browser_subresource_rejects_file_scheme() -> None:
    client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="t/1",
        resolver=StaticResolver("93.184.216.34"),
    )
    with pytest.raises(AcquisitionPolicyError) as exc:
        validate_browser_subresource_url(client, "file:///etc/passwd")
    assert exc.value.error_code == "unsupported_scheme"


def test_browser_subresource_rejects_loopback_http() -> None:
    client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="t/1",
        resolver=StaticResolver("127.0.0.1"),
    )
    with pytest.raises(AcquisitionPolicyError) as exc:
        validate_browser_subresource_url(client, "http://127.0.0.1:8080/")
    assert exc.value.error_code == "target_blocked"
