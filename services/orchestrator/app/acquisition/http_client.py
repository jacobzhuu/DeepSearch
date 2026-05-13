from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from services.orchestrator.app.acquisition.failure_classification import (
    classify_http_response,
    classify_httpx_request_error,
)
from services.orchestrator.app.acquisition.response_cap_policy import effective_response_byte_cap

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


@dataclass(frozen=True)
class _TargetValidationResult:
    resolved_ips: tuple[str, ...]
    allowed_ips: tuple[str, ...]
    blocked_ips: tuple[str, ...]
    decision_reason: str
    warning: str | None = None

    def to_trace(self) -> dict[str, Any]:
        trace: dict[str, Any] = {
            "resolved_ips": list(self.resolved_ips),
            "allowed_ips": list(self.allowed_ips),
            "blocked_ips": list(self.blocked_ips),
            "decision_reason": self.decision_reason,
        }
        if self.warning:
            trace["safety_warning"] = self.warning
        return trace


class HttpAcquisitionClient:
    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_redirects: int,
        max_response_bytes: int,
        user_agent: str,
        accept_language: str = "en-US,en;q=0.9",
        resolver: HostResolver | None = None,
        client: httpx.Client | None = None,
        trust_env_proxy: bool = False,
        trusted_docs_domains: frozenset[str] | None = None,
        trusted_docs_max_response_bytes: int | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects
        self.max_response_bytes = max(1, int(max_response_bytes))
        self.user_agent = user_agent
        self.accept_language = accept_language
        self.resolver = resolver or SocketHostResolver()
        self.client = client
        self.trust_env_proxy = trust_env_proxy
        self._trusted_docs_domains = trusted_docs_domains or frozenset()
        self._trusted_docs_max_response_bytes = trusted_docs_max_response_bytes

    def validate_fetch_target(self, url: str) -> dict[str, Any]:
        """
        SSRF / scheme guard for a single URL (initial navigation target).

        Used by browser acquisition to mirror static ``HttpAcquisitionClient`` policy before
        ``page.goto``. Redirect targets inside the page are not re-validated here.
        """
        return self._validate_target_url(url).to_trace()

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
                trace=_merge_trace(_proxy_trace_for_url(url, self.trust_env_proxy), error.trace),
            )
        except httpx.RequestError as error:
            classified = classify_httpx_request_error(error)
            return HttpFetchResult(
                requested_url=url,
                final_url=None,
                http_status=None,
                error_code=classified,
                mime_type=None,
                content=None,
                content_hash=None,
                trace=_merge_trace(
                    _request_error_trace(
                        url=url,
                        error=error,
                        trust_env_proxy=self.trust_env_proxy,
                    ),
                    {"transport_error_class": classified},
                ),
            )

    def _fetch_with_redirects(self, url: str) -> HttpFetchResult:
        current_url = url
        redirect_chain: list[dict[str, Any]] = []

        for redirect_count in range(self.max_redirects + 1):
            target_validation = self._validate_target_url(current_url)
            try:
                response_data = self._perform_request(current_url)
            except AcquisitionPolicyError as error:
                raise AcquisitionPolicyError(
                    error_code=error.error_code,
                    http_status=error.http_status,
                    trace=_merge_trace(
                        {
                            "requested_url": url,
                            "final_url": current_url,
                            **_proxy_trace_for_url(current_url, self.trust_env_proxy),
                            **target_validation.to_trace(),
                        },
                        error.trace,
                    ),
                ) from error
            except httpx.RequestError as error:
                classified = classify_httpx_request_error(error)
                raise AcquisitionPolicyError(
                    error_code=classified,
                    trace=_merge_trace(
                        {
                            "requested_url": url,
                            "final_url": current_url,
                            **_proxy_trace_for_url(current_url, self.trust_env_proxy),
                            **target_validation.to_trace(),
                            "transport_error_class": classified,
                        },
                        _request_error_trace(
                            url=current_url,
                            error=error,
                            trust_env_proxy=self.trust_env_proxy,
                        ),
                    ),
                ) from error
            location = response_data.headers.get("location")
            if location is not None and response_data.http_status in {301, 302, 303, 307, 308}:
                if redirect_count >= self.max_redirects:
                    raise AcquisitionPolicyError(
                        error_code="redirect_loop",
                        http_status=response_data.http_status,
                        trace={
                            "requested_url": url,
                            "final_url": response_data.final_url,
                            "redirect_chain": redirect_chain,
                            **_proxy_trace_for_url(current_url, self.trust_env_proxy),
                            **target_validation.to_trace(),
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
            stub_redirect_target = _extract_html_redirect_stub_target(response_data)
            if stub_redirect_target is not None:
                if redirect_count >= self.max_redirects:
                    raise AcquisitionPolicyError(
                        error_code="redirect_loop",
                        http_status=response_data.http_status,
                        trace={
                            "requested_url": url,
                            "final_url": response_data.final_url,
                            "redirect_chain": redirect_chain,
                            "redirect_stub_target": stub_redirect_target,
                            **_proxy_trace_for_url(current_url, self.trust_env_proxy),
                            **target_validation.to_trace(),
                        },
                    )
                next_url = urljoin(response_data.final_url, stub_redirect_target)
                redirect_chain.append(
                    {
                        "from_url": response_data.final_url,
                        "status_code": response_data.http_status,
                        "to_url": next_url,
                        "reason": "html_redirect_stub",
                    }
                )
                current_url = next_url
                continue

            error_code, strip_body = classify_http_response(
                http_status=response_data.http_status,
                mime_type=response_data.mime_type,
                body_len=len(response_data.content),
            )
            if error_code is not None and strip_body:
                content_out: bytes | None = None
                content_hash_out: str | None = None
                trace_extra: dict[str, Any] = {
                    "post_fetch_gate": error_code,
                    "gated_bytes": len(response_data.content),
                }
            elif error_code is not None:
                content_out = response_data.content
                content_hash_out = f"sha256:{hashlib.sha256(response_data.content).hexdigest()}"
                trace_extra = {}
            else:
                content_out = response_data.content
                content_hash_out = f"sha256:{hashlib.sha256(response_data.content).hexdigest()}"
                trace_extra = {}
            return HttpFetchResult(
                requested_url=url,
                final_url=response_data.final_url,
                http_status=response_data.http_status,
                error_code=error_code,
                mime_type=response_data.mime_type,
                content=content_out,
                content_hash=content_hash_out,
                trace={
                    "requested_url": url,
                    "final_url": response_data.final_url,
                    "redirect_chain": redirect_chain,
                    **_proxy_trace_for_url(current_url, self.trust_env_proxy),
                    **target_validation.to_trace(),
                    "response_bytes": len(response_data.content),
                    **(response_data.response_cap_policy or {}),
                    **trace_extra,
                },
            )

        raise AcquisitionPolicyError(
            error_code="redirect_loop",
            trace={"requested_url": url, "redirect_chain": redirect_chain},
        )

    def _perform_request(self, url: str) -> _HttpResponseData:
        parsed = urlsplit(url)
        host = parsed.hostname
        effective_max, cap_fragment = effective_response_byte_cap(
            host=host,
            global_max_response_bytes=self.max_response_bytes,
            trusted_domains=self._trusted_docs_domains,
            trusted_max_response_bytes=self._trusted_docs_max_response_bytes,
        )
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": self.accept_language,
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": self.user_agent,
        }
        if self.client is not None:
            return self._perform_request_with_client(
                self.client,
                url,
                headers,
                max_response_bytes=effective_max,
                response_cap_policy=cap_fragment,
            )

        with httpx.Client(
            follow_redirects=False,
            timeout=self.timeout_seconds,
            trust_env=self.trust_env_proxy,
        ) as client:
            return self._perform_request_with_client(
                client,
                url,
                headers,
                max_response_bytes=effective_max,
                response_cap_policy=cap_fragment,
            )

    def _perform_request_with_client(
        self,
        client: httpx.Client,
        url: str,
        headers: dict[str, str],
        *,
        max_response_bytes: int,
        response_cap_policy: dict[str, Any],
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
                    response_cap_policy=response_cap_policy,
                )

            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > max_response_bytes:
                    raise AcquisitionPolicyError(
                        error_code="body_too_large",
                        http_status=response.status_code,
                        trace=_merge_trace(
                            {
                                "final_url": str(response.url),
                                "response_bytes": len(content),
                                "max_response_bytes": max_response_bytes,
                            },
                            response_cap_policy,
                        ),
                    )

            return _HttpResponseData(
                final_url=str(response.url),
                http_status=response.status_code,
                headers=dict(response.headers),
                mime_type=_normalize_mime_type(response.headers.get("content-type")),
                content=bytes(content),
                response_cap_policy=response_cap_policy,
            )

    def _validate_target_url(self, url: str) -> _TargetValidationResult:
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
                    "decision_reason": "blocked_hostname",
                },
            )

        port = parsed.port or (443 if scheme == "https" else 80)
        resolved_ips = self._resolve_ips(normalized_host, port)
        allowed_ips = tuple(address for address in resolved_ips if not _is_blocked_ip(address))
        blocked_ips = tuple(address for address in resolved_ips if _is_blocked_ip(address))
        if not allowed_ips:
            raise AcquisitionPolicyError(
                error_code="target_blocked",
                trace={
                    "requested_url": url,
                    "host": normalized_host,
                    "resolved_ips": list(resolved_ips),
                    "allowed_ips": [],
                    "blocked_ips": list(blocked_ips),
                    "reason": "non_global_ip",
                    "decision_reason": "all_resolved_ips_non_global",
                },
            )

        if blocked_ips:
            return _TargetValidationResult(
                resolved_ips=resolved_ips,
                allowed_ips=allowed_ips,
                blocked_ips=blocked_ips,
                decision_reason="public_ip_present_with_non_global_dns_answers",
                warning=(
                    "DNS answers included non-global IPs, but at least one global IP was "
                    "available; continuing with SSRF guard metadata."
                ),
            )

        return _TargetValidationResult(
            resolved_ips=resolved_ips,
            allowed_ips=allowed_ips,
            blocked_ips=(),
            decision_reason="all_resolved_ips_global",
        )

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


def _extract_html_redirect_stub_target(response_data: _HttpResponseData) -> str | None:
    if response_data.http_status < 200 or response_data.http_status >= 300:
        return None
    if response_data.mime_type not in {"text/html", "text/plain"}:
        return None
    if len(response_data.content) > 2048:
        return None
    text = response_data.content.decode("utf-8", errors="replace")
    normalized = " ".join(text.split())
    lower = normalized.lower()
    markers = (
        "redirecting to ",
        "you are being redirected",
        "moved permanently",
        "click here if you are not redirected",
    )
    if not any(marker in lower for marker in markers):
        return None
    for pattern in (
        r"https?://[^\s<>'\")]+",
        r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]+content=[\"'][^\"']*url=([^\"'>]+)",
        r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is None:
            continue
        target = match.group(1) if match.lastindex else match.group(0)
        target = target.strip().rstrip(".,;")
        if target.startswith(("http://", "https://", "/")):
            return target
    return None


def _request_error_trace(
    *,
    url: str,
    error: httpx.RequestError,
    trust_env_proxy: bool = False,
) -> dict[str, Any]:
    request = _request_from_error(error)
    request_url = str(request.url) if request is not None else url
    return {
        "exception_type": type(error).__name__,
        "message": str(error),
        "requested_url": url,
        "final_url": request_url,
        **_proxy_trace_for_url(url, trust_env_proxy),
    }


def _proxy_trace_for_url(url: str, trust_env_proxy: bool = False) -> dict[str, Any]:
    proxy_url, env_var, no_proxy_matched = _proxy_env_for_url(url)
    proxy_enabled = trust_env_proxy and proxy_url is not None
    return {
        "proxy_enabled": proxy_enabled,
        "proxy_source": "env" if proxy_enabled else "none",
        "proxy_env_var": env_var,
        "proxy_url_masked": _mask_proxy_url(proxy_url) if proxy_url is not None else None,
        "proxy_env_detected": proxy_url is not None,
        "proxy_env_trusted": trust_env_proxy,
        "no_proxy_matched": no_proxy_matched,
    }


def _request_from_error(error: httpx.RequestError) -> httpx.Request | None:
    try:
        return error.request
    except RuntimeError:
        return None


def _proxy_env_for_url(url: str) -> tuple[str | None, str | None, bool]:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    no_proxy = _env_value("NO_PROXY") or _env_value("no_proxy")
    if _host_matches_no_proxy(host, no_proxy):
        return None, None, True

    scheme = parsed.scheme.lower()
    env_names = [f"{scheme.upper()}_PROXY", f"{scheme}_proxy", "ALL_PROXY", "all_proxy"]
    for env_name in env_names:
        value = _env_value(env_name)
        if value:
            return value, env_name, False
    return None, None, False


def _env_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _host_matches_no_proxy(host: str, no_proxy: str | None) -> bool:
    if not host or not no_proxy:
        return False
    normalized_host = host.rstrip(".").lower()
    for raw_entry in no_proxy.split(","):
        entry = raw_entry.strip().lower()
        if not entry:
            continue
        if entry == "*":
            return True
        if entry.startswith(".") and normalized_host.endswith(entry):
            return True
        if normalized_host == entry or normalized_host.endswith(f".{entry}"):
            return True
    return False


def _mask_proxy_url(proxy_url: str) -> str:
    parsed = urlsplit(proxy_url)
    if parsed.username is None and parsed.password is None:
        return proxy_url
    hostname = parsed.hostname or ""
    host = hostname
    if ":" in hostname and not hostname.startswith("["):
        host = f"[{hostname}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit(
        (
            parsed.scheme,
            f"***:***@{host}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def _merge_trace(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(patch)
    return merged


@dataclass(frozen=True)
class _HttpResponseData:
    final_url: str
    http_status: int
    headers: dict[str, str]
    mime_type: str
    content: bytes
    response_cap_policy: dict[str, Any] | None = None
