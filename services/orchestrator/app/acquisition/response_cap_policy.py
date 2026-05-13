"""Trusted-docs and global HTTP response size caps (post-SSRF validation)."""

from __future__ import annotations

from typing import Any


def parse_trusted_docs_domain_allowlist(raw: str) -> frozenset[str]:
    parts: list[str] = []
    for segment in (raw or "").replace(";", ",").split(","):
        host = segment.strip().lower().rstrip(".")
        if host:
            parts.append(host)
    return frozenset(parts)


def effective_response_byte_cap(
    *,
    host: str | None,
    global_max_response_bytes: int,
    trusted_domains: frozenset[str],
    trusted_max_response_bytes: int | None,
) -> tuple[int, dict[str, Any]]:
    """
    Return (effective_max_bytes, trace_fragment) after scheme/host validation.

    Trusted cap applies only when ``trusted_max_response_bytes`` is set and is
    strictly greater than the global cap, and the host matches the allowlist.
    """
    global_max = max(1, int(global_max_response_bytes))
    normalized = (host or "").strip().lower().rstrip(".")
    trusted_max = (
        int(trusted_max_response_bytes)
        if trusted_max_response_bytes is not None
        else global_max
    )
    base_trace: dict[str, Any] = {
        "response_cap_source": "global",
        "response_cap_domain": normalized or None,
        "global_max_response_bytes": global_max,
        "effective_max_response_bytes": global_max,
        "cap_decision": "global_default",
    }
    if not trusted_domains or trusted_max <= global_max:
        return global_max, base_trace

    if normalized and normalized in trusted_domains:
        return trusted_max, {
            "response_cap_source": "trusted_docs_allowlist",
            "response_cap_domain": normalized,
            "global_max_response_bytes": global_max,
            "effective_max_response_bytes": trusted_max,
            "cap_decision": "trusted_docs_elevated_cap",
        }

    return global_max, {
        **base_trace,
        "cap_decision": "global_not_allowlisted_host",
    }
