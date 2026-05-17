from __future__ import annotations

from collections.abc import Iterable

from services.orchestrator.app.reporting.markdown import ReportClaimItem, ReportEvidenceItem
from services.orchestrator.app.research_quality.source_intent import source_intent_report_core_eligible


def build_claim_evidence_index(claims: Iterable[ReportClaimItem]) -> dict[str, ReportEvidenceItem]:
    out: dict[str, ReportEvidenceItem] = {}
    for claim in claims:
        for ev in claim.support_evidence:
            out[str(ev.claim_evidence_id)] = ev
    return out


def evidence_ids_core_eligible(
    evidence_ids: list[str],
    *,
    index: dict[str, ReportEvidenceItem],
) -> bool:
    if not evidence_ids:
        return False
    for eid in evidence_ids:
        ev = index.get(eid)
        if ev is None:
            return False
        if not source_intent_report_core_eligible(ev.source_intent):
            return False
    return True


def claims_for_evidence_ids(
    evidence_ids: list[str],
    *,
    claims: list[ReportClaimItem],
) -> list[ReportClaimItem]:
    id_set = set(evidence_ids)
    matched: list[ReportClaimItem] = []
    for claim in claims:
        for ev in claim.support_evidence:
            if str(ev.claim_evidence_id) in id_set:
                matched.append(claim)
                break
    return matched
