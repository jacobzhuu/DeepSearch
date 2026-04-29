from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

from services.orchestrator.app.acquisition.http_client import HttpAcquisitionClient, HttpFetchResult

SMOKE_FIXTURE_HOST = "deepsearch-smoke.local"


@dataclass(frozen=True)
class SmokeAcquisitionClient(HttpAcquisitionClient):
    """Network-free acquisition client for explicitly configured development smoke runs."""

    def fetch(self, url: str) -> HttpFetchResult:
        parsed = urlsplit(url)
        if parsed.hostname != SMOKE_FIXTURE_HOST:
            return HttpFetchResult(
                requested_url=url,
                final_url=None,
                http_status=None,
                error_code="smoke_fixture_missing",
                mime_type=None,
                content=None,
                content_hash=None,
                trace={
                    "requested_url": url,
                    "smoke_mode": True,
                    "synthetic_fixture": True,
                    "message": "smoke acquisition only serves deepsearch-smoke.local fixtures",
                },
            )
        content = _smoke_html_for_path(parsed.path).encode("utf-8")
        return HttpFetchResult(
            requested_url=url,
            final_url=url,
            http_status=200,
            error_code=None,
            mime_type="text/html",
            content=content,
            content_hash=f"sha256:{hashlib.sha256(content).hexdigest()}",
            trace={
                "requested_url": url,
                "final_url": url,
                "smoke_mode": True,
                "synthetic_fixture": True,
                "response_bytes": len(content),
            },
        )


def _smoke_html_for_path(path: str) -> str:
    parts = [item for item in path.strip("/").split("/") if item]
    topic_slug = parts[0] if parts else "generic-research-topic"
    page = parts[1] if len(parts) > 1 else "overview"
    topic_label = _topic_label(topic_slug)
    paragraphs = _paragraphs_for_page(topic_label, page)
    paragraph_html = "\n".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
    return f"""<!doctype html>
<html>
  <head>
    <title>Synthetic development smoke source: {topic_label} {page}</title>
  </head>
  <body>
    <main data-smoke-fixture="true">
      <h1>Synthetic development smoke source: {topic_label} {page}</h1>
      <p>This page is a synthetic DeepSearch development fixture, not real web evidence.</p>
      {paragraph_html}
    </main>
  </body>
</html>
"""


def _paragraphs_for_page(topic_label: str, page: str) -> tuple[str, ...]:
    if page == "mechanism":
        return (
            _mechanism_sentence(topic_label),
            f"{topic_label} returns results through explicit processing stages so source "
            "selection, evidence extraction, verification, and reporting can be observed.",
        )
    if page == "privacy":
        return (
            f"{topic_label} can provide a privacy advantage when operators minimize retained "
            "logs, avoid user profiling, and keep sensitive configuration under local control.",
            f"A privacy limitation of {topic_label} is that upstream services, hosted APIs, or "
            "external integrations may still receive operational metadata or query text.",
        )
    if page == "deployment":
        return (
            f"{topic_label} can be deployed with Docker by running a container, mounting "
            "configuration files, and connecting it to required storage or network services.",
            f"Operators deploying {topic_label} should configure secrets, base URLs, persistent "
            "storage, health checks, and reverse-proxy settings before exposing the service.",
        )
    if page == "comparison":
        return (
            f"A comparison of {topic_label} should separate hosting model, API control, privacy "
            "properties, source coverage, cost, and operational maintenance.",
            "In this smoke fixture, self-hosted tools offer more operator control, hosted search "
            "APIs reduce maintenance, and AI-oriented search APIs can simplify agent integration.",
        )
    if page == "limitations":
        return (
            f"A limitation of {topic_label} is that output quality depends on source coverage, "
            "freshness, parsing quality, and whether evidence actually supports each claim.",
            f"{topic_label} should not be treated as complete when required answer slots are "
            "missing, weakly supported, or based on only one low-yield source.",
        )
    return (
        _definition_sentence(topic_label),
        f"{topic_label} is a suitable development smoke topic because it has observable source "
        "scope, evidence candidates, verification metadata, and report artifacts.",
    )


def _topic_label(topic_slug: str) -> str:
    labels = {
        "searxng": "SearXNG",
        "opensearch": "OpenSearch",
        "langgraph": "LangGraph",
        "model-context-protocol": "Model Context Protocol",
        "dify": "Dify",
        "retrieval-augmented-generation": "Retrieval-Augmented Generation",
        "chatgpt-deep-research-and-gemini-deep-research": (
            "ChatGPT Deep Research and Gemini Deep Research"
        ),
        "ai-search-comparison": "SearXNG, Brave Search API, and Tavily",
    }
    if topic_slug in labels:
        return labels[topic_slug]
    return " ".join(item.capitalize() for item in topic_slug.split("-") if item) or "Topic"


def _definition_sentence(topic_label: str) -> str:
    definitions = {
        "SearXNG": (
            "SearXNG is a privacy-oriented metasearch engine that combines results from "
            "multiple upstream search engines."
        ),
        "OpenSearch": (
            "OpenSearch is an open-source distributed search and analytics engine for storing, "
            "searching, and analyzing data."
        ),
        "LangGraph": (
            "LangGraph is a framework for building stateful graph-based workflows around "
            "language-model applications."
        ),
        "Model Context Protocol": (
            "Model Context Protocol is an open protocol for connecting AI applications to "
            "tools, data sources, and contextual services."
        ),
        "Dify": (
            "Dify is an application platform for building and operating AI workflows, agents, "
            "and retrieval-augmented applications."
        ),
        "Retrieval-Augmented Generation": (
            "Retrieval-Augmented Generation is a pattern that combines information retrieval "
            "with generation so model answers can use external evidence."
        ),
        "ChatGPT Deep Research and Gemini Deep Research": (
            "ChatGPT Deep Research and Gemini Deep Research are research-agent products that "
            "plan searches, inspect sources, and produce cited reports."
        ),
    }
    return definitions.get(
        topic_label,
        f"{topic_label} is a research topic with a definition, mechanism, source scope, and "
        "limitations.",
    )


def _mechanism_sentence(topic_label: str) -> str:
    mechanisms = {
        "SearXNG": (
            "SearXNG sends queries to upstream engines, normalizes the responses, and returns "
            "aggregated results to the user."
        ),
        "OpenSearch": (
            "OpenSearch works by indexing documents into shards, executing queries across "
            "those shards, and returning matching results."
        ),
        "LangGraph": (
            "LangGraph works by representing application steps as graph nodes and routing "
            "state between those nodes until the workflow reaches a result."
        ),
        "Model Context Protocol": (
            "Model Context Protocol works by defining messages that let a client discover "
            "servers, call tools, and retrieve contextual results."
        ),
        "Dify": (
            "Dify works by combining prompts, retrieval, tools, model configuration, and "
            "workflow steps into deployable AI applications."
        ),
        "Retrieval-Augmented Generation": (
            "Retrieval-Augmented Generation works by retrieving relevant documents, passing "
            "the evidence into a prompt, and generating an answer from those results."
        ),
        "ChatGPT Deep Research and Gemini Deep Research": (
            "Deep Research products work by planning research steps, searching the web, "
            "reading sources, and returning results with citations."
        ),
    }
    return mechanisms.get(
        topic_label,
        f"{topic_label} works by organizing inputs, processing evidence, and returning "
        "results that can be checked by the user.",
    )
