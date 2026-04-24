from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
}


class HostResolver(Protocol):
    def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


class SocketHostResolver:
    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        addresses: list[str] = []
        seen_addresses: set[str] = set()
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        for info in infos:
            sockaddr = info[4]
            if not sockaddr:
                continue
            address = str(sockaddr[0])
            if address in seen_addresses:
                continue
            addresses.append(address)
            seen_addresses.add(address)
        return tuple(addresses)


class AcquisitionPolicyError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        trace: dict[str, Any],
        http_status: int | None = None,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.trace = trace
        self.http_status = http_status


@dataclass(frozen=True)
class HttpFetchResult:
    requested_url: str
    final_url: str | None
    http_status: int | None
    error_code: str | None
    mime_type: str | None
    content: bytes | None
    content_hash: str | None
    trace: dict[str, Any] = field(default_factory=dict)


class HttpAcquisitionClient:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_redirects: int,
        max_response_bytes: int,
        user_agent: str,
        resolver: HostResolver | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self.user_agent = user_agent
        self.resolver = resolver or SocketHostResolver()
        self.client = client

    def fetch(self, url: str) -> HttpFetchResult:
        try:
            return self._fetch_with_redirects(url)
        except AcquisitionPolicyError as error:
            return HttpFetchResult(
                requested_url=url,
                final_url=None,
                http_status=error.http_status,
                error_code=error.error_code,
                mime_type=None,
                content=None,
                content_hash=None,
                trace=error.trace,
            )
        except httpx.RequestError as error:
            return HttpFetchResult(
                requested_url=url,
                final_url=None,
                http_status=None,
                error_code="network_error",
                mime_type=None,
                content=None,
                content_hash=None,
                trace={
                    "exception_type": type(error).__name__,
                    "message": str(error),
                    "requested_url": url,
                },
            )

    def _fetch_with_redirects(self, url: str) -> HttpFetchResult:
        current_url = url
        redirect_chain: list[dict[str, Any]] = []

        for redirect_count in range(self.max_redirects + 1):
            resolved_ips = self._validate_target_url(current_url)
            response_data = self._perform_request(current_url)
            location = response_data.headers.get("location")
            if location is not None and response_data.http_status in {301, 302, 303, 307, 308}:
                if redirect_count >= self.max_redirects:
                    raise AcquisitionPolicyError(
                        error_code="too_many_redirects",
                        http_status=response_data.http_status,
                        trace={
                            "requested_url": url,
                            "final_url": response_data.final_url,
                            "redirect_chain": redirect_chain,
                            "resolved_ips": list(resolved_ips),
                        },
                    )

                next_url = urljoin(response_data.final_url, location)
                redirect_chain.append(
                    {
                        "from_url": response_data.final_url,
                        "status_code": response_data.http_status,
                        "to_url": next_url,
                    }
                )
                current_url = next_url
                continue

            error_code = None if 200 <= response_data.http_status < 300 else "http_error_status"
            content_hash = f"sha256:{hashlib.sha256(response_data.content).hexdigest()}"
            return HttpFetchResult(
                requested_url=url,
                final_url=response_data.final_url,
                http_status=response_data.http_status,
                error_code=error_code,
                mime_type=response_data.mime_type,
                content=response_data.content,
                content_hash=content_hash,
                trace={
                    "requested_url": url,
                    "final_url": response_data.final_url,
                    "redirect_chain": redirect_chain,
                    "resolved_ips": list(resolved_ips),
                    "response_bytes": len(response_data.content),
                },
            )

        raise AcquisitionPolicyError(
            error_code="too_many_redirects",
            trace={"requested_url": url, "redirect_chain": redirect_chain},
        )

    def _perform_request(self, url: str) -> _HttpResponseData:
        headers = {
            "Accept": "*/*",
            "User-Agent": self.user_agent,
        }
        if self.client is not None:
            return self._perform_request_with_client(self.client, url, headers)

        with httpx.Client(
            follow_redirects=False,
            timeout=self.timeout_seconds,
            trust_env=False,
        ) as client:
            return self._perform_request_with_client(client, url, headers)

    def _perform_request_with_client(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
    ) -> _HttpResponseData:
        with client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            if response.headers.get("location") is not None and response.status_code in {
                301,
                302,
                303,
                307,
                308,
            }:
                return _HttpResponseData(
                    final_url=str(response.url),
                    http_status=response.status_code,
                    headers=dict(response.headers),
                    mime_type=_normalize_mime_type(response.headers.get("content-type")),
                    content=b"",
                )

            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > self.max_response_bytes:
                    raise AcquisitionPolicyError(
                        error_code="body_too_large",
                        http_status=response.status_code,
                        trace={
                            "final_url": str(response.url),
                            "response_bytes": len(content),
                            "max_response_bytes": self.max_response_bytes,
                        },
                    )

            return _HttpResponseData(
                final_url=str(response.url),
                http_status=response.status_code,
                headers=dict(response.headers),
                mime_type=_normalize_mime_type(response.headers.get("content-type")),
                content=bytes(content),
            )

    def _validate_target_url(self, url: str) -> tuple[str, ...]:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise AcquisitionPolicyError(
                error_code="unsupported_scheme",
                trace={"requested_url": url, "scheme": parsed.scheme},
            )

        host = parsed.hostname
        if host is None:
            raise AcquisitionPolicyError(
                error_code="invalid_target",
                trace={"requested_url": url},
            )

        normalized_host = host.rstrip(".").lower()
        if normalized_host in BLOCKED_HOSTNAMES:
            raise AcquisitionPolicyError(
                error_code="target_blocked",
                trace={
                    "requested_url": url,
                    "host": normalized_host,
                    "reason": "blocked_hostname",
                },
            )

        port = parsed.port or (443 if scheme == "https" else 80)
        resolved_ips = self._resolve_ips(normalized_host, port)
        blocked_ips = [address for address in resolved_ips if _is_blocked_ip(address)]
        if blocked_ips:
            raise AcquisitionPolicyError(
                error_code="target_blocked",
                trace={
                    "requested_url": url,
                    "host": normalized_host,
                    "resolved_ips": list(resolved_ips),
                    "blocked_ips": blocked_ips,
                    "reason": "non_global_ip",
                },
            )

        return resolved_ips

    def _resolve_ips(self, host: str, port: int) -> tuple[str, ...]:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            try:
                resolved_ips = self.resolver.resolve(host, port)
            except OSError as error:
                raise AcquisitionPolicyError(
                    error_code="dns_resolution_failed",
                    trace={
                        "host": host,
                        "port": port,
                        "exception_type": type(error).__name__,
                        "message": str(error),
                    },
                ) from error
        else:
            return (host,)

        if not resolved_ips:
            raise AcquisitionPolicyError(
                error_code="dns_resolution_failed",
                trace={"host": host, "port": port, "reason": "no_addresses"},
            )

        return resolved_ips


def _normalize_mime_type(content_type: str | None) -> str:
    if content_type is None:
        return "application/octet-stream"
    mime_type = content_type.split(";", 1)[0].strip().lower()
    if not mime_type:
        return "application/octet-stream"
    return mime_type


def _is_blocked_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not ip.is_global


@dataclass(frozen=True)
class _HttpResponseData:
    final_url: str
    http_status: int
    headers: dict[str, str]
    mime_type: str
    content: bytes
