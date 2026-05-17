from __future__ import annotations

import json
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from services.orchestrator.app.reporting.markdown import (
    RenderedMarkdownReport,
    ReportClaimItem,
    ReportEvidenceItem,
    ReportSourceItem,
)
from services.orchestrator.app.services.reporting import ReportSynthesisService


class _DummyLLM:
    """Non-noop provider token so structured synthesis may call invoke when patched."""

    name = "dummy"

    def generate(self, request):  # noqa: ARG002
        from services.orchestrator.app.llm.types import LLMResponse

        return LLMResponse(
            text="{}",
            model="m",
            provider=self.name,
            usage=None,
            raw_response_id="dummy",
            finish_reason="stop",
        )


def _ev(*, eid, sid, url: str) -> ReportEvidenceItem:
    return ReportEvidenceItem(
        claim_evidence_id=eid,
        citation_span_id=uuid4(),
        source_document_id=sid,
        source_chunk_id=uuid4(),
        relation_type="support",
        score=0.9,
        canonical_url=url,
        domain=url.split("//", 1)[-1].split("/", 1)[0],
        chunk_no=1,
        start_offset=0,
        end_offset=8,
        excerpt="excerpt text",
        source_intent="official_docs_reference",
    )


def _claim(statement: str, ev: ReportEvidenceItem) -> ReportClaimItem:
    return ReportClaimItem(
        claim_id=uuid4(),
        statement=statement,
        claim_type="factual",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[ev],
        contradict_evidence=[],
        claim_category="mechanism",
        support_level="normal",
    )


def _service(**kwargs: object) -> ReportSynthesisService:
    base: dict[str, object] = {
        "session": MagicMock(),
        "task_repository": MagicMock(),
        "claim_repository": MagicMock(),
        "claim_evidence_repository": MagicMock(),
        "report_artifact_repository": MagicMock(),
        "task_event_repository": MagicMock(),
        "object_store": MagicMock(),
        "report_storage_bucket": "reports",
        "llm_provider": _DummyLLM(),
        "llm_model": "m",
        "llm_structured_synthesis_enabled": False,
        "llm_report_structure_enabled": False,
        "llm_method_card_extraction_enabled": False,
        "llm_comparison_table_enabled": False,
        "llm_synthesis_insights_enabled": False,
    }
    base.update(kwargs)
    return ReportSynthesisService(**base)  # type: ignore[arg-type]


def _rendered() -> RenderedMarkdownReport:
    return RenderedMarkdownReport(
        title="T",
        markdown="# Title\n\nBody paragraph.\n",
        supported_count=1,
        mixed_count=0,
        unsupported_count=0,
        contradicted_count=0,
        draft_count=0,
        answer_relevant_count=1,
        excluded_low_quality_count=0,
    )


def test_structured_synthesis_disabled_preserves_original_report() -> None:
    svc = _service(llm_structured_synthesis_enabled=False)
    r0 = _rendered()
    out, diag = svc._maybe_append_structured_llm_synthesis(
        rendered=r0,
        task=SimpleNamespace(id=uuid4(), query="Compare PyTorch and TensorFlow"),
        report_claims=[],
        sources=[],
        report_language="zh-CN",
        research_plan_payload=None,
        report_archetype="technical_comparison",
        plan_intent=None,
    )
    assert out.markdown == r0.markdown
    assert "LLM 辅助结构化综合" not in out.markdown
    assert diag["enabled"] is False
    assert diag["attempted"] is False
    assert diag["rendered"] is False


def test_structured_synthesis_invalid_payload_falls_back_to_original_report() -> None:
    eid = uuid4()
    sid = uuid4()
    ev = _ev(eid=eid, sid=sid, url="https://pytorch.org/docs/stable/index.html")
    claim = _claim("PyTorch provides tensor APIs.", ev)
    svc = _service(
        llm_structured_synthesis_enabled=True,
        llm_report_structure_enabled=True,
        llm_method_card_extraction_enabled=False,
        llm_comparison_table_enabled=False,
        llm_synthesis_insights_enabled=False,
    )
    r0 = _rendered()
    with patch(
        "services.orchestrator.app.services.reporting.invoke_structured_synthesis_bundle",
        return_value={},
    ):
        out, diag = svc._maybe_append_structured_llm_synthesis(
            rendered=r0,
            task=SimpleNamespace(id=uuid4(), query="PyTorch overview survey"),
            report_claims=[claim],
            sources=[],
            report_language="zh-CN",
            research_plan_payload=None,
            report_archetype="research_survey",
            plan_intent=None,
        )
    assert out.markdown == r0.markdown
    assert diag["rendered"] is False
    assert diag["attempted"] is True
    assert diag["skipped_reason"] == "missing_archetype_judge"
    assert diag["warnings_count"] >= 1

    with patch(
        "services.orchestrator.app.services.reporting.invoke_structured_synthesis_bundle",
        side_effect=json.JSONDecodeError("expecting value", "doc", 0),
    ):
        out2, diag2 = svc._maybe_append_structured_llm_synthesis(
            rendered=r0,
            task=SimpleNamespace(id=uuid4(), query="PyTorch overview survey"),
            report_claims=[claim],
            sources=[],
            report_language="zh-CN",
            research_plan_payload=None,
            report_archetype="research_survey",
            plan_intent=None,
        )
    assert out2.markdown == r0.markdown
    assert diag2["skipped_reason"] == "parse_error"
    assert diag2["warnings_count"] >= 1


def test_structured_synthesis_valid_payload_appends_checked_section() -> None:
    eid1 = uuid4()
    eid2 = uuid4()
    sid1 = uuid4()
    sid2 = uuid4()
    ev1 = _ev(eid=eid1, sid=sid1, url="https://pytorch.org/docs/stable/index.html")
    ev2 = _ev(eid=eid2, sid=sid2, url="https://www.tensorflow.org/learn")
    c1 = _claim("PyTorch uses eager execution by default.", ev1)
    c2 = _claim("TensorFlow 2 defaults to eager execution.", ev2)
    raw = {
        "comparison_table": {
            "entities": ["PyTorch", "TensorFlow"],
            "dimensions": [
                {
                    "name": "Execution",
                    "why_relevant": "default mode",
                    "cells": {
                        "PyTorch": {"text": "Eager-first API", "evidence_ids": [str(eid1)]},
                        "TensorFlow": {"text": "Eager default in TF2", "evidence_ids": [str(eid2)]},
                    },
                }
            ],
        }
    }
    svc = _service(
        llm_structured_synthesis_enabled=True,
        llm_report_structure_enabled=False,
        llm_method_card_extraction_enabled=False,
        llm_comparison_table_enabled=True,
        llm_synthesis_insights_enabled=False,
    )
    r0 = _rendered()
    with patch(
        "services.orchestrator.app.services.reporting.invoke_structured_synthesis_bundle",
        return_value=raw,
    ):
        out, diag = svc._maybe_append_structured_llm_synthesis(
            rendered=r0,
            task=SimpleNamespace(id=uuid4(), query="Compare PyTorch and TensorFlow for training"),
            report_claims=[c1, c2],
            sources=[
                ReportSourceItem(
                    source_document_id=sid1,
                    canonical_url=ev1.canonical_url,
                    domain=ev1.domain,
                    title="PyTorch docs",
                    source_intent="official_docs_reference",
                ),
                ReportSourceItem(
                    source_document_id=sid2,
                    canonical_url=ev2.canonical_url,
                    domain=ev2.domain,
                    title="TensorFlow",
                    source_intent="official_docs_reference",
                ),
            ],
            report_language="zh-CN",
            research_plan_payload=None,
            report_archetype="technical_comparison",
            plan_intent=None,
        )
    assert "LLM 辅助结构化综合（证据绑定，已校验）" in out.markdown
    assert diag["rendered"] is True
    assert diag["attempted"] is True
    assert "comparison_table" in diag["sections_rendered"]
    appendix = out.markdown.split("LLM 辅助结构化综合", 1)[-1]
    def_lines = [ln for ln in appendix.splitlines() if re.match(r"^\[\^\d+\]:", ln.strip())]
    nums: list[int] = []
    for ln in def_lines:
        m = re.match(r"^\[\^(\d+)\]:", ln.strip())
        if m:
            nums.append(int(m.group(1)))
    assert nums == sorted(set(nums))


def test_structured_synthesis_skips_recency_query() -> None:
    eid = uuid4()
    sid = uuid4()
    ev = _ev(eid=eid, sid=sid, url="https://pytorch.org/docs/stable/index.html")
    claim = _claim("PyTorch is a framework.", ev)
    svc = _service(
        llm_structured_synthesis_enabled=True,
        llm_report_structure_enabled=False,
        llm_method_card_extraction_enabled=False,
        llm_comparison_table_enabled=True,
        llm_synthesis_insights_enabled=False,
    )
    r0 = _rendered()
    with patch(
        "services.orchestrator.app.services.reporting.invoke_structured_synthesis_bundle"
    ) as inv:
        out, diag = svc._maybe_append_structured_llm_synthesis(
            rendered=r0,
            task=SimpleNamespace(id=uuid4(), query="TensorFlow 今年的官方更新有哪些"),
            report_claims=[claim],
            sources=[],
            report_language="zh-CN",
            research_plan_payload=None,
            report_archetype="technical_comparison",
            plan_intent=None,
        )
        inv.assert_not_called()
    assert out.markdown == r0.markdown
    assert diag["skipped_reason"] == "recency_query"
    assert diag["attempted"] is False
    assert diag["rendered"] is False
