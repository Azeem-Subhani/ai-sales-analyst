"""The only OpenRouter client in the project: main model plus fallback (design rule 3).

Every model call in the project goes through complete() or chat_with_tools(). No other
file imports openai. Settings come from .env:

    OPENROUTER_API_KEY    required
    LLM_BASE_URL          default https://openrouter.ai/api/v1
    LLM_MODEL             main model ID (required; no model IDs are hard-coded)
    LLM_FALLBACK_MODEL    fallback model ID (required)
    LLM_TIMEOUT_SECONDS   default 60

Every request uses temperature 0, OpenRouter's throughput-first provider routing
(the documented alternative to the ":nitro" suffix, see docs/decisions.md) and the
X-Title header. If the main model times out, is rate limited, returns a 5xx, cannot be
reached or returns an empty or invalid response, the same request is retried once on
the fallback model. Auth errors (401/403) never fall back: a bad key fails the same way
on every model.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai

from src.reports.delivery_config import ConfigError

logger = logging.getLogger(__name__)

KEY_ENV = "OPENROUTER_API_KEY"
BASE_URL_ENV = "LLM_BASE_URL"
MODEL_ENV = "LLM_MODEL"
FALLBACK_MODEL_ENV = "LLM_FALLBACK_MODEL"
TIMEOUT_ENV = "LLM_TIMEOUT_SECONDS"

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT_SECONDS = 60.0
TEMPERATURE = 0
APP_TITLE = "ai-sales-analyst"
# Fastest provider first; the equivalent of ":nitro" without changing model IDs.
PROVIDER_ROUTING: dict[str, Any] = {"provider": {"sort": "throughput"}}
# We handle fallback ourselves, so the SDK's own retries would only add delay and cost.
SDK_MAX_RETRIES = 0
SERVER_ERROR_STATUS = 500
SETUP_HINT = "See 'LLM setup' in README.md."
TOKENS_PER_PRICE_UNIT = 1_000_000

# USD per 1M input and output tokens, used only when OpenRouter's response carries no
# usage.cost. Filled from OpenRouter's /models prices by the Week 4 model check.
PRICE_TABLE: dict[str, tuple[float, float]] = {}


class LLMConfigError(ConfigError):
    """An LLM setting is missing or invalid in .env; retrying will not help."""


class LLMAuthError(ConfigError):
    """OpenRouter rejected the API key (401/403); no fallback is tried."""


class LLMUnavailableError(RuntimeError):
    """Both the main and the fallback model failed (or the main failed with fallback off)."""


class LLMRequestError(LLMUnavailableError):
    """The request itself was rejected (e.g. 400); the fallback would reject it too."""


class EmptyResponseError(RuntimeError):
    """The model answered with no text and no tool calls, or a malformed response."""


@dataclass(frozen=True)
class LLMSettings:
    """Connection settings read from the environment."""

    api_key: str
    base_url: str
    model: str
    fallback_model: str
    timeout_seconds: float


@dataclass(frozen=True)
class ToolCall:
    """One tool call requested by the model; arguments is the raw JSON string."""

    id: str
    name: str
    arguments: str


@dataclass
class LLMResult:
    """What one model call returned, plus what it cost."""

    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    fallback_used: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_seconds: float = 0.0

    def assistant_message(self) -> dict[str, Any]:
        """The assistant turn to append to the conversation before sending tool results."""
        message: dict[str, Any] = {"role": "assistant", "content": self.text or ""}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return message


class ChatClient(Protocol):
    """The slice of the OpenAI client this module uses (tests pass a fake)."""

    chat: Any


# ---------------------------------------------------------------- settings


def llm_settings() -> LLMSettings:
    """Read the LLM settings from the environment, or raise one clear LLMConfigError."""
    api_key = os.environ.get(KEY_ENV, "").strip()
    model = os.environ.get(MODEL_ENV, "").strip()
    fallback_model = os.environ.get(FALLBACK_MODEL_ENV, "").strip()
    missing = [
        name
        for name, value in (
            (KEY_ENV, api_key),
            (MODEL_ENV, model),
            (FALLBACK_MODEL_ENV, fallback_model),
        )
        if not value
    ]
    if missing:
        raise LLMConfigError(f"{' and '.join(missing)} not set in .env. {SETUP_HINT}")
    raw_timeout = os.environ.get(TIMEOUT_ENV, "").strip()
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_SECONDS
    except ValueError as error:
        raise LLMConfigError(f"{TIMEOUT_ENV} must be a number of seconds.") from error
    if timeout <= 0:
        raise LLMConfigError(f"{TIMEOUT_ENV} must be greater than 0.")
    base_url = os.environ.get(BASE_URL_ENV, "").strip() or DEFAULT_BASE_URL
    return LLMSettings(api_key, base_url, model, fallback_model, timeout)


def make_client(settings: LLMSettings) -> ChatClient:
    """An OpenAI SDK client pointed at OpenRouter."""
    return openai.OpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        max_retries=SDK_MAX_RETRIES,
    )


# ---------------------------------------------------------------- public calls


def complete(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    fallback: bool = True,
    max_tokens: int | None = None,
    client: ChatClient | None = None,
    settings: LLMSettings | None = None,
) -> LLMResult:
    """Plain text completion. model overrides LLM_MODEL (e.g. for the model check)."""
    request: dict[str, Any] = {"messages": messages}
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    return _call(request, model=model, fallback=fallback, client=client, settings=settings)


def chat_with_tools(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    tool_choice: str = "auto",
    model: str | None = None,
    fallback: bool = True,
    client: ChatClient | None = None,
    settings: LLMSettings | None = None,
) -> LLMResult:
    """Completion that may return tool calls. tool_choice="none" forces a text answer."""
    request: dict[str, Any] = {"messages": messages, "tools": tools, "tool_choice": tool_choice}
    return _call(request, model=model, fallback=fallback, client=client, settings=settings)


# ---------------------------------------------------------------- internals


def _call(
    request: dict[str, Any],
    *,
    model: str | None,
    fallback: bool,
    client: ChatClient | None,
    settings: LLMSettings | None,
) -> LLMResult:
    """Try the main model, then once on the fallback model for recoverable failures."""
    settings = settings or llm_settings()
    client = client or make_client(settings)
    main_model = model or settings.model
    try:
        return _call_model(client, settings, main_model, request, fallback_used=False)
    except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
        raise _auth_error(error) from error
    except Exception as error:
        if not _should_fall_back(error):
            if isinstance(error, openai.APIError):
                raise _request_error(main_model, error) from error
            raise  # a bug in our code, not an API problem
        main_error = error
    if not fallback or settings.fallback_model == main_model:
        raise LLMUnavailableError(f"{main_model} failed: {_describe(main_error)}") from main_error
    logger.warning(
        "LLM %s failed (%s); retrying on %s",
        main_model,
        _describe(main_error),
        settings.fallback_model,
    )
    try:
        return _call_model(client, settings, settings.fallback_model, request, fallback_used=True)
    except (openai.AuthenticationError, openai.PermissionDeniedError) as error:
        raise _auth_error(error) from error
    except Exception as error:
        if not _should_fall_back(error):
            if isinstance(error, openai.APIError):
                raise _request_error(settings.fallback_model, error) from error
            raise  # a bug in our code, not an API problem
        raise LLMUnavailableError(
            f"Both models failed. {main_model}: {_describe(main_error)}; "
            f"{settings.fallback_model}: {_describe(error)}"
        ) from error


def _call_model(
    client: ChatClient,
    settings: LLMSettings,
    model: str,
    request: dict[str, Any],
    *,
    fallback_used: bool,
) -> LLMResult:
    """One request to one model, parsed into an LLMResult."""
    started = time.monotonic()
    response = client.chat.completions.create(
        model=model,
        temperature=TEMPERATURE,
        timeout=settings.timeout_seconds,
        extra_body=PROVIDER_ROUTING,
        extra_headers={"X-Title": APP_TITLE},
        **request,
    )
    latency = time.monotonic() - started
    result = _parse_response(response, model)
    result.fallback_used = fallback_used
    result.latency_seconds = latency
    return result


def _parse_response(response: Any, requested_model: str) -> LLMResult:
    """Turn an SDK response into an LLMResult, or raise EmptyResponseError."""
    choices = getattr(response, "choices", None)
    if not choices:
        raise EmptyResponseError("response has no choices")
    message = getattr(choices[0], "message", None)
    if message is None:
        raise EmptyResponseError("response has no message")
    text = (getattr(message, "content", None) or "").strip() or None
    tool_calls = [_parse_tool_call(call) for call in (getattr(message, "tool_calls", None) or [])]
    if text is None and not tool_calls:
        raise EmptyResponseError("response has no text and no tool calls")

    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    return LLMResult(
        text=text,
        tool_calls=tool_calls,
        model=getattr(response, "model", None) or requested_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=_cost(usage, requested_model, input_tokens, output_tokens),
    )


def _parse_tool_call(call: Any) -> ToolCall:
    """One SDK tool call as a ToolCall; a call without a function name is invalid."""
    function = getattr(call, "function", None)
    name = getattr(function, "name", None)
    if not name:
        raise EmptyResponseError("tool call without a function name")
    arguments = getattr(function, "arguments", None) or "{}"
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments)
    return ToolCall(id=getattr(call, "id", None) or name, name=name, arguments=arguments)


def _cost(usage: Any, model: str, input_tokens: int, output_tokens: int) -> float | None:
    """USD cost: OpenRouter's usage.cost (credits, 1 credit = 1 USD), else the price table."""
    reported = getattr(usage, "cost", None)
    if reported is None:
        extra = getattr(usage, "model_extra", None) or {}
        reported = extra.get("cost")
    if reported is not None:
        return float(reported)
    prices = PRICE_TABLE.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return (input_tokens * input_price + output_tokens * output_price) / TOKENS_PER_PRICE_UNIT


def _should_fall_back(error: Exception) -> bool:
    """Timeouts, rate limits, 5xx, connection errors and empty responses fall back."""
    if isinstance(error, openai.APIConnectionError | openai.RateLimitError | EmptyResponseError):
        return True  # APITimeoutError is a subclass of APIConnectionError
    if isinstance(error, openai.APIStatusError):
        return error.status_code >= SERVER_ERROR_STATUS
    return isinstance(error, openai.APIResponseValidationError)


def _auth_error(error: Exception) -> LLMAuthError:
    """A clear message about the key; the key itself is never included."""
    status = getattr(error, "status_code", "401/403")
    return LLMAuthError(
        f"OpenRouter rejected the API key ({status}). Check {KEY_ENV} in .env "
        f"and the key's credit limit. {SETUP_HINT}"
    )


def _request_error(model: str, error: Exception) -> LLMRequestError:
    """Wrap an error that is not worth a fallback so callers need not import openai."""
    return LLMRequestError(f"{model} rejected the request: {_describe(error)}")


def _describe(error: Exception) -> str:
    """Short description of an error for logs and LLMUnavailableError messages."""
    status = getattr(error, "status_code", None)
    name = type(error).__name__
    return f"{name} {status}" if status else f"{name}: {error}"
