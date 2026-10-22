"""Tests for src/agent/llm.py with a fake client: no request ever leaves the machine.

The SDK's error classes are reached through llm.openai, because llm.py is the only
module allowed to load the SDK (design rule 3).
"""

import socket
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest

from src.agent import llm

SETTINGS = llm.LLMSettings(
    api_key="test-key-not-real",
    base_url="https://openrouter.ai/api/v1",
    model="main/model",
    fallback_model="fallback/model",
    timeout_seconds=60.0,
)
MESSAGES = [{"role": "user", "content": "hi"}]
REQUEST = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")


def response(
    text: str | None = "hello",
    *,
    model: str = "main/model",
    tool_calls: list | None = None,
    cost: float | None = 0.0012,
    finish_reason: str | None = "stop",
    reasoning_tokens: int | None = None,
) -> SimpleNamespace:
    """A minimal stand-in for the SDK's ChatCompletion object."""
    message = SimpleNamespace(content=text, tool_calls=tool_calls)
    details = SimpleNamespace(reasoning_tokens=reasoning_tokens)
    usage = SimpleNamespace(
        prompt_tokens=100, completion_tokens=20, cost=cost, completion_tokens_details=details
    )
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


def status_error(cls: type, status: int) -> Exception:
    """Build one of the SDK's HTTP status errors."""
    return cls("failed", response=httpx2.Response(status, request=REQUEST), body=None)


class FakeClient:
    """Returns or raises the scripted outcomes in order and records every request."""

    def __init__(self, *outcomes: Any) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def call(client: FakeClient, **kwargs: Any) -> llm.LLMResult:
    """complete() with the fake client and dummy settings."""
    return llm.complete(MESSAGES, client=client, settings=SETTINGS, **kwargs)


def test_main_model_success() -> None:
    client = FakeClient(response("Revenue rose."))
    result = call(client)
    assert result.text == "Revenue rose."
    assert result.model == "main/model"
    assert result.fallback_used is False
    assert (result.input_tokens, result.output_tokens) == (100, 20)
    assert result.cost_usd == pytest.approx(0.0012)
    assert result.latency_seconds >= 0
    assert [r["model"] for r in client.requests] == ["main/model"]


def test_fallback_on_timeout() -> None:
    client = FakeClient(
        llm.openai.APITimeoutError(request=REQUEST), response(model="fallback/model")
    )
    result = call(client)
    assert result.fallback_used is True
    assert result.model == "fallback/model"
    assert [r["model"] for r in client.requests] == ["main/model", "fallback/model"]


@pytest.mark.parametrize("status", [500, 502, 503])
def test_fallback_on_5xx(status: int) -> None:
    error = status_error(llm.openai.InternalServerError, status)
    client = FakeClient(error, response(model="fallback/model"))
    assert call(client).fallback_used is True


@pytest.mark.parametrize(
    "error",
    [
        status_error(llm.openai.RateLimitError, 429),
        llm.openai.APIConnectionError(request=REQUEST),
    ],
)
def test_fallback_on_rate_limit_and_connection_error(error: Exception) -> None:
    client = FakeClient(error, response(model="fallback/model"))
    assert call(client).fallback_used is True


@pytest.mark.parametrize(
    "bad",
    [response(text=None), response(text="   "), SimpleNamespace(choices=[], usage=None)],
)
def test_fallback_on_empty_or_invalid_response(bad: SimpleNamespace) -> None:
    client = FakeClient(bad, response(model="fallback/model"))
    assert call(client).fallback_used is True


def test_both_failing_raises_one_clear_error() -> None:
    client = FakeClient(
        llm.openai.APITimeoutError(request=REQUEST),
        status_error(llm.openai.InternalServerError, 503),
    )
    with pytest.raises(llm.LLMUnavailableError, match="Both models failed") as caught:
        call(client)
    assert "main/model" in str(caught.value) and "fallback/model" in str(caught.value)


@pytest.mark.parametrize(
    ("cls", "status"),
    [(llm.openai.AuthenticationError, 401), (llm.openai.PermissionDeniedError, 403)],
)
def test_no_fallback_on_auth_error(cls: type, status: int) -> None:
    client = FakeClient(status_error(cls, status), response())
    with pytest.raises(llm.LLMAuthError, match="rejected the API key") as caught:
        call(client)
    assert "test-key-not-real" not in str(caught.value)  # never echo the key
    assert len(client.requests) == 1


def test_fallback_off_raises_after_main_failure() -> None:
    client = FakeClient(llm.openai.APITimeoutError(request=REQUEST), response())
    with pytest.raises(llm.LLMUnavailableError):
        call(client, fallback=False)
    assert len(client.requests) == 1


def test_bad_request_is_not_retried() -> None:
    client = FakeClient(status_error(llm.openai.BadRequestError, 400), response())
    with pytest.raises(llm.LLMRequestError):
        call(client)
    assert len(client.requests) == 1


def test_every_request_has_temperature_0_routing_and_title() -> None:
    client = FakeClient(llm.openai.APITimeoutError(request=REQUEST), response(), response())
    call(client)
    llm.chat_with_tools(MESSAGES, [], client=client, settings=SETTINGS)
    assert len(client.requests) == 3
    for request in client.requests:
        assert request["temperature"] == 0
        assert request["extra_body"] == {"provider": {"sort": "throughput"}}
        assert request["extra_headers"] == {"X-Title": "ai-sales-analyst"}
        assert request["timeout"] == 60.0
        assert ":nitro" not in request["model"]


def test_model_argument_overrides_main_model() -> None:
    client = FakeClient(response(model="other/model"))
    call(client, model="other/model")
    assert client.requests[0]["model"] == "other/model"


def test_chat_with_tools_returns_tool_calls() -> None:
    tool_call = SimpleNamespace(
        id="call_1", function=SimpleNamespace(name="get_kpis", arguments='{"x": 1}')
    )
    client = FakeClient(response(text=None, tool_calls=[tool_call]))
    result = llm.chat_with_tools(MESSAGES, [{"type": "function"}], client=client, settings=SETTINGS)
    assert result.tool_calls == [llm.ToolCall("call_1", "get_kpis", '{"x": 1}')]
    assert client.requests[0]["tool_choice"] == "auto"
    assert result.assistant_message()["tool_calls"][0]["function"]["name"] == "get_kpis"


def test_cost_falls_back_to_price_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(llm.PRICE_TABLE, "main/model", (1.0, 4.0))
    result = call(FakeClient(response(cost=None)))
    assert result.cost_usd == pytest.approx((100 * 1.0 + 20 * 4.0) / 1_000_000)


def test_cost_is_none_without_usage_cost_or_price() -> None:
    assert call(FakeClient(response(cost=None))).cost_usd is None


def test_missing_key_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")  # conftest sets a fake one for every test
    with pytest.raises(llm.LLMConfigError) as caught:
        llm.llm_settings()
    message = str(caught.value)
    assert message.startswith("OPENROUTER_API_KEY and LLM_MODEL and LLM_FALLBACK_MODEL not set")
    assert "\n" not in message


def test_settings_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setenv("LLM_MODEL", "a/b")
    monkeypatch.setenv("LLM_FALLBACK_MODEL", "c/d")
    settings = llm.llm_settings()
    assert settings.timeout_seconds == 60.0
    assert settings.base_url == "https://openrouter.ai/api/v1"
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "abc")
    with pytest.raises(llm.LLMConfigError, match="LLM_TIMEOUT_SECONDS"):
        llm.llm_settings()


def test_network_guard_blocks_openrouter() -> None:
    with pytest.raises(OSError, match="Tests may not call"):
        socket.getaddrinfo("openrouter.ai", 443)
    with pytest.raises(OSError, match="Tests may not call"):
        socket.create_connection(("openrouter.ai", 443))


def test_real_client_cannot_reach_openrouter() -> None:
    """Even the real SDK client cannot be created inside tests, so no credit is ever spent."""
    with pytest.raises(RuntimeError, match="OpenRouter is blocked in tests"):
        llm.complete(MESSAGES, settings=SETTINGS)


def test_zero_reported_cost_with_tokens_uses_price_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: OpenRouter reported cost 0 for real calls in the Week 4 model check."""
    monkeypatch.setitem(llm.PRICE_TABLE, "main/model", (1.0, 4.0))
    result = call(FakeClient(response(cost=0.0)))
    assert result.cost_usd == pytest.approx((100 * 1.0 + 20 * 4.0) / 1_000_000)


def test_finish_reason_and_reasoning_tokens_are_recorded() -> None:
    client = FakeClient(response("cut", finish_reason="length", reasoning_tokens=15))
    result = llm.complete(MESSAGES, client=client, settings=SETTINGS, max_tokens=20)
    assert (result.finish_reason, result.reasoning_tokens) == ("length", 15)
    assert result.truncated is True
    assert client.requests[0]["max_tokens"] == 20


def test_normal_stop_is_not_truncated() -> None:
    result = llm.complete(MESSAGES, client=FakeClient(response("done")), settings=SETTINGS)
    assert (result.finish_reason, result.reasoning_tokens, result.truncated) == ("stop", 0, False)


def test_missing_finish_reason_is_not_truncated() -> None:
    bare = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi", tool_calls=None))],
        usage=None,
        model="main/model",
    )
    result = llm.complete(MESSAGES, client=FakeClient(bare), settings=SETTINGS)
    assert (result.finish_reason, result.truncated) == (None, False)
