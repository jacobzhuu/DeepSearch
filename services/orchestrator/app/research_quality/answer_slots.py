from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnswerSlot:
    slot_id: str
    label: str
    expected_claim_categories: tuple[str, ...]
    required: bool = True
    description: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "slot_id": self.slot_id,
            "label": self.label,
            "expected_claim_categories": list(self.expected_claim_categories),
            "required": self.required,
        }
        if self.description is not None:
            payload["description"] = self.description
        return payload


def answer_slots_for_query(query: str | None) -> list[AnswerSlot]:
    lower = (query or "").strip().lower()
    if _asks_comparison(lower):
        return [
            AnswerSlot(
                "comparison_scope",
                "Comparison scope",
                ("definition", "other"),
                description="What is being compared and why it matters.",
            ),
            AnswerSlot(
                "comparison_mechanism",
                "How each option works",
                ("mechanism", "feature"),
            ),
            AnswerSlot(
                "comparison_tradeoffs",
                "Tradeoffs and limitations",
                ("privacy", "feature", "deployment/self_hosting", "other"),
            ),
        ]
    if _asks_deployment(lower):
        return [
            AnswerSlot(
                "deployment_prerequisites",
                "Prerequisites",
                ("deployment/self_hosting",),
            ),
            AnswerSlot(
                "deployment_run_or_compose",
                "Docker run / Docker Compose",
                ("deployment/self_hosting", "feature"),
            ),
            AnswerSlot(
                "deployment_volumes",
                "Volumes",
                ("deployment/self_hosting", "feature"),
            ),
            AnswerSlot(
                "deployment_ports",
                "Ports",
                ("deployment/self_hosting", "feature"),
            ),
            AnswerSlot(
                "deployment_configuration",
                "Configuration",
                ("deployment/self_hosting", "feature", "mechanism"),
            ),
            AnswerSlot(
                "deployment_security",
                "Security",
                ("deployment/self_hosting", "privacy", "feature", "other"),
            ),
            AnswerSlot(
                "deployment_troubleshooting",
                "Troubleshooting",
                ("privacy", "feature", "other"),
            ),
            AnswerSlot(
                "deployment_update_maintenance",
                "Update / maintenance",
                ("deployment/self_hosting", "feature", "other"),
            ),
        ]
    if _asks_privacy(lower):
        return [
            AnswerSlot("definition", "What it is", ("definition",), required=False),
            AnswerSlot("privacy_advantages", "Privacy advantages", ("privacy",)),
            AnswerSlot(
                "privacy_limitations",
                "Privacy limitations",
                ("privacy", "feature", "other"),
            ),
            AnswerSlot("mechanism", "How the privacy model works", ("mechanism", "privacy")),
        ]
    if _asks_technical_explanation(lower):
        return [
            AnswerSlot(
                "definition",
                "Definition",
                ("definition",),
                description="What the system is and its core purpose.",
            ),
            AnswerSlot(
                "motivation_problem",
                "Motivation / problem solved",
                ("definition", "feature", "mechanism"),
                description="Why it exists and what problem it is meant to solve.",
            ),
            AnswerSlot(
                "core_abstractions",
                "Core abstractions",
                ("mechanism", "feature"),
                description="Primary concepts such as state, nodes, edges, graphs, tools, or APIs.",
            ),
            AnswerSlot(
                "architecture",
                "Architecture",
                ("mechanism",),
                description="How the main pieces fit together.",
            ),
            AnswerSlot(
                "execution_model",
                "Execution model",
                ("mechanism",),
                description="How work is scheduled, routed, resumed, or executed.",
            ),
            AnswerSlot(
                "workflow_lifecycle",
                "Workflow / lifecycle",
                ("mechanism", "feature"),
                description="How a task, graph, workflow, or run moves through its lifecycle.",
            ),
            AnswerSlot(
                "key_features",
                "Key features",
                ("feature", "mechanism"),
                required=False,
                description="Notable capabilities and integrations.",
            ),
            AnswerSlot(
                "examples_use_cases",
                "Examples / use cases",
                ("feature", "other"),
                required=False,
                description="Representative applications or examples.",
            ),
            AnswerSlot(
                "limitations",
                "Limitations",
                ("feature", "privacy", "other"),
                required=False,
                description="Caveats, tradeoffs, or constraints.",
            ),
            AnswerSlot(
                "comparison_positioning",
                "Comparison / positioning",
                ("definition", "feature", "other"),
                required=False,
                description="How it is positioned relative to adjacent tools or frameworks.",
            ),
            AnswerSlot(
                "official_sources",
                "Official sources",
                ("definition", "mechanism", "feature", "privacy"),
                description=(
                    "Evidence from official documentation, reference pages, or repositories."
                ),
            ),
        ]
    if _asks_definition_mechanism(lower):
        return [
            AnswerSlot("definition", "What it is", ("definition",)),
            AnswerSlot("mechanism", "How it works", ("mechanism",)),
            AnswerSlot(
                "privacy",
                "Privacy or trust model",
                ("privacy",),
                required=False,
            ),
            AnswerSlot(
                "features",
                "Key features or limitations",
                ("feature", "deployment/self_hosting"),
                required=False,
            ),
        ]
    return [
        AnswerSlot("overview", "Overview", ("definition", "other")),
        AnswerSlot("details", "Important details", ("mechanism", "feature")),
        AnswerSlot(
            "limitations",
            "Limitations or caveats",
            ("privacy", "feature", "other"),
            required=False,
        ),
    ]


def claim_categories_for_slots(slots: list[AnswerSlot]) -> list[str]:
    categories: list[str] = []
    seen: set[str] = set()
    for slot in slots:
        for category in slot.expected_claim_categories:
            if category in seen:
                continue
            categories.append(category)
            seen.add(category)
    return categories


def slot_ids_for_claim_category(category: str, *, query: str | None) -> list[str]:
    normalized_category = category.strip()
    if _asks_deployment((query or "").strip().lower()) and normalized_category == (
        "deployment/self_hosting"
    ):
        return []
    if _asks_technical_explanation((query or "").strip().lower()):
        return _technical_slot_ids_for_claim_category(normalized_category)
    return [
        slot.slot_id
        for slot in answer_slots_for_query(query)
        if normalized_category in slot.expected_claim_categories
    ]


def technical_slot_ids_for_text(
    *,
    text: str,
    category: str,
    query: str | None,
    source_intent: str | None = None,
) -> tuple[str, ...]:
    """Classify a technical-explanation evidence line into answer slots.

    This stays deterministic and deliberately lexical. It only refines slot ids;
    it does not create claims or evidence.
    """
    if not _asks_technical_explanation((query or "").strip().lower()):
        return ()

    normalized_category = category.strip()
    lower = text.lower()
    slots: list[str] = []

    def add(slot_id: str) -> None:
        if slot_id not in slots:
            slots.append(slot_id)

    if normalized_category == "definition" or _contains_any(
        lower,
        (
            " is a ",
            " is an ",
            " is the ",
            " are a ",
            " are an ",
            "framework",
            "library",
            "platform",
        ),
    ):
        add("definition")

    if _contains_any(
        lower,
        (
            "designed",
            "helps",
            "allows",
            "enables",
            "problem",
            "solve",
            "reliable",
            "complex",
            "long-running",
            "production",
            "agent",
            "agents",
            "orchestrat",
        ),
    ):
        add("motivation_problem")

    if normalized_category == "mechanism" or _contains_any(
        lower,
        (
            "abstraction",
            "state",
            "stategraph",
            "state graph",
            "graph",
            "node",
            "nodes",
            "edge",
            "edges",
            "tool",
            "tools",
            "api",
            "apis",
            "message",
            "messages",
        ),
    ):
        add("core_abstractions")

    if _contains_any(
        lower,
        (
            "architecture",
            "architectural",
            "component",
            "components",
            "graph-based",
            "graph based",
            "directed graph",
            "subgraph",
            "runtime",
            "low-level",
            "low level",
        ),
    ):
        add("architecture")

    if _contains_any(
        lower,
        (
            "execution",
            "execute",
            "executes",
            "runtime",
            "routing",
            "route",
            "conditional",
            "transition",
            "durable",
            "checkpoint",
            "checkpointing",
            "resume",
            "resum",
            "interrupt",
            "streaming",
        ),
    ):
        add("execution_model")

    if _contains_any(
        lower,
        (
            "workflow",
            "workflows",
            "lifecycle",
            "quickstart",
            "quick start",
            "getting started",
            "getting-started",
            "usage",
            "how to",
            "start",
            "end",
            "thread",
            "threads",
            "persistence",
            "persistent",
            "memory",
            "human-in-the-loop",
            "human in the loop",
            "review",
        ),
    ):
        add("workflow_lifecycle")

    if normalized_category == "feature" or _contains_any(
        lower,
        (
            "supports",
            "features",
            "capabilities",
            "integrations",
            "integration",
            "memory",
            "checkpoint",
            "streaming",
            "debug",
            "observability",
            "human-in-the-loop",
            "human in the loop",
            "sdk",
            "cli",
            "api",
            "apis",
        ),
    ):
        add("key_features")

    if _contains_any(
        lower,
        (
            "example",
            "examples",
            "use case",
            "use cases",
            "sample",
            "samples",
            "snippet",
            "snippets",
            "tutorial",
            "tutorials",
            "walkthrough",
            "walk-through",
            "guide",
            "guides",
            "reference app",
            "reference apps",
            "starter app",
            "starter apps",
            "boilerplate",
            "cookbook",
            "chatbot",
            "chatbots",
            "multi-agent",
            "multi agent",
            "customer support",
            "research assistant",
            "application",
            "applications",
        ),
    ):
        add("examples_use_cases")

    if _contains_any(
        lower,
        (
            "limitation",
            "limitations",
            "caveat",
            "caveats",
            "tradeoff",
            "trade-off",
            "constraint",
            "constraints",
            "requires",
            "not ",
            "unable",
            "unsupported",
            "known issue",
            "known issues",
            "experimental",
            "deprecated",
            "deprecation",
            "preview",
            "breaking change",
            "stability",
            "caution",
            "rough edge",
            "edge case",
            "security consideration",
            "not supported",
            "does not support",
        ),
    ):
        add("limitations")

    if _contains_any(
        lower,
        (
            "compare",
            "comparison",
            "alternative",
            "position",
            "positioning",
            "differs",
            "versus",
            " vs ",
            "langchain",
            "framework",
            "library",
            "platform",
        ),
    ):
        add("comparison_positioning")

    if _is_official_source_intent(source_intent):
        add("official_sources")

    if not slots:
        slots.extend(_technical_slot_ids_for_claim_category(normalized_category))
    return tuple(dict.fromkeys(slots))


def answer_slot_coverage(
    query: str | None,
    covered_claim_categories: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for slot in answer_slots_for_query(query):
        matched_categories = [
            category
            for category in slot.expected_claim_categories
            if category in covered_claim_categories
        ]
        rows.append(
            {
                **slot.to_payload(),
                "covered": bool(matched_categories),
                "matched_claim_categories": matched_categories,
            }
        )
    return rows


def missing_answer_slots(query: str | None, covered_claim_categories: set[str]) -> list[str]:
    return [
        row["slot_id"]
        for row in answer_slot_coverage(query, covered_claim_categories)
        if row["required"] and not row["covered"]
    ]


def _asks_definition_mechanism(lower_query: str) -> bool:
    return (
        "what is" in lower_query
        or "what are" in lower_query
        or "overview" in lower_query
        or "how does" in lower_query
        or "how do" in lower_query
    )


def _asks_technical_explanation(lower_query: str) -> bool:
    if not _asks_definition_mechanism(lower_query):
        return False
    if any(term in lower_query for term in ("deploy", "deployment", "docker", "install")):
        return False
    if "how does" in lower_query or "how do" in lower_query or "how it works" in lower_query:
        return True
    return any(
        term in lower_query
        for term in (
            "technical explanation",
            "architecture",
            "execution model",
            "workflow",
            "framework",
            "library",
            "agent",
            "orchestration",
        )
    )


def _asks_privacy(lower_query: str) -> bool:
    return any(
        term in lower_query
        for term in (
            "privacy",
            "private",
            "tracking",
            "tracked",
            "limitations",
            "advantages",
        )
    )


def _asks_deployment(lower_query: str) -> bool:
    return any(
        term in lower_query
        for term in ("deploy", "deployment", "docker", "install", "self-host", "self host")
    )


def _asks_comparison(lower_query: str) -> bool:
    return any(term in lower_query for term in ("compare", "comparison", "differences between"))


def _technical_slot_ids_for_claim_category(category: str) -> list[str]:
    return {
        "definition": ["definition", "motivation_problem", "comparison_positioning"],
        "mechanism": [
            "core_abstractions",
            "architecture",
            "execution_model",
            "workflow_lifecycle",
        ],
        "feature": ["key_features", "examples_use_cases", "limitations"],
        "privacy": ["limitations"],
        "deployment/self_hosting": ["key_features", "limitations"],
        "other": ["examples_use_cases", "comparison_positioning", "limitations"],
    }.get(category, [])


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    padded = f" {value} "
    return any(needle in padded for needle in needles)


def _is_official_source_intent(source_intent: str | None) -> bool:
    if not isinstance(source_intent, str):
        return False
    normalized = source_intent.strip().lower()
    return normalized.startswith("official") or normalized in {
        "github_readme_or_repo",
        "reference",
    }
