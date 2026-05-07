from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, TypedDict
from urllib.parse import urlsplit

_SUBJECT_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_COMPACT_PATTERN = re.compile(r"[^a-z0-9]+")
_OFF_SUBJECT_PRIORITY_SCORE = 35
_SECONDARY_REFERENCE_PRIORITY_SCORE = 30
_SUBJECT_SENSITIVE_SOURCE_CATEGORIES = frozenset(
    {
        "official_about",
        "official_home",
        "official_docs_reference",
        "official_repository",
        "wikipedia_reference",
        "github_readme_or_repo",
    }
)


class _ProjectOwnershipProfile(TypedDict):
    owned_domains: tuple[str, ...]
    github_repos: tuple[tuple[str, str], ...]
    secondary_domains: tuple[str, ...]


_PROJECT_OWNERSHIP: dict[str, _ProjectOwnershipProfile] = {
    "langgraph": {
        "owned_domains": ("langchain.com",),
        "github_repos": (("langchain-ai", "langgraph"),),
        "secondary_domains": (
            "github.langchain.ac.cn",
            "langchain-doc.cn",
            "langgraph.com.cn",
        ),
    },
    "langchain": {
        "owned_domains": ("langchain.com",),
        "github_repos": (("langchain-ai", "langchain"),),
        "secondary_domains": ("github.langchain.ac.cn", "langchain-doc.cn"),
    },
    "langsmith": {
        "owned_domains": ("langchain.com",),
        "github_repos": (("langchain-ai", "langsmith-sdk"),),
        "secondary_domains": ("github.langchain.ac.cn", "langchain-doc.cn"),
    },
    "searxng": {
        "owned_domains": ("searxng.org",),
        "github_repos": (("searxng", "searxng"), ("searxng", "searxng-docker")),
        "secondary_domains": (),
    },
    "fastapi": {
        "owned_domains": ("fastapi.tiangolo.com", "tiangolo.com"),
        "github_repos": (("fastapi", "fastapi"),),
        "secondary_domains": (),
    },
    "pytorch": {
        "owned_domains": ("pytorch.org",),
        "github_repos": (("pytorch", "pytorch"),),
        "secondary_domains": (),
    },
    "kubernetes": {
        "owned_domains": ("kubernetes.io",),
        "github_repos": (("kubernetes", "kubernetes"),),
        "secondary_domains": (),
    },
    "autogen": {
        "owned_domains": ("microsoft.github.io",),
        "github_repos": (("microsoft", "autogen"),),
        "secondary_domains": (),
    },
    "lora": {
        "owned_domains": ("huggingface.co",),
        "github_repos": (),
        "secondary_domains": (),
    },
    "opensearch": {
        "owned_domains": ("opensearch.org",),
        "github_repos": (("opensearch-project", "opensearch"),),
        "secondary_domains": (),
    },
    "dify": {
        "owned_domains": ("dify.ai",),
        "github_repos": (("langgenius", "dify"),),
        "secondary_domains": (),
    },
    "mcp": {
        "owned_domains": ("modelcontextprotocol.io",),
        "github_repos": (("modelcontextprotocol", "servers"),),
        "secondary_domains": (),
    },
}


@dataclass(frozen=True)
class SourceIntentClassification:
    source_category: str
    source_intent: str
    fetch_priority_score: int
    fetch_priority_reason: str
    source_quality_score: float
    source_selection_reason: str
    selected_reason: str
    selected_by: str
    downrank_reason: str | None
    source_selection_guardrail_applied: bool
    known_path_candidate: bool

    def to_metadata(self) -> dict[str, Any]:
        return {
            "fetch_priority_score": self.fetch_priority_score,
            "fetch_priority_reason": self.fetch_priority_reason,
            "source_quality_score": self.source_quality_score,
            "source_category": self.source_category,
            "source_intent": self.source_intent,
            "source_selection_reason": self.source_selection_reason,
            "selected_reason": self.selected_reason,
            "selected_by": self.selected_by,
            "downrank_reason": self.downrank_reason,
            "known_path_candidate": self.known_path_candidate,
            "whether_known_path_candidate": self.known_path_candidate,
            "source_selection_guardrail_applied": self.source_selection_guardrail_applied,
        }


def classify_source_intent(
    *,
    canonical_url: str,
    domain: str | None,
    title: str | None,
    query: str | None = None,
    known_path_candidate: bool = False,
) -> SourceIntentClassification:
    category = _source_category(
        canonical_url=canonical_url,
        domain=domain,
        title=title,
        query=query,
    )
    score = source_intent_priority(category, query=query)
    if _should_downrank_off_subject_source(
        category=category,
        canonical_url=canonical_url,
        domain=domain,
        title=title,
        query=query,
    ):
        score = max(score, _OFF_SUBJECT_PRIORITY_SCORE)
    if (
        category == "official_home"
        and not _is_definition_or_overview_query(query)
        and _is_docs_home(canonical_url=canonical_url, domain=domain, title=title)
    ):
        score = 5
    if _is_langgraph_upstream_repo(canonical_url=canonical_url, domain=domain, title=title):
        score = min(score, 9)
    if _is_langgraph_product_page(canonical_url=canonical_url, domain=domain, title=title):
        score = max(score, 13)
    reason = _fetch_priority_reason(score)
    selected_reason = _selected_reason_for_source_category(category, score)
    return SourceIntentClassification(
        source_category=category,
        source_intent=category,
        fetch_priority_score=score,
        fetch_priority_reason=reason,
        source_quality_score=_source_quality_score_for_fetch_priority(score),
        source_selection_reason=selected_reason,
        selected_reason=selected_reason,
        selected_by=_selected_by(category, known_path_candidate),
        downrank_reason=_downrank_reason_for_source_category(category, score),
        source_selection_guardrail_applied=category
        in {
            "official_about",
            "official_home",
            "official_repository",
            "wikipedia_reference",
            "official_docs_reference",
            "github_readme_or_repo",
            "package_registry",
            "standards_or_academic",
            "secondary_reference",
            "official_architecture_admin",
            "official_installation_admin",
            "official_api_dev",
        },
        known_path_candidate=known_path_candidate,
    )


def source_intent_metadata(
    *,
    canonical_url: str,
    domain: str | None,
    title: str | None,
    query: str | None = None,
    known_path_candidate: bool = False,
) -> dict[str, Any]:
    return classify_source_intent(
        canonical_url=canonical_url,
        domain=domain,
        title=title,
        query=query,
        known_path_candidate=known_path_candidate,
    ).to_metadata()


def source_intent_priority(category: str, *, query: str | None = None) -> int:
    overview_query = _is_definition_or_overview_query(query)
    explicit_admin_or_setup = _query_explicitly_asks_admin_or_setup(query)

    if overview_query:
        return {
            "official_about": 0,
            "wikipedia_reference": 1,
            "official_home": 2,
            "official_docs_reference": 10,
            "official_repository": 11,
            "github_readme_or_repo": 12,
            "package_registry": 14,
            "standards_or_academic": 15,
            "generic_article": 20,
            "secondary_reference": _SECONDARY_REFERENCE_PRIORITY_SCORE,
            "official_architecture_admin": 40,
            "official_installation_admin": 42,
            "official_api_dev": 44,
            "forum_social_video": 90,
            "low_quality_or_blocked": 99,
        }.get(category, 50)

    if explicit_admin_or_setup:
        if category == "official_repository" and _query_asks_installation(query):
            return 0
        if category == "official_architecture_admin" and _query_asks_architecture(query):
            return 0
        if category == "official_installation_admin" and _query_asks_installation(query):
            return 0
        if category == "official_api_dev" and _query_asks_api_or_developer(query):
            return 0

    return {
        "official_about": 5,
        "official_docs_reference": 5,
        "official_repository": 5,
        "package_registry": 8,
        "standards_or_academic": 8,
        "official_home": 10,
        "wikipedia_reference": 20,
        "secondary_reference": 30,
        "generic_article": 50,
        "github_readme_or_repo": 80,
        "forum_social_video": 90,
        "low_quality_or_blocked": 99,
    }.get(category, 50)


def _source_category(
    *,
    canonical_url: str,
    domain: str | None,
    title: str | None,
    query: str | None,
) -> str:
    normalized_domain = (domain or "").strip().lower().removeprefix("www.")
    parsed = urlsplit(canonical_url or "")
    path = parsed.path.strip().lower().rstrip("/")
    normalized_title = (title or "").strip().lower()
    subject_terms = _query_subject_terms(query)
    official_context = _has_official_project_context(
        domain=normalized_domain,
        path=path,
        title=normalized_title,
        subject_terms=subject_terms,
    )

    if not normalized_domain or path in {"/404", "/403"}:
        return "low_quality_or_blocked"
    if _is_low_value_overview_result(
        domain=normalized_domain,
        path=path,
        title=normalized_title,
        query=query,
    ):
        return "low_quality_or_blocked"
    if _is_social_video_or_forum_domain(normalized_domain):
        return "forum_social_video"
    if normalized_domain.endswith("wikipedia.org") and path.startswith("/wiki/"):
        return "wikipedia_reference"
    if _is_package_registry_domain(normalized_domain):
        return "package_registry"
    if _is_standards_or_academic_domain(normalized_domain):
        return "standards_or_academic"
    if normalized_domain == "raw.githubusercontent.com":
        if _is_official_raw_deployment_repository_path(
            path=path,
            subject_terms=subject_terms,
            query=query,
        ):
            return "official_repository"
        if _is_official_github_project_path(path=path, subject_terms=subject_terms):
            return "github_readme_or_repo"
    if normalized_domain == "github.com":
        if _is_official_deployment_repository_path(
            path=path,
            subject_terms=subject_terms,
            query=query,
        ):
            return "official_repository"
        if _is_official_github_project_path(path=path, subject_terms=subject_terms):
            return "github_readme_or_repo"
        if _looks_like_github_project_path(path, normalized_title):
            return (
                "secondary_reference"
                if _candidate_mentions_subject(
                    domain=normalized_domain,
                    path=path,
                    title=normalized_title,
                    subject_terms=subject_terms,
                )
                else "generic_article"
            )
    if _is_secondary_project_reference_domain(normalized_domain, subject_terms):
        return "secondary_reference"
    if _is_project_homepage(domain=normalized_domain, path=path) and official_context:
        return "official_home"
    if _looks_like_installation_path(path) and official_context:
        return "official_installation_admin"
    if _looks_like_architecture_path(path) and official_context:
        return "official_architecture_admin"
    if official_context and _looks_like_api_or_developer_path(
        domain=normalized_domain,
        path=path,
        title=normalized_title,
    ):
        return "official_api_dev"
    if _looks_like_about_path(path, normalized_title) and official_context:
        return "official_about"
    if official_context and _is_docs_like(
        domain=normalized_domain, path=path, title=normalized_title
    ):
        return "official_docs_reference"
    if _looks_like_project_landing_path(path=path, title=normalized_title, query=query):
        return "official_about" if official_context else "generic_article"
    return "generic_article"


def _is_docs_home(*, canonical_url: str, domain: str | None, title: str | None) -> bool:
    normalized_domain = (domain or "").strip().lower().removeprefix("www.")
    path = urlsplit(canonical_url or "").path.strip().lower().rstrip("/")
    normalized_title = (title or "").strip().lower()
    return path in {"", "/"} and _is_docs_like(
        domain=normalized_domain,
        path=path,
        title=normalized_title,
    )


def _is_langgraph_product_page(
    *,
    canonical_url: str,
    domain: str | None,
    title: str | None,
) -> bool:
    normalized_domain = (domain or "").strip().lower().removeprefix("www.")
    path = urlsplit(canonical_url or "").path.strip().lower().rstrip("/")
    normalized_title = (title or "").strip().lower()
    return (
        normalized_domain == "langchain.com"
        and path == "/langgraph"
        and "langgraph" in normalized_title
    )


def _is_langgraph_upstream_repo(
    *,
    canonical_url: str,
    domain: str | None,
    title: str | None,
) -> bool:
    normalized_domain = (domain or "").strip().lower().removeprefix("www.")
    path = urlsplit(canonical_url or "").path.strip().lower().rstrip("/")
    normalized_title = (title or "").strip().lower()
    return (
        normalized_domain == "github.com"
        and path == "/langchain-ai/langgraph"
        and "langgraph" in normalized_title
    )


def _fetch_priority_reason(score: int) -> str:
    if score == 0:
        return "official_docs"
    if score == 1:
        return "wikipedia_article"
    if score == 2:
        return "project_homepage"
    if score in {5, 8, 11, 12, 14, 15}:
        return "official_docs_reference"
    if score == 10:
        return "project_homepage"
    if score == 9:
        return "github_repository_landing_page"
    if score == 20:
        return "wikipedia_or_generic_article"
    if score == _SECONDARY_REFERENCE_PRIORITY_SCORE:
        return "secondary_reference"
    if score == _OFF_SUBJECT_PRIORITY_SCORE:
        return "off_subject_source"
    if score in {40, 42, 44}:
        return "admin_or_setup_page_demoted"
    if score == 80:
        return "github_repository_landing_page"
    if score == 90:
        return "social_video_or_forum"
    if score == 99:
        return "low_quality_or_blocked"
    return "generic_web_page"


def _source_quality_score_for_fetch_priority(score: int) -> float:
    if score == 0:
        return 0.95
    if score == 1:
        return 0.78
    if score == 2:
        return 0.72
    if score in {5, 8, 10, 11, 12, 14, 15}:
        return 0.72
    if score == 20:
        return 0.6
    if score == _SECONDARY_REFERENCE_PRIORITY_SCORE:
        return 0.55
    if score == _OFF_SUBJECT_PRIORITY_SCORE:
        return 0.48
    if score in {40, 42, 44}:
        return 0.5
    if score == 80:
        return 0.45
    if score == 90:
        return 0.2
    if score == 99:
        return 0.1
    return 0.55


def _selected_reason_for_source_category(source_category: str, score: int) -> str:
    if score == _OFF_SUBJECT_PRIORITY_SCORE:
        return "source_selection_guardrail: source did not match the query subject and was demoted"
    if source_category == "official_about":
        return "source_selection_guardrail: official about page prioritized for overview query"
    if source_category == "wikipedia_reference":
        return "source_selection_guardrail: wikipedia reference allowed for overview query"
    if source_category == "official_home":
        return "source_selection_guardrail: official home page retained as overview source"
    if source_category == "official_repository":
        return "source_selection_guardrail: official repository prioritized for deployment query"
    if source_category == "github_readme_or_repo":
        return "source_selection_guardrail: upstream repository kept behind about/reference pages"
    if source_category == "official_docs_reference":
        return "source_selection_guardrail: official reference docs kept behind about/home pages"
    if source_category == "package_registry":
        return (
            "source_selection_guardrail: package registry retained as authoritative "
            "software metadata"
        )
    if source_category == "standards_or_academic":
        return (
            "source_selection_guardrail: standards or academic source retained as "
            "authoritative reference"
        )
    if source_category == "official_architecture_admin" and score >= 40:
        return "source_selection_guardrail: admin architecture page demoted unless requested"
    if source_category == "official_installation_admin" and score >= 40:
        return "source_selection_guardrail: installation page demoted unless requested"
    if source_category == "official_api_dev" and score >= 40:
        return "source_selection_guardrail: API/developer page demoted unless requested"
    if source_category == "secondary_reference":
        return "source_selection_guardrail: secondary project reference, not official-owned"
    if source_category == "forum_social_video":
        return "source_selection_guardrail: social/forum/video source lowest priority"
    return _fetch_priority_reason(score)


def _selected_by(source_category: str, known_path_candidate: bool) -> str:
    if known_path_candidate:
        return "planner_guardrail"
    if source_category in {
        "official_about",
        "official_home",
        "official_docs_reference",
        "official_repository",
        "wikipedia_reference",
        "github_readme_or_repo",
        "package_registry",
        "standards_or_academic",
    }:
        return "source_quality"
    return "search_rank"


def _downrank_reason_for_source_category(source_category: str, score: int) -> str | None:
    if score == _OFF_SUBJECT_PRIORITY_SCORE:
        return "off_subject_source_downranked_for_query"
    if source_category == "secondary_reference":
        return "secondary_reference_not_official_owned"
    if score < 40:
        return None
    if source_category == "official_architecture_admin":
        return "architecture_page_downranked_for_overview_query"
    if source_category == "official_installation_admin":
        return "installation_page_downranked_for_overview_query"
    if source_category == "official_api_dev":
        return "developer_or_api_page_downranked_for_overview_query"
    if source_category == "forum_social_video":
        return "forum_social_video_downranked_for_overview_query"
    if source_category == "low_quality_or_blocked":
        return "low_quality_source_downranked"
    return "lower_priority_source_for_overview_query"


def _is_definition_or_overview_query(query: str | None) -> bool:
    if query is None:
        return False
    lower = query.lower()
    return (
        "what is" in lower
        or "what are" in lower
        or "overview" in lower
        or "how does" in lower
        or "how do" in lower
    )


def _query_explicitly_asks_admin_or_setup(query: str | None) -> bool:
    return (
        _query_asks_architecture(query)
        or _query_asks_installation(query)
        or _query_asks_api_or_developer(query)
    )


def _query_asks_architecture(query: str | None) -> bool:
    if query is None:
        return False
    lower = query.lower()
    return any(term in lower for term in ("admin", "architecture", "deployment", "deploy"))


def _query_asks_installation(query: str | None) -> bool:
    if query is None:
        return False
    lower = query.lower()
    return any(
        term in lower for term in ("configure", "docker", "install", "installation", "setup")
    )


def _query_asks_api_or_developer(query: str | None) -> bool:
    if query is None:
        return False
    lower = query.lower()
    return any(term in lower for term in ("api", "developer", "development", "dev docs"))


def _looks_like_about_path(path: str, title: str) -> bool:
    path_markers = (
        "/about",
        "/overview",
        "/introduction",
        "/intro",
        "/concepts",
        "/learn",
        "/user/about",
    )
    title_markers = (" about", "overview", "introduction", "what is")
    return any(marker in path for marker in path_markers) or any(
        marker in f" {title}" for marker in title_markers
    )


def _looks_like_project_landing_path(*, path: str, title: str, query: str | None) -> bool:
    subject_terms = _query_subject_terms(query)
    if not subject_terms:
        return False
    normalized_path = path.strip("/").lower()
    compact_path = _compact(normalized_path)
    return any(
        normalized_path == term
        or normalized_path.endswith(f"/{term}")
        or f"/{term}/overview" in f"/{normalized_path}"
        or compact_path.endswith(term)
        for term in subject_terms
    )


def _looks_like_installation_path(path: str) -> bool:
    return any(
        marker in path
        for marker in (
            "/admin/installation",
            "/admin/install",
            "/installation",
            "/install",
            "/setup",
            "/docker",
            "/deployment",
            "/deploy",
        )
    )


def _looks_like_architecture_path(path: str) -> bool:
    return "architecture" in path or "/admin/" in path and "install" not in path


def _looks_like_api_or_developer_path(*, domain: str, path: str, title: str) -> bool:
    return _is_docs_like(domain=domain, path=path, title=title) and (
        path.startswith("/dev")
        or path.startswith("/api")
        or "/developer" in path
        or "/reference/api" in path
        or "developer documentation" in title
        or "api" in title
    )


def _looks_like_github_project_path(path: str, title: str) -> bool:
    path_parts = [part for part in path.split("/") if part]
    return len(path_parts) >= 2 or "readme" in path or "github" in title


def _is_docs_like(*, domain: str, path: str, title: str) -> bool:
    if domain.startswith(("docs.", "reference.", "documentation.")):
        return True
    docs_markers = (
        "/docs",
        "/documentation",
        "/guide",
        "/guides",
        "/manual",
        "/reference",
    )
    if any(marker in path for marker in docs_markers):
        return True
    return "documentation" in title or "docs" in title


def _is_project_homepage(*, domain: str, path: str) -> bool:
    if not domain or domain.endswith("wikipedia.org"):
        return False
    normalized_path = path.rstrip("/")
    return normalized_path in {"", "/"}


def _should_downrank_off_subject_source(
    *,
    category: str,
    canonical_url: str,
    domain: str | None,
    title: str | None,
    query: str | None,
) -> bool:
    subject_terms = _query_subject_terms(query)
    if not subject_terms:
        return False
    normalized_domain = (domain or "").strip().lower().removeprefix("www.")
    path = urlsplit(canonical_url or "").path.strip().lower().rstrip("/")
    normalized_title = (title or "").strip().lower()
    profiles = _project_profiles(subject_terms)
    if category == "generic_article" and profiles:
        return any(
            _domain_matches_owned_profile(normalized_domain, profile) for profile in profiles
        ) and not _candidate_mentions_subject(
            domain=normalized_domain,
            path=path,
            title=normalized_title,
            subject_terms=subject_terms,
        )
    if category not in _SUBJECT_SENSITIVE_SOURCE_CATEGORIES:
        return False
    return not _candidate_mentions_subject(
        domain=normalized_domain,
        path=path,
        title=normalized_title,
        subject_terms=subject_terms,
    )


def _has_official_project_context(
    *,
    domain: str,
    path: str,
    title: str,
    subject_terms: tuple[str, ...],
) -> bool:
    profiles = _project_profiles(subject_terms)
    if profiles:
        return any(
            _domain_matches_owned_profile(domain, profile)
            and _candidate_mentions_subject(
                domain=domain,
                path=path,
                title=title,
                subject_terms=subject_terms,
            )
            for profile in profiles
        )
    if not subject_terms:
        return _is_strict_docs_domain(domain) or _is_known_project_domain(domain)
    if _domain_matches_subject(domain, subject_terms):
        return True
    if _is_strict_docs_domain(domain) and _candidate_mentions_subject(
        domain=domain,
        path=path,
        title=title,
        subject_terms=subject_terms,
    ):
        return True
    return False


def _candidate_mentions_subject(
    *,
    domain: str,
    path: str,
    title: str,
    subject_terms: tuple[str, ...],
) -> bool:
    haystack = " ".join((domain, path, title))
    compact_haystack = _compact(haystack)
    return any(term in compact_haystack for term in subject_terms)


def _domain_matches_subject(domain: str, subject_terms: tuple[str, ...]) -> bool:
    compact_domain = _compact(domain)
    return any(term in compact_domain for term in subject_terms)


def _project_profile(subject_terms: tuple[str, ...]) -> _ProjectOwnershipProfile | None:
    profiles = _project_profiles(subject_terms)
    return profiles[0] if profiles else None


def _project_profiles(subject_terms: tuple[str, ...]) -> tuple[_ProjectOwnershipProfile, ...]:
    profiles: list[_ProjectOwnershipProfile] = []
    seen_ids: set[int] = set()
    for term in subject_terms:
        profile = _PROJECT_OWNERSHIP.get(term)
        if profile is None or id(profile) in seen_ids:
            continue
        profiles.append(profile)
        seen_ids.add(id(profile))
    return tuple(profiles)


def _domain_matches_owned_profile(domain: str, profile: _ProjectOwnershipProfile) -> bool:
    owned_domains = profile["owned_domains"]
    return any(domain == item or domain.endswith(f".{item}") for item in owned_domains)


def _is_secondary_project_reference_domain(
    domain: str,
    subject_terms: tuple[str, ...],
) -> bool:
    profiles = _project_profiles(subject_terms)
    if not profiles:
        return False
    return any(
        domain == item or domain.endswith(f".{item}")
        for profile in profiles
        for item in profile["secondary_domains"]
    )


def _is_official_github_project_path(
    *,
    path: str,
    subject_terms: tuple[str, ...],
) -> bool:
    profiles = _project_profiles(subject_terms)
    if not profiles:
        return False
    owner_repo = _github_owner_repo(path)
    if owner_repo is None:
        return False
    owner, repo = owner_repo
    compact_owner = _compact(owner)
    compact_repo = _compact(repo)
    return any(
        compact_owner == _compact(expected_owner) and compact_repo == _compact(expected_repo)
        for profile in profiles
        for expected_owner, expected_repo in profile["github_repos"]
    )


def _is_official_deployment_repository_path(
    *,
    path: str,
    subject_terms: tuple[str, ...],
    query: str | None,
) -> bool:
    if not _query_asks_installation(query):
        return False
    profiles = _project_profiles(subject_terms)
    if not profiles:
        return False
    owner_repo = _github_owner_repo(path)
    if owner_repo is None:
        return False
    owner, repo = owner_repo
    compact_owner = _compact(owner)
    compact_repo = _compact(repo)
    return any(
        compact_owner == _compact(expected_owner)
        and compact_repo == _compact(expected_repo)
        and any(marker in compact_repo for marker in ("docker", "container", "compose"))
        for profile in profiles
        for expected_owner, expected_repo in profile["github_repos"]
    )


def _is_official_raw_deployment_repository_path(
    *,
    path: str,
    subject_terms: tuple[str, ...],
    query: str | None,
) -> bool:
    if not _query_asks_installation(query):
        return False
    profiles = _project_profiles(subject_terms)
    if not profiles:
        return False
    raw_owner_repo = _github_raw_owner_repo(path)
    if raw_owner_repo is None:
        return False
    owner, repo = raw_owner_repo
    compact_owner = _compact(owner)
    compact_repo = _compact(repo)
    return any(
        compact_owner == _compact(expected_owner)
        and compact_repo == _compact(expected_repo)
        and (
            any(marker in compact_repo for marker in ("docker", "container", "compose"))
            or _raw_path_looks_like_deployment_file(path)
        )
        for profile in profiles
        for expected_owner, expected_repo in profile["github_repos"]
    )


def _github_owner_repo(path: str) -> tuple[str, str] | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _github_raw_owner_repo(path: str) -> tuple[str, str] | None:
    parts = [part for part in path.strip("/").split("/") if part]
    if len(parts) < 4:
        return None
    return parts[0], parts[1]


def _raw_path_looks_like_deployment_file(path: str) -> bool:
    lower = path.lower()
    return any(
        marker in lower
        for marker in (
            "docker-compose",
            "compose.y",
            "/container/",
            ".env",
            "settings.yml",
            "limiter.toml",
            "readme.md",
        )
    )


def _is_strict_docs_domain(domain: str) -> bool:
    return domain.startswith(("docs.", "reference.", "documentation."))


def _is_known_project_domain(domain: str) -> bool:
    project_markers = (
        "searxng",
        "opensearch",
        "langchain",
        "langgraph",
        "fastapi",
        "pytorch",
        "kubernetes",
        "autogen",
        "dify",
        "modelcontextprotocol",
    )
    compact_domain = _compact(domain)
    return any(marker in compact_domain for marker in project_markers)


def _query_subject_terms(query: str | None) -> tuple[str, ...]:
    if query is None:
        return ()
    words = _SUBJECT_TOKEN_PATTERN.findall(query)
    if not words:
        return ()
    lowered = [word.lower() for word in words]
    low_value = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "compare",
        "does",
        "for",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "versus",
        "vs",
        "what",
        "with",
        "work",
        "works",
    }
    for index, token in enumerate(lowered[:-1]):
        if token == "what" and lowered[index + 1] in {"is", "are"}:
            for candidate in words[index + 2 : index + 6]:
                candidate_lower = _compact(candidate.lower())
                if candidate_lower and candidate_lower not in low_value:
                    return (candidate_lower,)
    proper_terms = [
        _compact(word.lower())
        for word in words
        if word[:1].isupper() and _compact(word.lower()) not in low_value
    ]
    return tuple(item for item in dict.fromkeys(proper_terms) if item)[:2]


def _compact(value: str) -> str:
    return _COMPACT_PATTERN.sub("", value.lower())


def _is_low_value_overview_result(
    *,
    domain: str,
    path: str,
    title: str,
    query: str | None,
) -> bool:
    if not _is_definition_or_overview_query(query):
        return False
    low_value_domains = (
        "freelancer.hk",
        "freelancer.com",
        "jobsdb.com",
        "jooble.org",
        "ctgoodjobs.hk",
        "adg.csdn.net",
    )
    if any(domain == item or domain.endswith(f".{item}") for item in low_value_domains):
        return True
    listing_markers = (
        "/job-search/",
        "/jobs/",
        "/job/",
        "/jdp/",
        "/freelance",
        "/thread-",
    )
    if any(marker in path for marker in listing_markers):
        return True
    lower_title = title.lower()
    return any(
        marker in lower_title
        for marker in (
            "工作,",
            "工作，",
            "雇佣",
            "jobs",
            "job ",
            "freelancer",
            "resource download",
            "资源下载",
        )
    )


def _is_social_video_or_forum_domain(domain: str) -> bool:
    social_video_forum_domains = (
        "reddit.com",
        "youtube.com",
        "youtu.be",
        "x.com",
        "twitter.com",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "medium.com",
        "news.ycombinator.com",
        "stackoverflow.com",
        "stackexchange.com",
        "quora.com",
    )
    return any(domain == item or domain.endswith(f".{item}") for item in social_video_forum_domains)


def _is_package_registry_domain(domain: str) -> bool:
    registries = (
        "pypi.org",
        "npmjs.com",
        "crates.io",
        "pkg.go.dev",
        "rubygems.org",
        "packagist.org",
        "mvnrepository.com",
        "repo1.maven.org",
    )
    return any(domain == item or domain.endswith(f".{item}") for item in registries)


def _is_standards_or_academic_domain(domain: str) -> bool:
    domains = (
        "arxiv.org",
        "doi.org",
        "ietf.org",
        "w3.org",
        "iso.org",
        "ieee.org",
        "acm.org",
        "springer.com",
        "nature.com",
        "sciencedirect.com",
        "semanticscholar.org",
    )
    return any(domain == item or domain.endswith(f".{item}") for item in domains)
