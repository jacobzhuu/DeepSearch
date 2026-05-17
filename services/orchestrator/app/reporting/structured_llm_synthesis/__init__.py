"""Optional LLM-produced structured synthesis (JSON) with deterministic validation + render."""

from services.orchestrator.app.reporting.structured_llm_synthesis.errors import (
    StructuredSynthesisValidationError,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.invoke import (
    invoke_structured_synthesis_bundle,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.render import (
    append_to_rendered_markdown,
    render_validated_bundle_markdown,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.schema import (
    ArchetypeJudgePayload,
    ComparisonTablePayload,
    InsightRow,
    MethodCardPayload,
    StructuredSynthesisBundle,
    StructuredSynthesisStageFlags,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.validate import (
    bundle_has_renderable_content,
    validate_and_sanitize_bundle,
)

__all__ = [
    "ArchetypeJudgePayload",
    "ComparisonTablePayload",
    "InsightRow",
    "MethodCardPayload",
    "StructuredSynthesisBundle",
    "StructuredSynthesisStageFlags",
    "StructuredSynthesisValidationError",
    "append_to_rendered_markdown",
    "bundle_has_renderable_content",
    "invoke_structured_synthesis_bundle",
    "render_validated_bundle_markdown",
    "validate_and_sanitize_bundle",
]
