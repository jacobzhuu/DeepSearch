from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


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

    def to_metadata(self) -> dict[str, object]:
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
    category = _source_category(canonical_url=canonical_url, domain=domain, title=title)
    score = source_intent_priority(category, query=query)
    if (
        category == "official_home"
        and not _is_definition_or_overview_query(query)
        and _is_docs_home(canonical_url=canonical_url, domain=domain, title=title)
    ):
        score = 5
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
            "wikipedia_reference",
            "github_readme_or_repo",
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
) -> dict[str, object]:
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
            "github_readme_or_repo": 10,
            "official_docs_reference": 12,
            "generic_article": 20,
            "official_architecture_admin": 40,
            "official_installation_admin": 42,
            "official_api_dev": 44,
            "forum_social_video": 90,
            "low_quality_or_blocked": 99,
        }.get(category, 50)

    if explicit_admin_or_setup:
        if category == "official_architecture_admin" and _query_asks_architecture(query):
            return 0
        if category == "official_installation_admin" and _query_asks_installation(query):
            return 0
        if category == "official_api_dev" and _query_asks_api_or_developer(query):
            return 0

    return {
        "official_about": 5,
        "official_docs_reference": 5,
        "official_home": 10,
        "wikipedia_reference": 20,
        "generic_article": 50,
        "github_readme_or_repo": 80,
        "forum_social_video": 90,
        "low_quality_or_blocked": 99,
    }.get(category, 50)


def _source_category(*, canonical_url: str, domain: str | None, title: str | None) -> str:
    normalized_domain = (domain or "").strip().lower().removeprefix("www.")
    parsed = urlsplit(canonical_url or "")
    path = parsed.path.strip().lower().rstrip("/")
    normalized_title = (title or "").strip().lower()

    if not normalized_domain or path in {"/404", "/403"}:
        return "low_quality_or_blocked"
    if _is_social_video_or_forum_domain(normalized_domain):
        return "forum_social_video"
    if normalized_domain.endswith("wikipedia.org") and path.startswith("/wiki/"):
        return "wikipedia_reference"
    if normalized_domain == "github.com" and _looks_like_github_project_path(
        path,
        normalized_title,
    ):
        return "github_readme_or_repo"
    if _is_project_homepage(domain=normalized_domain, path=path):
        return "official_home"
    if _looks_like_installation_path(path):
        return "official_installation_admin"
    if _looks_like_architecture_path(path):
        return "official_architecture_admin"
    if _looks_like_api_or_developer_path(
        domain=normalized_domain,
        path=path,
        title=normalized_title,
    ):
        return "official_api_dev"
    if _looks_like_about_path(path, normalized_title):
        return "official_about"
    if _is_docs_like(domain=normalized_domain, path=path, title=normalized_title):
        return "official_docs_reference"
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


def _fetch_priority_reason(score: int) -> str:
    if score == 0:
        return "official_docs"
    if score == 1:
        return "wikipedia_article"
    if score == 2:
        return "project_homepage"
    if score in {5, 12}:
        return "official_docs_reference"
    if score == 10:
        return "project_homepage"
    if score == 20:
        return "wikipedia_or_generic_article"
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
    if score in {5, 10, 12}:
        return 0.72
    if score == 20:
        return 0.6
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
    if source_category == "official_about":
        return "source_selection_guardrail: official about page prioritized for overview query"
    if source_category == "wikipedia_reference":
        return "source_selection_guardrail: wikipedia reference allowed for overview query"
    if source_category == "official_home":
        return "source_selection_guardrail: official home page retained as overview source"
    if source_category == "github_readme_or_repo":
        return "source_selection_guardrail: upstream repository kept behind about/reference pages"
    if source_category == "official_docs_reference":
        return "source_selection_guardrail: official reference docs kept behind about/home pages"
    if source_category == "official_architecture_admin" and score >= 40:
        return "source_selection_guardrail: admin architecture page demoted unless requested"
    if source_category == "official_installation_admin" and score >= 40:
        return "source_selection_guardrail: installation page demoted unless requested"
    if source_category == "official_api_dev" and score >= 40:
        return "source_selection_guardrail: API/developer page demoted unless requested"
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
        "wikipedia_reference",
        "github_readme_or_repo",
    }:
        return "source_quality"
    return "search_rank"


def _downrank_reason_for_source_category(source_category: str, score: int) -> str | None:
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
    if domain.startswith("docs.") or domain.startswith("documentation."):
        return True
    docs_markers = (
        "/docs",
        "/doc/",
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
