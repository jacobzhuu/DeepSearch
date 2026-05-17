from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReportArchetypeLiteral = Literal[
    "research_survey",
    "technical_comparison",
    "news_update",
    "general",
]


class StructuredSynthesisStageFlags(BaseModel):
    """Feature flags for which LLM sections are requested / rendered."""

    structure: bool = True
    method_cards: bool = True
    comparison_table: bool = True
    insights: bool = True


class EvidenceBackedText(BaseModel):
    text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class ArchetypeSectionOutline(BaseModel):
    title: str = ""
    purpose: str = ""
    required_evidence_types: list[str] = Field(default_factory=list)


class ArchetypeJudgePayload(BaseModel):
    report_archetype: ReportArchetypeLiteral = "general"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    section_outline: list[ArchetypeSectionOutline] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class MethodInsightPayload(BaseModel):
    text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    inference_strength: Literal["low", "moderate", "high"] = "low"
    caveat: str = ""


class MethodCardPayload(BaseModel):
    method_name: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    paper_title: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    problem: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    motivation: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    core_method: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    architecture_or_algorithm: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    objective_or_loss: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    datasets_or_tasks: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    metrics_or_results: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    limitations: EvidenceBackedText = Field(default_factory=EvidenceBackedText)
    insight: MethodInsightPayload = Field(default_factory=MethodInsightPayload)


class ComparisonCell(BaseModel):
    text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    competitive_framing: bool = False


class ComparisonDimension(BaseModel):
    name: str = ""
    why_relevant: str = ""
    cells: dict[str, ComparisonCell] = Field(default_factory=dict)


class ComparisonTablePayload(BaseModel):
    entities: list[str] = Field(default_factory=list)
    dimensions: list[ComparisonDimension] = Field(default_factory=list)


class InsightRow(BaseModel):
    text: str = ""
    type: Literal["synthesis", "inference", "caveated_projection"] = "synthesis"
    evidence_ids: list[str] = Field(default_factory=list)
    caveat: str = ""


class InsightsPayload(BaseModel):
    insights: list[InsightRow] = Field(default_factory=list)


class StructuredSynthesisBundle(BaseModel):
    """Root object returned by the LLM (or golden fixtures)."""

    model_config = ConfigDict(extra="ignore")

    archetype_judge: ArchetypeJudgePayload | None = None
    method_cards: list[MethodCardPayload] = Field(default_factory=list)
    comparison_table: ComparisonTablePayload | None = None
    insights: InsightsPayload | None = None
