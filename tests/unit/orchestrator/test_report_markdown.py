from __future__ import annotations

import re
from uuid import uuid4

from services.orchestrator.app.reporting.markdown import (
    ReportClaimItem,
    ReportEvidenceItem,
    ReportSourceItem,
    render_markdown_report,
)
from services.orchestrator.app.reporting.survey_cards import build_method_survey_cards


def _evidence(url: str = "https://arxiv.org/abs/1234.5678") -> ReportEvidenceItem:
    return ReportEvidenceItem(
        claim_evidence_id=uuid4(),
        citation_span_id=uuid4(),
        source_document_id=uuid4(),
        source_chunk_id=uuid4(),
        relation_type="support",
        score=0.9,
        canonical_url=url,
        domain=url.split("//", 1)[-1].split("/", 1)[0],
        chunk_no=1,
        start_offset=0,
        end_offset=10,
        excerpt="stub excerpt for tests",
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


def test_render_markdown_research_survey_fixture_offline_regression_zh() -> None:
    """Offline research_survey: fake multi-source claims → stable Markdown sections + table."""
    rq = "请给出测试主题的多方法文献综述"
    titles = [
        "Method A: Graph Routing Paper",
        "Method B: Message Passing Survey",
        "Method C: Application Notes",
        "Method D: Optimization Study",
    ]
    claims: list[ReportClaimItem] = []
    sources: list[ReportSourceItem] = []
    for i, title in enumerate(titles):
        sid = uuid4()
        url = f"https://arxiv.org/abs/2026.{1000 + i}.0001"
        ev = ReportEvidenceItem(
            claim_evidence_id=uuid4(),
            citation_span_id=uuid4(),
            source_document_id=sid,
            source_chunk_id=uuid4(),
            relation_type="support",
            score=0.9,
            canonical_url=url,
            domain="arxiv.org",
            chunk_no=1,
            start_offset=0,
            end_offset=20,
            excerpt=f"excerpt for {title}",
        )

        def add(statement: str, category: str) -> None:
            claims.append(
                ReportClaimItem(
                    claim_id=uuid4(),
                    statement=statement,
                    claim_type="factual",
                    confidence=0.9,
                    verification_status="supported",
                    rationale=None,
                    support_evidence=[ev],
                    contradict_evidence=[],
                    claim_category=category,
                    support_level="normal",
                )
            )

        if i == 0:
            add("Method A 的研究问题是图上的最短路径路由。", "definition")
            add("Method A 使用稀疏邻接矩阵作为输入图数据。", "mechanism")
            add("Method A 的关键技术是分层注意力路由模块。", "mechanism")
            add("Method A 的优化目标是最小化期望路径长度。", "mechanism")
            add("Method A 在公开基准上 F1 提升约 3%。", "feature")
            add("Method A 的优势是更低的平均跳数。", "feature")
            add("Method A 的局限是对超大规模图内存压力较大。", "privacy")
            add("Method A 适用于中等规模网络规划场景。", "deployment/self_hosting")
        elif i == 1:
            add("Method B 的研究问题是如何在多智能体间传递证据。", "definition")
            add("Method B 以消息日志作为核心图式数据。", "mechanism")
            add("Method B 的关键技术是可验证消息封装。", "mechanism")
            add("Method B 的优化目标是降低通信轮次。", "mechanism")
            add("Method B 的实验指标显示延迟下降 12%。", "feature")
            add("Method B 的优势是更清晰的失败边界。", "feature")
            add("Method B 的局限是引入额外序列化开销。", "privacy")
            add("Method B 适用于需要审计轨迹的编排场景。", "deployment/self_hosting")
        elif i == 2:
            add("Method C 适用于在线风控流水线。", "deployment/self_hosting")
            add("Method C 在 A/B 测试中 Recall 提升。", "feature")
            add("Method C 的局限是依赖高质量特征工程。", "privacy")
        else:
            add("Method D 的研究问题是长文档下的证据聚合。", "definition")
            add("Method D 的关键技术是跨段落指针网络。", "mechanism")
            add("Method D 的优化目标是最大化证据一致性分数。", "mechanism")
            add("Method D 的实验指标在 HotpotQA 上 EM 提升。", "feature")
            add("Method D 的优势是更稳的可解释引用。", "feature")
            add("Method D 的局限是训练成本较高。", "privacy")
            add("Method D 适用于问答式研究助理场景。", "deployment/self_hosting")

        sources.append(
            ReportSourceItem(
                source_document_id=sid,
                canonical_url=url,
                domain="arxiv.org",
                title=title,
            )
        )

    out = render_markdown_report(
        task_id=uuid4(),
        research_question=rq,
        revision_no=1,
        claims=claims,
        sources=sources,
        report_language="zh-CN",
        report_archetype="research_survey",
    )
    md = out.markdown
    assert "绪论" in md
    assert "研究脉络" in md
    assert "方法深读" in md
    assert "横向对比表" in md
    assert "综合判断" in md
    for name in ("Method A", "Method B", "Method C"):
        assert name in md
    assert "| ---" in md or "| --- |" in md
    assert "当前证据不足" in md
    assert "**证据脚注**" in md
    foot_idx = md.index("**证据脚注**")
    foot_block = md[foot_idx : foot_idx + 800]
    nums = [int(x) for x in re.findall(r"\[\^(\d+)\]:", foot_block)]
    assert nums == list(range(1, len(nums) + 1))


def test_render_markdown_research_survey_structure_zh() -> None:
    ev1 = _evidence("https://arxiv.org/abs/1111.1111")
    ev2 = _evidence("https://arxiv.org/abs/2222.2222")
    c1 = _claim("Method A uses recurrent state for routing.", ev1)
    c2 = _claim("Method B relies on explicit message passing.", ev2)
    src1 = ReportSourceItem(
        source_document_id=ev1.source_document_id,
        canonical_url=ev1.canonical_url,
        domain=ev1.domain,
        title="Paper title alpha",
    )
    src2 = ReportSourceItem(
        source_document_id=ev2.source_document_id,
        canonical_url=ev2.canonical_url,
        domain=ev2.domain,
        title="Paper title beta",
    )
    out = render_markdown_report(
        task_id=uuid4(),
        research_question="请给出多智能体编排方法的文献综述",
        revision_no=1,
        claims=[c1, c2],
        sources=[src1, src2],
        report_language="zh-CN",
        report_archetype="research_survey",
    )
    md = out.markdown
    assert "绪论" in md
    assert "研究脉络" in md
    assert "横向对比" in md
    assert "综合判断" in md
    assert "附录" in md


def test_build_method_survey_cards_clusters_by_source_document() -> None:
    ev_a = _evidence("https://arxiv.org/abs/aaaa.bbbb")
    ev_b = _evidence("https://openreview.net/pdf?id=xyz")
    c1 = _claim("Claim one for cluster A", ev_a)
    c2 = _claim("Claim two for cluster A", ev_a)
    c3 = _claim("Claim for cluster B", ev_b)
    src_a = ReportSourceItem(
        source_document_id=ev_a.source_document_id,
        canonical_url=ev_a.canonical_url,
        domain=ev_a.domain,
        title="Shared title A",
    )
    src_b = ReportSourceItem(
        source_document_id=ev_b.source_document_id,
        canonical_url=ev_b.canonical_url,
        domain=ev_b.domain,
        title="Title B",
    )
    cards = build_method_survey_cards([c1, c2, c3], [src_a, src_b])
    assert len(cards) == 2
    keys = {c.card_key for c in cards}
    assert str(ev_a.source_document_id) in keys
    assert str(ev_b.source_document_id) in keys
