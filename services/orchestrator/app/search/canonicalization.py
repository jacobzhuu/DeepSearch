from __future__ import annotations

from dataclasses import dataclass
from posixpath import normpath
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

TRACKING_QUERY_KEYS = {
    "_hsenc",
    "_hsmi",
    "fbclid",
    "gclid",
    "ga_source",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref_src",
    "spm",
    "vero_id",
    "yclid",
}
REDIRECT_QUERY_KEYS = ("url", "u", "q", "target", "redirect_url", "redirect_uri")
REDIRECT_DOMAINS = {
    "duckduckgo.com",
    "www.duckduckgo.com",
    "google.com",
    "www.google.com",
    "bing.com",
    "www.bing.com",
}


@dataclass(frozen=True)
class CanonicalUrl:
    original_url: str
    canonical_url: str
    domain: str


def canonicalize_url(raw_url: str) -> CanonicalUrl | None:
    return _canonicalize_url(raw_url, depth=0, original_url=None)


def _canonicalize_url(
    raw_url: str,
    *,
    depth: int,
    original_url: str | None,
) -> CanonicalUrl | None:
    stripped = raw_url.strip()
    if not stripped:
        return None

    parsed = urlsplit(stripped)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if parsed.hostname is None:
        return None

    redirect_target = _redirect_target(parsed)
    if redirect_target is not None and depth < 2:
        target = _canonicalize_url(
            redirect_target,
            depth=depth + 1,
            original_url=original_url or stripped,
        )
        if target is not None:
            return target

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
        normalized_path = normalized_path.rstrip("/")

    filtered_query_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    normalized_query = urlencode(sorted(filtered_query_params), doseq=True)

    canonical_url = urlunsplit((scheme, netloc, normalized_path, normalized_query, ""))
    return CanonicalUrl(
        original_url=original_url or stripped,
        canonical_url=canonical_url,
        domain=hostname,
    )


def _redirect_target(parsed: object) -> str | None:
    hostname = getattr(parsed, "hostname", None)
    path = getattr(parsed, "path", "") or ""
    query = getattr(parsed, "query", "") or ""
    if hostname is None:
        return None
    normalized_hostname = hostname.lower()
    if normalized_hostname not in REDIRECT_DOMAINS:
        return None
    if path not in {"/url", "/l/", "/l"}:
        return None
    for key, value in parse_qsl(query, keep_blank_values=False):
        if key.lower() not in REDIRECT_QUERY_KEYS:
            continue
        decoded = unquote(value.strip())
        if decoded.startswith(("http://", "https://")):
            return decoded
    return None


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
