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
            AnswerSlot("deployment_target", "Deployment target", ("deployment/self_hosting",)),
            AnswerSlot(
                "deployment_steps",
                "Deployment steps",
                ("deployment/self_hosting", "feature"),
            ),
            AnswerSlot("deployment_configuration", "Configuration", ("feature", "mechanism")),
            AnswerSlot(
                "deployment_limitations",
                "Operational limitations",
                ("privacy", "feature", "other"),
                required=False,
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
    return [
        slot.slot_id
        for slot in answer_slots_for_query(query)
        if normalized_category in slot.expected_claim_categories
    ]


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
