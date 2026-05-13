from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class CandidateTriageStatus(str, Enum):
    REJECT_FATAL = "reject_fatal"
    ACCEPT_CANDIDATE = "accept_candidate"
    NEEDS_LLM_REVIEW = "needs_llm_review"

@dataclass(frozen=True)
class ClaimCandidateTriage:
    status: CandidateTriageStatus
    reason: str | None

CLAIM_TYPE_FACT = "fact"
CLAIM_VERIFICATION_STATUS_DRAFT = "draft"
CLAIM_EVIDENCE_RELATION_CANDIDATE_SUPPORT = "candidate_support"
CLAIM_EVIDENCE_RELATION_SUPPORT = "support"

_SENTENCE_PATTERN = re.compile(r"[^\n.!?。！？]+(?:[.!?。！？]+)?", re.MULTILINE)
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_CLAIM_IDENTITY_PUNCTUATION_PATTERN = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")
_CJK_CHAR_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_TERMINAL_SENTENCE_PATTERN = re.compile(r"[.!。！]$")
_REFERENCE_MARKER_PATTERN = re.compile(r"^(?:\[\d+\]|\d+\.|\([a-z]\))\s+", re.IGNORECASE)
_AUTHOR_REFERENCE_PATTERN = re.compile(r"^[A-Z][A-Za-z' -]+,\s+[A-Z](?:\.|\w+)")
_LEADING_DASH_FRAGMENT_PATTERN = re.compile(
    r"^[^.!?。！？]{1,120}[\u2014\u2013-]\s+" r"(?=[A-Z][A-Za-z0-9_.-]{1,40}\s+(?:is|are)\b)"
)
_MEANINGLESS_CLAIMS = frozenset({"c", "data", "none", "null", "undefined"})
_LOW_VALUE_QUERY_TOKENS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "does",
    "for",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "what",
    "with",
}
_GENERIC_QUERY_TOKENS = {
    "about",
    "current",
    "currently",
    "explain",
    "known",
    "overview",
    "position",
    "research",
    "tell",
    "work",
    "works",
    "working",
}
_REFERENCE_PHRASES = (
    "(pdf)",
    "(bachelor thesis)",
    "bachelor thesis",
    "master thesis",
    "dissertation",
    "doi:",
    "retrieved from",
    "implementación de un prototipo",
)
_DEFINITION_PATTERNS = (
    " is a ",
    " is an ",
    " are a ",
    " are an ",
    " is the ",
    " are the ",
    "是一个",
    "是一种",
    "是一个低级",
    "是一个低级别",
    "是一个框架",
    "是一个低级编排框架",
    "是一个低级别的编排框架",
)
_MECHANISM_TERMS = (
    "aggregat",
    "branching",
    "cycles",
    "conditional edge",
    "conditional route",
    "cyclical graph",
    "directed graph",
    "edge",
    "edges",
    "execution path",
    "graph-based",
    "graph based",
    "graph architecture",
    "node",
    "nodes",
    "orchestrat",
    "route",
    "routes",
    "routing",
    "state graph",
    "state management",
    "state transition",
    "stateful graph",
    "stateful workflow",
    "stategraph",
    "workflow",
    "workflows",
    "mixes your quer",
    "mixing your quer",
    "results",
    "search engines",
    "search services",
    "sends queries",
    "send queries",
    "upstream engines",
    "other platforms",
    "分支",
    "图状态",
    "工作流",
    "有状态工作流",
    "有状态图",
    "状态图",
    "状态转换",
    "节点",
    "边",
    "路由",
    "执行路径",
    "编排",
)
_PRIVACY_TERMS = (
    "identify users",
    "audit",
    "auditable",
    "compliance",
    "governance",
    "human oversight",
    "human-in-the-loop",
    "human in the loop",
    "inspect and modify",
    "little to no information",
    "monitor deployments",
    "private data",
    "privacy",
    "security",
    "sensitive data",
    "not storing",
    "without storing",
    "stores no",
    "stores little to no",
    "doesn't generate a profile",
    "does not generate a profile",
    "profile about you",
    "tracking",
    "third-party",
    "user data",
    "search data",
    "人机协作",
    "人工审核",
    "安全",
    "审计",
    "治理",
    "隐私",
)
_FEATURE_TERMS = (
    "api",
    "apis",
    "checkpoint",
    "checkpointing",
    "checkpoints",
    "debug",
    "debugging",
    "deployment",
    "durable execution",
    "example",
    "examples",
    "use case",
    "use cases",
    "guide",
    "guides",
    "quickstart",
    "quick start",
    "sample",
    "samples",
    "snippet",
    "snippets",
    "tutorial",
    "tutorials",
    "cookbook",
    "human-in-the-loop",
    "human in the loop",
    "integration",
    "integrations",
    "langsmith",
    "limitation",
    "limitations",
    "long-running",
    "memory",
    "observability",
    "opensearch",
    "over 70 different search engines",
    "persistence",
    "persistent",
    "scalable",
    "streaming",
    "supports",
    "stateful",
    "default search engine",
    "browser's search bar",
    "browser search bar",
    "categories",
    "engines",
    "人机协作",
    "内存",
    "可观察性",
    "持久化",
    "持久执行",
    "检查点",
    "流式传输",
    "调试",
    "部署",
    "集成",
    "限制",
)
_DEPLOYMENT_TERMS = (
    "archived",
    "container",
    "containers",
    "docker",
    "self-hosted",
    "self hosted",
    "self host",
    "selfhost",
    "deploy",
    "deploying",
    "deployed as",
    "deployment",
    "host your own",
    "mounting",
    "persistent storage",
    "reverse-proxy",
    "reverse proxy",
    "superseded",
)
_SETUP_TERMS = (
    "add your instance",
    "add searxng",
    "browser setup",
    "configure",
    "configuration",
    "get started",
    "how do i set it as",
    "install",
    "listed at",
    "run it yourself",
    "set as default",
    "set up your own",
    "using one of the instances",
)
_COMMUNITY_TERMS = (
    "come join",
    "contribution",
    "contributions",
    "development",
    "join matrix",
    "make it better",
    "make searxng better",
    "matrix",
    "open community",
    "report issues",
    "send contributions",
    "source code",
    "sources and run",
    "translations",
    "weblate",
)
_SLOGAN_TERMS = (
    "make the internet freer",
    "reclaim their privacy",
    "reclaim your privacy",
    "search without being tracked",
)
_NAVIGATION_POINTER_TERMS = (
    "developer documentation",
    "documentation page",
    "documentation pages",
    "for more information",
    "learn more",
    "read the documentation",
    "see installation",
    "see the documentation",
    "visit documentation",
    "visit the documentation",
)
_IMPERATIVE_PREFIX_PATTERN = re.compile(
    r"^(?:"
    r"add(?:\s+your)?\s+instance|"
    r"come\s+join|"
    r"get\s+started|"
    r"make\s+searxng\s+better|"
    r"report\s+issues|"
    r"send\s+contributions|"
    r"take\s+the\s+code|"
    r"track\s+development|"
    r"run\s+it\s+yourself"
    r")\b",
    re.IGNORECASE,
)
_BROKEN_LINK_RESIDUE_PATTERN = re.compile(
    r"(?:\blisted\s+at\s+\.|\bsee\s+\.|\bat\s+\.|\bfrom\s+up\s+to\s+\d+\s+\.)",
    re.IGNORECASE,
)
_FIGURE_OR_CAPTION_PATTERN = re.compile(
    r"^(?:" r"fig\.?\s*\d+\b|" r"figure\s+\d+\b|" r"\d+\s+reference\s+architecture\b" r")",
    re.IGNORECASE,
)
_DIAGRAM_OR_CONFIG_FRAGMENT_PATTERN = re.compile(
    r"(?:"
    r"\bdigraph\s+g\b|"
    r"\bsubgraph\s+cluster\b|"
    r"\bnode\s*\[\s*style\s*=|"
    r"\b[a-z0-9_]+\s*->\s*[a-z0-9_]+\b|"
    r"\bvalkey://|"
    r"\buse_default_settings\s*:|"
    r"\bsecret_key\s*:"
    r")",
    re.IGNORECASE,
)
_DEPLOYMENT_COMMAND_LINE_PATTERN = re.compile(
    r"^\s*(?:\$|#)?\s*(?:sudo\s+)?(?:docker|docker-compose|podman|curl|mkdir|git|cd|make)\b",
    re.IGNORECASE,
)
_DEPLOYMENT_CONFIG_LINE_PATTERN = re.compile(
    r"^\s*(?:[-\w./{}$]+:\s*|-\s+['\"]?\d{2,5}:\d{2,5}|-\s+['\"]?\.?/[^:]+:|"
    r"(?:SEARXNG|GRANIAN|FORCE_OWNERSHIP|BASE_URL)[A-Z0-9_]*=)",
    re.IGNORECASE,
)
_MAX_DEPLOYMENT_EVIDENCE_CHARS = 4000
_MAX_DEPLOYMENT_EVIDENCE_LINES = 80
_DEPLOYMENT_EVIDENCE_MARKERS = (
    ".env",
    ".env.example",
    "bot protection",
    "certificate",
    "certificates",
    "custom certificate",
    "custom certificates",
    "docker ",
    "docker-compose",
    "docker compose",
    "podman ",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yml",
    "docker-compose.yaml",
    "docker.io/searxng/searxng",
    "ghcr.io/searxng/searxng",
    "searxng/searxng",
    "/etc/searxng",
    "/var/cache/searxng",
    "searxng-valkey",
    "limiter",
    "public_instance",
    "public instance",
    "public exposure",
    "publicly accessible",
    "reverse proxy",
    "searxng_",
    "searxng_secret",
    "settings.yml",
    "valkey://",
    "secret_key",
    "secret key",
    "base_url",
    "update-ca-certificates",
)
_DEPLOYMENT_STATEMENT_PREFIXES = (
    "Deployment command evidence:",
    "Deployment Compose evidence:",
    "Deployment configuration evidence:",
    "Deployment maintenance evidence:",
    "Deployment troubleshooting evidence:",
)
_SETUP_ALLOWED_QUERY_TERMS = {
    "add",
    "browser",
    "configure",
    "default",
    "deploy",
    "deployment",
    "docker",
    "install",
    "opensearch",
    "setup",
}
_CONTRIBUTION_ALLOWED_QUERY_TERMS = {
    "community",
    "contribute",
    "contribution",
    "development",
    "matrix",
    "translate",
    "translation",
    "weblate",
}
_CATEGORY_PRIORITY = {
    "definition": 0,
    "mechanism": 1,
    "privacy": 2,
    "feature": 3,
    "deployment/self_hosting": 4,
    "other": 5,
    "navigation": 6,
    "setup": 7,
    "community": 8,
    "slogan": 9,
    "reference": 10,
}
ANSWER_CLAIM_CATEGORIES = frozenset(
    {"definition", "mechanism", "privacy", "feature", "deployment/self_hosting"}
)
CORE_OVERVIEW_CLAIM_CATEGORIES = frozenset({"definition", "mechanism", "privacy", "feature"})
NON_ANSWER_CLAIM_CATEGORIES = frozenset({"navigation", "setup", "community", "slogan", "reference"})
MIN_CLAIM_STATEMENT_CHARS = 32
MIN_CLAIM_STATEMENT_TOKENS = 5
MIN_DRAFT_CLAIM_QUALITY_SCORE = 0.45
MIN_DRAFT_QUERY_ANSWER_SCORE = 0.35
REPORT_CLAIM_QUALITY_THRESHOLD = 0.45
REPORT_QUERY_ANSWER_THRESHOLD = 0.45


class CitationSpanValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SupportingSpan:
    start_offset: int
    end_offset: int
    excerpt: str


@dataclass(frozen=True)
class QueryIntent:
    intent_name: str
    expected_claim_types: tuple[str, ...]
    avoid_claim_types: tuple[str, ...]
    subject_terms: tuple[str, ...]
    setup_allowed: bool
    contribution_allowed: bool


@dataclass(frozen=True)
class ClaimCandidateScore:
    claim_category: str
    answer_role: str
    answer_relevant: bool
    content_quality_score: float
    query_relevance_score: float
    claim_quality_score: float
    query_answer_score: float
    source_quality_score: float
    source_suitability_score: float
    final_score: float
    candidate_tier: str
    rejected_reason: str | None
    triage_status: CandidateTriageStatus = CandidateTriageStatus.ACCEPT_CANDIDATE
    analysis_flags: tuple[str, ...] = ()

    def as_notes(self) -> dict[str, Any]:
        return {
            "claim_category": self.claim_category,
            "answer_role": self.answer_role,
            "answer_relevant": self.answer_relevant,
            "content_quality_score": self.content_quality_score,
            "query_relevance_score": self.query_relevance_score,
            "claim_quality_score": self.claim_quality_score,
            "query_answer_score": self.query_answer_score,
            "source_quality_score": self.source_quality_score,
            "source_suitability_score": self.source_suitability_score,
            "claim_selection_score": self.final_score,
            "candidate_tier": self.candidate_tier,
            "rejected_reason": self.rejected_reason,
            "triage_status": self.triage_status.value,
            "analysis_flags": list(self.analysis_flags),
        }


def draft_claim_statement(excerpt: str) -> str:
    normalized = _normalize_quotes(_normalize_whitespace(excerpt))
    normalized = _strip_leading_dash_fragment(normalized)
    if not normalized:
        raise ValueError("claim statement must not be empty")
    return normalized


def is_claimable_statement(statement: str, query: str | None = None) -> bool:
    normalized = _normalize_quotes(_normalize_whitespace(statement))
    triage = _triage_claim_candidate(normalized, query=query)
    if triage.status == CandidateTriageStatus.REJECT_FATAL:
        return False
    return True


def _looks_like_news_query(lower: str, query_tokens: set[str]) -> bool:
    if "news" in query_tokens:
        return True
    if "latest" in query_tokens and "updates" in query_tokens:
        return True
    if "recent" in query_tokens and "developments" in query_tokens:
        return True
    if "最新动态" in lower:
        return True
    if "近期讯息" in lower or "近期消息" in lower:
        return True
    return False


def classify_query_intent(query: str | None) -> QueryIntent:
    if query is None or not query.strip():
        return QueryIntent(
            intent_name="generic",
            expected_claim_types=("other", "definition", "mechanism", "privacy", "feature"),
            avoid_claim_types=("community", "slogan", "reference", "navigation"),
            subject_terms=(),
            setup_allowed=False,
            contribution_allowed=False,
        )

    normalized = _normalize_whitespace(query)
    lower = normalized.lower()
    query_tokens = set(_tokenize(normalized))
    subject_terms = _extract_subject_terms(normalized)
    setup_allowed = bool(query_tokens & _SETUP_ALLOWED_QUERY_TERMS)
    contribution_allowed = bool(query_tokens & _CONTRIBUTION_ALLOWED_QUERY_TERMS)
    deployment_relevant = bool(
        query_tokens
        & {
            "deploy",
            "deployment",
            "host",
            "hosting",
            "docker",
            "container",
            "self",
            "selfhost",
            "selfhosting",
        }
    )

    if subject_terms and deployment_relevant:
        return QueryIntent(
            intent_name="deployment",
            expected_claim_types=("deployment/self_hosting", "feature", "mechanism", "privacy"),
            avoid_claim_types=("community", "slogan", "reference", "navigation"),
            subject_terms=subject_terms,
            setup_allowed=True,
            contribution_allowed=contribution_allowed,
        )

    if _looks_like_news_query(lower, query_tokens):
        return QueryIntent(
            intent_name="news",
            expected_claim_types=("feature", "mechanism", "definition", "privacy", "other"),
            avoid_claim_types=("community", "slogan", "reference", "navigation"),
            subject_terms=subject_terms,
            setup_allowed=setup_allowed,
            contribution_allowed=contribution_allowed,
        )

    if (
        subject_terms
        and "what is" in lower
        and "how" in query_tokens
        and ("work" in query_tokens or "works" in query_tokens)
    ):
        expected: tuple[str, ...] = ("definition", "mechanism", "privacy", "feature")
        if deployment_relevant:
            expected = (*expected, "deployment/self_hosting")
        return QueryIntent(
            intent_name="definition_mechanism",
            expected_claim_types=expected,
            avoid_claim_types=("setup", "community", "slogan", "reference", "navigation"),
            subject_terms=subject_terms,
            setup_allowed=setup_allowed,
            contribution_allowed=contribution_allowed,
        )

    if subject_terms and ("what is" in lower or "what are" in lower):
        expected = ("definition", "privacy", "feature", "mechanism")
        if deployment_relevant:
            expected = (*expected, "deployment/self_hosting")
        return QueryIntent(
            intent_name="definition",
            expected_claim_types=expected,
            avoid_claim_types=("setup", "community", "slogan", "reference", "navigation"),
            subject_terms=subject_terms,
            setup_allowed=setup_allowed,
            contribution_allowed=contribution_allowed,
        )

    return QueryIntent(
        intent_name="generic",
        expected_claim_types=("other", "definition", "mechanism", "privacy", "feature"),
        avoid_claim_types=("community", "slogan", "reference", "navigation"),
        subject_terms=subject_terms,
        setup_allowed=setup_allowed,
        contribution_allowed=contribution_allowed,
    )


def score_claim_statement(
    *,
    statement: str,
    query: str | None,
    content_quality_score: float | None = None,
    source_quality_score: float | None = None,
    domain: str | None = None,
    source_url: str | None = None,
    page_title: str | None = None,
    target_slot_id: str | None = None,
) -> ClaimCandidateScore:
    normalized = _normalize_quotes(_normalize_whitespace(statement))
    intent = classify_query_intent(query)
    category = classify_claim_category(normalized, intent=intent)
    
    triage = _triage_claim_candidate(
        normalized, query=query, intent=intent, category=category
    )
    rejected_reason = triage.reason if triage.status == CandidateTriageStatus.REJECT_FATAL else None

    content_score = _clamp_score(
        content_quality_score if content_quality_score is not None else 0.6
    )
    source_score = _clamp_score(source_quality_score if source_quality_score is not None else 0.5)

    query_relevance = _compute_query_relevance_score(
        normalized, query=query, category=category, page_title=page_title
    )
    claim_quality = _compute_claim_quality_score(normalized, category=category)
    query_answer = _compute_query_answer_score(
        category=category,
        query_relevance=query_relevance,
        intent=intent,
        target_slot_id=target_slot_id,
    )
    source_suitability = _compute_source_suitability(
        domain=domain,
        source_url=source_url,
        intent=intent,
        category=category,
    )

    answer_role = answer_role_for_claim_category(
        category, intent=intent, target_slot_id=target_slot_id
    )
    answer_relevant = _is_answer_relevant_components(
        category=category,
        answer_role=answer_role,
        rejected_reason=rejected_reason,
        claim_quality_score=claim_quality,
        query_answer_score=query_answer,
        intent=intent,
    )

    if rejected_reason is not None:
        claim_quality = min(claim_quality, 0.2)
        query_answer = min(query_answer, 0.2)
        answer_role = "non_answer"
        answer_relevant = False
    elif triage.status == CandidateTriageStatus.NEEDS_LLM_REVIEW:
        # Soft penalty for candidates that need review so they don't dominate perfectly clean candidates
        claim_quality = min(claim_quality, 0.6)
        query_answer = min(query_answer, 0.6)

    # New weights:
    # relevance: 35%, answer: 25%, suitability: 20%, source: 10%, quality: 10%
    final_score = round(
        (query_relevance * 0.35)
        + (query_answer * 0.25)
        + (source_suitability * 0.20)
        + (source_score * 0.10)
        + (claim_quality * 0.10),
        4,
    )

    tier = "rejected"
    if rejected_reason is None:
        main_threshold = 0.45
        supporting_threshold = 0.35
        recall_threshold = 0.25

        if intent.intent_name in {"definition", "definition_mechanism"}:
            main_threshold = 0.38
            supporting_threshold = 0.30
            recall_threshold = 0.22
        elif intent.intent_name == "news":
            main_threshold = 0.35
            supporting_threshold = 0.28
            recall_threshold = 0.20
        elif intent.intent_name == "deployment":
            main_threshold = 0.48

        if not answer_relevant:
            # Check for query_focus_mismatch (pronoun issues)
            # If it's otherwise high quality and has context support, downgrade to weak instead of rejected
            if final_score >= recall_threshold and _is_contextually_relevant(
                normalized, query, page_title
            ):
                tier = "recall_candidate"
        elif final_score >= main_threshold:
            tier = "main_candidate"
        elif final_score >= supporting_threshold:
            tier = "supporting_candidate"
        elif final_score >= recall_threshold:
            tier = "recall_candidate"

    return ClaimCandidateScore(
        claim_category=category,
        answer_role=answer_role,
        answer_relevant=answer_relevant,
        content_quality_score=round(content_score, 4),
        query_relevance_score=round(query_relevance, 4),
        claim_quality_score=round(claim_quality, 4),
        query_answer_score=round(query_answer, 4),
        source_quality_score=round(source_score, 4),
        source_suitability_score=round(source_suitability, 4),
        final_score=final_score,
        candidate_tier=tier,
        rejected_reason=rejected_reason,
        triage_status=triage.status,
    )


def classify_claim_category(statement: str, *, intent: QueryIntent | None = None) -> str:
    normalized = _normalize_quotes(_normalize_whitespace(statement))
    lower = normalized.lower()
    padded = f" {lower} "
    intent = intent or classify_query_intent(None)

    if is_deployment_evidence_statement(normalized):
        return "deployment/self_hosting"
    if _looks_like_reference_statement(normalized, query=None):
        return "reference"
    if _contains_any(lower, _SLOGAN_TERMS):
        return "slogan"
    if _looks_like_navigation_or_documentation_pointer(normalized):
        return "navigation"
    if _contains_any(lower, _COMMUNITY_TERMS):
        return "community"
    if any(pattern in padded for pattern in _DEFINITION_PATTERNS):
        return "definition"
    if _is_deployment_statement(lower, intent=intent):
        return "deployment/self_hosting"
    if _IMPERATIVE_PREFIX_PATTERN.search(normalized) or _contains_any(lower, _SETUP_TERMS):
        return "setup"
    if any(term in lower for term in _PRIVACY_TERMS):
        return "privacy"
    if ("supports" in lower or "supported" in lower) and any(
        term in lower for term in _FEATURE_TERMS
    ):
        return "feature"
    if any(term in lower for term in _MECHANISM_TERMS):
        return "mechanism"
    if any(term in lower for term in _FEATURE_TERMS):
        return "feature"
    if any(subject in _tokenize(normalized) for subject in intent.subject_terms):
        return "other"
    return "other"


def candidate_category_sort_key(category: str) -> int:
    return _CATEGORY_PRIORITY.get(category, 99)


def _is_deployment_statement(lower_statement: str, *, intent: QueryIntent) -> bool:
    if any(term in lower_statement for term in _DEPLOYMENT_TERMS):
        return True
    if intent.intent_name != "deployment":
        return False
    return any(
        term in lower_statement
        for term in (
            "base url",
            "base urls",
            "bot protection",
            "certificate",
            "certificates",
            "compose pull",
            "configuration",
            "configure",
            "custom certificate",
            "custom certificates",
            "docker group",
            "docker/podman",
            "health check",
            "health checks",
            "limiter",
            "mount",
            "network",
            "persistent",
            "public instance",
            "public_instance",
            "publicly accessible",
            "review new templates",
            "searxng_",
            "searxng_secret",
            "secret",
            "secret key",
            "secrets",
            "settings.yml",
            "storage",
            "superseded",
        )
    )


def is_overview_answer_intent(intent: QueryIntent) -> bool:
    return intent.intent_name in {"definition", "definition_mechanism"}


def is_answer_claim_category(category: str) -> bool:
    return category in ANSWER_CLAIM_CATEGORIES


def answer_role_for_claim_category(
    category: str,
    *,
    intent: QueryIntent,
    target_slot_id: str | None = None,
) -> str:
    if (
        target_slot_id == "limitations"
        and category == "other"
        and is_overview_answer_intent(intent)
    ):
        # Limitations answer slot accepts ``other`` (see ``answer_slots``). Overview intents
        # otherwise treat ``other`` as non-answer; official planner-aligned limitations override.
        return "feature"
    if category in CORE_OVERVIEW_CLAIM_CATEGORIES:
        return category
    if category == "deployment/self_hosting":
        if category in intent.expected_claim_types or is_overview_answer_intent(intent):
            return category
        if intent.intent_name == "generic":
            return category
    if intent.intent_name == "generic" and category not in intent.avoid_claim_types:
        return category
    if intent.intent_name == "news" and category in intent.expected_claim_types:
        if category in CORE_OVERVIEW_CLAIM_CATEGORIES:
            return category
        return "feature"

    return "non_answer"


def is_answer_relevant_score(score: ClaimCandidateScore, *, query: str | None) -> bool:
    intent = classify_query_intent(query)
    answer_role = score.answer_role
    if not answer_role or answer_role == "non_answer":
        answer_role = answer_role_for_claim_category(
            score.claim_category, intent=intent, target_slot_id=None
        )
    return _is_answer_relevant_components(
        category=score.claim_category,
        answer_role=answer_role,
        rejected_reason=score.rejected_reason,
        claim_quality_score=score.claim_quality_score,
        query_answer_score=score.query_answer_score,
        intent=intent,
    )


def _triage_claim_candidate(
    statement: str,
    *,
    query: str | None,
    intent: QueryIntent | None = None,
    category: str | None = None,
) -> ClaimCandidateTriage:
    normalized = _normalize_quotes(_normalize_whitespace(statement))
    intent = intent or classify_query_intent(query)
    category = category or classify_claim_category(normalized, intent=intent)
    
    # 1. FATAL REJECTIONS (obvious garbage, fragments, etc)
    if normalized.lower() in _MEANINGLESS_CLAIMS:
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "meaningless_fragment")
    if normalized.endswith(("?", "？")):
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "question_like")
    if _has_unbalanced_quotes(normalized):
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "unbalanced_quotes")
    if _has_broken_link_residue(normalized):
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "broken_link_residue")
    if _looks_like_figure_caption(normalized):
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "figure_caption_or_diagram")
    deployment_evidence_statement = is_deployment_evidence_statement(normalized)
    if _looks_like_diagram_or_config_fragment(normalized) and not deployment_evidence_statement:
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "diagram_or_config_fragment")
    if _looks_like_reference_statement(normalized, query=query):
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "reference_or_citation")
    
    lower = normalized.lower()
    if normalized.endswith("!") and (
        category in {"setup", "community", "slogan"} or "run it yourself" in lower
    ):
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "promotional_or_imperative_exclamation")
    if category == "slogan":
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "slogan_fragment")
    if category == "navigation":
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "navigation_or_documentation_pointer")
    
    # Boilerplate / Cookie / Copyright rejections
    if _contains_any(lower, ("use cookies", "cookie policy", "privacy policy", "all rights reserved", "terms of service", "copyright \u00a9", "search without being tracked", "join our community")):
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "boilerplate_or_navigation")
        
    if category == "community" and not intent.contribution_allowed:
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "community_or_contribution")
    if category == "setup" and not intent.setup_allowed and _is_setup_instruction(lower):
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "setup_instruction")
    if query is not None and normalize_claim_identity(normalized) == normalize_claim_identity(query):
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "duplicates_query")

    # Fatal rejection for extremely short non-CJK fragments that lack substance
    has_cjk = bool(_CJK_CHAR_PATTERN.search(normalized))
    if len(normalized) < 24 and not has_cjk:
        return ClaimCandidateTriage(CandidateTriageStatus.REJECT_FATAL, "too_short_fragment")

    # 2. NEEDS LLM REVIEW (short text, incomplete surface structures, etc)
    tokens = _tokenize(normalized)
    has_cjk = bool(_CJK_CHAR_PATTERN.search(normalized))
    semantic_units = len(tokens) + (len(_CJK_CHAR_PATTERN.findall(normalized)) // 2)
    
    review_reasons = []
    
    if is_overview_answer_intent(intent) and category == "other":
        review_reasons.append("not_answer_focused")
    if category == "other" and _looks_like_caption_or_heading_fragment(normalized):
        review_reasons.append("caption_or_heading_fragment")
    
    if len(normalized) < MIN_CLAIM_STATEMENT_CHARS and not (category == "feature" and len(normalized) >= 24):
        if not has_cjk:
            review_reasons.append("too_short")
            
    if semantic_units < MIN_CLAIM_STATEMENT_TOKENS and not (category == "feature" and semantic_units >= 3):
        review_reasons.append("too_few_informative_terms")
        
    if _TERMINAL_SENTENCE_PATTERN.search(normalized) is None:
        review_reasons.append("incomplete_sentence")
        
    if _starts_with_lowercase_fragment(normalized, intent=intent):
        review_reasons.append("lowercase_fragment")
        
    if _IMPERATIVE_PREFIX_PATTERN.search(normalized):
        review_reasons.append("imperative_or_call_to_action")

    if review_reasons:
        return ClaimCandidateTriage(CandidateTriageStatus.NEEDS_LLM_REVIEW, review_reasons[0])

    # 3. ACCEPT
    return ClaimCandidateTriage(CandidateTriageStatus.ACCEPT_CANDIDATE, None)


def is_claimable_excerpt(excerpt: str, query: str | None = None) -> bool:
    return is_claimable_statement(draft_claim_statement(excerpt), query=query)


def normalize_claim_identity(statement: str) -> str:
    normalized = _normalize_whitespace(statement).lower()
    return _CLAIM_IDENTITY_PUNCTUATION_PATTERN.sub("", normalized)


def select_supporting_span(text: str, query: str) -> SupportingSpan:
    spans = [
        span
        for span in iter_supporting_spans(text)
        if is_claimable_excerpt(span.excerpt, query=query)
    ]
    if not spans:
        raise CitationSpanValidationError("source chunk text does not contain a claimable span")

    query_tokens = tuple(_tokenize(query))
    
    def _span_sort_key(span: SupportingSpan) -> tuple[int, float, float, float, int]:
        score = score_claim_statement(statement=span.excerpt, query=query)
        # Prefer ACCEPT_CANDIDATE over NEEDS_LLM_REVIEW
        triage_order = 1 if score.triage_status == CandidateTriageStatus.ACCEPT_CANDIDATE else 0
        return (
            triage_order,
            score.final_score,
            _query_overlap_score(span.excerpt, query_tokens),
            _informative_length_score(span.excerpt),
            -span.start_offset,
        )

    best_span = max(spans, key=_span_sort_key)
    validate_citation_span(text, best_span.start_offset, best_span.end_offset, best_span.excerpt)
    return best_span


def validate_citation_span(
    source_text: str,
    start_offset: int,
    end_offset: int,
    excerpt: str,
) -> None:
    if start_offset < 0:
        raise CitationSpanValidationError("citation span start_offset must be non-negative")
    if end_offset <= start_offset:
        raise CitationSpanValidationError(
            "citation span end_offset must be greater than start_offset"
        )
    if end_offset > len(source_text):
        raise CitationSpanValidationError("citation span end_offset exceeds source chunk length")
    actual_excerpt = source_text[start_offset:end_offset]
    if excerpt != actual_excerpt:
        raise CitationSpanValidationError(
            "citation span excerpt does not match the source chunk text at the given offsets"
        )
    if not excerpt.strip():
        raise CitationSpanValidationError("citation span excerpt must not be blank")


def normalized_excerpt_hash(excerpt: str) -> str:
    normalized = _normalize_whitespace(excerpt).lower()
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def compute_claim_confidence(
    *,
    query: str,
    statement: str,
    retrieval_score: float | None,
) -> float:
    query_tokens = tuple(_tokenize(query))
    statement_tokens = tuple(_tokenize(statement))

    if not query_tokens:
        coverage = 0.0
    else:
        coverage = len({token for token in statement_tokens if token in query_tokens}) / len(
            set(query_tokens)
        )

    length_score = min(len(statement), 240) / 240
    retrieval_component = min(max(retrieval_score or 0.0, 0.0), 5.0) / 5.0
    confidence = 0.45 + (coverage * 0.3) + (length_score * 0.15) + (retrieval_component * 0.1)
    return round(min(0.95, max(0.35, confidence)), 2)


def iter_supporting_spans(text: str) -> Iterable[SupportingSpan]:
    seen_offsets: set[tuple[int, int]] = set()
    yielded_any = False
    for match in _SENTENCE_PATTERN.finditer(text):
        raw_excerpt = match.group(0)
        leading = len(raw_excerpt) - len(raw_excerpt.lstrip())
        trailing = len(raw_excerpt.rstrip())
        if trailing <= leading:
            continue
        start_offset = match.start() + leading
        end_offset = match.start() + trailing
        excerpt = text[start_offset:end_offset]
        if not excerpt.strip():
            continue
        seen_offsets.add((start_offset, end_offset))
        yielded_any = True
        yield SupportingSpan(
            start_offset=start_offset,
            end_offset=end_offset,
            excerpt=excerpt,
        )

    if not yielded_any and text.strip():
        stripped = text.strip()
        start_offset = text.index(stripped)
        end_offset = start_offset + len(stripped)
        if (start_offset, end_offset) not in seen_offsets:
            yield SupportingSpan(
                start_offset=start_offset,
                end_offset=end_offset,
                excerpt=text[start_offset:end_offset],
            )


def iter_deployment_evidence_spans(text: str) -> Iterable[SupportingSpan]:
    line_spans = _line_spans(text)
    if not line_spans:
        return

    yielded_offsets: set[tuple[int, int]] = set()
    for fenced_span in _iter_fenced_code_block_spans(text, line_spans):
        if is_deployment_evidence_excerpt(fenced_span.excerpt):
            yielded_offsets.add((fenced_span.start_offset, fenced_span.end_offset))
            yield fenced_span

    index = 0
    while index < len(line_spans):
        start, end, line = line_spans[index]
        if _offset_inside_yielded_span(start, yielded_offsets):
            index += 1
            continue
        if not _deployment_line_candidate(line):
            index += 1
            continue

        block_start = start
        block_end = end
        block_lines = [line]
        next_index = index + 1
        while next_index < len(line_spans) and len(block_lines) < _MAX_DEPLOYMENT_EVIDENCE_LINES:
            _, next_end, next_line = line_spans[next_index]
            if not next_line.strip():
                break
            candidate_excerpt = text[block_start:next_end].strip()
            if len(candidate_excerpt) > _MAX_DEPLOYMENT_EVIDENCE_CHARS:
                break
            if not (
                _deployment_line_candidate(next_line)
                or _looks_like_continuation_line(next_line)
                or _looks_like_yaml_context_line(next_line)
            ):
                break
            block_end = next_end
            block_lines.append(next_line)
            next_index += 1

        excerpt = text[block_start:block_end].strip()
        if is_deployment_evidence_excerpt(excerpt):
            block_text = text[block_start:block_end]
            stripped_start = block_start + len(block_text) - len(block_text.lstrip())
            stripped_end = block_end - (len(block_text) - len(block_text.rstrip()))
            key = (stripped_start, stripped_end)
            if stripped_end > stripped_start and key not in yielded_offsets:
                yielded_offsets.add(key)
                yield SupportingSpan(
                    start_offset=stripped_start,
                    end_offset=stripped_end,
                    excerpt=text[stripped_start:stripped_end],
                )
        index = max(next_index, index + 1)


def deployment_evidence_statement(excerpt: str) -> str:
    normalized = _normalize_code_excerpt(excerpt)
    label = "configuration"
    lower = normalized.lower()
    if any(term in lower for term in ("logs", "exec ", "container list")):
        label = "troubleshooting"
    elif any(term in lower for term in ("pull", "stop", "rm ", "restart", "up -d")):
        label = "maintenance"
    elif "docker compose" in lower or "docker-compose" in lower or "compose.y" in lower:
        label = "Compose"
    elif "docker " in lower or "podman " in lower:
        label = "command"
    prefix = {
        "command": "Deployment command evidence",
        "Compose": "Deployment Compose evidence",
        "maintenance": "Deployment maintenance evidence",
        "troubleshooting": "Deployment troubleshooting evidence",
    }.get(label, "Deployment configuration evidence")
    return f"{prefix}: `{normalized}`."


def is_deployment_evidence_statement(statement: str) -> bool:
    normalized = _normalize_whitespace(statement)
    return normalized.startswith(_DEPLOYMENT_STATEMENT_PREFIXES)


def is_deployment_evidence_excerpt(excerpt: str) -> bool:
    normalized = _normalize_code_excerpt(excerpt)
    if len(normalized) < 6 or len(normalized) > _MAX_DEPLOYMENT_EVIDENCE_CHARS:
        return False
    lower = normalized.lower()
    if any(marker in lower for marker in _DEPLOYMENT_EVIDENCE_MARKERS):
        return True
    return any(
        _DEPLOYMENT_COMMAND_LINE_PATTERN.search(line)
        or _DEPLOYMENT_CONFIG_LINE_PATTERN.search(line)
        for line in normalized.splitlines()
    )


def deployment_slot_ids_for_evidence(statement: str, excerpt: str) -> tuple[str, ...]:
    return _deployment_slot_ids_for_text(statement, excerpt, default_to_run_or_compose=True)


def deployment_slot_ids_for_claim_text(statement: str, excerpt: str) -> tuple[str, ...]:
    return _deployment_slot_ids_for_text(statement, excerpt, default_to_run_or_compose=False)


def _deployment_slot_ids_for_text(
    statement: str,
    excerpt: str,
    *,
    default_to_run_or_compose: bool,
) -> tuple[str, ...]:
    lower = f"{statement}\n{excerpt}".lower()
    slot_ids: list[str] = []
    if any(
        term in lower
        for term in (
            "docker or podman",
            "working docker",
            "working podman",
            "usermod",
            "docker group",
            "docker/podman",
            "install docker",
            "install podman",
            "requires docker",
            "requires podman",
        )
    ):
        slot_ids.append("deployment_prerequisites")
    if any(
        term in lower
        for term in (
            "docker run",
            "docker compose up",
            "docker compose down",
            "docker-compose up",
            "docker-compose down",
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.y",
            "container/docker-compose",
            "compose up",
            "compose down",
            "curl ",
            "mkdir ",
            "cp .env",
            "uses docker compose",
        )
    ):
        slot_ids.append("deployment_run_or_compose")
    if any(
        term in lower
        for term in (
            "force_ownership",
            "ownership",
            "volume",
            "volumes:",
            "-v ",
            "/etc/searxng",
            "/var/cache",
        )
    ):
        slot_ids.append("deployment_volumes")
    if any(term in lower for term in ("ports:", "-p ", ":8080", "8888:8080", "localhost:8888")):
        slot_ids.append("deployment_ports")
    if any(
        term in lower
        for term in (
            "settings.yml",
            "core-config",
            "limiter.toml",
            "limiter",
            ".env",
            ".env.example",
            "environment:",
            "searxng_",
            "searxng_secret",
            "base_url",
            "secret_key",
            "secret key",
            "valkey://",
            "force_ownership",
            "review new templates",
            "reviewing new templates",
        )
    ):
        slot_ids.append("deployment_configuration")
    if any(
        term in lower
        for term in (
            "bot protection",
            "certificate",
            "certificates",
            "custom certificate",
            "custom certificates",
            "limiter",
            "public exposure",
            "public instance",
            "public_instance",
            "publicly accessible",
            "reverse proxy",
            "searxng_secret",
            "secret_key",
            "secret key",
        )
    ):
        slot_ids.append("deployment_security")
    if any(
        term in lower
        for term in ("logs", "exec ", "shell", "troubleshooting", "container list", "health")
    ):
        slot_ids.append("deployment_troubleshooting")
    if any(
        term in lower
        for term in (
            "archived",
            "compose pull",
            "pull",
            "restart",
            "review new templates",
            "reviewing new templates",
            "stop",
            "superseded",
            "rm ",
            "update",
            "maintenance",
        )
    ):
        slot_ids.append("deployment_update_maintenance")
    if not slot_ids and default_to_run_or_compose:
        slot_ids.append("deployment_run_or_compose")
    return tuple(dict.fromkeys(slot_ids))


def _normalize_whitespace(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()


def _normalize_code_excerpt(value: str) -> str:
    lines = [line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _line_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        start = cursor
        end = cursor + len(line)
        spans.append((start, end, line.rstrip("\r\n")))
        cursor = end
    if text and not text.endswith(("\n", "\r")) and (not spans or spans[-1][1] < len(text)):
        spans.append((cursor, len(text), text[cursor:]))
    return spans


def _iter_fenced_code_block_spans(
    text: str,
    line_spans: list[tuple[int, int, str]],
) -> Iterable[SupportingSpan]:
    index = 0
    while index < len(line_spans):
        start, _, line = line_spans[index]
        stripped = line.strip()
        fence = _opening_code_fence(stripped)
        if fence is None:
            index += 1
            continue

        block_end: int | None = None
        next_index = index + 1
        while next_index < len(line_spans):
            _, next_end, next_line = line_spans[next_index]
            if next_line.strip().startswith(fence):
                block_end = next_end
                break
            next_index += 1

        if block_end is None:
            index += 1
            continue

        excerpt = text[start:block_end].rstrip()
        if excerpt.strip() and len(excerpt) <= _MAX_DEPLOYMENT_EVIDENCE_CHARS:
            yield SupportingSpan(
                start_offset=start,
                end_offset=start + len(excerpt),
                excerpt=excerpt,
            )
        index = next_index + 1


def _opening_code_fence(stripped_line: str) -> str | None:
    if stripped_line.startswith("```"):
        return "```"
    if stripped_line.startswith("~~~"):
        return "~~~"
    return None


def _offset_inside_yielded_span(
    offset: int,
    yielded_offsets: set[tuple[int, int]],
) -> bool:
    return any(start <= offset < end for start, end in yielded_offsets)


def _deployment_line_candidate(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    if any(marker in lower for marker in _DEPLOYMENT_EVIDENCE_MARKERS):
        return True
    return bool(
        _DEPLOYMENT_COMMAND_LINE_PATTERN.search(stripped)
        or _DEPLOYMENT_CONFIG_LINE_PATTERN.search(stripped)
    )


def _looks_like_continuation_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return stripped.startswith(("-", "$", "#")) or stripped.endswith("\\")


def _looks_like_yaml_context_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(re.match(r"^[A-Za-z0-9_.-]+:\s*(?:$|.+)", stripped))


def _normalize_quotes(value: str) -> str:
    normalized = value.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = re.sub(r'"([^",]{1,80}),\s+"', r'"\1", "', normalized)
        normalized = re.sub(r'"([^"]{1,80}),"', r'"\1",', normalized)
    return normalized


def _strip_leading_dash_fragment(value: str) -> str:
    match = _LEADING_DASH_FRAGMENT_PATTERN.match(value)
    if match is None:
        return value
    return value[match.end() :].strip()


def _has_unbalanced_quotes(value: str) -> bool:
    return value.count('"') % 2 == 1


def _tokenize(value: str) -> tuple[str, ...]:
    return tuple(token.lower() for token in _TOKEN_PATTERN.findall(value))


def _looks_like_reference_statement(statement: str, query: str | None) -> bool:
    lower = statement.lower()
    if _REFERENCE_MARKER_PATTERN.search(statement):
        return True
    if _AUTHOR_REFERENCE_PATTERN.search(statement) and not _has_explanatory_claim_verb(lower):
        return True
    if any(phrase in lower for phrase in _REFERENCE_PHRASES):
        return True
    if (
        _looks_like_title_case_citation(statement)
        and _query_overlap_score(
            statement,
            _meaningful_query_tokens(query),
        )
        == 0
    ):
        return True
    return False


def _looks_like_title_case_citation(statement: str) -> bool:
    tokens = [token for token in re.findall(r"[^\W\d_]+", statement, flags=re.UNICODE)]
    if len(tokens) < 5:
        return False
    uppercase_initial = sum(1 for token in tokens if token[:1].isupper())
    lower = statement.lower()
    has_explanatory_verb = _has_explanatory_claim_verb(lower)
    return uppercase_initial / len(tokens) >= 0.65 and not has_explanatory_verb


def _has_explanatory_claim_verb(lower_statement: str) -> bool:
    return any(
        f" {verb} " in f" {lower_statement} "
        for verb in (
            "aggregates",
            "are",
            "functions",
            "is",
            "removes",
            "returns",
            "sends",
            "stores",
            "supports",
            "uses",
            "works",
        )
    )


def _meaningful_query_tokens(query: str | None) -> tuple[str, ...]:
    if query is None:
        return ()
    return tuple(
        token
        for token in _tokenize(query)
        if token not in _LOW_VALUE_QUERY_TOKENS and token not in _GENERIC_QUERY_TOKENS
    )


def _query_overlap_score(excerpt: str, query_tokens: tuple[str, ...]) -> int:
    if not query_tokens:
        return 0
    excerpt_lower = excerpt.lower()
    return sum(1 for token in dict.fromkeys(query_tokens) if token in excerpt_lower)


def _informative_length_score(excerpt: str) -> int:
    return min(len(_normalize_whitespace(excerpt)), 240)


def _extract_subject_terms(query: str) -> tuple[str, ...]:
    words = re.findall(r"[A-Za-z0-9_.-]+", query)
    if not words:
        return ()

    lowered = [word.lower() for word in words]
    for index, token in enumerate(lowered[:-1]):
        if token == "what" and lowered[index + 1] in {"is", "are"}:
            for candidate in words[index + 2 : index + 6]:
                candidate_lower = candidate.lower()
                if candidate_lower in _LOW_VALUE_QUERY_TOKENS:
                    continue
                if candidate_lower in _GENERIC_QUERY_TOKENS:
                    continue
                if candidate[:1].isupper() or any(char.isdigit() for char in candidate):
                    return (candidate_lower,)
                break

    proper_terms = [
        word.lower()
        for word in words
        if word[:1].isupper()
        and word.lower() not in _LOW_VALUE_QUERY_TOKENS
        and word.lower() not in _GENERIC_QUERY_TOKENS
    ]
    return tuple(dict.fromkeys(proper_terms[:2]))


def _compute_query_relevance_score(
    statement: str,
    *,
    query: str | None,
    category: str,
    page_title: str | None = None,
) -> float:
    query_tokens = set(_meaningful_query_tokens(query))
    statement_tokens = set(_tokenize(statement))
    literal_score = 0.0
    if query_tokens:
        literal_score = len(query_tokens & statement_tokens) / len(query_tokens)

    # Boost if page title matches query subject and statement uses context-indicating language
    context_boost = 0.0
    if page_title and query_tokens:
        title_tokens = set(_tokenize(page_title))
        if len(query_tokens & title_tokens) >= 1:
            if statement.lower().startswith(("they ", "it ", "this ", "the ")):
                context_boost = 0.15

    category_floor = {
        "definition": 0.9,
        "mechanism": 0.85,
        "privacy": 0.75,
        "feature": 0.7,
        "deployment/self_hosting": 0.65,
        "other": 0.0,
        "navigation": 0.0,
        "setup": 0.2,
        "community": 0.1,
        "slogan": 0.1,
        "reference": 0.0,
    }.get(category, 0.0)
    if classify_query_intent(query).intent_name == "generic":
        category_floor = min(category_floor, 0.55)
    return _clamp_score(max(literal_score + context_boost, category_floor))


def _compute_claim_quality_score(statement: str, *, category: str) -> float:
    normalized = _normalize_whitespace(statement)
    lower = normalized.lower()
    score = 0.55
    if _TERMINAL_SENTENCE_PATTERN.search(normalized):
        score += 0.15
    if _starts_with_explanatory_subject(normalized):
        score += 0.1
    if 60 <= len(normalized) <= 260:
        score += 0.1
    if any(verb in f" {lower} " for verb in (" is ", " are ", " provides ", " supports ")):
        score += 0.08
    if category in ANSWER_CLAIM_CATEGORIES:
        score += 0.08
    if normalized.endswith("!"):
        score -= 0.2
    if category in {"community", "slogan", "reference", "navigation"}:
        score -= 0.35
    if category == "setup":
        score -= 0.2
    return _clamp_score(score)


def _compute_query_answer_score(
    *,
    category: str,
    query_relevance: float,
    intent: QueryIntent,
    target_slot_id: str | None = None,
) -> float:
    if intent.intent_name == "generic":
        if category in intent.avoid_claim_types:
            return 0.15
        return _clamp_score(max(query_relevance, 0.45))

    expected_scores = {
        "definition": 1.0,
        "mechanism": 0.95,
        "privacy": 0.85,
        "feature": 0.75,
        "deployment/self_hosting": 0.7,
    }
    score = expected_scores.get(category, max(query_relevance, 0.6))

    # Boost if this category matches the target slot's expected categories
    if target_slot_id:
        score += 0.05

    if category in intent.expected_claim_types:
        return _clamp_score(score)
    if category in intent.avoid_claim_types:
        return 0.1
    if category == "deployment/self_hosting" and is_overview_answer_intent(intent):
        return _clamp_score(max(query_relevance * 0.55, 0.45))
    if (
        target_slot_id == "limitations"
        and category == "other"
        and is_overview_answer_intent(intent)
    ):
        # Default overview path crushes ``other`` to ~0.25; official planner ``limitations``
        # slots need bounded answer credit so caveat sentences can reach main tiers without
        # global ``other`` lift.
        boosted = max(
            score,
            query_relevance * 0.65 + 0.2,
            MIN_DRAFT_QUERY_ANSWER_SCORE + 0.02,
        )
        return _clamp_score(boosted)
    if category == "other" and is_overview_answer_intent(intent):
        return _clamp_score(min(query_relevance * 0.35, 0.25))
    return _clamp_score(query_relevance * 0.55)


def _is_answer_relevant_components(
    *,
    category: str,
    answer_role: str,
    rejected_reason: str | None,
    claim_quality_score: float,
    query_answer_score: float,
    intent: QueryIntent,
) -> bool:
    if rejected_reason is not None:
        return False
    if claim_quality_score < MIN_DRAFT_CLAIM_QUALITY_SCORE:
        return False
    if query_answer_score < MIN_DRAFT_QUERY_ANSWER_SCORE:
        return False
    if answer_role == "non_answer":
        return False
    if is_overview_answer_intent(intent):
        return answer_role in ANSWER_CLAIM_CATEGORIES
    if intent.intent_name == "generic":
        return category not in intent.avoid_claim_types
    return category in intent.expected_claim_types


def _starts_with_lowercase_fragment(statement: str, *, intent: QueryIntent) -> bool:
    stripped = statement.lstrip()
    first_alpha = re.search(r"[A-Za-z]", stripped)
    if first_alpha is None:
        return False
    first_char = first_alpha.group(0)
    if not first_char.islower():
        return False
    lower = stripped.lower()
    if lower.startswith(("it ", "its ", "the ", "this ")):
        return False
    return not any(lower.startswith(f"{subject} ") for subject in intent.subject_terms)


def _starts_with_explanatory_subject(statement: str) -> bool:
    stripped = statement.lstrip()
    return bool(
        stripped.startswith(("It ", "Its ", "The ", "This "))
        or re.match(r"^[A-Z][A-Za-z0-9_.-]+(?:\s+[A-Z][A-Za-z0-9_.-]+){0,3}\s+", stripped)
    )


def _is_setup_instruction(lower_statement: str) -> bool:
    if _contains_any(
        lower_statement,
        (
            "add your instance",
            "get started",
            "how do i set it as",
            "listed at",
            "run it yourself",
            "set up your own",
            "using one of the instances",
        ),
    ):
        return True
    return bool(
        lower_statement.startswith(("add ", "click ", "configure ", "install ", "set "))
        or "follow these" in lower_statement
    )


def _has_broken_link_residue(statement: str) -> bool:
    return _BROKEN_LINK_RESIDUE_PATTERN.search(statement) is not None


def _looks_like_figure_caption(statement: str) -> bool:
    return _FIGURE_OR_CAPTION_PATTERN.search(statement.strip()) is not None


def _looks_like_diagram_or_config_fragment(statement: str) -> bool:
    return _DIAGRAM_OR_CONFIG_FRAGMENT_PATTERN.search(statement) is not None


def _looks_like_caption_or_heading_fragment(statement: str) -> bool:
    lower = statement.lower()
    if _has_explanatory_claim_verb(lower):
        return False
    tokens = _tokenize(statement)
    if len(tokens) <= 8 and any(
        term in lower for term in ("architecture", "configuration", "diagram", "reference setup")
    ):
        return True
    return bool(re.match(r"^\d+\s+[A-Z][A-Za-z0-9 -]{8,120}[.!]?$", statement))


def _looks_like_navigation_or_documentation_pointer(statement: str) -> bool:
    normalized = _normalize_whitespace(statement)
    lower = normalized.lower()
    if _contains_any(lower, _NAVIGATION_POINTER_TERMS):
        return True
    if lower in {"documentation", "developer documentation", "user documentation"}:
        return True
    if lower.startswith(("visit ", "read ", "see ")) and "documentation" in lower:
        return True
    if "documentation" in lower and not _has_explanatory_claim_verb(lower):
        tokens = _tokenize(normalized)
        return len(tokens) <= 8
    return False


def _contains_any(value: str, terms: Iterable[str]) -> bool:
    return any(term in value for term in terms)


def _clamp_score(value: float) -> float:
    return min(1.0, max(0.0, float(value)))

def rewrite_claim_self_contained(
    statement: str,
    *,
    page_title: str | None = None,
    query: str | None = None,
) -> str:
    normalized = _normalize_whitespace(statement)
    if not normalized:
        return statement

    intent = classify_query_intent(query)
    subject = (intent.subject_terms[0] if intent.subject_terms else None) or (
        _extract_title_subject(page_title) if page_title else None
    )

    if not subject:
        return normalized

    # Simple pronoun resolution
    # Handle "They", "It", "This", "The company", "The tool", etc.
    rewritten = normalized

    pronouns = [
        (r"^[Tt]hey\s+", f"{subject.capitalize()} "),
        (r"^[Ii]t\s+", f"{subject.capitalize()} "),
        (r"^[Tt]his\s+(?:tool|app|framework|model|service)\s+", f"{subject.capitalize()} "),
        (r"^[Tt]he\s+(?:tool|app|framework|model|service)\s+", f"{subject.capitalize()} "),
    ]

    for pattern, replacement in pronouns:
        if re.search(pattern, rewritten):
            rewritten = re.sub(pattern, replacement, rewritten)
            break

    return rewritten


def _is_contextually_relevant(
    statement: str,
    query: str | None,
    page_title: str | None,
) -> bool:
    if not query:
        return True
    intent = classify_query_intent(query)
    if not intent.subject_terms:
        return True

    lower_statement = statement.lower()
    # Check if statement already has focus
    if any(subject in lower_statement for subject in intent.subject_terms):
        return True

    # Check if page title gives it focus
    if page_title and any(subject in page_title.lower() for subject in intent.subject_terms):
        # If it uses a pronoun at the start, it's likely relevant contextually
        if lower_statement.startswith(("they ", "it ", "this ", "the ")):
            return True

    return False


def _extract_title_subject(title: str) -> str | None:
    # Extract the first significant noun phrase from the title
    cleaned = re.split(r"[-|:|?|!]", title)[0].strip()
    if cleaned.lower().startswith("what is "):
        cleaned = cleaned[7:].strip()
    tokens = _tokenize(cleaned)
    if not tokens:
        return None
    return tokens[0]

def _compute_source_suitability(
    *,
    domain: str | None,
    source_url: str | None,
    intent: QueryIntent,
    category: str,
) -> float:
    # High score for official domains if relevant
    score = 0.5
    if not domain:
        return score
    
    lower_domain = domain.lower()

    # Example: boost official domains for news or definition
    official_domains = {"openai.com", "microsoft.com", "google.com", "anthropic.com"}
    if lower_domain in official_domains:
        score += 0.2

    # Boost reference sources for definitions
    if category == "definition":
        if "wikipedia.org" in lower_domain:
            score += 0.15
        elif "arxiv.org" in lower_domain:
            score += 0.20
        elif lower_domain.startswith("docs."):
            score += 0.10
    
    # Penalize raw source code for factual claims unless it's a deployment task.
    # README content from official repositories is useful for technical explanations,
    # so avoid over-penalizing those URLs.
    if "raw.githubusercontent.com" in lower_domain:
        lower_url = (source_url or "").lower()
        is_readme = lower_url.endswith("/readme.md")
        if intent.intent_name != "deployment" and not is_readme:
            score -= 0.15
        elif is_readme:
            score += 0.05

    if "github.com" in lower_domain:
        lower_url = (source_url or "").lower()
        if (
            "/issues" not in lower_url
            and "/pull" not in lower_url
            and category in {"definition", "mechanism", "feature"}
            and intent.intent_name in {"definition_mechanism", "generic"}
        ):
            score += 0.05

    return _clamp_score(score)
