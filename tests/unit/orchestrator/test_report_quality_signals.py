from __future__ import annotations

from uuid import uuid4

from services.orchestrator.app.claims.drafting import classify_query_intent
from services.orchestrator.app.planning.planner import build_default_research_plan
from services.orchestrator.app.reporting.grounded_llm import (
    _build_evidence_anchor_map,
    _dedupe_executive_against_sections,
    _dedupe_sections_cross_section,
    _display_anchor_by_evidence_id,
    _GroundedItem,
    _migrate_competitive_core_items,
    _system_prompt,
)
from services.orchestrator.app.reporting.markdown import ReportClaimItem, ReportEvidenceItem, render_markdown_report
from services.orchestrator.app.research_quality.answer_slots import answer_slots_for_query
from services.orchestrator.app.query_intent_signals import (
    conservative_research_survey_source_hint,
    detect_report_archetype,
    extract_comparison_entities,
    query_asks_comparison,
    query_asks_technical_explanation,
    query_requests_explanation_comparison_template,
    query_requests_research_survey,
)
from services.orchestrator.app.research_quality.source_intent import source_intent_report_core_eligible
from services.orchestrator.app.services.reporting import _merge_github_readme_evidence_items


SAMPLE_ZH = (
    "详细解释 LangGraph 是什么、它如何工作，以及它相比 AutoGen / CrewAI 的主要区别。"
)


def test_search_aggregate_url_classifies_non_core_intent() -> None:
    from services.orchestrator.app.research_quality.source_intent import classify_source_intent

    row = classify_source_intent(
        canonical_url="https://www.google.com/search?q=LangGraph+CrewAI",
        domain="google.com",
        title="Google Search",
        query=SAMPLE_ZH,
    )
    assert row.source_category == "search_or_topic_aggregate"
    assert source_intent_report_core_eligible(row.source_intent) is False


def test_chinese_technical_comparison_query_gets_explanation_and_comparison_slots() -> None:
    assert query_asks_technical_explanation(SAMPLE_ZH)
    assert query_asks_comparison(SAMPLE_ZH)
    slots = answer_slots_for_query(SAMPLE_ZH)
    slot_ids = {s.slot_id for s in slots}
    assert "definition" in slot_ids
    assert "execution_model" in slot_ids or "core_abstractions" in slot_ids
    assert "comparison_mechanism" in slot_ids
    assert "comparison_tradeoffs" in slot_ids


def test_chinese_langgraph_plan_uses_explanation_comparison_template() -> None:
    prompt = _system_prompt("zh-CN", research_question=SAMPLE_ZH, research_plan={})
    assert "必须恰好 4 节" not in prompt
    assert "至少 6 节" in prompt
    assert "Markdown 对比表" in prompt
    assert "是什么" in prompt or "如何工作" in prompt
    assert "LangGraph" in prompt


def test_grounded_report_dedupes_summary_against_sections() -> None:
    item = _GroundedItem(
        text="Same claim text.",
        claim_ids=("c1",),
        claim_evidence_ids=("e1",),
        citation_span_ids=("s1",),
    )
    exec_items = [item]
    sections = [("Body", [item])]
    _, new_secs = _dedupe_executive_against_sections(exec_items, sections)
    assert new_secs[0][1] == []


def test_github_topics_are_not_report_core_evidence() -> None:
    assert source_intent_report_core_eligible("github_topic") is False
    assert source_intent_report_core_eligible("search_or_topic_aggregate") is False
    assert source_intent_report_core_eligible("official_docs_reference") is True


def test_competitive_claim_is_moved_out_of_core_sections() -> None:
    q = "LangGraph vs CrewAI tradeoffs"
    cid = str(uuid4())
    eid = str(uuid4())
    span = str(uuid4())
    sdoc = uuid4()
    schunk = uuid4()
    ev = ReportEvidenceItem(
        claim_evidence_id=uuid4(),
        citation_span_id=uuid4(),
        source_document_id=sdoc,
        source_chunk_id=schunk,
        relation_type="support",
        score=0.9,
        canonical_url="https://github.com/crewaiinc/crewai/blob/main/README.md",
        domain="github.com",
        chunk_no=1,
        start_offset=0,
        end_offset=10,
        excerpt="sample",
        source_intent="github_readme_or_repo",
        source_role="official_repository",
    )
    claim = ReportClaimItem(
        claim_id=uuid4(),
        statement="CrewAI demonstrates performance advantages over LangGraph when executing agents.",
        claim_type="factual",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[ev],
        contradict_evidence=[],
        support_level="normal",
    )
    claim_by_id = {str(claim.claim_id): claim}
    evidence_by_id = {str(ev.claim_evidence_id): ev}
    exec_item = _GroundedItem(
        text="Competitive line.",
        claim_ids=(str(claim.claim_id),),
        claim_evidence_ids=(str(ev.claim_evidence_id),),
        citation_span_ids=(span,),
    )
    exec2, sections2, unc2 = _migrate_competitive_core_items(
        [exec_item],
        [],
        [],
        claim_by_id=claim_by_id,
        evidence_by_id=evidence_by_id,
        research_question=q,
    )
    assert exec2 == []
    assert len(unc2) == 1
    assert unc2[0].support_type == "inference"


def test_readme_raw_main_master_blob_share_one_report_anchor() -> None:
    urls = [
        "https://raw.githubusercontent.com/crewaiinc/crewai/main/README.md",
        "https://raw.githubusercontent.com/crewaiinc/crewai/master/README.md",
        "https://github.com/crewaiinc/crewai/blob/main/README.md",
    ]
    claims = []
    for url in urls:
        ev = ReportEvidenceItem(
            claim_evidence_id=uuid4(),
            citation_span_id=uuid4(),
            source_document_id=uuid4(),
            source_chunk_id=uuid4(),
            relation_type="support",
            score=0.9,
            canonical_url=url,
            domain="github.com",
            chunk_no=1,
            start_offset=0,
            end_offset=4,
            excerpt="body",
        )
        claims.append(
            ReportClaimItem(
                claim_id=uuid4(),
                statement=f"claim for {url}",
                claim_type="factual",
                confidence=0.9,
                verification_status="supported",
                rationale=None,
                support_evidence=[ev],
                contradict_evidence=[],
                support_level="normal",
            )
        )
    anchors = _build_evidence_anchor_map(claims)
    assert len(set(anchors.values())) == 1
    merged = _merge_github_readme_evidence_items([c.support_evidence[0] for c in claims])
    assert len(merged) == 1


def test_report_prepare_expands_incomplete_excerpt_to_sentence_boundary() -> None:
    from types import SimpleNamespace

    from services.orchestrator.app.services.reporting import _display_excerpt_for_report_bundle

    chunk = (
        "First sentence is stable. CrewAI demonstrates significant performance advantages over "
        "LangGraph, executing 5. Second sentence completes the thought."
    )
    excerpt = "CrewAI demonstrates significant performance"
    start = chunk.index(excerpt)
    end = start + len(excerpt)
    span = SimpleNamespace(excerpt=excerpt, start_offset=start, end_offset=end)
    chunk_obj = SimpleNamespace(text=chunk)
    expanded = _display_excerpt_for_report_bundle(span, chunk_obj)
    assert expanded.endswith(".")
    assert "executing 5" in expanded
    assert not expanded.rstrip().endswith(",")


def test_display_anchor_skips_unreferenced_middle_stable_ids() -> None:
    items = [
        _GroundedItem(
            text="a",
            claim_ids=("c1",),
            claim_evidence_ids=("e1", "e3"),
            citation_span_ids=("s",),
        )
    ]
    stable = {"e1": "e1", "e2": "e2", "e3": "e3"}
    display = _display_anchor_by_evidence_id(
        executive_items=items,
        sections=[],
        uncertainty_items=[],
        stable_anchor_by_eid=stable,
        table_eid_order=None,
    )
    assert display["e1"] == "e1"
    assert display["e3"] == "e2"


def test_classify_query_intent_technical_comparison_chinese() -> None:
    intent = classify_query_intent(SAMPLE_ZH)
    assert intent.intent_name == "technical_comparison"


def test_plan_news_intent_does_not_override_chinese_tech_compare_template() -> None:
    assert query_requests_explanation_comparison_template(
        SAMPLE_ZH, plan_intent="news"
    )
    prompt = _system_prompt(
        "zh-CN", research_question=SAMPLE_ZH, research_plan={"intent": "news"}
    )
    assert "必须恰好 4 节" not in prompt
    assert "至少 6 节" in prompt


def test_dedupe_sections_cross_section_removes_repeat() -> None:
    item = _GroundedItem(
        text="Dup.",
        claim_ids=("c1",),
        claim_evidence_ids=("e1",),
        citation_span_ids=("s1",),
    )
    sections = [("A", [item]), ("B", [item])]
    out = _dedupe_sections_cross_section(sections)
    assert len(out[0][1]) == 1
    assert out[1][1] == []


def test_planner_technical_comparison_adds_queries() -> None:
    plan = build_default_research_plan(
        SAMPLE_ZH,
        max_subquestions=8,
        max_search_queries=20,
        planner_mode="deterministic",
    )
    texts = " ".join(q.query_text for q in plan.search_queries).lower()
    assert "langgraph" in texts
    assert "autogen" in texts or "crewai" in texts


def test_detect_report_archetype_news_beats_survey_cue() -> None:
    q = "综述 Graph Neural Networks 近30天的最新突破"
    assert detect_report_archetype(q, plan_intent=None) == "news_update"


def test_detect_report_archetype_survey_beats_technical_comparison() -> None:
    q = "请给出 LangGraph 与 AutoGen 的系统性文献综述"
    assert detect_report_archetype(q, plan_intent=None) == "research_survey"


def test_query_requests_research_survey_markers() -> None:
    assert query_requests_research_survey("Write a literature review on causal discovery")
    assert query_requests_research_survey("多智能体编排方法的综述")


def test_conservative_paper_cluster_survey_hint_requires_many_paper_domains() -> None:
    assert not conservative_research_survey_source_hint(
        "taxonomy of agent methods",
        source_domains=["arxiv.org"] * 5,
    )
    assert conservative_research_survey_source_hint(
        "taxonomy of agent methods and families overview",
        source_domains=["arxiv.org"] * 6,
    )


def test_grounded_llm_system_prompt_research_survey_zh() -> None:
    prompt = _system_prompt(
        "zh-CN",
        research_question="survey related work on memory architectures",
        research_plan={"intent": "definition_how_it_works"},
        report_archetype="research_survey",
    )
    assert "绪论" in prompt
    assert "横向对比" in prompt


def test_extract_comparison_entities_bert_gpt_t5_zh() -> None:
    q = "比较 BERT、GPT 和 T5 的预训练目标"
    assert extract_comparison_entities(q) == ["BERT", "GPT", "T5"]


def test_extract_comparison_entities_transformer_cnn_zh() -> None:
    q = "Transformer 和 CNN 的区别是什么？各自如何工作？"
    ents = extract_comparison_entities(q)
    assert "Transformer" in ents and "CNN" in ents


def test_extract_comparison_entities_react_vue_zh() -> None:
    q = "React 和 Vue 的主要区别是什么？"
    assert extract_comparison_entities(q) == ["React", "Vue"]


def test_extract_comparison_entities_postgres_mysql_zh() -> None:
    q = "PostgreSQL 和 MySQL 有什么区别？"
    ents = extract_comparison_entities(q)
    assert "PostgreSQL" in ents and "MySQL" in ents


def test_query_requests_technical_comparison_template_generalizes() -> None:
    q = "React 和 Vue 的主要区别是什么？各自如何工作？"
    assert query_requests_explanation_comparison_template(q, plan_intent=None) is True


def test_detect_report_archetype_recency_beats_named_entity_comparison() -> None:
    q = "比较 BERT、GPT 和 T5 在近30天的最新论文突破"
    assert detect_report_archetype(q, plan_intent=None) == "news_update"


def test_deterministic_markdown_technical_comparison_table_not_agent_framework_centric() -> None:
    q = "PostgreSQL 和 MySQL 有什么区别？各自如何工作？"
    ev_pg = ReportEvidenceItem(
        claim_evidence_id=uuid4(),
        citation_span_id=uuid4(),
        source_document_id=uuid4(),
        source_chunk_id=uuid4(),
        relation_type="support",
        score=0.9,
        canonical_url="https://postgresql.org/docs/",
        domain="postgresql.org",
        chunk_no=1,
        start_offset=0,
        end_offset=8,
        excerpt="postgres excerpt",
    )
    ev_my = ReportEvidenceItem(
        claim_evidence_id=uuid4(),
        citation_span_id=uuid4(),
        source_document_id=uuid4(),
        source_chunk_id=uuid4(),
        relation_type="support",
        score=0.9,
        canonical_url="https://dev.mysql.com/doc/",
        domain="mysql.com",
        chunk_no=1,
        start_offset=0,
        end_offset=8,
        excerpt="mysql excerpt",
    )
    claims = [
        ReportClaimItem(
            claim_id=uuid4(),
            statement="PostgreSQL 的核心定位强调标准一致性与扩展类型。",
            claim_type="factual",
            confidence=0.9,
            verification_status="supported",
            rationale=None,
            support_evidence=[ev_pg],
            contradict_evidence=[],
            claim_category="definition",
            support_level="normal",
        ),
        ReportClaimItem(
            claim_id=uuid4(),
            statement="PostgreSQL 通过 MVCC 管理事务隔离与可见性。",
            claim_type="factual",
            confidence=0.9,
            verification_status="supported",
            rationale=None,
            support_evidence=[ev_pg],
            contradict_evidence=[],
            claim_category="mechanism",
            support_level="normal",
        ),
        ReportClaimItem(
            claim_id=uuid4(),
            statement="MySQL 的核心定位更偏广谱 OLTP 与生态工具链。",
            claim_type="factual",
            confidence=0.9,
            verification_status="supported",
            rationale=None,
            support_evidence=[ev_my],
            contradict_evidence=[],
            claim_category="definition",
            support_level="normal",
        ),
        ReportClaimItem(
            claim_id=uuid4(),
            statement="MySQL 在默认配置下对长事务与锁竞争更敏感。",
            claim_type="factual",
            confidence=0.9,
            verification_status="supported",
            rationale=None,
            support_evidence=[ev_my],
            contradict_evidence=[],
            claim_category="mechanism",
            support_level="normal",
        ),
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
    md = out.markdown.lower()
    assert "技术对象横向对比表" in out.markdown
    assert "langgraph" not in md
    assert "autogen" not in md
    assert "crewai" not in md
    assert "postgresql" in md and "mysql" in md
    assert "| ---" in md or "| --- |" in md


def test_planner_bert_gpt_t5_comparison_queries_avoid_agent_stack_hardcoding() -> None:
    plan = build_default_research_plan(
        "比较 BERT、GPT 和 T5 的预训练目标",
        max_subquestions=8,
        max_search_queries=30,
        planner_mode="deterministic",
    )
    blob = " ".join(q.query_text for q in plan.search_queries).lower()
    assert "langgraph" not in blob
    assert "autogen" not in blob
    assert "crewai" not in blob
    assert "bert" in blob and "gpt" in blob and "t5" in blob


def test_classify_intent_technical_comparison_generalized_zh() -> None:
    intent = classify_query_intent("Transformer 和 CNN 的区别是什么？各自如何工作？")
    assert intent.intent_name == "technical_comparison"
