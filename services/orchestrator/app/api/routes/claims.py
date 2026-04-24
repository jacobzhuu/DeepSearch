from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from services.orchestrator.app.api.schemas.claims import (
    ClaimEvidenceListResponse,
    ClaimEvidenceResponse,
    ClaimListResponse,
    ClaimResponse,
    DraftClaimEntryResponse,
    DraftClaimsRequest,
    DraftClaimsResponse,
    VerifiedClaimResponse,
    VerifyClaimsRequest,
    VerifyClaimsResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.indexing import (
    ChunkIndexBackend,
    IndexBackendOperationError,
    build_chunk_index_backend,
)
from services.orchestrator.app.services.claims import (
    ClaimDraftingConflictError,
    ClaimDraftingDataIntegrityError,
    ClaimDraftingInputError,
    ClaimDraftingService,
    ClaimNotFoundError,
    ClaimSourceChunkNotFoundError,
    ClaimVerificationConflictError,
    create_claim_drafting_service,
)
from services.orchestrator.app.services.research_tasks import TaskNotFoundError
from services.orchestrator.app.settings import get_settings

router = APIRouter(prefix="/api/v1/research/tasks", tags=["claims"])
SessionDep = Annotated[Session, Depends(get_db_session)]


def get_claim_chunk_index_backend() -> ChunkIndexBackend:
    settings = get_settings()
    return build_chunk_index_backend(
        backend=settings.index_backend,
        opensearch_base_url=settings.opensearch_base_url,
        opensearch_index_name=settings.opensearch_index_name,
        opensearch_username=settings.opensearch_username,
        opensearch_password=settings.opensearch_password,
        opensearch_verify_tls=settings.opensearch_verify_tls,
        opensearch_ca_bundle_path=settings.opensearch_ca_bundle_path,
        opensearch_timeout_seconds=settings.opensearch_timeout_seconds,
        opensearch_validate_connectivity=False,
    )


def get_claim_drafting_service(
    session: SessionDep,
    index_backend: Annotated[ChunkIndexBackend, Depends(get_claim_chunk_index_backend)],
) -> ClaimDraftingService:
    settings = get_settings()
    return create_claim_drafting_service(
        session,
        index_backend=index_backend,
        max_candidates_per_request=settings.claim_drafting_max_candidates_per_request,
        verification_max_claims_per_request=settings.claim_verification_max_claims_per_request,
        retrieval_max_results_per_request=settings.retrieval_max_results_per_request,
    )


ServiceDep = Annotated[ClaimDraftingService, Depends(get_claim_drafting_service)]


@router.post(
    "/{task_id}/claims/draft",
    response_model=DraftClaimsResponse,
    status_code=status.HTTP_200_OK,
)
def draft_task_claims(
    task_id: UUID,
    service: ServiceDep,
    request: Annotated[DraftClaimsRequest, Body()],
) -> DraftClaimsResponse:
    try:
        result = service.draft_claims(
            task_id,
            query=request.query,
            source_chunk_ids=request.source_chunk_ids,
            limit=request.limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ClaimSourceChunkNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ClaimDraftingConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ClaimDraftingInputError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    except ClaimDraftingDataIntegrityError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except IndexBackendOperationError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    return DraftClaimsResponse(
        task_id=result.task.id,
        effective_query=result.effective_query,
        created_claims=result.created_claims,
        reused_claims=result.reused_claims,
        created_citation_spans=result.created_citation_spans,
        reused_citation_spans=result.reused_citation_spans,
        created_claim_evidence=result.created_claim_evidence,
        reused_claim_evidence=result.reused_claim_evidence,
        claims=[
            DraftClaimEntryResponse(
                claim_id=entry.claim.id,
                citation_span_id=entry.citation_span.id,
                claim_evidence_id=entry.claim_evidence.id,
                source_chunk_id=entry.source_chunk.id,
                source_document_id=entry.source_chunk.source_document_id,
                statement=entry.claim.statement,
                claim_type=entry.claim.claim_type,
                confidence=entry.claim.confidence,
                verification_status=entry.claim.verification_status,
                relation_type=entry.claim_evidence.relation_type,
                evidence_score=entry.claim_evidence.score,
                start_offset=entry.citation_span.start_offset,
                end_offset=entry.citation_span.end_offset,
                excerpt=entry.citation_span.excerpt,
                reused_claim=entry.reused_claim,
                reused_citation_span=entry.reused_citation_span,
                reused_claim_evidence=entry.reused_claim_evidence,
                retrieval_score=entry.retrieval_score,
            )
            for entry in result.entries
        ],
    )


@router.get("/{task_id}/claims", response_model=ClaimListResponse)
def list_task_claims(
    task_id: UUID,
    service: ServiceDep,
    verification_status: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> ClaimListResponse:
    try:
        claims = service.list_claim_summaries(
            task_id,
            verification_status=verification_status,
            limit=limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return ClaimListResponse(
        task_id=task_id,
        claims=[
            ClaimResponse(
                claim_id=entry.claim.id,
                statement=entry.claim.statement,
                claim_type=entry.claim.claim_type,
                confidence=entry.claim.confidence,
                verification_status=entry.claim.verification_status,
                support_evidence_count=entry.support_evidence_count,
                contradict_evidence_count=entry.contradict_evidence_count,
                rationale=entry.rationale,
                notes=entry.claim.notes_json,
            )
            for entry in claims
        ],
    )


@router.get("/{task_id}/claim-evidence", response_model=ClaimEvidenceListResponse)
def list_task_claim_evidence(
    task_id: UUID,
    service: ServiceDep,
    claim_id: Annotated[UUID | None, Query()] = None,
    relation_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> ClaimEvidenceListResponse:
    try:
        claim_evidence = service.list_claim_evidence(
            task_id,
            claim_id=claim_id,
            relation_type=relation_type,
            limit=limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return ClaimEvidenceListResponse(
        task_id=task_id,
        claim_evidence=[
            ClaimEvidenceResponse(
                claim_evidence_id=evidence.id,
                claim_id=evidence.claim_id,
                citation_span_id=evidence.citation_span_id,
                source_chunk_id=evidence.citation_span.source_chunk_id,
                source_document_id=evidence.citation_span.source_chunk.source_document_id,
                statement=evidence.claim.statement,
                relation_type=evidence.relation_type,
                score=evidence.score,
                start_offset=evidence.citation_span.start_offset,
                end_offset=evidence.citation_span.end_offset,
                excerpt=evidence.citation_span.excerpt,
                normalized_excerpt_hash=evidence.citation_span.normalized_excerpt_hash,
            )
            for evidence in claim_evidence
        ],
    )


@router.post(
    "/{task_id}/claims/verify",
    response_model=VerifyClaimsResponse,
    status_code=status.HTTP_200_OK,
)
def verify_task_claims(
    task_id: UUID,
    service: ServiceDep,
    request: Annotated[VerifyClaimsRequest | None, Body()] = None,
) -> VerifyClaimsResponse:
    verify_request = request or VerifyClaimsRequest()
    try:
        result = service.verify_claims(
            task_id,
            claim_ids=verify_request.claim_ids,
            limit=verify_request.limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ClaimNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ClaimVerificationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ClaimDraftingDataIntegrityError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except IndexBackendOperationError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error

    return VerifyClaimsResponse(
        task_id=result.task.id,
        verified_claims=result.verified_claims,
        created_citation_spans=result.created_citation_spans,
        reused_citation_spans=result.reused_citation_spans,
        created_claim_evidence=result.created_claim_evidence,
        reused_claim_evidence=result.reused_claim_evidence,
        claims=[
            VerifiedClaimResponse(
                claim_id=entry.claim.id,
                statement=entry.claim.statement,
                claim_type=entry.claim.claim_type,
                confidence=entry.claim.confidence,
                verification_status=entry.claim.verification_status,
                support_evidence_count=entry.support_evidence_count,
                contradict_evidence_count=entry.contradict_evidence_count,
                rationale=entry.rationale,
                notes=entry.claim.notes_json,
            )
            for entry in result.entries
        ],
    )
