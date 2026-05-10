from __future__ import annotations

import json

import httpx
import pytest

from services.orchestrator.app.llm import (
    LLMError,
    LLMRequest,
    NoopLLMProvider,
    OpenAICompatibleLLMProvider,
    build_chat_completions_url,
    create_llm_provider,
)
from services.orchestrator.app.llm import providers as provider_module
from services.orchestrator.app.settings import Settings


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)


def test_llm_settings_default_to_noop_provider_with_local_proxy_disabled() -> None:
    settings = _settings()

    assert settings.llm_enabled is True
    assert settings.llm_provider == "noop"
    assert settings.research_planner_enabled is True
    assert settings.llm_report_writer_enabled is True
    assert settings.llm_source_judge_enabled is True
    assert settings.llm_source_judge_active_rerank is True
    assert settings.llm_trust_env_proxy is False
    assert settings.llm_claim_reviewer_enabled is True


def test_llm_api_key_is_not_in_repr_or_safe_summary() -> None:
    settings = _settings(LLM_API_KEY="test-api-key")

    assert "test-api-key" not in repr(settings)
    assert "test-api-key" not in str(settings.llm_safe_summary())
    assert settings.llm_safe_summary()["llm_api_key_present"] is True


def test_noop_llm_provider_returns_deterministic_planner_response() -> None:
    provider = NoopLLMProvider()

    response = provider.generate(
        LLMRequest(
            system_prompt="plan",
            user_prompt="query",
            model="noop",
            max_output_tokens=1200,
            metadata={"query": "What is SearXNG and how does it work?"},
        )
    )

    assert response.provider == "noop"
    assert "SearXNG official documentation" in response.text
    assert "privacy" in response.text.lower()


def test_unsupported_llm_provider_returns_structured_error() -> None:
    provider = create_llm_provider(_settings(LLM_ENABLED=True, LLM_PROVIDER="unsupported-test"))

    with pytest.raises(LLMError) as exc_info:
        provider.generate(
            LLMRequest(
                system_prompt="system",
                user_prompt="user",
                model="test-model",
                max_output_tokens=64,
            )
        )

    assert exc_info.value.error_code == "unsupported_provider"
    assert "unsupported-test" in str(exc_info.value)


@pytest.mark.parametrize(
    ("base_url", "expected_url"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com/chat/completions"),
        ("https://api.deepseek.com/", "https://api.deepseek.com/chat/completions"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
        ("https://api.deepseek.com/v1/", "https://api.deepseek.com/v1/chat/completions"),
    ],
)
def test_openai_compatible_base_url_normalization(
    base_url: str,
    expected_url: str,
) -> None:
    assert build_chat_completions_url(base_url) == expected_url


def test_openai_compatible_provider_builds_request_without_api_key_in_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type")
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "model": "test-model",
                "choices": [
                    {
                        "message": {"content": '{"subquestions": ["a"], "search_queries": ["b"]}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 3},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleLLMProvider(
            base_url="https://api.example.com/v1",
            api_key="test-api-key",
            model="test-model",
            timeout_seconds=30,
            max_retries=0,
            client=client,
        )
        response = provider.generate(
            LLMRequest(
                system_prompt="system",
                user_prompt="user",
                model="test-model",
                max_output_tokens=64,
            )
        )

    assert captured["authorization"] == "Bearer test-api-key"
    assert captured["content_type"] == "application/json"
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert "test-api-key" not in str(captured["body"])
    request_body = captured["json"]
    assert isinstance(request_body, dict)
    assert request_body["model"] == "test-model"
    assert request_body["max_tokens"] == 64
    assert isinstance(request_body["messages"], list)
    assert request_body["messages"][0]["role"] == "system"
    assert response.raw_response_id == "chatcmpl-test"
    assert response.text == '{"subquestions": ["a"], "search_queries": ["b"]}'


def test_research_loop_fetch_more_candidates_setting() -> None:
    settings = _settings(RESEARCH_LOOP_FETCH_MORE_CANDIDATES_PER_ROUND=5)
    assert settings.research_loop_fetch_more_candidates_per_round == 5
    assert settings.llm_safe_summary()["research_loop_fetch_more_candidates_per_round"] == 5


def test_openai_compatible_provider_disables_environment_proxy_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["trust_env"] = kwargs.get("trust_env")

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def post(
            self,
            url: str,
            *,
            headers: dict[str, str],
            json: dict[str, object],
        ) -> httpx.Response:
            del url, headers, json
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-test",
                    "model": "test-model",
                    "choices": [
                        {
                            "message": {"content": '{"subquestions": [], "search_queries": []}'},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

    monkeypatch.setenv("HTTPS_PROXY", "socks5://127.0.0.1:7890")
    monkeypatch.setattr(provider_module.httpx, "Client", FakeClient)
    provider = OpenAICompatibleLLMProvider(
        base_url="https://api.example.com/v1",
        api_key="test-api-key",
        model="test-model",
        timeout_seconds=30,
        max_retries=0,
    )

    response = provider.generate(
        LLMRequest(
            system_prompt="system",
            user_prompt="user",
            model="test-model",
            max_output_tokens=64,
        )
    )

    assert captured["trust_env"] is False
    assert response.raw_response_id == "chatcmpl-test"


@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (401, "auth_error", False),
        (429, "rate_limited", True),
        (500, "server_error", True),
    ],
)
def test_openai_compatible_provider_http_errors_are_sanitized(
    status_code: int,
    expected_code: str,
    retryable: bool,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            status_code,
            json={"error": {"message": "bad key test-api-key"}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleLLMProvider(
            base_url="https://api.example.com/v1",
            api_key="test-api-key",
            model="test-model",
            timeout_seconds=30,
            max_retries=0,
            client=client,
        )
        with pytest.raises(LLMError) as exc_info:
            provider.generate(
                LLMRequest(
                    system_prompt="system",
                    user_prompt="user",
                    model="test-model",
                    max_output_tokens=64,
                )
            )

    error = exc_info.value
    assert error.error_code == expected_code
    assert error.retryable is retryable
    assert "test-api-key" not in str(error)
    assert "test-api-key" not in str(error.to_payload())


def test_openai_compatible_provider_timeout_is_structured_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleLLMProvider(
            base_url="https://api.example.com/v1",
            api_key="test-api-key",
            model="test-model",
            timeout_seconds=30,
            max_retries=0,
            client=client,
        )
        with pytest.raises(LLMError) as exc_info:
            provider.generate(
                LLMRequest(
                    system_prompt="system",
                    user_prompt="user",
                    model="test-model",
                    max_output_tokens=64,
                )
            )

    assert exc_info.value.error_code == "timeout"
    assert exc_info.value.retryable is True


def test_openai_compatible_provider_invalid_json_is_structured_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, content=b"not json")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleLLMProvider(
            base_url="https://api.example.com/v1",
            api_key="test-api-key",
            model="test-model",
            timeout_seconds=30,
            max_retries=0,
            client=client,
        )
        with pytest.raises(LLMError) as exc_info:
            provider.generate(
                LLMRequest(
                    system_prompt="system",
                    user_prompt="user",
                    model="test-model",
                    max_output_tokens=64,
                )
            )

    assert exc_info.value.error_code == "invalid_json"
