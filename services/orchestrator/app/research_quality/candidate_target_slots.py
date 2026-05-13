"""Join planner ``target_slots`` from candidate metadata into technical-explanation claim slots."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.db.models import (
    CandidateUrl,
    ContentSnapshot,
    FetchAttempt,
    FetchJob,
    SourceDocument,
)
from services.orchestrator.app.research_quality.answer_slots import answer_slots_for_query

ROLES_ELIGIBLE_FOR_PLANNER_TARGET_SLOT_MERGE: frozenset[str] = frozenset(
    {
        "official_docs",
        "official_reference",
        "official_repository",
        "official_blog_or_changelog",
        "academic_or_standard",
    }
)


@lru_cache(maxsize=1)
def technical_planner_slot_allowlist() -> frozenset[str]:
    """Slot ids valid for a technical_explanation template (English probe query)."""
    slots = answer_slots_for_query("What is LangGraph and how does it work?")
    return frozenset(slot.slot_id for slot in slots)


def _parse_target_slots_from_metadata(metadata: Mapping[str, Any] | None) -> frozenset[str]:
    if not isinstance(metadata, Mapping):
        return frozenset()
    raw = metadata.get("target_slots") or metadata.get("technical_slot_targets")
    if not isinstance(raw, list):
        return frozenset()
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return frozenset(out)


def load_candidate_target_slots_by_source_document(
    session: Session,
    task_id: UUID,
) -> tuple[dict[UUID, frozenset[str]], dict[str, int]]:
    """Batch-load filtered planner target slots keyed by ``source_document.id``."""
    allowlist = technical_planner_slot_allowlist()
    stmt = (
        select(SourceDocument.id, CandidateUrl.metadata_json)
        .join(ContentSnapshot, SourceDocument.content_snapshot_id == ContentSnapshot.id)
        .join(FetchAttempt, ContentSnapshot.fetch_attempt_id == FetchAttempt.id)
        .join(FetchJob, FetchAttempt.fetch_job_id == FetchJob.id)
        .join(CandidateUrl, FetchJob.candidate_url_id == CandidateUrl.id)
        .where(SourceDocument.task_id == task_id)
    )
    rows = session.execute(stmt).all()
    merged: dict[UUID, set[str]] = {}
    for sd_id, metadata_json in rows:
        parsed = _parse_target_slots_from_metadata(
            metadata_json if isinstance(metadata_json, Mapping) else None
        )
        filtered = {s for s in parsed if s in allowlist}
        if not filtered:
            continue
        bucket = merged.setdefault(sd_id, set())
        bucket.update(filtered)
    out_map = {doc_id: frozenset(slots) for doc_id, slots in merged.items()}
    meta = {
        "candidate_urls_joined": len(rows),
        "documents_with_slots": sum(1 for v in out_map.values() if v),
    }
    return out_map, meta


def merge_technical_lexical_and_planner_slots(
    *,
    lexical_slots: tuple[str, ...],
    source_document_id: UUID,
    source_role: str | None,
    document_target_slots: dict[UUID, frozenset[str]] | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(merged_slots, planner_only_slots)``.

    ``planner_only_slots`` lists slots contributed from ``candidate_url`` metadata that are
    not already present in ``lexical_slots``. Low-quality ``source_role`` values never merge.
    """
    if not document_target_slots:
        return lexical_slots, ()
    role = (source_role or "").strip()
    if role not in ROLES_ELIGIBLE_FOR_PLANNER_TARGET_SLOT_MERGE:
        return lexical_slots, ()
    planner = document_target_slots.get(source_document_id)
    if not planner:
        return lexical_slots, ()
    merged = tuple(dict.fromkeys([*lexical_slots, *planner]))
    lexical_set = set(lexical_slots)
    planner_only = tuple(s for s in merged if s in planner and s not in lexical_set)
    return merged, planner_only


def weak_optional_slots_without_planner_propagation(
    *,
    query: str,
    diversified: list[Any],
) -> list[str]:
    """Optional technical slots that received no planner-backed ``candidate_target_slot_ids``."""
    slots = answer_slots_for_query(query)
    weak: list[str] = []
    for slot in slots:
        if slot.required:
            continue
        has_planner = any(
            slot.slot_id in getattr(c, "candidate_target_slot_ids", ()) for c in diversified
        )
        if not has_planner:
            weak.append(slot.slot_id)
    return sorted(dict.fromkeys(weak))
