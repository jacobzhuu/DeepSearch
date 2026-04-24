from __future__ import annotations

from dataclasses import dataclass
from posixpath import normpath
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
}


@dataclass(frozen=True)
class CanonicalUrl:
    original_url: str
    canonical_url: str
    domain: str


def canonicalize_url(raw_url: str) -> CanonicalUrl | None:
    stripped = raw_url.strip()
    if not stripped:
        return None

    parsed = urlsplit(stripped)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if parsed.hostname is None:
        return None

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    port = parsed.port
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        netloc = hostname
    else:
        netloc = f"{hostname}:{port}"

    path = parsed.path or "/"
    had_trailing_slash = path.endswith("/")
    normalized_path = normpath(path)
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    if normalized_path == "/.":
        normalized_path = "/"
    if had_trailing_slash and normalized_path != "/":
        normalized_path = f"{normalized_path}/"

    filtered_query_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    normalized_query = urlencode(sorted(filtered_query_params), doseq=True)

    canonical_url = urlunsplit((scheme, netloc, normalized_path, normalized_query, ""))
    return CanonicalUrl(
        original_url=stripped,
        canonical_url=canonical_url,
        domain=hostname,
    )


def is_domain_allowed(
    domain: str,
    *,
    allow_domains: tuple[str, ...],
    deny_domains: tuple[str, ...],
) -> bool:
    normalized_domain = _normalize_domain(domain)
    normalized_allow = tuple(_normalize_domain(item) for item in allow_domains if item.strip())
    normalized_deny = tuple(_normalize_domain(item) for item in deny_domains if item.strip())

    if any(_domain_matches(normalized_domain, denied) for denied in normalized_deny):
        return False
    if normalized_allow and not any(
        _domain_matches(normalized_domain, allowed) for allowed in normalized_allow
    ):
        return False
    return True


def _normalize_domain(domain: str) -> str:
    return domain.strip().lower().lstrip(".")


def _domain_matches(domain: str, rule: str) -> bool:
    return domain == rule or domain.endswith(f".{rule}")
