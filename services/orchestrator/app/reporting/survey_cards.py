"""Deterministic method / paper cards for ``research_survey`` report archetype."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from services.orchestrator.app.query_intent_signals import domain_looks_paper_like


@dataclass(frozen=True)
class MethodSurveyCard:
    """One clustered line of work / source bundle for survey-shaped reports."""

    card_key: str
    display_name: str
    paper_title: str | None
    primary_domain: str
    claim_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "card_key": self.card_key,
            "display_name": self.display_name,
            "paper_title": self.paper_title,
            "primary_domain": self.primary_domain,
            "claim_ids": list(self.claim_ids),
            "evidence_ids": list(self.evidence_ids),
        }


def _source_title_map(sources: list[Any]) -> dict[UUID, str | None]:
    out: dict[UUID, str | None] = {}
    for src in sources:
        sid = getattr(src, "source_document_id", None)
        if isinstance(sid, UUID):
            out[sid] = getattr(src, "title", None)
    return out


def build_method_survey_cards(claims: list[Any], sources: list[Any]) -> list[MethodSurveyCard]:
    """Cluster claims conservatively by primary supporting source document."""
    title_by_doc = _source_title_map(sources)
    clusters: dict[str, list[Any]] = defaultdict(list)
    for claim in claims:
        if getattr(claim, "verification_status", None) != "supported":
            continue
        evs = list(getattr(claim, "support_evidence", ()) or ())
        if not evs:
            continue
        doc_id = getattr(evs[0], "source_document_id", None)
        if not isinstance(doc_id, UUID):
            continue
        clusters[str(doc_id)].append(claim)

    scored = sorted(
        clusters.items(),
        key=lambda item: len(item[1]),
        reverse=True,
    )[:10]

    cards: list[MethodSurveyCard] = []
    for doc_key, bucket in scored:
        doc_uuid = UUID(doc_key)
        first_ev = None
        for c in bucket:
            for ev in getattr(c, "support_evidence", ()) or ():
                if getattr(ev, "source_document_id", None) == doc_uuid:
                    first_ev = ev
                    break
            if first_ev is not None:
                break
        domain = ""
        if first_ev is not None:
            domain = str(getattr(first_ev, "domain", "") or "")
        title = title_by_doc.get(doc_uuid)
        display = (title or "").strip() or (domain or "source")
        paper_title = None
        if domain_looks_paper_like(domain) and title and title.strip():
            paper_title = title.strip()
        elif domain_looks_paper_like(domain):
            paper_title = None

        cids: list[str] = []
        eids: list[str] = []
        for cl in bucket:
            cid = getattr(cl, "claim_id", None)
            if isinstance(cid, UUID):
                cids.append(str(cid))
            for ev in getattr(cl, "support_evidence", ()) or ():
                if getattr(ev, "source_document_id", None) != doc_uuid:
                    continue
                eid = getattr(ev, "claim_evidence_id", None)
                if isinstance(eid, UUID):
                    eids.append(str(eid))
        cards.append(
            MethodSurveyCard(
                card_key=doc_key,
                display_name=display[:200],
                paper_title=paper_title,
                primary_domain=domain,
                claim_ids=tuple(dict.fromkeys(cids)),
                evidence_ids=tuple(dict.fromkeys(eids)),
            )
        )
    return cards
