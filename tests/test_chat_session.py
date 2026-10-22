"""The dashboard chat's 20-question cap and history (src/ui/chat_session.py, Week 8)."""

import pytest

from src.agent import agent
from src.ui import chat_session
from src.ui.chat_session import ChatTurn


def answered(n: int = 0, error: str | None = None, guard: str = "ok") -> ChatTurn:
    """A chat turn as the page stores it."""
    return ChatTurn(
        agent.AgentAnswer(
            question_id=f"q{n}",
            question=f"Question {n}?",
            answer=f"Answer {n}.",
            error=error,
            answer_guard=guard,
            tool_messages=[
                {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{n}"}]},
                {"role": "tool", "tool_call_id": f"c{n}", "content": "{}"},
            ],
        )
    )


def chat(n: int) -> list[ChatTurn]:
    return [answered(i) for i in range(n)]


@pytest.mark.parametrize(
    ("turns", "can_ask", "left", "counter"),
    [(0, True, 20, False), (14, True, 6, False), (15, True, 5, True), (19, True, 1, True),
     (20, False, 0, False)],
)  # fmt: skip
def test_cap_helpers(turns: int, can_ask: bool, left: int, counter: bool) -> None:
    session = chat(turns)
    assert chat_session.can_ask(session) is can_ask
    assert chat_session.questions_left(session) == left
    assert chat_session.show_counter(session) is counter


def test_counter_text() -> None:
    assert chat_session.turns_left_text(chat(15)) == "5 questions left in this chat"
    assert chat_session.turns_left_text(chat(19)) == "1 question left in this chat"


def test_limit_text_and_cap() -> None:
    assert chat_session.MAX_TURNS == 20
    assert chat_session.LIMIT_TEXT == (
        "This chat has reached 20 questions. Start a new chat to keep going."
    )


def test_add_turn_stops_at_the_cap() -> None:
    session = chat(19)
    assert chat_session.add_turn(session, answered(19)) is True
    assert len(session) == 20
    assert chat_session.add_turn(session, answered(20)) is False
    assert len(session) == 20


def test_clear_resets_the_counter() -> None:
    session = chat(20)
    chat_session.clear(session)
    assert session == []
    assert chat_session.can_ask(session) and chat_session.questions_left(session) == 20


def test_failed_question_is_not_stored_or_counted() -> None:
    session = chat(3)
    assert chat_session.add_turn(session, answered(3, error="Both models failed.")) is False
    assert len(session) == 3 and chat_session.questions_left(session) == 17


def test_withheld_answer_counts_and_is_carried_as_withheld() -> None:
    session: list[ChatTurn] = []
    assert chat_session.add_turn(session, answered(0, guard="withheld")) is True
    [turn] = chat_session.agent_history(session)
    assert turn.withheld is True


def test_agent_history_keeps_order_and_tool_messages() -> None:
    session = chat(3)
    turns = chat_session.agent_history(session)
    assert [t.question for t in turns] == ["Question 0?", "Question 1?", "Question 2?"]
    assert turns[1].tool_messages[1]["tool_call_id"] == "c1"
