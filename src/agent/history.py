"""Earlier turns of a dashboard chat, sent with each new question (Week 8).

A Turn holds one earlier question, its answer, and the exact tool messages the agent
produced for it: the assistant messages that asked for tools and the tool result
messages, in order. agent.ask(history=...) puts them before the new question, so a
follow-up like "what did they order?" knows who "they" are.

Size limits (all here, in one place):
- each carried tool result is cut to MAX_RESULT_CHARS, followed by TRUNCATED_MARKER;
- carried tool-result text is capped at MAX_CARRIED_CHARS in total; when over, the
  oldest turns' results are replaced by TRIMMED_MARKER first.
Questions and answers are never trimmed, and the current question's own tool results
are not carried here, so these limits never touch them. Turns keep their tool results
untrimmed: the answer number guard checks against the originals.

A tool call without a matching result, or a result without its call, is never sent
(the API rejects it); paired() drops either side.
"""

from typing import Any

from pydantic import BaseModel, Field

MAX_RESULT_CHARS = 4_000
MAX_CARRIED_CHARS = 40_000
TRUNCATED_MARKER = "[truncated]"
TRIMMED_MARKER = "[earlier result trimmed]"
WITHHELD_NOTE = "[answer withheld: it failed the number check]"


class Turn(BaseModel):
    """One earlier question and answer, with the tool messages that produced it."""

    question: str
    answer: str
    withheld: bool = False
    tool_messages: list[dict[str, Any]] = Field(default_factory=list)


def paired(tool_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copies of the messages with every unmatched tool call or tool result dropped.

    A result counts only if its call came earlier in the list. An assistant message
    left with no calls and no text is dropped too.
    """
    called: set[str] = set()
    answered: set[str] = set()
    for message in tool_messages:
        if message.get("role") == "assistant":
            called.update(call["id"] for call in message.get("tool_calls") or [])
        elif message.get("role") == "tool" and message.get("tool_call_id") in called:
            answered.add(message["tool_call_id"])

    kept: list[dict[str, Any]] = []
    sent: set[str] = set()
    for message in tool_messages:
        if message.get("role") == "assistant":
            message = dict(message)
            calls = [call for call in message.get("tool_calls") or [] if call["id"] in answered]
            if calls:
                message["tool_calls"] = calls
            else:
                message.pop("tool_calls", None)
                if not message.get("content"):
                    continue
            kept.append(message)
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if call_id in answered and call_id not in sent:  # one result per call
                sent.add(call_id)
                kept.append(dict(message))
    return kept


def _cut(content: str) -> str:
    """One tool result, cut to MAX_RESULT_CHARS with a marker when longer."""
    if len(content) <= MAX_RESULT_CHARS:
        return content
    return f"{content[:MAX_RESULT_CHARS]} {TRUNCATED_MARKER}"


def _carried_tool_messages(history: list[Turn]) -> list[list[dict[str, Any]]]:
    """Each turn's paired tool messages, with the per-result and total limits applied."""
    turns = [paired(turn.tool_messages) for turn in history]
    results = [m for messages in turns for m in messages if m["role"] == "tool"]
    for message in results:
        message["content"] = _cut(str(message.get("content") or ""))
    total = sum(len(m["content"]) for m in results)
    for message in results:  # oldest first, since turns are in order
        if total <= MAX_CARRIED_CHARS:
            break
        total -= len(message["content"]) - len(TRIMMED_MARKER)
        message["content"] = TRIMMED_MARKER
    return turns


def history_messages(history: list[Turn] | None) -> tuple[list[dict[str, Any]], int]:
    """The messages for earlier turns, and the characters of tool results they carry.

    Per turn: the user question, that turn's tool messages, then the answer (or the
    withheld note). Goes between the system prompt and the new question.
    """
    if not history:
        return [], 0
    messages: list[dict[str, Any]] = []
    carried_chars = 0
    for turn, tool_messages in zip(history, _carried_tool_messages(history), strict=True):
        messages.append({"role": "user", "content": turn.question})
        messages.extend(tool_messages)
        carried_chars += sum(len(m["content"]) for m in tool_messages if m["role"] == "tool")
        answer = WITHHELD_NOTE if turn.withheld else turn.answer
        messages.append({"role": "assistant", "content": answer})
    return messages, carried_chars


def carried_results(history: list[Turn] | None) -> list[str]:
    """Every earlier tool result, untrimmed, for the answer number guard."""
    return [
        str(m.get("content") or "")
        for turn in history or []
        for m in paired(turn.tool_messages)
        if m["role"] == "tool"
    ]


def earlier_questions(history: list[Turn] | None) -> list[str]:
    """Every earlier question, for the answer number guard."""
    return [turn.question for turn in history or []]
