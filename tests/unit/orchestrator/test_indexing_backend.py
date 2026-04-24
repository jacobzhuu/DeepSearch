from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from services.orchestrator.app.indexing import (
    ChunkIndexDocument,
    IndexBackendConfigurationError,
    IndexBackendOperationError,
    LocalChunkIndexBackend,
    OpenSearchChunkIndexBackend,
)


def test_opensearch_backend_upserts_lists_and_retrieves_chunks() -> None:
    task_id = uuid4()
    source_document_id = uuid4()
    source_chunk_id = uuid4()
    index_state = {"exists": False}
    documents: dict[str, dict[str, Any]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        path = request.url.path
        if request.method == "HEAD" and path == "/source-chunks-v1":
            return httpx.Response(200 if index_state["exists"] else 404)

        if request.method == "PUT" and path == "/source-chunks-v1":
            index_state["exists"] = True
            payload = _load_json_object(request)
            mappings = payload["mappings"]
            assert isinstance(mappings, dict)
            properties = mappings["properties"]
            assert isinstance(properties, dict)
            metadata_mapping = properties["metadata"]
            assert metadata_mapping == {
                "type": "object",
                "dynamic": True,
            }
            return httpx.Response(200, json={"acknowledged": True})

        if request.method == "PUT" and path == f"/source-chunks-v1/_doc/{source_chunk_id}":
            documents[str(source_chunk_id)] = _load_json_object(request)
            return httpx.Response(201, json={"result": "created"})

        if request.method == "POST" and path == "/source-chunks-v1/_search":
            return httpx.Response(200, json=_search_payload(_load_json_object(request), documents))

        raise AssertionError(f"unexpected request: {request.method} {path}")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    backend = OpenSearchChunkIndexBackend(
        base_url="http://opensearch.test",
        index_name="source-chunks-v1",
        timeout_seconds=5.0,
        client=client,
    )

    backend.validate_configuration()
    backend.upsert_chunks(
        [
            ChunkIndexDocument(
                task_id=task_id,
                source_document_id=source_document_id,
                source_chunk_id=source_chunk_id,
                canonical_url="https://example.com/source",
                domain="example.com",
                chunk_no=0,
                text="Alpha beta gamma",
                metadata={"strategy": "paragraph_window_v1"},
            )
        ]
    )

    listed = backend.list_chunks(task_id=task_id, offset=0, limit=10)
    retrieved = backend.retrieve_chunks(task_id=task_id, query="beta", offset=0, limit=10)

    assert listed.total == 1
    assert listed.hits[0].source_chunk_id == source_chunk_id
    assert listed.hits[0].score is None
    assert retrieved.total == 1
    assert retrieved.hits[0].source_chunk_id == source_chunk_id
    assert retrieved.hits[0].score == 1.0


def test_opensearch_backend_returns_empty_page_when_index_does_not_exist() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "HEAD"
        assert request.url.path == "/source-chunks-v1"
        return httpx.Response(404)

    backend = OpenSearchChunkIndexBackend(
        base_url="http://opensearch.test",
        index_name="source-chunks-v1",
        timeout_seconds=5.0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    listed = backend.list_chunks(task_id=uuid4(), offset=0, limit=10)
    assert listed.total == 0
    assert listed.hits == []


def test_opensearch_backend_can_validate_live_connectivity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        assert request.method == "GET"
        assert request.url.path == "/"
        return httpx.Response(200, json={"version": {"distribution": "opensearch"}})

    backend = OpenSearchChunkIndexBackend(
        base_url="http://opensearch.test",
        index_name="source-chunks-v1",
        timeout_seconds=5.0,
        validate_connectivity=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    backend.validate_configuration()


def test_opensearch_backend_wraps_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(404)
        if request.method == "PUT" and request.url.path == "/source-chunks-v1":
            return httpx.Response(503, json={"error": "cluster unavailable"})
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    backend = OpenSearchChunkIndexBackend(
        base_url="http://opensearch.test",
        index_name="source-chunks-v1",
        timeout_seconds=5.0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(IndexBackendOperationError):
        backend.upsert_chunks(
            [
                ChunkIndexDocument(
                    task_id=uuid4(),
                    source_document_id=uuid4(),
                    source_chunk_id=uuid4(),
                    canonical_url="https://example.com/source",
                    domain="example.com",
                    chunk_no=0,
                    text="Alpha beta gamma",
                    metadata={},
                )
            ]
        )


def test_opensearch_backend_rejects_invalid_base_url() -> None:
    backend = OpenSearchChunkIndexBackend(
        base_url="not a url",
        index_name="source-chunks-v1",
        timeout_seconds=5.0,
    )

    with pytest.raises(IndexBackendConfigurationError):
        backend.validate_configuration()


def test_opensearch_backend_sends_basic_auth_when_configured() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        expected_auth = base64.b64encode(b"admin:secret").decode("ascii")
        assert request.headers["authorization"] == f"Basic {expected_auth}"
        return httpx.Response(200, json={"version": {"distribution": "opensearch"}})

    backend = OpenSearchChunkIndexBackend(
        base_url="https://opensearch.test",
        index_name="source-chunks-v1",
        username="admin",
        password="secret",
        verify_tls=False,
        ca_bundle_path=None,
        timeout_seconds=5.0,
        validate_connectivity=True,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    backend.validate_configuration()


def test_opensearch_backend_rejects_missing_ca_bundle_path(tmp_path: Path) -> None:
    backend = OpenSearchChunkIndexBackend(
        base_url="https://opensearch.test",
        index_name="source-chunks-v1",
        username="",
        password="",
        verify_tls=True,
        ca_bundle_path=str(tmp_path / "missing.pem"),
        timeout_seconds=5.0,
    )

    with pytest.raises(IndexBackendConfigurationError):
        backend.validate_configuration()


def test_local_index_backend_upserts_lists_and_retrieves_chunks() -> None:
    task_id = uuid4()
    other_task_id = uuid4()
    source_document_id = uuid4()
    source_chunk_id = uuid4()
    backend = LocalChunkIndexBackend()

    backend.upsert_chunks(
        [
            ChunkIndexDocument(
                task_id=task_id,
                source_document_id=source_document_id,
                source_chunk_id=source_chunk_id,
                canonical_url="https://example.com/source",
                domain="example.com",
                chunk_no=0,
                text="Alpha beta gamma",
                metadata={"strategy": "paragraph_window_v1"},
            ),
            ChunkIndexDocument(
                task_id=other_task_id,
                source_document_id=uuid4(),
                source_chunk_id=uuid4(),
                canonical_url="https://example.net/source",
                domain="example.net",
                chunk_no=0,
                text="Alpha beta gamma",
                metadata={},
            ),
        ]
    )

    listed = backend.list_chunks(task_id=task_id, offset=0, limit=10)
    retrieved = backend.retrieve_chunks(task_id=task_id, query="beta", offset=0, limit=10)

    assert listed.total == 1
    assert listed.hits[0].source_chunk_id == source_chunk_id
    assert listed.hits[0].score is None
    assert retrieved.total == 1
    assert retrieved.hits[0].source_chunk_id == source_chunk_id
    assert retrieved.hits[0].score == 1.0


def _search_payload(
    payload: dict[str, object],
    documents: dict[str, dict[str, Any]],
) -> dict[str, object]:
    raw_query = payload.get("query", {})
    filtered_documents = list(documents.values())
    text_query: str | None = None

    if isinstance(raw_query, dict) and "term" in raw_query:
        task_id = _term_task_id(raw_query["term"])
        filtered_documents = [item for item in filtered_documents if item["task_id"] == task_id]
    elif isinstance(raw_query, dict) and "bool" in raw_query:
        bool_query = raw_query["bool"]
        if isinstance(bool_query, dict):
            filters = bool_query.get("filter", [])
            if isinstance(filters, list):
                for item in filters:
                    if isinstance(item, dict) and "term" in item:
                        task_id = _term_task_id(item["term"])
                        filtered_documents = [
                            document
                            for document in filtered_documents
                            if document["task_id"] == task_id
                        ]
            must = bool_query.get("must", [])
            if isinstance(must, list):
                for item in must:
                    if isinstance(item, dict):
                        match = item.get("match", {})
                        if isinstance(match, dict):
                            text = match.get("text", {})
                            if isinstance(text, dict):
                                candidate = text.get("query")
                                if isinstance(candidate, str):
                                    text_query = candidate
    scored_documents = []
    for document in filtered_documents:
        score = None
        if text_query is not None:
            score = _match_score(text_query, str(document["text"]))
            if score <= 0:
                continue
        scored_documents.append((document, score))

    if text_query is None:
        scored_documents.sort(
            key=lambda item: (
                str(item[0]["source_document_id"]),
                _int_value(item[0]["chunk_no"]),
                str(item[0]["source_chunk_id"]),
            )
        )
    else:
        scored_documents.sort(
            key=lambda item: (
                -(item[1] or 0.0),
                str(item[0]["source_document_id"]),
                _int_value(item[0]["chunk_no"]),
                str(item[0]["source_chunk_id"]),
            )
        )

    start = _int_value(payload.get("from", 0))
    size = _int_value(payload.get("size", 10))
    page = scored_documents[start : start + size]
    return {
        "hits": {
            "total": {"value": len(scored_documents)},
            "hits": [{"_source": document, "_score": score} for document, score in page],
        }
    }


def _term_task_id(term_payload: object) -> str:
    if not isinstance(term_payload, dict):
        return ""
    task_term = term_payload.get("task_id", {})
    if not isinstance(task_term, dict):
        return ""
    value = task_term.get("value")
    return str(value) if value is not None else ""


def _match_score(query: str, text: str) -> float:
    tokens = [token for token in query.lower().split() if token]
    if not tokens:
        return 0.0
    lower_text = text.lower()
    return float(sum(1 for token in tokens if token in lower_text))


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return 0


def _load_json_object(request: httpx.Request) -> dict[str, object]:
    loaded = json.loads(request.content.decode("utf-8"))
    if not isinstance(loaded, dict):
        raise AssertionError("expected JSON object payload")
    return loaded
