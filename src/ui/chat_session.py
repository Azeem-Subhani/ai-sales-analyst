"""One dashboard chat: its answered turns, the 20-turn cap and the history for the agent.

Pure helpers for the Chat page (Week 8). No SQL, no database, no model calls. The turns
live in st.session_state only, never on disk, so a new browser session starts empty.

A turn counts once it has an answer or a withheld answer. A question that failed
(the model was unavailable, a bad key) is not stored and does not count.
"""

from dataclasses import dataclass, field

from src.agent import agent, history
from src.ui import chat_steps

MAX_TURNS = 20  # answered turns per chat; then the input locks
COUNTER_FROM = 16  # show "N questions left" from this question number on
TURNS_LEFT_TEXT = "{n} questions left in this chat"
TURNS_LEFT_ONE = "1 question left in this chat"
LIMIT_TEXT = f"This chat has reached {MAX_TURNS} questions. Start a new chat to keep going."


@dataclass
class ChatTurn:
    """One answered question as the page shows it, with the steps it took."""

    answer: agent.AgentAnswer
    steps: list[chat_steps.Step] = field(default_factory=list)

    @property
    def agent_turn(self) -> history.Turn | None:
        """The turn as history for the next question (None for a failed question)."""
        return agent.turn_from(self.answer)


def can_ask(turns: list[ChatTurn]) -> bool:
    """True while the chat is under the cap."""
    return len(turns) < MAX_TURNS


def questions_left(turns: list[ChatTurn]) -> int:
    """How many more questions this chat takes."""
    return max(0, MAX_TURNS - len(turns))


def show_counter(turns: list[ChatTurn]) -> bool:
    """True when the next question is number COUNTER_FROM or later, below the cap."""
    return COUNTER_FROM <= len(turns) + 1 <= MAX_TURNS


def turns_left_text(turns: list[ChatTurn]) -> str:
    """'N questions left in this chat'."""
    left = questions_left(turns)
    return TURNS_LEFT_ONE if left == 1 else TURNS_LEFT_TEXT.format(n=left)


def add_turn(turns: list[ChatTurn], turn: ChatTurn) -> bool:
    """Store an answered turn; a failed one is not stored. True if it was stored."""
    if turn.agent_turn is None or not can_ask(turns):
        return False
    turns.append(turn)
    return True


def agent_history(turns: list[ChatTurn]) -> list[history.Turn]:
    """The earlier turns, oldest first, as agent.ask's history argument."""
    return [t for t in (turn.agent_turn for turn in turns) if t is not None]


def clear(turns: list[ChatTurn]) -> None:
    """Start a new chat: forget every turn (and so reset the counter)."""
    turns.clear()
