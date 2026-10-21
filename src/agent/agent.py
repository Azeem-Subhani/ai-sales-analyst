"""Tool-calling loop: question in, answer out, max 10 tool calls (plan section 10).

A plain loop, no framework:
1. Send the system prompt, the question and the tool schemas to the model.
2. If the model asks for tools, run them (src/agent/tools.py) and send the results back.
3. Stop when the model answers in text.

Limits per question: 10 tool calls and 120 seconds. At the tool limit the model is
asked once more, with tools switched off, to answer from what it already has, and
the answer says it stopped early. Every question and tool call is written to
agent_log in data/audit.duckdb.
"""

import dataclasses
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from src.agent import agent_log, llm, prompts, tools
from src.analytics import kpis
from src.reports.audit import AUDIT_DB_PATH

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 10
MAX_SECONDS = 120.0
MIN_CALL_SECONDS = 5.0  # don't start a model call with less time than this left

TOOL_LIMIT_NOTE = f"(Stopped early: reached the limit of {MAX_TOOL_CALLS} tool calls.)"
TIME_LIMIT_NOTE = f"(Stopped early: reached the {MAX_SECONDS:g}-second limit.)"
FINAL_ANSWER_REQUEST = (
    "You have used all {limit} tool calls for this question. Answer now, using only the "
    "tool results above. Say which parts of the question you could not check."
)
LIMIT_REACHED_RESULT = {
    "error": "tool_call_limit",
    "message": f"Not run: the limit of {MAX_TOOL_CALLS} tool calls was reached.",
}
UNAVAILABLE_ANSWER = (
    "The AI model is unavailable right now, so I can't answer this. Please try again "
    "in a few minutes."
)
NO_TEXT_ANSWER = "I could not put together an answer from the tool results."
TIME_LIMIT_ANSWER = (
    "I couldn't finish within the time limit for one question. Please ask again, "
    "perhaps as a narrower question."
)

ChatFunction = Callable[..., llm.LLMResult]


@dataclass
class AgentAnswer:
    """The answer to one question, with what it took to get there."""

    question_id: str
    question: str
    answer: str
    model: str | None = None
    fallback_used: bool = False
    tool_calls: list[tools.ToolOutcome] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_seconds: float = 0.0
    stopped_early: str | None = None  # tool_limit | time_limit
    error: str | None = None

    @property
    def total_tokens(self) -> int:
        """Input plus output tokens over every model call for this question."""
        return self.input_tokens + self.output_tokens

    @property
    def charts(self) -> list[str]:
        """Paths of charts made while answering."""
        return [
            o.result["chart_path"]
            for o in self.tool_calls
            if o.success and o.name == "make_chart" and "chart_path" in o.result
        ]


@dataclass
class AgentConfig:
    """Everything one run needs; tests and the eval pass their own."""

    db_path: Path = kpis.DB_PATH
    audit_path: Path | None = AUDIT_DB_PATH  # None: do not write agent_log
    charts_dir: Path = tools.CHARTS_DIR
    data_end: date | None = None
    model: str | None = None  # overrides LLM_MODEL
    fallback: bool = True
    max_tool_calls: int = MAX_TOOL_CALLS
    max_seconds: float = MAX_SECONDS
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)


def ask(
    question: str,
    config: AgentConfig | None = None,
    *,
    chat: ChatFunction | None = None,
    settings: llm.LLMSettings | None = None,
) -> AgentAnswer:
    """Answer one question with tools. Never raises for model or tool problems."""
    config = config or AgentConfig()
    chat = chat or llm.chat_with_tools
    started = config.clock()
    asked_at = datetime.now(UTC).replace(tzinfo=None)
    answer = AgentAnswer(question_id=uuid.uuid4().hex, question=question, answer="")
    try:
        settings = settings or llm.llm_settings()
        _run_loop(question, config, chat, settings, started, answer)
    except (llm.LLMAuthError, llm.LLMConfigError) as error:
        answer.answer, answer.error = str(error), str(error)
    except llm.LLMUnavailableError as error:
        logger.error("Agent could not reach the model: %s", error)
        answer.answer, answer.error = UNAVAILABLE_ANSWER, str(error)
    answer.latency_seconds = round(config.clock() - started, 3)
    _log(answer, asked_at, config)
    return answer


def _run_loop(
    question: str,
    config: AgentConfig,
    chat: ChatFunction,
    settings: llm.LLMSettings,
    started: float,
    answer: AgentAnswer,
) -> None:
    """The tool loop; fills answer in place."""
    session = tools.ToolSession(
        tools.ToolContext(db_path=config.db_path, charts_dir=config.charts_dir)
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": prompts.build_system_prompt(config.db_path, config.data_end),
        },
        {"role": "user", "content": question},
    ]
    schemas = tools.tool_schemas()

    while True:
        remaining = config.max_seconds - (config.clock() - started)
        if remaining < MIN_CALL_SECONDS:
            answer.stopped_early = "time_limit"
            answer.answer = TIME_LIMIT_ANSWER
            return
        at_limit = len(answer.tool_calls) >= config.max_tool_calls
        if at_limit:
            messages.append(
                {
                    "role": "user",
                    "content": FINAL_ANSWER_REQUEST.format(limit=config.max_tool_calls),
                }
            )
        result = chat(
            messages,
            schemas,
            tool_choice="none" if at_limit else "auto",
            model=config.model,
            fallback=config.fallback,
            settings=_with_timeout(settings, remaining),
        )
        _add_usage(answer, result)
        if at_limit or not result.tool_calls:
            text = result.text or NO_TEXT_ANSWER
            if at_limit:
                answer.stopped_early = "tool_limit"
                text = f"{TOOL_LIMIT_NOTE}\n\n{text}".strip()
            answer.answer = text
            return

        messages.append(result.assistant_message())
        for call in result.tool_calls:
            # Every tool call needs a reply, even the ones over the limit.
            if len(answer.tool_calls) >= config.max_tool_calls:
                content = json.dumps(LIMIT_REACHED_RESULT)
            else:
                outcome = session.execute(call.name, call.arguments)
                answer.tool_calls.append(outcome)
                content = outcome.content()
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content})


def _with_timeout(settings: llm.LLMSettings, remaining: float) -> llm.LLMSettings:
    """The settings with the request timeout capped by the time left for this question."""
    return dataclasses.replace(
        settings, timeout_seconds=max(1.0, min(settings.timeout_seconds, remaining))
    )


def _add_usage(answer: AgentAnswer, result: llm.LLMResult) -> None:
    """Accumulate tokens, cost and which model answered."""
    answer.model = result.model
    answer.fallback_used = answer.fallback_used or result.fallback_used
    answer.input_tokens += result.input_tokens
    answer.output_tokens += result.output_tokens
    if result.cost_usd is not None:
        answer.cost_usd = (answer.cost_usd or 0.0) + result.cost_usd


def _log(answer: AgentAnswer, asked_at: datetime, config: AgentConfig) -> None:
    """Write agent_log rows; a logging failure must not lose the answer."""
    if config.audit_path is None:
        return
    question = agent_log.QuestionRecord(
        question_id=answer.question_id,
        asked_at_utc=asked_at,
        question=answer.question,
        model=answer.model,
        fallback_used=answer.fallback_used,
        tool_call_count=len(answer.tool_calls),
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
        total_tokens=answer.total_tokens,
        cost_usd=answer.cost_usd,
        latency_seconds=answer.latency_seconds,
        stopped_early=answer.stopped_early,
        final_answer=answer.answer,
        error=answer.error,
    )
    calls = [
        agent_log.ToolCallRecord(
            question_id=answer.question_id,
            call_index=index,
            tool_name=outcome.name,
            arguments=outcome.arguments,
            success=outcome.success,
            row_count=outcome.row_count,
            error=outcome.error,
            duration_seconds=outcome.duration_seconds,
            called_at_utc=outcome.called_at_utc or asked_at,
        )
        for index, outcome in enumerate(answer.tool_calls, start=1)
    ]
    try:
        agent_log.record_question(question, calls, config.audit_path)
    except Exception as error:  # noqa: BLE001 - the user still gets the answer
        logger.error("Could not write agent_log: %s", error)
