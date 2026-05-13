from __future__ import annotations

import httpx
import pytest

from services.orchestrator.app.acquisition import HttpAcquisitionClient
from services.orchestrator.app.acquisition import http_client as http_client_module


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


def test_http_acquisition_client_records_proxy_env_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@127.0.0.1:7890")
    monkeypatch.delenv("NO_PROXY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"ok",
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
        trust_env_proxy=True,
    )

    result = fetch_client.fetch("https://example.com/report")

    assert result.error_code is None
    assert result.trace["proxy_enabled"] is True
    assert result.trace["proxy_source"] == "env"
    assert result.trace["proxy_env_var"] == "HTTPS_PROXY"
    assert result.trace["proxy_url_masked"] == "http://***:***@127.0.0.1:7890"
    assert result.trace["proxy_env_detected"] is True
    assert result.trace["proxy_env_trusted"] is True
    assert result.trace["resolved_ips"] == ["93.184.216.34"]


def test_http_acquisition_client_disables_environment_proxy_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "socks5://127.0.0.1:7890")
    captured: dict[str, object] = {}

    class FakeStream:
        headers = {"content-type": "text/plain"}
        status_code = 200
        url = httpx.URL("https://example.com/report")

        def __enter__(self) -> FakeStream:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def iter_bytes(self) -> list[bytes]:
            return [b"ok"]

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["trust_env"] = kwargs.get("trust_env")

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def stream(self, *args: object, **kwargs: object) -> FakeStream:
            del args, kwargs
            return FakeStream()

    monkeypatch.setattr(http_client_module.httpx, "Client", FakeClient)
    fetch_client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="deepresearch-tests/1.0",
        resolver=StaticResolver("93.184.216.34"),
    )

    result = fetch_client.fetch("https://example.com/report")

    assert result.error_code is None
    assert captured["trust_env"] is False
    assert result.trace["proxy_enabled"] is False
    assert result.trace["proxy_source"] == "none"
    assert result.trace["proxy_env_detected"] is True
    assert result.trace["proxy_env_trusted"] is False


def test_http_acquisition_client_blocks_non_global_targets_before_request() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        del request
        raise AssertionError("request should not be attempted for blocked targets")

    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    fetch_client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="deepresearch-tests/1.0",
        resolver=StaticResolver("10.0.0.8", "169.254.1.2", "::1"),
        client=client,
    )

    result = fetch_client.fetch("http://blocked.example/")

    assert request_count == 0
    assert result.http_status is None
    assert result.error_code == "target_blocked"
    assert result.content is None
    assert result.trace["reason"] == "non_global_ip"
    assert result.trace["decision_reason"] == "all_resolved_ips_non_global"
    assert result.trace["allowed_ips"] == []
    assert result.trace["blocked_ips"] == ["10.0.0.8", "169.254.1.2", "::1"]


@pytest.mark.parametrize(
    ("url", "addresses"),
    (
        ("https://en.wikipedia.org/wiki/SearXNG", ("31.13.88.169", "2001::1")),
        (
            "https://www.reddit.com/r/degoogle/comments/example",
            ("199.232.161.140", "2001::1"),
        ),
    ),
)
def test_http_acquisition_client_allows_public_domain_with_non_global_dns_warning(
    url: str,
    addresses: tuple[str, ...],
) -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"public response",
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)
    fetch_client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="deepresearch-tests/1.0",
        resolver=StaticResolver(*addresses),
        client=client,
    )

    result = fetch_client.fetch(url)

    assert request_count == 1
    assert result.http_status == 200
    assert result.error_code is None
    assert result.content == b"public response"
    assert result.trace["resolved_ips"] == list(addresses)
    assert result.trace["allowed_ips"] == [addresses[0]]
    assert result.trace["blocked_ips"] == ["2001::1"]
    assert result.trace["decision_reason"] == "public_ip_present_with_non_global_dns_answers"
    assert "non-global IPs" in result.trace["safety_warning"]


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
    assert result.error_code == "redirect_loop"
    assert result.content is None
    assert len(result.trace["redirect_chain"]) == 1


def test_http_acquisition_client_follows_html_redirect_stub() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html><body>Redirecting to https://example.com/docs/</body></html>",
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<main><p>Documentation body.</p></main>",
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

    result = fetch_client.fetch("https://example.com/")

    assert result.error_code is None
    assert result.final_url == "https://example.com/docs/"
    assert result.content == b"<main><p>Documentation body.</p></main>"
    assert result.trace["redirect_chain"][0]["reason"] == "html_redirect_stub"


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
    assert result.trace.get("response_cap_source") == "global"
    assert result.trace.get("cap_decision") == "global_default"


def test_http_acquisition_client_trusted_docs_allowlist_raises_body_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"x" * 48,
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
        trusted_docs_domains=frozenset({"docs.langchain.com"}),
        trusted_docs_max_response_bytes=64,
    )

    result = fetch_client.fetch("https://docs.langchain.com/langgraph/overview")

    assert result.http_status == 200
    assert result.error_code is None
    assert len(result.content or b"") == 48
    assert result.trace.get("response_cap_source") == "trusted_docs_allowlist"
    assert result.trace.get("effective_max_response_bytes") == 64


def test_http_acquisition_client_non_allowlisted_host_keeps_global_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"x" * 48,
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
        trusted_docs_domains=frozenset({"docs.langchain.com"}),
        trusted_docs_max_response_bytes=64,
    )

    result = fetch_client.fetch("https://evil.example/large")

    assert result.error_code == "body_too_large"
    assert result.trace.get("cap_decision") == "global_not_allowlisted_host"
    assert result.trace.get("effective_max_response_bytes") == 32
