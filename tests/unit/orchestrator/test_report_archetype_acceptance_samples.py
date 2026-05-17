"""Local acceptance checks for report archetype routing and deterministic Markdown shape.

No live pipeline or network; uses ``render_markdown_report`` + ``detect_report_archetype``.
"""

from __future__ import annotations

import re
from uuid import uuid4

from services.orchestrator.app.query_intent_signals import detect_report_archetype
from services.orchestrator.app.reporting.markdown import (
    ReportClaimItem,
    ReportEvidenceItem,
    ReportSourceItem,
    render_markdown_report,
)


def _evidence(
    *,
    url: str,
    source_document_id: object | None = None,
) -> ReportEvidenceItem:
    sid = source_document_id if source_document_id is not None else uuid4()
    return ReportEvidenceItem(
        claim_evidence_id=uuid4(),
        citation_span_id=uuid4(),
        source_document_id=sid,
        source_chunk_id=uuid4(),
        relation_type="support",
        score=0.9,
        canonical_url=url,
        domain=url.split("//", 1)[-1].split("/", 1)[0],
        chunk_no=1,
        start_offset=0,
        end_offset=12,
        excerpt="fixture excerpt",
    )


def _claim(statement: str, ev: ReportEvidenceItem, *, category: str = "mechanism") -> ReportClaimItem:
    return ReportClaimItem(
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


def test_acceptance_a_research_survey_structural_holes_query() -> None:
    q = "请写一份结构洞理论在图神经网络与推荐系统中的综合研究报告"
    assert detect_report_archetype(q, plan_intent=None) == "research_survey"
    sid1, sid2 = uuid4(), uuid4()
    ev1 = _evidence(url="https://arxiv.org/abs/2026.0001.0001", source_document_id=sid1)
    ev2 = _evidence(url="https://arxiv.org/abs/2026.0002.0002", source_document_id=sid2)
    c1 = _claim("结构洞理论刻画了网络中信息互补位置。", ev1, category="definition")
    c2 = _claim("图神经网络利用消息传递聚合邻域信号。", ev2, category="mechanism")
    src1 = ReportSourceItem(
        source_document_id=sid1,
        canonical_url=ev1.canonical_url,
        domain=ev1.domain,
        title="GNN structural holes survey A",
    )
    src2 = ReportSourceItem(
        source_document_id=sid2,
        canonical_url=ev2.canonical_url,
        domain=ev2.domain,
        title="Recsys structural holes survey B",
    )
    out = render_markdown_report(
        task_id=uuid4(),
        research_question=q,
        revision_no=1,
        claims=[c1, c2],
        sources=[src1, src2],
        report_language="zh-CN",
        report_archetype=detect_report_archetype(q),
    )
    md = out.markdown
    for needle in ("绪论", "研究脉络", "方法深读", "横向对比表", "综合判断与展望"):
        assert needle in md
    assert "GNN structural" in md or "Recsys structural" in md
    for bad in ("## 新闻更新", "## News update", "近期更新主线"):
        assert bad not in md


def test_acceptance_b_langgraph_technical_comparison_zh() -> None:
    q = (
        "详细解释 LangGraph 是什么、它如何工作，以及它相比 AutoGen / CrewAI 的主要区别。"
    )
    assert detect_report_archetype(q, plan_intent=None) == "technical_comparison"
    ev_lg = _evidence(url="https://docs.langchain.com/oss/python/langgraph/overview")
    ev_ag = _evidence(url="https://microsoft.github.io/autogen/stable/")
    ev_cr = _evidence(url="https://docs.crewai.com/en/index.html")
    claims = [
        _claim("LangGraph 以状态图组织多步推理与检查点。", ev_lg, category="mechanism"),
        _claim("AutoGen 侧重对话式代理编排与工具调用。", ev_ag, category="mechanism"),
        _claim("CrewAI 以角色与任务队列组织协作代理。", ev_cr, category="mechanism"),
    ]
    out = render_markdown_report(
        task_id=uuid4(),
        research_question=q,
        revision_no=1,
        claims=claims,
        sources=[],
        report_language="zh-CN",
        report_archetype=detect_report_archetype(q),
    )
    md = out.markdown
    assert "技术对象横向对比表" in md
    assert "LangGraph" in md and "AutoGen" in md and "CrewAI" in md
    assert "是什么" in md and "如何工作" in md
    assert "方法深读" not in md
    assert "绪论（材料边界）" not in md


def test_acceptance_c_transformer_cnn_no_agent_stack() -> None:
    q = "Transformer 和 CNN 的区别是什么？各自如何工作？"
    assert detect_report_archetype(q, plan_intent=None) == "technical_comparison"
    ev_t = _evidence(url="https://arxiv.org/abs/1706.03762")
    ev_c = _evidence(url="https://arxiv.org/abs/1511.00561")
    claims = [
        _claim("Transformer 依赖自注意力建立全局依赖。", ev_t, category="mechanism"),
        _claim("CNN 通过局部卷积与池化提取平移不变特征。", ev_c, category="mechanism"),
    ]
    out = render_markdown_report(
        task_id=uuid4(),
        research_question=q,
        revision_no=1,
        claims=claims,
        sources=[],
        report_language="zh-CN",
        report_archetype=detect_report_archetype(q),
    )
    md = out.markdown
    assert "技术对象横向对比表" in md
    assert "Transformer" in md and "CNN" in md
    low = md.lower()
    for bad in ("langgraph", "autogen", "crewai"):
        assert bad not in low
    for bad in ("## 新闻更新", "## News update"):
        assert bad not in md
    assert "方法深读" not in md


def test_acceptance_d_chatgpt_news_no_survey_sections() -> None:
    q = "介绍一下 ChatGPT 最近的官方更新"
    assert detect_report_archetype(q, plan_intent=None) == "news_update"
    ev = _evidence(url="https://openai.com/index/")
    claim = _claim("OpenAI 发布了 ChatGPT 的补丁说明与功能调整。", ev, category="feature")
    out = render_markdown_report(
        task_id=uuid4(),
        research_question=q,
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="zh-CN",
        report_archetype=detect_report_archetype(q),
    )
    md = out.markdown
    assert "方法深读" not in md
    assert "绪论（材料边界）" not in md
    assert "横向对比表" not in md


def test_acceptance_footnotes_monotonic_in_comparison_table() -> None:
    q = "React 和 Vue 的主要区别是什么？各自如何工作？"
    ev1 = _evidence(url="https://react.dev/learn")
    ev2 = _evidence(url="https://vuejs.org/guide/introduction.html")
    claims = [
        _claim("React 使用虚拟 DOM 协调更新。", ev1, category="mechanism"),
        _claim("Vue 提供渐进式视图层与组合式 API。", ev2, category="mechanism"),
    ]
    out = render_markdown_report(
        task_id=uuid4(),
        research_question=q,
        revision_no=1,
        claims=claims,
        sources=[],
        report_language="zh-CN",
        report_archetype="technical_comparison",
    )
    md = out.markdown
    foot_block = md.split("**证据脚注**", 1)[-1] if "**证据脚注**" in md else md
    nums = [int(x) for x in re.findall(r"\[\^(\d+)\]:", foot_block)]
    assert nums == list(range(1, len(nums) + 1))
