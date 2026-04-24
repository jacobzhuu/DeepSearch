from __future__ import annotations

import httpx

from services.orchestrator.app.acquisition import HttpAcquisitionClient


class StaticResolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        del host, port
        return self.addresses


def test_http_acquisition_client_fetches_content_and_hashes_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == "deepresearch-tests/1.0"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html>ok</html>",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    fetch_client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="deepresearch-tests/1.0",
        resolver=StaticResolver("93.184.216.34"),
        client=client,
    )

    result = fetch_client.fetch("https://example.com/report")

    assert result.http_status == 200
    assert result.error_code is None
    assert result.mime_type == "text/html"
    assert result.content == b"<html>ok</html>"
    assert result.content_hash is not None
    assert result.trace["response_bytes"] == len(b"<html>ok</html>")


def test_http_acquisition_client_blocks_non_global_targets_before_request() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        raise AssertionError("request should not be attempted for blocked targets")

    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    fetch_client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="deepresearch-tests/1.0",
        resolver=StaticResolver("127.0.0.1"),
        client=client,
    )

    result = fetch_client.fetch("http://blocked.example/")

    assert request_count == 0
    assert result.http_status is None
    assert result.error_code == "target_blocked"
    assert result.content is None
    assert result.trace["reason"] == "non_global_ip"


def test_http_acquisition_client_limits_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/first":
            return httpx.Response(
                302,
                headers={"location": "https://example.com/second"},
                request=request,
            )
        if request.url.path == "/second":
            return httpx.Response(
                302,
                headers={"location": "https://example.com/third"},
                request=request,
            )
        return httpx.Response(200, content=b"ok", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    fetch_client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=1,
        max_response_bytes=1024,
        user_agent="deepresearch-tests/1.0",
        resolver=StaticResolver("93.184.216.34"),
        client=client,
    )

    result = fetch_client.fetch("https://example.com/first")

    assert result.http_status == 302
    assert result.error_code == "too_many_redirects"
    assert result.content is None
    assert len(result.trace["redirect_chain"]) == 1


def test_http_acquisition_client_rejects_oversized_bodies() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"x" * 33,
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    fetch_client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=32,
        user_agent="deepresearch-tests/1.0",
        resolver=StaticResolver("93.184.216.34"),
        client=client,
    )

    result = fetch_client.fetch("https://example.com/large")

    assert result.http_status == 200
    assert result.error_code == "body_too_large"
    assert result.content is None
    assert result.trace["max_response_bytes"] == 32
