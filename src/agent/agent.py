"""Tool-calling loop: question in, answer out, max 10 tool calls (plan section 10).

A plain loop, no framework:
1. Send the system prompt, the question and the tool schemas to the model.
2. If the model asks for tools, run them (src/agent/tools.py) and send the results back.
3. Stop when the model answers in text.

Limits per question: 10 tool calls and 120 seconds. At the tool limit the model is
asked once more, with tools switched off, to answer from what it already has, and
the answer says it stopped early.

The written answer then goes through the number guard (src/agent/answer_guard.py):
every number must come from a tool result. On a failure the model gets one retry;
if that fails too, the written answer is withheld and the tool results are shown
instead. Every question, tool call and guard result is written to agent_log in
data/audit.duckdb.

An optional history (the dashboard chat, Week 8) carries earlier turns: their
questions, answers and exact tool messages go before the new question, within the size
limits in src/agent/history.py. Numbers from carried tool results and earlier
questions then pass the guard too; numbers only in an earlier answer do not. Without
history (the CLI, the eval) a question is single-turn, exactly as before.

An optional on_progress callback (the dashboard's chat page) hears about each step as
it happens: model calls, tool calls, a fallback model, a guard retry. It only sees the
final answer through ask()'s return value, after the guard, so unchecked text is
never shown. A callback that fails is logged and ignored.
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

from src.agent import agent_log, answer_guard, history, llm, prompts, tools
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

# ProgressEvent kinds, in the order a question usually produces them.
EVENT_THINKING = "thinking"  # a model call starts
EVENT_THINKING_DONE = "thinking_done"
EVENT_TOOL_START = "tool_start"
EVENT_TOOL_DONE = "tool_done"
EVENT_FALLBACK = "fallback"  # the main model failed; the fallback model answered
EVENT_GUARD_RETRY = "guard_retry"  # the answer used numbers no tool returned; one retry
EVENT_WITHHELD = "withheld"  # the retry failed too; tool results are shown instead
THINKING_LABEL = "Thinking"
GUARD_RETRY_LABEL = "Number check failed, asking the model again"
WITHHELD_LABEL = "Number check failed again, showing the tool results instead"


@dataclass(frozen=True)
class ProgressEvent:
    """One step of answering a question, for a live progress display."""

    kind: str
    label: str
    tool: str | None = None
    seconds: float | None = None  # for *_done events
    success: bool | None = None  # for *_done events
    arguments: str | None = None  # tool_start / tool_done: the JSON arguments
    result: dict | None = None  # tool_done: the tool result


ProgressCallback = Callable[[ProgressEvent], None]


class _Progress:
    """Sends events to the optional callback; a failing callback never stops the agent."""

    def __init__(self, callback: ProgressCallback | None) -> None:
        self.callback = callback

    def emit(self, kind: str, label: str, **fields: Any) -> None:
        if self.callback is None:
            return
        try:
            self.callback(ProgressEvent(kind, label, **fields))
        except Exception:
            logger.exception("Progress callback failed on %s", kind)


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
    answer_guard: str = answer_guard.GUARD_NOT_RUN  # ok | retried_ok | withheld | not_run
    answer_guard_failed: list[str] = field(default_factory=list)  # numbers no tool returned
    # This turn's assistant tool-call messages and tool results, in order (for history).
    tool_messages: list[dict[str, Any]] = field(default_factory=list)
    history_turns: int | None = None  # earlier turns sent; None: no history passed
    history_chars: int | None = None  # characters of carried tool results sent

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
    on_progress: ProgressCallback | None = None,
    history: list[history.Turn] | None = None,
) -> AgentAnswer:
    """Answer one question with tools. Never raises for model or tool problems.

    on_progress, if given, is called with a ProgressEvent for each step. history, if
    given, holds the earlier turns of the chat, oldest first.
    """
    config = config or AgentConfig()
    progress = _Progress(on_progress)
    chat = chat or llm.chat_with_tools
    started = config.clock()
    asked_at = datetime.now(UTC).replace(tzinfo=None)
    answer = AgentAnswer(question_id=uuid.uuid4().hex, question=question, answer="")
    try:
        settings = settings or llm.llm_settings()
        _run_loop(question, config, chat, settings, started, answer, progress, history)
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
    progress: _Progress,
    earlier: list[history.Turn] | None = None,
) -> None:
    """The tool loop; fills answer in place."""
    session = tools.ToolSession(
        tools.ToolContext(
            db_path=config.db_path, charts_dir=config.charts_dir, data_end=config.data_end
        )
    )
    carried, carried_chars = history.history_messages(earlier)
    if earlier is not None:
        answer.history_turns, answer.history_chars = len(earlier), carried_chars
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": prompts.build_system_prompt(config.db_path, config.data_end),
        },
        *carried,
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
        result = _model_call(
            chat,
            progress,
            messages,
            schemas,
            tool_choice="none" if at_limit else "auto",
            model=config.model,
            fallback=config.fallback,
            settings=_with_timeout(settings, remaining),
        )
        _add_usage(answer, result)
        if at_limit or not result.tool_calls:
            messages.append(result.assistant_message())
            text = _guarded_text(
                question,
                result.text,
                messages,
                config,
                chat,
                settings,
                started,
                answer,
                progress,
                earlier,
            )
            if at_limit:
                answer.stopped_early = "tool_limit"
                text = f"{TOOL_LIMIT_NOTE}\n\n{text}".strip()
            answer.answer = text
            return

        messages.append(result.assistant_message())
        answer.tool_messages.append(result.assistant_message())
        for call in result.tool_calls:
            # Every tool call needs a reply, even the ones over the limit.
            if len(answer.tool_calls) >= config.max_tool_calls:
                content = json.dumps(LIMIT_REACHED_RESULT)
            else:
                progress.emit(EVENT_TOOL_START, call.name, tool=call.name, arguments=call.arguments)
                outcome = session.execute(call.name, call.arguments)
                answer.tool_calls.append(outcome)
                progress.emit(
                    EVENT_TOOL_DONE,
                    call.name,
                    tool=call.name,
                    seconds=outcome.duration_seconds,
                    success=outcome.success,
                    arguments=outcome.arguments,
                    result=outcome.result,
                )
                content = outcome.content()
            tool_message = {"role": "tool", "tool_call_id": call.id, "content": content}
            messages.append(tool_message)
            answer.tool_messages.append(dict(tool_message))


def _guarded_text(
    question: str,
    text: str | None,
    messages: list[dict[str, Any]],
    config: AgentConfig,
    chat: ChatFunction,
    settings: llm.LLMSettings,
    started: float,
    answer: AgentAnswer,
    progress: _Progress,
    earlier: list[history.Turn] | None = None,
) -> str:
    """The model's answer if its numbers pass the guard, after at most one retry.

    messages already ends with the model's answer. If the retry also fails, or there
    is no time left for it, the written answer is withheld and the tool results are
    returned instead.
    """
    if not text:
        return NO_TEXT_ANSWER
    failed = answer_guard.check_answer(text, question, answer.tool_calls)
    if not failed:
        answer.answer_guard = answer_guard.GUARD_OK
        return text
    logger.warning("Answer used numbers no tool returned: %s", ", ".join(failed))
    remaining = config.max_seconds - (config.clock() - started)
    if remaining >= MIN_CALL_SECONDS:
        progress.emit(EVENT_GUARD_RETRY, GUARD_RETRY_LABEL)
        messages.append(
            {
                "role": "user",
                "content": answer_guard.RETRY_PROMPT.format(numbers=", ".join(failed)),
            }
        )
        result = _model_call(
            chat,
            progress,
            messages,
            tools.tool_schemas(),
            tool_choice="none",
            model=config.model,
            fallback=config.fallback,
            settings=_with_timeout(settings, remaining),
        )
        _add_usage(answer, result)
        retry_text = result.text or ""
        failed_again = answer_guard.check_answer(retry_text, question, answer.tool_calls)
        if retry_text and not failed_again:
            answer.answer_guard = answer_guard.GUARD_RETRIED_OK
            answer.answer_guard_failed = failed
            return retry_text
        if retry_text:
            failed = failed_again
        logger.warning("Answer withheld after the retry: %s", ", ".join(failed))
    progress.emit(EVENT_WITHHELD, WITHHELD_LABEL)
    answer.answer_guard = answer_guard.GUARD_WITHHELD
    answer.answer_guard_failed = failed
    return answer_guard.withheld_answer(failed, answer.tool_calls)


def turn_from(answer: AgentAnswer) -> history.Turn | None:
    """The answer as a Turn for the next question's history; None if it failed.

    A failed question (model unavailable, bad key or settings) is never carried.
    """
    if answer.error:
        return None
    return history.Turn(
        question=answer.question,
        answer=answer.answer,
        withheld=answer.answer_guard == answer_guard.GUARD_WITHHELD,
        tool_messages=answer.tool_messages,
    )


def _model_call(
    chat: ChatFunction, progress: _Progress, *args: Any, **kwargs: Any
) -> llm.LLMResult:
    """One model call, reported as a timed "Thinking" step (and a fallback, if used).

    Timed with time.monotonic, not config.clock, so the time limits work exactly as before.
    """
    progress.emit(EVENT_THINKING, THINKING_LABEL)
    started = time.monotonic()
    try:
        result = chat(*args, **kwargs)
    except Exception:
        seconds = time.monotonic() - started
        progress.emit(EVENT_THINKING_DONE, THINKING_LABEL, seconds=seconds, success=False)
        raise
    seconds = time.monotonic() - started
    progress.emit(EVENT_THINKING_DONE, THINKING_LABEL, seconds=seconds, success=True)
    if result.fallback_used:
        progress.emit(EVENT_FALLBACK, f"Main model failed, answered by {result.model}")
    return result


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
        answer_guard=answer.answer_guard,
        answer_guard_failed=", ".join(answer.answer_guard_failed) or None,
        history_turns=answer.history_turns,
        history_chars=answer.history_chars,
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
