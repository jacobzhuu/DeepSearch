from __future__ import annotations

from json import JSONDecodeError
from typing import Any, Protocol

import httpx

from services.orchestrator.app.llm.types import LLMError, LLMRequest, LLMResponse


class LLMProvider(Protocol):
    name: str

    def generate(self, request: LLMRequest) -> LLMResponse: ...


class NoopLLMProvider:
    name = "noop"

    def generate(self, request: LLMRequest) -> LLMResponse:
        query = str(request.metadata.get("query") or "").strip()
        text = _noop_planner_json(query)
        return LLMResponse(
            text=text,
            model=request.model or "noop",
            provider=self.name,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            raw_response_id="noop-research-plan",
            finish_reason="stop",
        )


class UnsupportedLLMProvider:
    def __init__(self, provider_name: str) -> None:
        self.name = provider_name or "unsupported"

    def generate(self, request: LLMRequest) -> LLMResponse:
        del request
        raise LLMError(
            provider=self.name,
            error_code="unsupported_provider",
            message=f"unsupported LLM provider: {self.name}",
            retryable=False,
        )


class OpenAICompatibleLLMProvider:
    name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = sanitize_openai_compatible_base_url(base_url)
        self.chat_completions_url = build_chat_completions_url(self.base_url)
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.client = client

    def generate(self, request: LLMRequest) -> LLMResponse:
        self._validate_configuration()
        payload = {
            "model": request.model or self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: LLMError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._post_chat_completions(payload=payload, headers=headers)
                return self._parse_response(response)
            except LLMError as error:
                last_error = error
                if not error.retryable or attempt >= self.max_retries:
                    raise

        if last_error is not None:
            raise last_error
        raise LLMError(
            provider=self.name,
            error_code="request_failed",
            message="LLM request failed before receiving a response.",
            retryable=True,
        )

    def _validate_configuration(self) -> None:
        missing = []
        if not self.base_url:
            missing.append("LLM_BASE_URL")
        if not self.model:
            missing.append("LLM_MODEL")
        if not self.api_key:
            missing.append("LLM_API_KEY")
        if missing:
            raise LLMError(
                provider=self.name,
                error_code="configuration_error",
                message=f"LLM provider is missing required configuration: {', '.join(missing)}",
                retryable=False,
            )

    def _post_chat_completions(
        self,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        try:
            if self.client is not None:
                return self.client.post(
                    self.chat_completions_url,
                    headers=headers,
                    json=payload,
                )
            with httpx.Client(timeout=self.timeout_seconds, trust_env=True) as client:
                return client.post(
                    self.chat_completions_url,
                    headers=headers,
                    json=payload,
                )
        except ImportError as error:
            raise LLMError(
                provider=self.name,
                error_code="network_error",
                message=_sanitize_message(str(error), self.api_key),
                retryable=True,
            ) from error
        except httpx.TimeoutException as error:
            raise LLMError(
                provider=self.name,
                error_code="timeout",
                message="LLM request timed out.",
                retryable=True,
            ) from error
        except httpx.HTTPError as error:
            raise LLMError(
                provider=self.name,
                error_code="network_error",
                message=_sanitize_message(str(error), self.api_key),
                retryable=True,
            ) from error

    def _parse_response(self, response: httpx.Response) -> LLMResponse:
        if response.status_code >= 400:
            error_code, retryable = _classify_http_status(response.status_code)
            raise LLMError(
                provider=self.name,
                error_code=error_code,
                status_code=response.status_code,
                message=_sanitize_message(_error_message(response), self.api_key),
                retryable=retryable,
            )

        try:
            payload = response.json()
        except (JSONDecodeError, ValueError) as error:
            raise LLMError(
                provider=self.name,
                error_code="invalid_json",
                status_code=response.status_code,
                message="LLM response was not valid JSON.",
                retryable=False,
            ) from error

        if not isinstance(payload, dict):
            raise LLMError(
                provider=self.name,
                error_code="invalid_response",
                status_code=response.status_code,
                message="LLM response JSON was not an object.",
                retryable=False,
            )

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError(
                provider=self.name,
                error_code="invalid_response",
                status_code=response.status_code,
                message="LLM response did not include choices.",
                retryable=False,
            )
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LLMError(
                provider=self.name,
                error_code="invalid_response",
                status_code=response.status_code,
                message="LLM response choice was not an object.",
                retryable=False,
            )
        message = first_choice.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise LLMError(
                provider=self.name,
                error_code="invalid_response",
                status_code=response.status_code,
                message="LLM response did not include message content.",
                retryable=False,
            )

        usage = payload.get("usage")
        return LLMResponse(
            text=text,
            model=str(payload.get("model") or self.model),
            provider=self.name,
            usage=usage if isinstance(usage, dict) else None,
            raw_response_id=str(payload.get("id")) if payload.get("id") is not None else None,
            finish_reason=(
                str(first_choice.get("finish_reason"))
                if first_choice.get("finish_reason") is not None
                else None
            ),
        )


def _classify_http_status(status_code: int) -> tuple[str, bool]:
    if status_code in {401, 403}:
        return "auth_error", False
    if status_code == 429:
        return "rate_limited", True
    if status_code >= 500:
        return "server_error", True
    return "http_error", False


def sanitize_openai_compatible_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def build_chat_completions_url(base_url: str) -> str:
    sanitized = sanitize_openai_compatible_base_url(base_url)
    if sanitized.endswith("/chat/completions"):
        return sanitized
    return f"{sanitized}/chat/completions"


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except (JSONDecodeError, ValueError):
        return f"LLM provider returned HTTP {response.status_code}."

    if isinstance(payload, dict):
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return f"LLM provider returned HTTP {response.status_code}."


def _sanitize_message(message: str, api_key: str) -> str:
    sanitized = message
    if api_key:
        sanitized = sanitized.replace(api_key, "[redacted]")
    return sanitized


def _noop_planner_json(query: str) -> str:
    import json

    normalized_query = query or "Research question"
    subject = _subject_from_query(normalized_query)
    plan = {
        "intent": "definition_how_it_works",
        "normalized_question": normalized_query,
        "subquestions": [
            f"What is {subject}?",
            f"How does {subject} work?",
            f"What privacy protections does {subject} provide?",
            f"What engines or features does {subject} support?",
        ],
        "search_queries": [
            {
                "query_text": f"{subject} official documentation what is {subject}",
                "rationale": "Find the official definition and project overview.",
                "expected_source_type": "official_docs",
                "priority": 1,
            },
            {
                "query_text": (f"{subject} how it works metasearch engine upstream search engines"),
                "rationale": "Find mechanism details about aggregation and upstream engines.",
                "expected_source_type": "official_docs",
                "priority": 2,
            },
            {
                "query_text": f"{subject} privacy not storing user information",
                "rationale": "Find privacy behavior and data handling claims.",
                "expected_source_type": "official_docs",
                "priority": 3,
            },
            {
                "query_text": f"{subject} features integrations limitations",
                "rationale": "Find features, integrations, and limitations.",
                "expected_source_type": "reference",
                "priority": 4,
            },
        ],
        "source_preferences": {
            "preferred_domains": [],
            "avoid_domains": [
                "reddit.com",
                "youtube.com",
                "facebook.com",
                "x.com",
                "twitter.com",
                "tiktok.com",
            ],
            "freshness_required": False,
        },
        "answer_outline": [
            "Definition",
            "How it works",
            "Privacy model",
            "Features and integrations",
        ],
        "risk_notes": [
            "Prefer official documentation and stable references over community discussion.",
        ],
        "warnings": [],
    }
    return json.dumps(plan, sort_keys=True)


def _subject_from_query(query: str) -> str:
    lowered = query.strip().rstrip("?")
    if lowered.lower().startswith("what is "):
        remainder = lowered[8:]
        if " and how" in remainder.lower():
            return remainder[: remainder.lower().index(" and how")].strip() or lowered
        return remainder.strip() or lowered
    return lowered
