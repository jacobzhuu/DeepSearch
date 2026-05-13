from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy.orm import Session

from packages.db.models import CandidateUrl, ResearchRun, ResearchTask, SearchQuery
from packages.db.repositories import (
    CandidateUrlRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    FetchJobRepository,
    ResearchRunRepository,
    SearchQueryRepository,
    TaskEventRepository,
)
from services.orchestrator.app.acquisition import HttpAcquisitionClient, SmokeAcquisitionClient
from services.orchestrator.app.services.acquisition import (
    ACQUISITION_FETCH_BATCH_SUMMARY_EVENT,
    FETCH_MODE_HTTP,
    FETCH_STATUS_FAILED,
    FETCH_STATUS_SUCCEEDED,
    AcquisitionConflictError,
    AcquisitionService,
    _sort_candidates_for_fetch,
    create_acquisition_service,
    fetch_priority_metadata,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore


class StaticResolver:
    def __init__(self, *addresses: str) -> None:
        self.addresses = addresses

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        del host, port
        return self.addresses


def test_smoke_acquisition_client_returns_synthetic_html_without_network() -> None:
    result = SmokeAcquisitionClient().fetch("https://deepsearch-smoke.local/opensearch/overview")

    assert result.http_status == 200
    assert result.error_code is None
    assert result.mime_type == "text/html"
    assert result.content is not None
    assert b"Synthetic development smoke source" in result.content
    assert b"OpenSearch is an open-source distributed search and analytics engine" in result.content
    assert result.trace["synthetic_fixture"] is True


def test_acquisition_defers_success_target_for_min_authoritative_snapshots(
    db_session: Session,
    tmp_path: Path,
) -> None:
    from hashlib import sha256
    from unittest.mock import MagicMock

    from services.orchestrator.app.acquisition.http_client import HttpFetchResult

    task, first = _seed_candidate(
        db_session,
        canonical_url="https://example.com/noise",
        query="authoritative defer task",
    )
    docs_candidate = _add_candidate(
        db_session,
        first,
        canonical_url="https://docs.langchain.com/langgraph",
        domain="docs.langchain.com",
        rank=2,
        title="Docs",
    )
    html_small = b"<html><body>ok</body></html>"
    digest = f"sha256:{sha256(html_small).hexdigest()}"

    def _fetch(url: str) -> HttpFetchResult:
        return HttpFetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            error_code=None,
            mime_type="text/html",
            content=html_small,
            content_hash=digest,
            trace={"final_url": url},
        )

    mock_client = MagicMock()
    mock_client.fetch.side_effect = _fetch

    service = create_acquisition_service(
        db_session,
        http_client=mock_client,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=10,
        min_successful_authoritative_snapshots=1,
    )
    result = service.acquire_candidates(
        task.id,
        candidate_url_ids=[first.id, docs_candidate.id],
        limit=10,
        target_successful_snapshots=1,
    )
    assert result.created == 2
    assert mock_client.fetch.call_count == 2


def test_acquisition_persists_snapshot_when_static_html_quality_hold(
    db_session: Session,
    tmp_path: Path,
) -> None:
    """Weak static HTML keeps bytes in object storage; trace blocks evidence parse."""
    from hashlib import sha256
    from unittest.mock import MagicMock

    from services.orchestrator.app.acquisition.http_client import HttpFetchResult

    task, candidate = _seed_candidate(db_session, canonical_url="https://spa.example/page")
    html = b"""<!doctype html><html><head><script>console.log(1)</script></head>
    <body><div id="app"></div><script src="/bundle.js"></script></body></html>"""
    mock_client = MagicMock()
    mock_client.fetch.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=html,
        content_hash=f"sha256:{sha256(html).hexdigest()}",
        trace={"final_url": candidate.canonical_url},
    )
    service = create_acquisition_service(
        db_session,
        http_client=mock_client,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
    )
    result = service.acquire_candidates(task.id, candidate_url_ids=[candidate.id], limit=1)
    assert result.succeeded == 1
    assert result.failed == 0
    entry = result.entries[0]
    assert entry.content_snapshot is not None
    assert entry.fetch_job.status == FETCH_STATUS_SUCCEEDED
    assert entry.fetch_attempt.error_code is None
    assert entry.fetch_attempt.trace_json.get("eligible_for_evidence_parse") is False
    assert entry.fetch_attempt.trace_json.get("static_html_quality_decision") == "spa_shell"


def _create_acquisition_service(
    db_session: Session,
    *,
    transport: httpx.BaseTransport,
    snapshot_root: Path,
    resolver: StaticResolver,
) -> AcquisitionService:
    http_client = HttpAcquisitionClient(
        timeout_seconds=5.0,
        max_redirects=3,
        max_response_bytes=1024,
        user_agent="deepresearch-tests/1.0",
        resolver=resolver,
        client=httpx.Client(transport=transport, trust_env=False),
    )
    return create_acquisition_service(
        db_session,
        http_client=http_client,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(snapshot_root)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
    )


def _seed_candidate(
    db_session: Session,
    *,
    query: str = "Acquisition service task",
    canonical_url: str = "https://example.com/report",
) -> tuple[ResearchTask, CandidateUrl]:
    task = create_research_task_service(db_session).create_task(query=query, constraints={})
    run = ResearchRunRepository(db_session).add(
        ResearchRun(
            task_id=task.id,
            round_no=1,
            current_state="PLANNED",
            checkpoint_json={"task_revision_no": 1},
        )
    )
    search_query = SearchQueryRepository(db_session).add(
        SearchQuery(
            task_id=task.id,
            run_id=run.id,
            query_text=query,
            provider="searxng",
            round_no=1,
            issued_at=datetime(2026, 4, 23, 12, 0, tzinfo=UTC),
            raw_response_json={"task_revision_no": 1},
        )
    )
    candidate_url = CandidateUrlRepository(db_session).add(
        CandidateUrl(
            task_id=task.id,
            search_query_id=search_query.id,
            original_url=canonical_url,
            canonical_url=canonical_url,
            domain="example.com" if "example.com" in canonical_url else "blocked.example",
            title="Example source",
            rank=1,
            selected=False,
            metadata_json={},
        )
    )
    db_session.commit()
    return task, candidate_url


def _add_candidate(
    db_session: Session,
    first_candidate: CandidateUrl,
    *,
    canonical_url: str,
    domain: str,
    rank: int,
    title: str = "Example source",
    metadata_json: dict[str, object] | None = None,
) -> CandidateUrl:
    candidate_url = CandidateUrlRepository(db_session).add(
        CandidateUrl(
            task_id=first_candidate.task_id,
            search_query_id=first_candidate.search_query_id,
            original_url=canonical_url,
            canonical_url=canonical_url,
            domain=domain,
            title=title,
            rank=rank,
            selected=False,
            metadata_json=metadata_json or {},
        )
    )
    db_session.commit()
    return candidate_url


def test_acquisition_service_creates_job_attempt_and_snapshot_for_success(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate_url = _seed_candidate(db_session)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html>service success</html>",
            request=request,
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    result = service.acquire_candidates(task.id, candidate_url_ids=[candidate_url.id], limit=1)
    fetch_jobs = FetchJobRepository(db_session).list_for_task(task.id)
    fetch_attempts = FetchAttemptRepository(db_session).list_for_task(task.id)
    content_snapshots = ContentSnapshotRepository(db_session).list_for_task(task.id)

    assert result.created == 1
    assert result.succeeded == 1
    assert result.failed == 0
    assert len(fetch_jobs) == 1
    assert fetch_jobs[0].mode == FETCH_MODE_HTTP
    assert fetch_jobs[0].status == FETCH_STATUS_SUCCEEDED
    assert len(fetch_attempts) == 1
    assert fetch_attempts[0].http_status == 200
    assert len(content_snapshots) == 1
    assert content_snapshots[0].mime_type == "text/html"
    assert content_snapshots[0].content_hash.startswith("sha256:")
    assert (tmp_path / "snapshots" / Path(content_snapshots[0].storage_key)).exists()


def test_acquisition_service_skips_existing_jobs_and_advances_to_next_candidate(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, first_candidate = _seed_candidate(
        db_session,
        query="First candidate task",
        canonical_url="https://example.com/first",
    )
    second_candidate = CandidateUrlRepository(db_session).add(
        CandidateUrl(
            task_id=task.id,
            search_query_id=first_candidate.search_query_id,
            original_url="https://example.com/second",
            canonical_url="https://example.com/second",
            domain="example.com",
            title="Second example source",
            rank=2,
            selected=False,
            metadata_json={},
        )
    )
    db_session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=f"body:{request.url.path}".encode(),
            request=request,
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    first_result = service.acquire_candidates(
        task.id,
        candidate_url_ids=[first_candidate.id],
        limit=1,
    )
    second_result = service.acquire_candidates(task.id, candidate_url_ids=None, limit=1)

    assert first_result.created == 1
    assert second_result.created == 1
    assert second_result.skipped_existing == 1
    assert second_result.entries[-1].candidate_url.id == second_candidate.id


def test_acquisition_service_prioritizes_raw_github_readme_over_repository_html(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, repo_candidate = _seed_candidate(
        db_session,
        query="How to deploy SearXNG with Docker?",
        canonical_url="https://github.com/searxng/searxng-docker",
    )
    repo_candidate.domain = "github.com"
    repo_candidate.title = "searxng/searxng-docker"
    raw_candidate = _add_candidate(
        db_session,
        repo_candidate,
        canonical_url="https://raw.githubusercontent.com/searxng/searxng-docker/master/README.md",
        domain="raw.githubusercontent.com",
        title="searxng-docker README.md",
        rank=0,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"This repository has been archived and superseded.",
            request=request,
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("185.199.108.133"),
    )

    result = service.acquire_candidates(
        task.id,
        candidate_url_ids=None,
        limit=1,
    )

    assert result.created == 1
    assert result.entries[0].candidate_url.id == raw_candidate.id


def test_acquisition_service_interleaves_authoritative_entities_for_comparison(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, langgraph_first = _seed_candidate(
        db_session,
        query="Compare LangGraph and AutoGen for multi-agent orchestration.",
        canonical_url="https://docs.langchain.com/oss/python/langgraph/overview",
    )
    langgraph_first.domain = "docs.langchain.com"
    langgraph_first.title = "LangGraph overview"
    langgraph_first.metadata_json = {
        "known_path_candidate": True,
        "candidate_source": "authoritative_source_resolver",
        "known_source_entity": "LangGraph",
    }
    _add_candidate(
        db_session,
        langgraph_first,
        canonical_url="https://docs.langchain.com/oss/javascript/langgraph/overview",
        domain="docs.langchain.com",
        title="LangGraph JavaScript overview",
        rank=11,
        metadata_json={
            "known_path_candidate": True,
            "candidate_source": "authoritative_source_resolver",
            "known_source_entity": "LangGraph",
        },
    )
    autogen_first = _add_candidate(
        db_session,
        langgraph_first,
        canonical_url="https://microsoft.github.io/autogen/stable/",
        domain="microsoft.github.io",
        title="AutoGen documentation",
        rank=10,
        metadata_json={
            "known_path_candidate": True,
            "candidate_source": "authoritative_source_resolver",
            "known_source_entity": "AutoGen",
        },
    )

    requested_hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host or "")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=f"<html><body>{request.url}</body></html>".encode(),
            request=request,
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    result = service.acquire_candidates(
        task.id,
        candidate_url_ids=None,
        limit=2,
        target_successful_snapshots=2,
    )

    assert result.created == 2
    assert [entry.candidate_url.id for entry in result.entries] == [
        langgraph_first.id,
        autogen_first.id,
    ]
    assert requested_hosts == ["docs.langchain.com", "microsoft.github.io"]


def test_acquisition_service_continues_until_later_candidate_succeeds(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, first_candidate = _seed_candidate(
        db_session,
        query="Fallback acquisition task",
        canonical_url="https://example.com/source-1",
    )
    for rank in range(2, 6):
        _add_candidate(
            db_session,
            first_candidate,
            canonical_url=f"https://example.com/source-{rank}",
            domain="example.com",
            rank=rank,
        )

    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path != "/source-4":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><body>source four works</body></html>",
            request=request,
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    result = service.acquire_candidates(
        task.id,
        candidate_url_ids=None,
        limit=5,
        target_successful_snapshots=2,
    )

    assert requested_paths == ["/source-1", "/source-2", "/source-3", "/source-4", "/source-5"]
    assert result.created == 5
    assert result.succeeded == 1
    assert result.failed == 4
    assert result.unattempted_candidates == []
    assert len(ContentSnapshotRepository(db_session).list_for_task(task.id)) == 1


def test_acquisition_service_reports_unattempted_candidates_when_attempt_limit_is_hit(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, first_candidate = _seed_candidate(
        db_session,
        query="All acquisition candidates fail",
        canonical_url="https://example.com/source-1",
    )
    for rank in range(2, 6):
        _add_candidate(
            db_session,
            first_candidate,
            canonical_url=f"https://example.com/source-{rank}",
            domain="example.com",
            rank=rank,
        )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unreachable", request=request)

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    result = service.acquire_candidates(task.id, candidate_url_ids=None, limit=3)

    assert result.created == 3
    assert result.succeeded == 0
    assert result.failed == 3
    assert [candidate.rank for candidate in result.unattempted_candidates] == [4, 5]
    assert len(FetchAttemptRepository(db_session).list_for_task(task.id)) == 3
    assert ContentSnapshotRepository(db_session).list_for_task(task.id) == []


def test_acquisition_service_prioritizes_stable_html_sources_for_fetch(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, github_candidate = _seed_candidate(
        db_session,
        query="What is SearXNG and how does it work?",
        canonical_url="https://github.com/searxng/searxng",
    )
    github_candidate.domain = "github.com"
    github_candidate.title = "SearXNG repository"
    _add_candidate(
        db_session,
        github_candidate,
        canonical_url="https://www.reddit.com/r/degoogle/comments/example",
        domain="www.reddit.com",
        rank=2,
        title="SearXNG Reddit discussion",
    )
    _add_candidate(
        db_session,
        github_candidate,
        canonical_url="https://en.wikipedia.org/wiki/SearXNG",
        domain="en.wikipedia.org",
        rank=3,
        title="SearXNG - Wikipedia",
    )
    _add_candidate(
        db_session,
        github_candidate,
        canonical_url="https://searxng.org/",
        domain="searxng.org",
        rank=4,
        title="SearXNG",
    )
    _add_candidate(
        db_session,
        github_candidate,
        canonical_url="https://www.youtube.com/watch?v=SlqGDoXPazY",
        domain="www.youtube.com",
        rank=5,
        title="SearXNG video",
    )
    db_session.commit()

    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><body>ok</body></html>",
            request=request,
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    result = service.acquire_candidates(task.id, candidate_url_ids=None, limit=5)

    assert result.selected_candidates_from_search[0].canonical_url == (
        "https://github.com/searxng/searxng"
    )
    assert requested_urls[:3] == [
        "https://searxng.org/",
        "https://github.com/searxng/searxng",
        "https://en.wikipedia.org/wiki/SearXNG",
    ]
    assert requested_urls[-2:] == [
        "https://www.reddit.com/r/degoogle/comments/example",
        "https://www.youtube.com/watch?v=SlqGDoXPazY",
    ]


def test_acquisition_service_prioritizes_official_docs_over_social_and_repo_pages(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, github_candidate = _seed_candidate(
        db_session,
        query="What is SearXNG and how does it work?",
        canonical_url="https://github.com/searxng/searxng",
    )
    github_candidate.domain = "github.com"
    docs_candidate = _add_candidate(
        db_session,
        github_candidate,
        canonical_url="https://docs.searxng.org/",
        domain="docs.searxng.org",
        rank=2,
        title="SearXNG Documentation",
    )
    reddit_candidate = _add_candidate(
        db_session,
        github_candidate,
        canonical_url="https://www.reddit.com/r/degoogle/comments/example",
        domain="www.reddit.com",
        rank=3,
    )
    youtube_candidate = _add_candidate(
        db_session,
        github_candidate,
        canonical_url="https://www.youtube.com/watch?v=SlqGDoXPazY",
        domain="www.youtube.com",
        rank=4,
    )
    db_session.commit()

    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"ok", request=request
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    service.acquire_candidates(task.id, candidate_url_ids=None, limit=4)

    docs_metadata = fetch_priority_metadata(docs_candidate)
    reddit_metadata = fetch_priority_metadata(reddit_candidate)
    youtube_metadata = fetch_priority_metadata(youtube_candidate)
    github_metadata = fetch_priority_metadata(github_candidate)

    assert requested_urls[0] == "https://docs.searxng.org/"
    assert docs_metadata["fetch_priority_reason"] == "official_docs_reference"
    assert docs_metadata["source_category"] == "official_home"
    assert docs_metadata["source_quality_score"] > github_metadata["source_quality_score"]
    assert docs_metadata["source_quality_score"] > reddit_metadata["source_quality_score"]
    assert docs_metadata["source_quality_score"] > youtube_metadata["source_quality_score"]


def test_acquisition_service_prioritizes_langgraph_official_sources_before_tutorials(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, tutorial_candidate = _seed_candidate(
        db_session,
        query="What is LangGraph and how does it work?",
        canonical_url="https://www.geeksforgeeks.org/machine-learning/what-is-langgraph/",
    )
    tutorial_candidate.domain = "www.geeksforgeeks.org"
    tutorial_candidate.title = "What is LangGraph - GeeksForGeeks"
    docs_candidate = _add_candidate(
        db_session,
        tutorial_candidate,
        canonical_url="https://docs.langchain.com/oss/python/langgraph/overview",
        domain="docs.langchain.com",
        rank=2,
        title="LangGraph overview - Docs by LangChain",
    )
    reference_candidate = _add_candidate(
        db_session,
        tutorial_candidate,
        canonical_url="https://reference.langchain.com/python/langgraph",
        domain="reference.langchain.com",
        rank=3,
        title="langgraph - LangChain Reference Docs",
    )
    github_candidate = _add_candidate(
        db_session,
        tutorial_candidate,
        canonical_url="https://github.com/langchain-ai/langgraph",
        domain="github.com",
        rank=4,
        title="langchain-ai/langgraph: Build resilient language agents as graphs.",
    )
    third_party_github_candidate = _add_candidate(
        db_session,
        tutorial_candidate,
        canonical_url="https://github.com/datawhalechina/easy-langent",
        domain="github.com",
        rank=5,
        title="datawhalechina/easy-langent LangGraph tutorial",
    )
    low_value_candidate = _add_candidate(
        db_session,
        tutorial_candidate,
        canonical_url="https://www.freelancer.hk/job-search/langgraph/",
        domain="www.freelancer.hk",
        rank=6,
        title="LangGraph jobs",
    )
    unrelated_docs_candidate = _add_candidate(
        db_session,
        tutorial_candidate,
        canonical_url="https://docs.langchain.com/langsmith/data-storage-and-privacy",
        domain="docs.langchain.com",
        rank=7,
        title="Data storage and privacy - Docs by LangChain",
    )
    db_session.commit()

    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"ok", request=request
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    service.acquire_candidates(task.id, candidate_url_ids=None, limit=5)

    assert requested_urls[0] == docs_candidate.canonical_url
    assert {reference_candidate.canonical_url, github_candidate.canonical_url}.issubset(
        set(requested_urls[:3])
    )
    assert low_value_candidate.canonical_url not in requested_urls
    tutorial_metadata = fetch_priority_metadata(tutorial_candidate, query=task.query)
    official_github_metadata = fetch_priority_metadata(github_candidate, query=task.query)
    third_party_github_metadata = fetch_priority_metadata(
        third_party_github_candidate,
        query=task.query,
    )
    low_value_metadata = fetch_priority_metadata(low_value_candidate, query=task.query)
    unrelated_docs_metadata = fetch_priority_metadata(unrelated_docs_candidate, query=task.query)

    assert tutorial_metadata["source_category"] == "generic_article"
    assert official_github_metadata["source_category"] == "github_readme_or_repo"
    assert third_party_github_metadata["source_category"] == "secondary_reference"
    assert (
        official_github_metadata["fetch_priority_score"]
        < third_party_github_metadata["fetch_priority_score"]
    )
    assert low_value_metadata["source_category"] == "low_quality_or_blocked"
    assert unrelated_docs_metadata["downrank_reason"] == "off_subject_source_downranked_for_query"


def test_acquisition_service_prioritizes_searxng_about_and_wikipedia_over_admin_pages(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, architecture_candidate = _seed_candidate(
        db_session,
        query="What is SearXNG and how does it work?",
        canonical_url="https://docs.searxng.org/admin/architecture.html",
    )
    architecture_candidate.domain = "docs.searxng.org"
    architecture_candidate.title = "SearXNG architecture"
    about_candidate = _add_candidate(
        db_session,
        architecture_candidate,
        canonical_url="https://docs.searxng.org/user/about.html",
        domain="docs.searxng.org",
        rank=2,
        title="SearXNG about",
    )
    wikipedia_candidate = _add_candidate(
        db_session,
        architecture_candidate,
        canonical_url="https://en.wikipedia.org/wiki/SearXNG",
        domain="en.wikipedia.org",
        rank=3,
        title="SearXNG - Wikipedia",
    )
    home_candidate = _add_candidate(
        db_session,
        architecture_candidate,
        canonical_url="https://docs.searxng.org/",
        domain="docs.searxng.org",
        rank=4,
        title="SearXNG docs",
    )
    installation_candidate = _add_candidate(
        db_session,
        architecture_candidate,
        canonical_url="https://docs.searxng.org/admin/installation.html",
        domain="docs.searxng.org",
        rank=5,
        title="SearXNG installation",
    )
    db_session.commit()

    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"ok", request=request
        )

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    service.acquire_candidates(task.id, candidate_url_ids=None, limit=5)

    assert requested_urls[:5] == [
        about_candidate.canonical_url,
        home_candidate.canonical_url,
        architecture_candidate.canonical_url,
        wikipedia_candidate.canonical_url,
        installation_candidate.canonical_url,
    ]
    about_metadata = fetch_priority_metadata(about_candidate, query=task.query)
    wikipedia_metadata = fetch_priority_metadata(wikipedia_candidate, query=task.query)
    architecture_metadata = fetch_priority_metadata(architecture_candidate, query=task.query)
    assert about_metadata["source_intent"] == "official_about"
    assert about_metadata["source_role"] == "official_docs"
    assert wikipedia_metadata["source_intent"] == "wikipedia_reference"
    assert wikipedia_metadata["source_role"] == "high_quality_secondary_reference"
    assert architecture_metadata["source_intent"] == "official_architecture_admin"
    assert architecture_metadata["downrank_reason"] == (
        "architecture_page_downranked_for_overview_query"
    )
    assert about_metadata["fetch_priority_score"] < architecture_metadata["fetch_priority_score"]
    assert (
        wikipedia_metadata["fetch_priority_score"] < architecture_metadata["fetch_priority_score"]
    )


def test_acquisition_service_records_failed_attempt_without_snapshot_for_blocked_target(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate_url = _seed_candidate(
        db_session,
        canonical_url="http://blocked.example/internal",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("blocked target should not trigger a request")

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("127.0.0.1"),
    )

    result = service.acquire_candidates(task.id, candidate_url_ids=[candidate_url.id], limit=1)
    fetch_job = FetchJobRepository(db_session).list_for_task(task.id)[0]
    fetch_attempt = FetchAttemptRepository(db_session).list_for_task(task.id)[0]

    assert result.created == 1
    assert result.failed == 1
    assert fetch_job.status == FETCH_STATUS_FAILED
    assert fetch_attempt.error_code == "target_blocked"
    assert ContentSnapshotRepository(db_session).list_for_task(task.id) == []


def test_acquire_candidates_emits_fetch_batch_summary_task_event(
    db_session: Session,
    tmp_path: Path,
) -> None:
    from hashlib import sha256
    from unittest.mock import MagicMock

    from sqlalchemy import select

    from packages.db.models import TaskEvent
    from services.orchestrator.app.acquisition.http_client import HttpFetchResult

    task, c1 = _seed_candidate(db_session)
    c2 = _add_candidate(
        db_session,
        c1,
        canonical_url="https://other.example/doc",
        domain="other.example",
        rank=2,
    )
    html = b"<html>ok</html>"
    digest = f"sha256:{sha256(html).hexdigest()}"
    mock_client = MagicMock()

    def fetch_side_effect(url: str, **_kwargs: object) -> HttpFetchResult:
        return HttpFetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            error_code=None,
            mime_type="text/html",
            content=html,
            content_hash=digest,
            trace={"final_url": url},
        )

    mock_client.fetch.side_effect = fetch_side_effect
    service = create_acquisition_service(
        db_session,
        http_client=mock_client,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
        task_event_repository=TaskEventRepository(db_session),
    )
    service.acquire_candidates(task.id, candidate_url_ids=[c1.id, c2.id], limit=1)
    db_session.commit()
    rows = list(
        db_session.scalars(
            select(TaskEvent).where(
                TaskEvent.task_id == task.id,
                TaskEvent.event_type == ACQUISITION_FETCH_BATCH_SUMMARY_EVENT,
            )
        ).all()
    )
    assert len(rows) == 1
    payload = rows[0].payload_json or {}
    assert payload.get("stop_reason") == "fetch_budget_exhausted"
    assert str(c2.id) in (payload.get("unattempted_candidate_ids") or [])
    jobs = FetchJobRepository(db_session).list_for_task(task.id)
    assert len([j for j in jobs if j.mode == FETCH_MODE_HTTP]) == 1


def test_acquisition_service_rejects_paused_task(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate_url = _seed_candidate(db_session)
    create_research_task_service(db_session).pause_task(task.id)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok", request=request)

    service = _create_acquisition_service(
        db_session,
        transport=httpx.MockTransport(handler),
        snapshot_root=tmp_path,
        resolver=StaticResolver("93.184.216.34"),
    )

    with pytest.raises(AcquisitionConflictError):
        service.acquire_candidates(task.id, candidate_url_ids=[candidate_url.id], limit=1)


def test_raw_github_readme_url_in_success_hold_lane() -> None:
    from uuid import uuid4

    from services.orchestrator.app.acquisition.acquisition_priority import (
        candidate_high_priority_for_success_hold,
    )

    cand = CandidateUrl(
        id=uuid4(),
        task_id=uuid4(),
        search_query_id=uuid4(),
        original_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md",
        canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md",
        domain="raw.githubusercontent.com",
        title="README",
        rank=1,
        selected=False,
        metadata_json={},
    )
    assert candidate_high_priority_for_success_hold(cand) is True


def test_sort_technical_explanation_elevates_readme(db_session: Session) -> None:
    q = "What is LangGraph and how does it work?"
    task, first = _seed_candidate(
        db_session,
        query=q,
        canonical_url="https://example.com/seed",
    )
    md_doc: dict[str, object] = {"source_role": "official_docs", "fetch_priority_score": 0}
    docs = [
        _add_candidate(
            db_session,
            first,
            canonical_url=f"https://docs.langchain.com/page{i}",
            domain="docs.langchain.com",
            rank=i,
            metadata_json=md_doc,
        )
        for i in range(1, 6)
    ]
    readme_meta: dict[str, object] = {
        "official_repository_readme_derivative": True,
        "source_intent": "official_repository_readme",
        "source_role": "official_repository",
        "fetch_priority_score": 99,
    }
    readme_main = _add_candidate(
        db_session,
        first,
        canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md",
        domain="raw.githubusercontent.com",
        rank=9999,
        metadata_json=readme_meta,
    )
    readme_master = _add_candidate(
        db_session,
        first,
        canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/master/README.md",
        domain="raw.githubusercontent.com",
        rank=10000,
        metadata_json=readme_meta,
    )
    license_cand = _add_candidate(
        db_session,
        first,
        canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/LICENSE",
        domain="raw.githubusercontent.com",
        rank=3,
        metadata_json={"source_role": "official_repository"},
    )
    combined = docs + [readme_main, readme_master, license_cand]
    ordered, _skipped = _sort_candidates_for_fetch(combined, query=q, max_must_fetch_per_round=3)
    ids = [c.id for c in ordered]
    assert ids.index(readme_main.id) < ids.index(docs[0].id)
    assert ids.index(readme_master.id) < ids.index(docs[0].id)
    assert ids.index(license_cand.id) > ids.index(readme_main.id)


def _triage_must_fetch_metadata(*, fetch_priority: int = 80) -> dict[str, object]:
    return {
        "source_role": "official_docs",
        "fetch_priority_score": 0,
        "llm_source_triage_active": True,
        "llm_source_judge": {
            "output_judgment": {
                "triage_decision": "must_fetch",
                "fetch_priority": fetch_priority,
            },
        },
    }


def test_sort_technical_explanation_readme_before_must_fetch_tail(db_session: Session) -> None:
    """Long must_fetch tails must not push raw README derivatives past the fetch budget."""
    q = "What is LangGraph and how does it work?"
    _task, first = _seed_candidate(
        db_session,
        query=q,
        canonical_url="https://example.com/seed",
    )
    triage = _triage_must_fetch_metadata()
    docs = [
        _add_candidate(
            db_session,
            first,
            canonical_url=f"https://docs.langchain.com/page{i}",
            domain="docs.langchain.com",
            rank=i,
            metadata_json=triage,
        )
        for i in range(1, 6)
    ]
    readme_meta: dict[str, object] = {
        "official_repository_readme_derivative": True,
        "source_intent": "official_repository_readme",
        "source_role": "official_repository",
        "fetch_priority_score": 99,
    }
    readme_main = _add_candidate(
        db_session,
        first,
        canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md",
        domain="raw.githubusercontent.com",
        rank=9999,
        metadata_json=readme_meta,
    )
    readme_master = _add_candidate(
        db_session,
        first,
        canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/master/README.md",
        domain="raw.githubusercontent.com",
        rank=10000,
        metadata_json=readme_meta,
    )
    combined = docs + [readme_main, readme_master]
    ordered, _skipped = _sort_candidates_for_fetch(combined, query=q, max_must_fetch_per_round=3)
    ids = [c.id for c in ordered]
    fourth_doc = docs[3]
    assert ids.index(readme_main.id) < ids.index(fourth_doc.id)
    assert ids.index(readme_master.id) < ids.index(fourth_doc.id)


def test_elevate_readme_noop_for_deployment_query(db_session: Session) -> None:
    from services.orchestrator.app.services.acquisition import (
        _elevate_official_repository_readme_candidates_for_technical_explanation,
    )

    q = "How to deploy LangGraph with Docker?"
    _task, first = _seed_candidate(
        db_session,
        query=q,
        canonical_url="https://example.com/seed",
    )
    readme_meta: dict[str, object] = {
        "official_repository_readme_derivative": True,
        "source_intent": "official_repository_readme",
        "source_role": "official_repository",
    }
    readme = _add_candidate(
        db_session,
        first,
        canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md",
        domain="raw.githubusercontent.com",
        rank=1,
        metadata_json=readme_meta,
    )
    doc = _add_candidate(
        db_session,
        first,
        canonical_url="https://docs.langchain.com/oss/python/langgraph/overview",
        domain="docs.langchain.com",
        rank=2,
        metadata_json={"source_role": "official_docs"},
    )
    candidates = [readme, doc]
    out = _elevate_official_repository_readme_candidates_for_technical_explanation(
        candidates,
        query=q,
        max_must_fetch_per_round=3,
    )
    assert out is candidates


def test_acquire_attempts_raw_readme_under_success_target_without_high_priority_defer(
    db_session: Session,
    tmp_path: Path,
) -> None:
    from hashlib import sha256
    from unittest.mock import MagicMock

    from services.orchestrator.app.acquisition.http_client import HttpFetchResult

    q = "What is LangGraph and how does it work?"
    task, seed = _seed_candidate(db_session, query=q, canonical_url="https://example.com/seed")
    triage = _triage_must_fetch_metadata()
    for i in range(1, 4):
        _add_candidate(
            db_session,
            seed,
            canonical_url=f"https://docs.langchain.com/x{i}",
            domain="docs.langchain.com",
            rank=i,
            metadata_json=triage,
        )
    readme_md: dict[str, object] = {
        "official_repository_readme_derivative": True,
        "source_intent": "official_repository_readme",
        "source_role": "official_repository",
    }
    _add_candidate(
        db_session,
        seed,
        canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md",
        domain="raw.githubusercontent.com",
        rank=9999,
        metadata_json=readme_md,
    )
    md_small = b"# LangGraph\n\nbody " + b"x" * 50
    md_hash = f"sha256:{sha256(md_small).hexdigest()}"

    def _fetch(url: str) -> HttpFetchResult:
        if "raw.githubusercontent.com" in url:
            return HttpFetchResult(
                requested_url=url,
                final_url=url,
                http_status=200,
                error_code=None,
                mime_type="text/markdown",
                content=md_small,
                content_hash=md_hash,
                trace={"eligible_for_evidence_parse": True},
            )
        html = b"<html><body>ok</body></html>"
        return HttpFetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            error_code=None,
            mime_type="text/html",
            content=html,
            content_hash=f"sha256:{sha256(html).hexdigest()}",
            trace={"eligible_for_evidence_parse": True},
        )

    mock_client = MagicMock()
    mock_client.fetch.side_effect = _fetch

    service = create_acquisition_service(
        db_session,
        http_client=mock_client,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=12,
        defer_success_target_for_high_priority=False,
    )
    service.acquire_candidates(
        task.id,
        candidate_url_ids=None,
        limit=12,
        target_successful_snapshots=1,
    )
    fetched_urls = [str(c.args[0]) for c in mock_client.fetch.call_args_list]
    assert any("raw.githubusercontent.com" in u for u in fetched_urls)


def test_official_repository_readme_acquire_hold_requires_raw_domain() -> None:
    from uuid import uuid4

    from services.orchestrator.app.acquisition.acquisition_priority import (
        candidate_high_priority_for_success_hold,
        official_repository_readme_acquire_hold,
    )

    cand = CandidateUrl(
        id=uuid4(),
        task_id=uuid4(),
        search_query_id=uuid4(),
        original_url="https://github.com/langchain-ai/langgraph/issues/1",
        canonical_url="https://github.com/langchain-ai/langgraph/issues/1",
        domain="github.com",
        rank=1,
        selected=False,
        metadata_json={
            "official_repository_readme_derivative": True,
            "source_intent": "official_repository_readme",
        },
    )
    assert official_repository_readme_acquire_hold(cand) is False
    assert candidate_high_priority_for_success_hold(cand) is True


def test_acquire_attempts_raw_readme_under_success_target_with_deferral(
    db_session: Session,
    tmp_path: Path,
) -> None:
    from hashlib import sha256
    from unittest.mock import MagicMock

    from services.orchestrator.app.acquisition.http_client import HttpFetchResult

    q = "What is LangGraph and how does it work?"
    task, seed = _seed_candidate(db_session, query=q, canonical_url="https://example.com/seed")
    md_doc: dict[str, object] = {
        "source_role": "official_docs",
        "known_path_candidate": True,
        "fetch_priority_score": 0,
    }
    for i in range(1, 4):
        _add_candidate(
            db_session,
            seed,
            canonical_url=f"https://docs.langchain.com/x{i}",
            domain="docs.langchain.com",
            rank=i,
            metadata_json=md_doc,
        )
    readme_md: dict[str, object] = {
        "official_repository_readme_derivative": True,
        "source_intent": "official_repository_readme",
        "source_role": "official_repository",
    }
    _add_candidate(
        db_session,
        seed,
        canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md",
        domain="raw.githubusercontent.com",
        rank=9999,
        metadata_json=readme_md,
    )
    md_small = b"# LangGraph\n\nbody " + b"x" * 50
    md_hash = f"sha256:{sha256(md_small).hexdigest()}"

    def _fetch(url: str) -> HttpFetchResult:
        if "raw.githubusercontent.com" in url:
            return HttpFetchResult(
                requested_url=url,
                final_url=url,
                http_status=200,
                error_code=None,
                mime_type="text/markdown",
                content=md_small,
                content_hash=md_hash,
                trace={"eligible_for_evidence_parse": True},
            )
        html = b"<html><body>ok</body></html>"
        return HttpFetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            error_code=None,
            mime_type="text/html",
            content=html,
            content_hash=f"sha256:{sha256(html).hexdigest()}",
            trace={"eligible_for_evidence_parse": True},
        )

    mock_client = MagicMock()
    mock_client.fetch.side_effect = _fetch

    service = create_acquisition_service(
        db_session,
        http_client=mock_client,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=12,
        defer_success_target_for_high_priority=True,
    )
    service.acquire_candidates(
        task.id,
        candidate_url_ids=None,
        limit=12,
        target_successful_snapshots=1,
    )
    fetched_urls = [str(c.args[0]) for c in mock_client.fetch.call_args_list]
    assert any("raw.githubusercontent.com" in u for u in fetched_urls)
