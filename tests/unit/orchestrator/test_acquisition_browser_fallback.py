from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session
from tests.unit.orchestrator.test_acquisition_service import _seed_candidate

from packages.db.repositories import (
    ContentSnapshotRepository,
    FetchJobRepository,
    TaskEventRepository,
)
from services.orchestrator.app.acquisition.http_client import HttpFetchResult
from services.orchestrator.app.services.acquisition import (
    ACQUISITION_BROWSER_FALLBACK_EVENT,
    FETCH_MODE_BROWSER_RENDERED,
    FETCH_MODE_HTTP,
    FETCH_STATUS_FAILED,
    create_acquisition_service,
)
from services.orchestrator.app.services.parsing import create_parsing_service
from services.orchestrator.app.services.research_tasks import PHASE2_ACTIVE_STATUS


def test_browser_fallback_second_snapshot_preserves_static_spa_shell(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate = _seed_candidate(db_session, canonical_url="https://spa.example/page")
    spa_html = b"""<!doctype html><html><head><script>x</script></head>
    <body><div id="app"></div><script src="/bundle.js"></script></body></html>"""
    rendered_html = b"""<!doctype html><html><head><title>Rendered</title></head>
    <body><main><h1>Article</h1><p>Substantive rendered body for evidence extraction.</p>
    <p>Second paragraph with more unique text for parsing.</p></main></body></html>"""

    mock_http = MagicMock()
    mock_http.fetch.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=spa_html,
        content_hash=f"sha256:{sha256(spa_html).hexdigest()}",
        trace={"final_url": candidate.canonical_url},
    )
    mock_browser = MagicMock()
    mock_browser.fetch_rendered.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=rendered_html,
        content_hash=f"sha256:{sha256(rendered_html).hexdigest()}",
        trace={
            "final_url": candidate.canonical_url,
            "browser_title": "Rendered",
            "acquisition_channel": "browser_playwright",
        },
    )

    from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

    service = create_acquisition_service(
        db_session,
        http_client=mock_http,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
        browser_fetch_backend_impl=mock_browser,
        browser_fetch_backend_setting="playwright",
    )
    result = service.acquire_candidates(task.id, candidate_url_ids=[candidate.id], limit=1)
    assert result.succeeded == 1
    entry = result.entries[0]
    assert entry.fetch_attempt is not None
    assert entry.fetch_attempt.trace_json.get("static_html_quality_decision") == "spa_shell"
    assert entry.fetch_attempt.trace_json.get("eligible_for_evidence_parse") is False
    assert entry.content_snapshot is not None

    assert entry.browser_fetch_job is not None
    assert entry.browser_fetch_attempt is not None
    assert entry.browser_content_snapshot is not None
    assert entry.browser_fetch_job.mode == FETCH_MODE_BROWSER_RENDERED
    assert entry.browser_fetch_attempt.error_code is None
    assert entry.browser_fetch_attempt.trace_json.get("eligible_for_evidence_parse") is True

    jobs = FetchJobRepository(db_session).list_for_task(task.id)
    assert sorted(j.mode for j in jobs) == sorted([FETCH_MODE_HTTP, FETCH_MODE_BROWSER_RENDERED])
    snapshots = ContentSnapshotRepository(db_session).list_for_task(task.id)
    assert len(snapshots) == 2
    mock_browser.fetch_rendered.assert_called_once()


def test_browser_fallback_skipped_for_policy_target_blocked(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate = _seed_candidate(db_session, canonical_url="https://blocked.example/x")
    mock_http = MagicMock()
    mock_http.fetch.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=None,
        http_status=None,
        error_code="target_blocked",
        mime_type=None,
        content=None,
        content_hash=None,
        trace={"requested_url": candidate.canonical_url, "reason": "blocked_hostname"},
    )
    mock_browser = MagicMock()
    from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

    service = create_acquisition_service(
        db_session,
        http_client=mock_http,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
        browser_fetch_backend_impl=mock_browser,
        browser_fetch_backend_setting="playwright",
    )
    service.acquire_candidates(task.id, candidate_url_ids=[candidate.id], limit=1)
    mock_browser.fetch_rendered.assert_not_called()
    jobs = FetchJobRepository(db_session).list_for_task(task.id)
    assert len(jobs) == 1
    assert jobs[0].mode == FETCH_MODE_HTTP


def test_browser_fetch_failure_is_non_fatal(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate = _seed_candidate(db_session)
    spa_html = b"""<!doctype html><html><head><script>x</script></head>
    <body><div id="app"></div></body></html>"""
    mock_http = MagicMock()
    mock_http.fetch.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=spa_html,
        content_hash=f"sha256:{sha256(spa_html).hexdigest()}",
        trace={"final_url": candidate.canonical_url},
    )
    mock_browser = MagicMock()
    mock_browser.fetch_rendered.side_effect = RuntimeError("playwright boom")

    from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

    service = create_acquisition_service(
        db_session,
        http_client=mock_http,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
        browser_fetch_backend_impl=mock_browser,
        browser_fetch_backend_setting="playwright",
    )
    result = service.acquire_candidates(task.id, candidate_url_ids=[candidate.id], limit=1)
    assert result.succeeded == 1
    entry = result.entries[0]
    assert entry.browser_fetch_job is not None
    assert entry.browser_fetch_attempt is not None
    assert entry.browser_fetch_attempt.error_code == "browser_fetch_failed"
    assert entry.browser_fetch_job.status == FETCH_STATUS_FAILED
    assert "boom" in (entry.browser_fetch_attempt.trace_json or {}).get("message", "")


def test_rendered_snapshot_can_parse_to_source_document(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate = _seed_candidate(db_session, canonical_url="https://spa.example/render")
    spa_html = b"""<!doctype html><html><head><script>x</script></head>
    <body><div id="app"></div></body></html>"""
    rendered_html = b"""<!doctype html><html><head><title>T</title></head><body><main>
    <h1>DeepSearch browser path</h1><p>First evidence paragraph for the parse test.</p>
    <p>Second evidence paragraph with distinct wording.</p></main></body></html>"""

    mock_http = MagicMock()
    mock_http.fetch.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=spa_html,
        content_hash=f"sha256:{sha256(spa_html).hexdigest()}",
        trace={"final_url": candidate.canonical_url},
    )
    mock_browser = MagicMock()
    mock_browser.fetch_rendered.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=rendered_html,
        content_hash=f"sha256:{sha256(rendered_html).hexdigest()}",
        trace={
            "final_url": candidate.canonical_url,
            "browser_title": "T",
            "acquisition_channel": "browser_playwright",
        },
    )
    from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

    store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path))
    acq = create_acquisition_service(
        db_session,
        http_client=mock_http,
        snapshot_object_store=store,
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
        browser_fetch_backend_impl=mock_browser,
        browser_fetch_backend_setting="playwright",
    )
    acq.acquire_candidates(task.id, candidate_url_ids=[candidate.id], limit=1)

    browser_snapshot = next(
        snap
        for snap in ContentSnapshotRepository(db_session).list_for_task(task.id)
        if snap.fetch_attempt.fetch_job.mode == FETCH_MODE_BROWSER_RENDERED
    )
    parse = create_parsing_service(
        db_session,
        snapshot_object_store=store,
        allowed_statuses=(PHASE2_ACTIVE_STATUS,),
    )
    batch = parse.parse_snapshots(
        task.id,
        content_snapshot_ids=[browser_snapshot.id],
        limit=5,
    )
    assert batch.created == 1
    entry = batch.entries[0]
    assert entry.source_document is not None
    assert entry.status == "CREATED"


@pytest.mark.parametrize(
    ("setting", "expect_browser"),
    [
        ("playwright", True),
        ("none", False),
    ],
)
def test_browser_only_when_setting_playwright(
    db_session: Session,
    tmp_path: Path,
    setting: str,
    expect_browser: bool,
) -> None:
    task, candidate = _seed_candidate(db_session, canonical_url="https://soft.example/p")
    soft_html = (
        b"<!doctype html><html><body><noscript>Please enable javascript to view this site."
        b"</noscript></body></html>"
    )
    ok_body = (
        b"<html><body><p>Browser recovered content paragraph one.</p>"
        b"<p>Browser recovered content paragraph two.</p></body></html>"
    )
    mock_http = MagicMock()
    mock_http.fetch.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=soft_html,
        content_hash=f"sha256:{sha256(soft_html).hexdigest()}",
        trace={"final_url": candidate.canonical_url},
    )
    mock_browser = MagicMock()
    mock_browser.fetch_rendered.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=ok_body,
        content_hash=f"sha256:{sha256(ok_body).hexdigest()}",
        trace={"final_url": candidate.canonical_url},
    )
    from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

    service = create_acquisition_service(
        db_session,
        http_client=mock_http,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
        browser_fetch_backend_impl=mock_browser,
        browser_fetch_backend_setting=setting,
    )
    service.acquire_candidates(task.id, candidate_url_ids=[candidate.id], limit=1)
    if expect_browser:
        mock_browser.fetch_rendered.assert_called_once()
    else:
        mock_browser.fetch_rendered.assert_not_called()


def test_body_too_large_skipped_records_reason_in_static_trace(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate = _seed_candidate(db_session, canonical_url="https://docs.langchain.com/large")
    mock_http = MagicMock()
    mock_http.fetch.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code="body_too_large",
        mime_type="text/html",
        content=None,
        content_hash=None,
        trace={"final_url": candidate.canonical_url, "post_fetch_gate": "body_too_large"},
    )
    mock_browser = MagicMock()
    from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

    service = create_acquisition_service(
        db_session,
        http_client=mock_http,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
        browser_fetch_backend_impl=mock_browser,
        browser_fetch_backend_setting="playwright",
    )
    service.acquire_candidates(task.id, candidate_url_ids=[candidate.id], limit=1)
    mock_browser.fetch_rendered.assert_not_called()
    from packages.db.repositories import FetchAttemptRepository

    attempt = FetchAttemptRepository(db_session).list_for_task(task.id)[0]
    bf = attempt.trace_json.get("browser_fallback") if isinstance(attempt.trace_json, dict) else {}
    assert isinstance(bf, dict)
    assert bf.get("browser_fallback_skipped_reason") == "excluded_body_too_large"
    assert bf.get("browser_fallback_attempted") is False


def test_browser_fallback_emits_task_event_when_repository_configured(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task, candidate = _seed_candidate(db_session)
    spa_html = b"""<!doctype html><html><head><script>x</script></head>
    <body><div id="app"></div></body></html>"""
    mock_http = MagicMock()
    mock_http.fetch.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=spa_html,
        content_hash=f"sha256:{sha256(spa_html).hexdigest()}",
        trace={"final_url": candidate.canonical_url},
    )
    mock_browser = MagicMock()
    ok_html = b"<html><body><p>ok</p></body></html>"
    mock_browser.fetch_rendered.return_value = HttpFetchResult(
        requested_url=candidate.canonical_url,
        final_url=candidate.canonical_url,
        http_status=200,
        error_code=None,
        mime_type="text/html",
        content=ok_html,
        content_hash=f"sha256:{sha256(ok_html).hexdigest()}",
        trace={"final_url": candidate.canonical_url},
    )
    from services.orchestrator.app.storage import FilesystemSnapshotObjectStore

    service = create_acquisition_service(
        db_session,
        http_client=mock_http,
        snapshot_object_store=FilesystemSnapshotObjectStore(root_directory=str(tmp_path)),
        snapshot_bucket="snapshots",
        max_candidates_per_request=5,
        browser_fetch_backend_impl=mock_browser,
        browser_fetch_backend_setting="playwright",
        task_event_repository=TaskEventRepository(db_session),
    )
    service.acquire_candidates(task.id, candidate_url_ids=[candidate.id], limit=1)
    events = TaskEventRepository(db_session).list_for_task(task.id)
    bf_events = [e for e in events if e.event_type == ACQUISITION_BROWSER_FALLBACK_EVENT]
    assert len(bf_events) == 1
    payload = bf_events[0].payload_json
    assert payload.get("browser_fallback_attempted") is True
    assert payload.get("browser_fallback_result") == "succeeded"
