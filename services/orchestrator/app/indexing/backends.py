from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx


@dataclass(frozen=True)
class ChunkIndexDocument:
    task_id: UUID
    source_document_id: UUID
    source_chunk_id: UUID
    canonical_url: str
    domain: str
    chunk_no: int
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class IndexedChunkRecord:
    task_id: UUID
    source_document_id: UUID
    source_chunk_id: UUID
    canonical_url: str
    domain: str
    chunk_no: int
    text: str
    metadata: dict[str, Any]
    score: float | None = None


@dataclass(frozen=True)
class IndexedChunkPage:
    total: int
    hits: list[IndexedChunkRecord]


class IndexBackendConfigurationError(RuntimeError):
    pass


class IndexBackendOperationError(RuntimeError):
    def __init__(
        self,
        *,
        operation: str,
        detail: str,
        status_code: int | None = None,
    ) -> None:
        message = f"OpenSearch operation failed during {operation}: {detail}"
        if status_code is not None:
            message = f"{message} (status={status_code})"
        super().__init__(message)
        self.operation = operation
        self.detail = detail
        self.status_code = status_code


class ChunkIndexBackend(Protocol):
    def validate_configuration(self) -> None: ...

    def ensure_index(self) -> None: ...

    def upsert_chunks(self, documents: Sequence[ChunkIndexDocument]) -> None: ...

    def list_chunks(
        self,
        *,
        task_id: UUID,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage: ...

    def retrieve_chunks(
        self,
        *,
        task_id: UUID,
        query: str,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage: ...


class LocalChunkIndexBackend:
    def __init__(self) -> None:
        self.documents = _LOCAL_INDEX_DOCUMENTS

    def validate_configuration(self) -> None:
        return None

    def ensure_index(self) -> None:
        return None

    def upsert_chunks(self, documents: Sequence[ChunkIndexDocument]) -> None:
        for document in documents:
            self.documents[document.source_chunk_id] = document

    def list_chunks(
        self,
        *,
        task_id: UUID,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage:
        records = self._records_for_task(task_id)
        return IndexedChunkPage(total=len(records), hits=records[offset : offset + limit])

    def retrieve_chunks(
        self,
        *,
        task_id: UUID,
        query: str,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage:
        query_tokens = _tokenize(query)
        records = []
        for record in self._records_for_task(task_id):
            score = _local_match_score(query_tokens, record.text)
            if score <= 0 and query_tokens:
                continue
            records.append(
                IndexedChunkRecord(
                    task_id=record.task_id,
                    source_document_id=record.source_document_id,
                    source_chunk_id=record.source_chunk_id,
                    canonical_url=record.canonical_url,
                    domain=record.domain,
                    chunk_no=record.chunk_no,
                    text=record.text,
                    metadata=record.metadata,
                    score=score or 1.0,
                )
            )
        records = rerank_indexed_chunks(records, query=query)
        return IndexedChunkPage(total=len(records), hits=records[offset : offset + limit])

    def _records_for_task(self, task_id: UUID) -> list[IndexedChunkRecord]:
        records = [
            IndexedChunkRecord(
                task_id=document.task_id,
                source_document_id=document.source_document_id,
                source_chunk_id=document.source_chunk_id,
                canonical_url=document.canonical_url,
                domain=document.domain,
                chunk_no=document.chunk_no,
                text=document.text,
                metadata=document.metadata,
                score=None,
            )
            for document in self.documents.values()
            if document.task_id == task_id
        ]
        records.sort(
            key=lambda item: (
                str(item.source_document_id),
                item.chunk_no,
                str(item.source_chunk_id),
            )
        )
        return records


class OpenSearchChunkIndexBackend:
    def __init__(
        self,
        *,
        base_url: str,
        index_name: str,
        username: str = "",
        password: str = "",
        verify_tls: bool = True,
        ca_bundle_path: str | None = None,
        timeout_seconds: float,
        validate_connectivity: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.index_name = index_name.strip()
        self.username = username.strip()
        self.password = password
        self.verify_tls = verify_tls
        self.ca_bundle_path = (
            ca_bundle_path.strip()
            if ca_bundle_path is not None and ca_bundle_path.strip()
            else None
        )
        self.timeout_seconds = timeout_seconds
        self.validate_connectivity = validate_connectivity
        self.client = client

    def validate_configuration(self) -> None:
        if not self.index_name:
            raise IndexBackendConfigurationError("opensearch index name must not be empty")
        if self.timeout_seconds <= 0:
            raise IndexBackendConfigurationError("opensearch timeout_seconds must be positive")

        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise IndexBackendConfigurationError(f"invalid OpenSearch base URL: {self.base_url}")
        if bool(self.username) != bool(self.password):
            raise IndexBackendConfigurationError(
                "opensearch username and password must be configured together"
            )
        if self.ca_bundle_path is not None and not Path(self.ca_bundle_path).is_file():
            raise IndexBackendConfigurationError(
                f"opensearch CA bundle does not exist: {self.ca_bundle_path}"
            )

        if self.validate_connectivity:
            response = self._request("GET", "/", operation="startup connectivity validation")
            self._ensure_success(response, operation="startup connectivity validation")

    def ensure_index(self) -> None:
        self._ensure_index()

    def upsert_chunks(self, documents: Sequence[ChunkIndexDocument]) -> None:
        if not documents:
            return

        self.ensure_index()
        for document in documents:
            response = self._request(
                "PUT",
                f"/{self.index_name}/_doc/{document.source_chunk_id}",
                operation="chunk upsert",
                params={"refresh": "true"},
                json=_serialize_document(document),
            )
            self._ensure_success(response, operation="chunk upsert")

    def list_chunks(
        self,
        *,
        task_id: UUID,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage:
        if not self._index_exists():
            return IndexedChunkPage(total=0, hits=[])

        response = self._request(
            "POST",
            f"/{self.index_name}/_search",
            operation="chunk list",
            json={
                "from": offset,
                "size": limit,
                "track_total_hits": True,
                "query": {"term": {"task_id": {"value": str(task_id)}}},
                "sort": [
                    {"source_document_id": "asc"},
                    {"chunk_no": "asc"},
                    {"source_chunk_id": "asc"},
                ],
            },
        )
        self._ensure_success(response, operation="chunk list")
        return _parse_search_response(response.json())

    def retrieve_chunks(
        self,
        *,
        task_id: UUID,
        query: str,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage:
        if not self._index_exists():
            return IndexedChunkPage(total=0, hits=[])

        response = self._request(
            "POST",
            f"/{self.index_name}/_search",
            operation="chunk retrieval",
            json={
                "from": offset,
                "size": limit,
                "track_total_hits": True,
                "query": {
                    "bool": {
                        "filter": [{"term": {"task_id": {"value": str(task_id)}}}],
                        "must": [{"match": {"text": {"query": query}}}],
                    }
                },
                "sort": [
                    {"_score": "desc"},
                    {"source_document_id": "asc"},
                    {"chunk_no": "asc"},
                    {"source_chunk_id": "asc"},
                ],
            },
        )
        self._ensure_success(response, operation="chunk retrieval")
        page = _parse_search_response(response.json())
        return IndexedChunkPage(
            total=page.total,
            hits=rerank_indexed_chunks(page.hits, query=query),
        )

    def _ensure_index(self) -> None:
        if self._index_exists():
            return

        response = self._request(
            "PUT",
            f"/{self.index_name}",
            operation="index creation",
            json={
                "settings": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                },
                "mappings": {
                    "dynamic": "strict",
                    "properties": {
                        "task_id": {"type": "keyword"},
                        "source_document_id": {"type": "keyword"},
                        "source_chunk_id": {"type": "keyword"},
                        "canonical_url": {"type": "keyword"},
                        "domain": {"type": "keyword"},
                        "chunk_no": {"type": "integer"},
                        "text": {"type": "text"},
                        "metadata": {"type": "object", "dynamic": True},
                    },
                },
            },
        )
        self._ensure_success(response, operation="index creation")

    def _index_exists(self) -> bool:
        response = self._request("HEAD", f"/{self.index_name}", operation="index existence check")
        if response.status_code == 404:
            return False
        self._ensure_success(response, operation="index existence check")
        return True

    def _request(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        request_headers = {"accept-encoding": "identity"}
        request_auth: tuple[str, str] | None = None
        if self.username:
            request_auth = (self.username, self.password)
        try:
            if self.client is not None:
                return self.client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=request_headers,
                    auth=request_auth,
                    params=params,
                    json=json,
                )

            with httpx.Client(
                timeout=self.timeout_seconds,
                trust_env=False,
                verify=self._resolve_tls_verify(),
            ) as client:
                return client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=request_headers,
                    auth=request_auth,
                    params=params,
                    json=json,
                )
        except httpx.RequestError as error:
            raise IndexBackendOperationError(
                operation=operation,
                detail=str(error),
            ) from error

    def _ensure_success(self, response: httpx.Response, *, operation: str) -> None:
        if response.status_code >= 400:
            raise IndexBackendOperationError(
                operation=operation,
                detail=_response_detail(response),
                status_code=response.status_code,
            )

    def _resolve_tls_verify(self) -> bool | str:
        if self.ca_bundle_path is not None:
            return self.ca_bundle_path
        return self.verify_tls


def build_chunk_index_backend(
    *,
    backend: str,
    opensearch_base_url: str,
    opensearch_index_name: str,
    opensearch_username: str = "",
    opensearch_password: str = "",
    opensearch_verify_tls: bool = True,
    opensearch_ca_bundle_path: str | None = None,
    opensearch_timeout_seconds: float,
    opensearch_validate_connectivity: bool = False,
) -> ChunkIndexBackend:
    normalized_backend = backend.strip().lower()
    if normalized_backend in {"local", "memory"}:
        return LocalChunkIndexBackend()
    if normalized_backend == "opensearch":
        return OpenSearchChunkIndexBackend(
            base_url=opensearch_base_url,
            index_name=opensearch_index_name,
            username=opensearch_username,
            password=opensearch_password,
            verify_tls=opensearch_verify_tls,
            ca_bundle_path=opensearch_ca_bundle_path,
            timeout_seconds=opensearch_timeout_seconds,
            validate_connectivity=opensearch_validate_connectivity,
        )

    raise IndexBackendConfigurationError(f"unsupported index backend: {backend}")


def _serialize_document(document: ChunkIndexDocument) -> dict[str, Any]:
    return {
        "task_id": str(document.task_id),
        "source_document_id": str(document.source_document_id),
        "source_chunk_id": str(document.source_chunk_id),
        "canonical_url": document.canonical_url,
        "domain": document.domain,
        "chunk_no": document.chunk_no,
        "text": document.text,
        "metadata": document.metadata,
    }


def _parse_search_response(payload: dict[str, Any]) -> IndexedChunkPage:
    raw_hits = payload.get("hits", {})
    total_value = raw_hits.get("total", 0)
    if isinstance(total_value, dict):
        total = total_value.get("value", 0)
    elif isinstance(total_value, int):
        total = total_value
    else:
        total = 0

    hits: list[IndexedChunkRecord] = []
    for raw_hit in raw_hits.get("hits", []):
        source = raw_hit.get("_source", {})
        metadata = source.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        hits.append(
            IndexedChunkRecord(
                task_id=UUID(source["task_id"]),
                source_document_id=UUID(source["source_document_id"]),
                source_chunk_id=UUID(source["source_chunk_id"]),
                canonical_url=str(source["canonical_url"]),
                domain=str(source["domain"]),
                chunk_no=int(source["chunk_no"]),
                text=str(source["text"]),
                metadata=metadata,
                score=_coerce_score(raw_hit.get("_score")),
            )
        )

    return IndexedChunkPage(total=int(total), hits=hits)


def rerank_indexed_chunks(
    records: Sequence[IndexedChunkRecord],
    *,
    query: str,
) -> list[IndexedChunkRecord]:
    query_tokens = _tokenize(query)
    domain_counts: dict[str, int] = {}
    reranked: list[IndexedChunkRecord] = []
    for record in records:
        prior_domain_count = domain_counts.get(record.domain, 0)
        domain_counts[record.domain] = prior_domain_count + 1
        metadata = dict(record.metadata)
        components = _retrieval_score_components(
            record,
            query_tokens=query_tokens,
            prior_domain_count=prior_domain_count,
        )
        metadata["retrieval_diagnostics"] = {
            "method": "deterministic_lexical_quality_rerank_v1",
            "backend_score": record.score,
            "rerank_score": components["rerank_score"],
            "components": components,
            "query_token_count": len(query_tokens),
            "fallback": "lexical_bm25_or_local_match",
        }
        reranked.append(
            IndexedChunkRecord(
                task_id=record.task_id,
                source_document_id=record.source_document_id,
                source_chunk_id=record.source_chunk_id,
                canonical_url=record.canonical_url,
                domain=record.domain,
                chunk_no=record.chunk_no,
                text=record.text,
                metadata=metadata,
                score=record.score,
            )
        )

    reranked.sort(
        key=lambda item: (
            -_metadata_float(item.metadata, "retrieval_diagnostics", "rerank_score"),
            -(item.score or 0.0),
            str(item.source_document_id),
            item.chunk_no,
            str(item.source_chunk_id),
        )
    )
    return reranked


def _retrieval_score_components(
    record: IndexedChunkRecord,
    *,
    query_tokens: set[str],
    prior_domain_count: int,
) -> dict[str, float]:
    lexical_score = min(1.0, max(0.0, float(record.score or 0.0)))
    if lexical_score <= 0.0 and query_tokens:
        lexical_score = _local_match_score(query_tokens, record.text)
    source_quality = _coerce_unit_score(
        record.metadata.get("source_quality_score")
        or _nested_metadata_value(record.metadata, "source_quality", "final_score"),
        default=0.5,
    )
    chunk_quality = _coerce_unit_score(record.metadata.get("content_quality_score"), default=0.5)
    information_density = _coerce_unit_score(
        record.metadata.get("information_density_score"),
        default=0.5,
    )
    freshness = _coerce_unit_score(
        _nested_metadata_value(record.metadata, "source_quality", "freshness_score"),
        default=0.5,
    )
    citation_precision = _citation_precision_likelihood(record.metadata)
    diversity_penalty = min(0.18, prior_domain_count * 0.06)
    rerank_score = (
        lexical_score * 0.48
        + source_quality * 0.22
        + chunk_quality * 0.12
        + information_density * 0.08
        + citation_precision * 0.06
        + freshness * 0.04
        - diversity_penalty
    )
    return {
        "lexical_score": round(lexical_score, 4),
        "source_quality_score": round(source_quality, 4),
        "chunk_quality_score": round(chunk_quality, 4),
        "information_density_score": round(information_density, 4),
        "freshness_score": round(freshness, 4),
        "citation_precision_likelihood": round(citation_precision, 4),
        "source_diversity_penalty": round(diversity_penalty, 4),
        "rerank_score": round(max(0.0, min(1.0, rerank_score)), 4),
    }


def _citation_precision_likelihood(metadata: dict[str, Any]) -> float:
    if metadata.get("page_range") or metadata.get("slide_range") or metadata.get("cell_ranges"):
        return 0.9
    if metadata.get("paragraph_range"):
        return 0.75
    if metadata.get("char_start") is not None and metadata.get("char_end") is not None:
        return 0.65
    return 0.45


def _nested_metadata_value(
    metadata: dict[str, Any],
    parent_key: str,
    child_key: str,
) -> object | None:
    parent = metadata.get(parent_key)
    if not isinstance(parent, dict):
        return None
    return parent.get(child_key)


def _metadata_float(metadata: dict[str, Any], parent_key: str, child_key: str) -> float:
    value = _nested_metadata_value(metadata, parent_key, child_key)
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _coerce_unit_score(value: object, *, default: float) -> float:
    if isinstance(value, int | float):
        return min(1.0, max(0.0, float(value)))
    return default


def _coerce_score(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _tokenize(value: str) -> set[str]:
    return {token.casefold() for token in value.split() if token.strip()}


def _local_match_score(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 1.0
    normalized_text = text.casefold()
    matched = sum(1 for token in query_tokens if token in normalized_text)
    if matched <= 0:
        return 0.0
    return round(matched / len(query_tokens), 4)


def _response_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:500] if text else "empty response body"
    return str(payload)[:500]


_LOCAL_INDEX_DOCUMENTS: dict[UUID, ChunkIndexDocument] = {}
