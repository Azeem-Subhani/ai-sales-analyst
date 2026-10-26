"""Chat history (Week 8): earlier turns become model messages in the right order,
tool calls and results always stay paired, and the size limits trim oldest first.

Pure functions only: no model, no database.
"""

from typing import Any

from src.agent import history
from src.agent.history import Turn


def call_message(*ids: str, text: str = "") -> dict[str, Any]:
    """An assistant message asking for tools."""
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {"id": i, "type": "function", "function": {"name": "run_sql", "arguments": "{}"}}
            for i in ids
        ],
    }


def result_message(call_id: str, content: str = '{"rows": [[1]]}') -> dict[str, Any]:
    """A tool result message."""
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def turn(question: str, answer: str, *messages: dict, withheld: bool = False) -> Turn:
    return Turn(question=question, answer=answer, withheld=withheld, tool_messages=list(messages))


def assert_valid_pairing(messages: list[dict]) -> None:
    """Every tool call has exactly one result right after its assistant message, and
    every result belongs to a call in the assistant message just before it."""
    index = 0
    while index < len(messages):
        message = messages[index]
        if message["role"] == "tool":
            raise AssertionError(f"tool result without a call before it: {message}")
        if message["role"] == "assistant" and message.get("tool_calls"):
            ids = [c["id"] for c in message["tool_calls"]]
            following = messages[index + 1 : index + 1 + len(ids)]
            assert [m["role"] for m in following] == ["tool"] * len(ids), messages
            assert sorted(m["tool_call_id"] for m in following) == sorted(ids)
            index += len(ids)
        index += 1


# ---------------------------------------------------------------- order


def test_history_becomes_messages_in_order() -> None:
    earlier = [
        turn("Q1?", "A1.", call_message("a"), result_message("a", "r-a")),
        turn("Q2?", "A2.", call_message("b", "c"), result_message("b"), result_message("c")),
    ]
    messages, _ = history.history_messages(earlier)
    assert [(m["role"], m.get("content")) for m in messages] == [
        ("user", "Q1?"),
        ("assistant", ""),
        ("tool", "r-a"),
        ("assistant", "A1."),
        ("user", "Q2?"),
        ("assistant", ""),
        ("tool", '{"rows": [[1]]}'),
        ("tool", '{"rows": [[1]]}'),
        ("assistant", "A2."),
    ]
    assert [c["id"] for c in messages[5]["tool_calls"]] == ["b", "c"]
    assert_valid_pairing(messages)


def test_turn_without_tools_is_question_then_answer() -> None:
    messages, chars = history.history_messages([turn("Hi?", "I can help with sales.")])
    assert messages == [
        {"role": "user", "content": "Hi?"},
        {"role": "assistant", "content": "I can help with sales."},
    ]
    assert chars == 0


def test_withheld_answer_becomes_the_note() -> None:
    withheld = turn("Q?", "(The written answer was withheld ...) 1,234", withheld=True)
    messages, _ = history.history_messages([withheld])
    assert messages[-1] == {"role": "assistant", "content": history.WITHHELD_NOTE}
    assert history.WITHHELD_NOTE == "[answer withheld: it failed the number check]"


def test_empty_or_no_history_adds_nothing() -> None:
    assert history.history_messages(None) == ([], 0)
    assert history.history_messages([]) == ([], 0)


def test_stored_turns_are_not_changed() -> None:
    long = "x" * (history.MAX_RESULT_CHARS + 50)
    earlier = [turn("Q?", "A.", call_message("a"), result_message("a", long))]
    history.history_messages(earlier)
    assert earlier[0].tool_messages[1]["content"] == long  # the guard needs the original


# ---------------------------------------------------------------- pairing


def test_orphan_call_and_orphan_result_are_never_sent() -> None:
    messages = [
        call_message("kept", "orphan_call"),
        result_message("kept"),
        result_message("orphan_result"),
    ]
    sent, _ = history.history_messages([turn("Q?", "A.", *messages)])
    assert [c["id"] for c in sent[1]["tool_calls"]] == ["kept"]
    assert [m.get("tool_call_id") for m in sent if m["role"] == "tool"] == ["kept"]
    assert_valid_pairing(sent)


def test_result_before_its_call_or_twice_is_dropped() -> None:
    messages = [result_message("a"), call_message("a"), result_message("a"), result_message("a")]
    assert history.paired(messages) == [call_message("a"), result_message("a")]


def test_result_separated_from_its_call_is_dropped() -> None:
    messages = [call_message("a"), {"role": "user", "content": "x"}, result_message("a")]
    assert history.paired(messages) == []


def test_call_message_with_no_results_is_dropped_or_keeps_its_text() -> None:
    assert history.paired([call_message("a")]) == []
    assert history.paired([call_message("a", text="Let me check.")]) == [
        {"role": "assistant", "content": "Let me check."}
    ]


def test_pairing_stays_valid_after_trimming() -> None:
    big = "9" * history.MAX_RESULT_CHARS
    earlier = [
        turn(
            f"Q{n}?",
            f"A{n}.",
            call_message(f"c{n}", f"orphan{n}"),
            result_message(f"c{n}", big),
            result_message(f"stray{n}"),
        )
        for n in range(15)
    ]
    messages, chars = history.history_messages(earlier)
    assert chars <= history.MAX_CARRIED_CHARS
    assert any(m.get("content") == history.TRIMMED_MARKER for m in messages)
    assert_valid_pairing(messages)


# ---------------------------------------------------------------- size limits


def test_each_result_is_cut_with_a_marker() -> None:
    long = "a" * (history.MAX_RESULT_CHARS + 100)
    messages, chars = history.history_messages(
        [turn("Q?", "A.", call_message("a"), result_message("a", long))]
    )
    content = messages[2]["content"]
    assert content == "a" * history.MAX_RESULT_CHARS + " " + history.TRUNCATED_MARKER
    assert chars == len(content)


def test_short_result_is_untouched() -> None:
    messages, chars = history.history_messages(
        [turn("Q?", "A.", call_message("a"), result_message("a", "short"))]
    )
    assert messages[2]["content"] == "short" and chars == len("short")


def test_total_cap_trims_oldest_turns_first() -> None:
    size = history.MAX_RESULT_CHARS
    # One result over the cap; the markers add a little, so two results are trimmed.
    turns_needed = history.MAX_CARRIED_CHARS // size + 1
    earlier = [
        turn(f"Q{n}?", f"A{n}.", call_message(f"c{n}"), result_message(f"c{n}", "r" * size))
        for n in range(turns_needed)
    ]
    messages, chars = history.history_messages(earlier)
    results = [m["content"] for m in messages if m["role"] == "tool"]
    trimmed = [i for i, content in enumerate(results) if content == history.TRIMMED_MARKER]
    assert trimmed == [0, 1]  # the two oldest
    assert all(len(c) == size for c in results[2:])  # newer ones untouched
    assert chars == sum(len(c) for c in results) <= history.MAX_CARRIED_CHARS


def test_questions_and_answers_are_never_trimmed() -> None:
    question = "Q" * 10_000
    answer = "A" * 10_000
    earlier = [
        turn(question, answer, call_message(f"c{n}"), result_message(f"c{n}", "x" * 4_000))
        for n in range(12)
    ]
    messages, _ = history.history_messages(earlier)
    assert [m["content"] for m in messages if m["role"] == "user"] == [question] * 12
    assert [
        m["content"] for m in messages if m["role"] == "assistant" and not m.get("tool_calls")
    ] == [answer] * 12


def test_limits_are_the_agreed_values() -> None:
    assert (history.MAX_RESULT_CHARS, history.MAX_CARRIED_CHARS) == (4_000, 40_000)
    assert (history.TRUNCATED_MARKER, history.TRIMMED_MARKER) == (
        "[truncated]",
        "[earlier result trimmed]",
    )


# ---------------------------------------------------------------- what the guard reads


def test_guard_sources_are_untrimmed_results_and_questions() -> None:
    long = "1" * history.MAX_RESULT_CHARS + " 98765.43"
    earlier = [turn("Top 4 customers?", "A.", call_message("a"), result_message("a", long))]
    assert history.carried_results(earlier) == [long]
    assert history.earlier_questions(earlier) == ["Top 4 customers?"]
    assert history.carried_results(None) == [] and history.earlier_questions(None) == []
