from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")
_COMPACT_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class KnownSourceCandidate:
    url: str
    title: str
    snippet: str
    rank: int
    reason: str
    source_class: str
    entity: str

    def to_known_path(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "rank": self.rank,
            "reason": self.reason,
            "source_class": self.source_class,
            "entity": self.entity,
        }


@dataclass(frozen=True)
class SourceProfile:
    labels: tuple[str, ...]
    official_urls: tuple[tuple[str, str], ...] = ()
    repository_urls: tuple[tuple[str, str], ...] = ()
    package_urls: tuple[tuple[str, str], ...] = ()
    academic_urls: tuple[tuple[str, str], ...] = ()
    reference_labels: tuple[str, ...] = ()


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "compare",
    "does",
    "for",
    "how",
    "in",
    "is",
    "it",
    "large",
    "models",
    "of",
    "on",
    "or",
    "the",
    "to",
    "versus",
    "vs",
    "what",
    "with",
    "work",
    "works",
}


_TECHNICAL_SOURCE_PROFILES: tuple[SourceProfile, ...] = (
    SourceProfile(
        labels=("LangGraph",),
        official_urls=(
            ("https://docs.langchain.com/oss/python/langgraph/overview", "official_docs"),
            ("https://docs.langchain.com/oss/javascript/langgraph/overview", "official_docs"),
            ("https://reference.langchain.com/python/langgraph/", "api_reference"),
            ("https://www.langchain.com/langgraph", "project_homepage"),
        ),
        repository_urls=(("https://github.com/langchain-ai/langgraph", "official_repository"),),
        package_urls=(("https://pypi.org/project/langgraph/", "package_registry"),),
    ),
    SourceProfile(
        labels=("LangChain",),
        official_urls=(
            ("https://docs.langchain.com/", "official_docs"),
            ("https://python.langchain.com/docs/introduction/", "official_docs"),
            ("https://www.langchain.com/", "project_homepage"),
        ),
        repository_urls=(("https://github.com/langchain-ai/langchain", "official_repository"),),
        package_urls=(("https://pypi.org/project/langchain/", "package_registry"),),
    ),
    SourceProfile(
        labels=("FastAPI",),
        official_urls=(
            ("https://fastapi.tiangolo.com/", "official_docs"),
            ("https://fastapi.tiangolo.com/tutorial/", "official_tutorial"),
            ("https://fastapi.tiangolo.com/features/", "official_about"),
        ),
        repository_urls=(("https://github.com/fastapi/fastapi", "official_repository"),),
        package_urls=(("https://pypi.org/project/fastapi/", "package_registry"),),
    ),
    SourceProfile(
        labels=("PyTorch",),
        official_urls=(
            ("https://pytorch.org/docs/stable/index.html", "official_docs"),
            ("https://pytorch.org/docs/stable/autograd.html", "api_reference"),
            (
                "https://pytorch.org/tutorials/beginner/blitz/autograd_tutorial.html",
                "official_tutorial",
            ),
            ("https://pytorch.org/", "project_homepage"),
        ),
        repository_urls=(("https://github.com/pytorch/pytorch", "official_repository"),),
        package_urls=(("https://pypi.org/project/torch/", "package_registry"),),
    ),
    SourceProfile(
        labels=("Kubernetes",),
        official_urls=(
            ("https://kubernetes.io/docs/concepts/overview/", "official_about"),
            (
                "https://kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler/",
                "official_docs",
            ),
            ("https://kubernetes.io/docs/concepts/scheduling-eviction/", "official_docs"),
            ("https://kubernetes.io/", "project_homepage"),
        ),
        repository_urls=(("https://github.com/kubernetes/kubernetes", "official_repository"),),
    ),
    SourceProfile(
        labels=("AutoGen",),
        official_urls=(
            ("https://microsoft.github.io/autogen/stable/", "official_docs"),
            (
                "https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/index.html",
                "official_docs",
            ),
            (
                "https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/index.html",
                "official_docs",
            ),
        ),
        repository_urls=(("https://github.com/microsoft/autogen", "official_repository"),),
        package_urls=(
            ("https://pypi.org/project/autogen-agentchat/", "package_registry"),
            ("https://pypi.org/project/pyautogen/", "package_registry"),
        ),
    ),
    SourceProfile(
        labels=("LoRA", "Low-Rank Adaptation", "Low Rank Adaptation"),
        official_urls=(
            (
                "https://huggingface.co/docs/peft/main/en/conceptual_guides/lora",
                "official_tutorial",
            ),
        ),
        academic_urls=(
            ("https://arxiv.org/abs/2106.09685", "academic_paper"),
            (
                "https://arxiv.org/search/?query=low-rank+adaptation+large+language+models&searchtype=all",
                "academic_search",
            ),
        ),
        reference_labels=("Low-rank adaptation",),
    ),
    SourceProfile(
        labels=("TensorFlow",),
        official_urls=(
            ("https://www.tensorflow.org/learn", "official_docs"),
            ("https://www.tensorflow.org/guide", "official_docs"),
        ),
        repository_urls=(("https://github.com/tensorflow/tensorflow", "official_repository"),),
        package_urls=(("https://pypi.org/project/tensorflow/", "package_registry"),),
    ),
    SourceProfile(
        labels=("Django",),
        official_urls=(("https://docs.djangoproject.com/en/stable/", "official_docs"),),
        repository_urls=(("https://github.com/django/django", "official_repository"),),
        package_urls=(("https://pypi.org/project/Django/", "package_registry"),),
    ),
    SourceProfile(
        labels=("Flask",),
        official_urls=(("https://flask.palletsprojects.com/en/stable/", "official_docs"),),
        repository_urls=(("https://github.com/pallets/flask", "official_repository"),),
        package_urls=(("https://pypi.org/project/Flask/", "package_registry"),),
    ),
    SourceProfile(
        labels=("React",),
        official_urls=(
            ("https://react.dev/learn", "official_docs"),
            ("https://react.dev/reference/react", "api_reference"),
        ),
        repository_urls=(("https://github.com/facebook/react", "official_repository"),),
        package_urls=(("https://www.npmjs.com/package/react", "package_registry"),),
    ),
    SourceProfile(
        labels=("Vue", "Vue.js"),
        official_urls=(("https://vuejs.org/guide/introduction.html", "official_docs"),),
        repository_urls=(("https://github.com/vuejs/core", "official_repository"),),
        package_urls=(("https://www.npmjs.com/package/vue", "package_registry"),),
    ),
    SourceProfile(
        labels=("Next.js", "NextJS"),
        official_urls=(("https://nextjs.org/docs", "official_docs"),),
        repository_urls=(("https://github.com/vercel/next.js", "official_repository"),),
        package_urls=(("https://www.npmjs.com/package/next", "package_registry"),),
    ),
    SourceProfile(
        labels=("NumPy",),
        official_urls=(("https://numpy.org/doc/stable/", "official_docs"),),
        repository_urls=(("https://github.com/numpy/numpy", "official_repository"),),
        package_urls=(("https://pypi.org/project/numpy/", "package_registry"),),
    ),
    SourceProfile(
        labels=("pandas", "Pandas"),
        official_urls=(("https://pandas.pydata.org/docs/", "official_docs"),),
        repository_urls=(("https://github.com/pandas-dev/pandas", "official_repository"),),
        package_urls=(("https://pypi.org/project/pandas/", "package_registry"),),
    ),
    SourceProfile(
        labels=("OpenSearch",),
        official_urls=(
            ("https://docs.opensearch.org/", "official_docs"),
            ("https://opensearch.org/", "project_homepage"),
        ),
        repository_urls=(
            ("https://github.com/opensearch-project/OpenSearch", "official_repository"),
        ),
    ),
    SourceProfile(
        labels=("SearXNG",),
        official_urls=(
            ("https://docs.searxng.org/user/about.html", "official_about"),
            ("https://docs.searxng.org/", "official_docs"),
        ),
        repository_urls=(("https://github.com/searxng/searxng", "official_repository"),),
    ),
)

_PROFILES_BY_KEY: dict[str, SourceProfile] = {
    _COMPACT_PATTERN.sub("", label.lower()): profile
    for profile in _TECHNICAL_SOURCE_PROFILES
    for label in profile.labels
}


def resolve_authoritative_source_candidates(
    query: str,
    *,
    limit: int = 12,
) -> list[KnownSourceCandidate]:
    entities = extract_query_entities(query)
    candidates: list[KnownSourceCandidate] = []
    seen_urls: set[str] = set()
    for entity in entities:
        profile = _PROFILES_BY_KEY.get(_compact(entity))
        if profile is None:
            continue
        for candidate in _profile_candidates(profile, matched_entity=entity):
            if candidate.url in seen_urls:
                continue
            candidates.append(candidate)
            seen_urls.add(candidate.url)
            if len(candidates) >= limit:
                return candidates
    return candidates


def extract_query_entities(query: str) -> tuple[str, ...]:
    normalized = " ".join(query.strip().split())
    entities: list[str] = []

    compare_match = re.search(
        r"\bcompare\s+(.+?)\s+(?:and|vs\.?|versus)\s+(.+?)(?:\s+for\b|\.|\?|$)",
        normalized,
        re.I,
    )
    if compare_match is not None:
        entities.extend(
            (_clean_entity(compare_match.group(1)), _clean_entity(compare_match.group(2)))
        )

    what_match = re.search(
        r"\bwhat\s+(?:is|are)\s+(.+?)(?:\s+and\s+how|\s+in\s+|\.|\?|$)",
        normalized,
        re.I,
    )
    if what_match is not None:
        entities.append(_clean_entity(what_match.group(1)))

    proper_terms = [
        token
        for token in _TOKEN_PATTERN.findall(normalized)
        if token.lower() not in _STOPWORDS
        and (token[:1].isupper() or token.isupper() or "." in token)
    ]
    entities.extend(proper_terms)

    deduped: list[str] = []
    seen: set[str] = set()
    for entity in entities:
        cleaned = _clean_entity(entity)
        key = _compact(cleaned)
        if not key or key in seen or key in _STOPWORDS:
            continue
        deduped.append(cleaned)
        seen.add(key)
    return tuple(deduped[:4])


def candidate_matches_query_subject(
    *,
    query: str,
    url: str,
    title: str | None,
    snippet: str | None,
) -> bool:
    entities = extract_query_entities(query)
    if not entities:
        return True
    haystack = _compact(" ".join((url, title or "", snippet or "")))
    return any(_compact(entity) in haystack for entity in entities)


def _profile_candidates(
    profile: SourceProfile,
    *,
    matched_entity: str,
) -> list[KnownSourceCandidate]:
    canonical_entity = profile.labels[0]
    rank = 10
    candidates: list[KnownSourceCandidate] = []
    for url, source_class in profile.official_urls:
        candidates.append(
            _candidate(
                url=url,
                entity=canonical_entity,
                source_class=source_class,
                rank=rank,
                reason=f"authoritative_source_resolver: {source_class} for {canonical_entity}",
            )
        )
        rank += 1
    for url, source_class in profile.repository_urls:
        candidates.append(
            _candidate(
                url=url,
                entity=canonical_entity,
                source_class=source_class,
                rank=rank,
                reason=f"authoritative_source_resolver: upstream repository for {canonical_entity}",
            )
        )
        rank += 1
    for url, source_class in profile.package_urls:
        candidates.append(
            _candidate(
                url=url,
                entity=canonical_entity,
                source_class=source_class,
                rank=rank,
                reason=f"authoritative_source_resolver: package registry for {canonical_entity}",
            )
        )
        rank += 1
    for url, source_class in profile.academic_urls:
        candidates.append(
            _candidate(
                url=url,
                entity=canonical_entity,
                source_class=source_class,
                rank=rank,
                reason=f"authoritative_source_resolver: academic source for {canonical_entity}",
            )
        )
        rank += 1
    for label in (matched_entity, canonical_entity, *profile.reference_labels):
        wikipedia_url = f"https://en.wikipedia.org/wiki/{quote(label.replace(' ', '_'))}"
        candidates.append(
            _candidate(
                url=wikipedia_url,
                entity=canonical_entity,
                source_class="reference",
                rank=rank,
                reason=(
                    "authoritative_source_resolver: stable reference page for "
                    f"{canonical_entity}"
                ),
            )
        )
        rank += 1
    return candidates


def _candidate(
    *,
    url: str,
    entity: str,
    source_class: str,
    rank: int,
    reason: str,
) -> KnownSourceCandidate:
    return KnownSourceCandidate(
        url=url,
        title=_title_for_source(entity=entity, source_class=source_class),
        snippet=reason,
        rank=rank,
        reason=reason,
        source_class=source_class,
        entity=entity,
    )


def _title_for_source(*, entity: str, source_class: str) -> str:
    return {
        "official_docs": f"{entity} official documentation",
        "official_about": f"{entity} official overview",
        "official_tutorial": f"{entity} official tutorial",
        "api_reference": f"{entity} API/reference documentation",
        "project_homepage": f"{entity} project homepage",
        "official_repository": f"{entity} upstream repository",
        "package_registry": f"{entity} package registry",
        "academic_paper": f"{entity} academic paper",
        "academic_search": f"{entity} academic search",
        "reference": f"{entity} reference",
    }.get(source_class, f"{entity} authoritative source")


def _clean_entity(value: str) -> str:
    cleaned = re.sub(
        r"\b(?:official|documentation|docs|repository|readme)\b", "", value, flags=re.I
    )
    cleaned = re.sub(r"[^A-Za-z0-9_.+ -]+", " ", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip(" .,-")


def _compact(value: str) -> str:
    return _COMPACT_PATTERN.sub("", value.lower())
