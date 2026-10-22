"""Chat page: the same agent as python -m src.agent.chat, one question at a time.

How a question is shown:
1. The question appears as a bubble straight away.
2. Below it, small muted lines show each step live (tools, a fallback model, a
   number-check retry) from agent.ask's on_progress events, then collapse to one line
   that opens with <details> (src/ui/chat_steps.py). The model, tokens and cost are
   inside that opened view only. The answer is plain text on the page: no avatars.
3. The answer only arrives when agent.ask returns, after the answer number guard, and
   is then revealed word by word. Unchecked text is never shown. A withheld answer is
   shown as a warning with the tool results instead.

While a question is being answered the chat input is disabled, so a second question
cannot interrupt the run; the lock is released in a finally block, so an error or a
timeout never leaves it disabled.

Each question is answered on its own (single-turn, as in the eval); earlier questions
and answers are only shown as history. The agent's tools are read-only, and this page
has no approval actions.
"""

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

import streamlit as st

from src.agent import agent, llm
from src.reports import data_clock
from src.ui import chat_steps, common, style

logger = logging.getLogger(__name__)

HISTORY_KEY = "chat_history"
QUESTION_KEY = "chat_question"  # the question being answered
BUSY_KEY = "chat_busy"  # True while a question is answered: the input is disabled
ERROR_KEY = "chat_error"  # shown once after a question failed unexpectedly
FAILED_MESSAGE = (
    "Something went wrong while answering, and the question was not answered. "
    "Please ask again. The details are in the dashboard log."
)
UNAVAILABLE_MESSAGE = "The AI service isn't responding right now. Please try again in a minute."
PLACEHOLDER = "Ask about sales or demand. One question at a time."
TRUST_TEXT = "Every number in an answer comes from a tool, never from the model."
STREAM_DELAY_SECONDS = 0.012  # per word of the typewriter effect
EXAMPLES = [  # (Lucide icon, card title, the question it sends)
    ("users", "Lapsed wholesale customers", "Which wholesale customers have stopped ordering?"),
    ("package-x", "September stockouts", "Which products ran out of stock in September 2026?"),
    ("target", "Most biased forecast", "Which product's forecast is the most biased?"),
    (
        "factory",
        "Top pre-build",
        "What is the top pre-build recommendation, and how many units?",
    ),
]
NOTE_KINDS = {agent.EVENT_FALLBACK, agent.EVENT_GUARD_RETRY, agent.EVENT_WITHHELD}


@dataclass
class Turn:
    """One question and its answer, with the steps it took."""

    answer: agent.AgentAnswer
    steps: list[chat_steps.Step] = field(default_factory=list)


class LiveSteps:
    """agent.ask's on_progress callback: redraws the step lines in one placeholder."""

    def __init__(self, placeholder: object) -> None:
        self.placeholder = placeholder
        self.steps: list[chat_steps.Step] = []
        self.thinking = True  # the model starts first; show Thinking at once

    def __call__(self, event: agent.ProgressEvent) -> None:
        if event.kind == agent.EVENT_THINKING:
            self.thinking = True
        elif event.kind == agent.EVENT_THINKING_DONE:
            self.thinking = False
        elif event.kind == agent.EVENT_TOOL_START:
            self.steps.append(chat_steps.Step(tool=event.tool or "", arguments=event.arguments))
        elif event.kind == agent.EVENT_TOOL_DONE:
            self.finish(event)
        elif event.kind in NOTE_KINDS:
            self.steps.append(chat_steps.Step(note=event.label, state="ok"))
        self.draw()

    def finish(self, event: agent.ProgressEvent) -> None:
        """Mark the latest running tool step as done, with its duration and result."""
        for step in reversed(self.steps):
            if step.tool is not None and step.state == "running":
                step.state = "ok" if event.success else "fail"
                step.seconds = event.seconds
                step.arguments = event.arguments or step.arguments
                step.result = event.result
                return

    def draw(self) -> None:
        """Show the current steps."""
        self.placeholder.html(chat_steps.live_html(self.steps, self.thinking))


def meta_line(answer: agent.AgentAnswer) -> str:
    """model · tokens · cost · time, shown inside the opened steps view.

    The cost is left out when OpenRouter reports none or $0 (it often does; the real
    spend is measured from the key balance).
    """
    parts = [answer.model or "no model", f"{answer.total_tokens:,} tokens"]
    if answer.fallback_used:
        parts.insert(1, "fallback model")
    if answer.cost_usd:  # None or 0.0: no cost shown
        parts.append(f"${answer.cost_usd:.4f}")
    parts.append(f"{answer.latency_seconds:.1f} s")
    return " · ".join(parts)  # chat_steps.collapsed_html escapes it


def words(text: str) -> Iterator[str]:
    """The checked answer, word by word, for the typewriter effect."""
    for word in text.split(" "):
        yield word + " "
        time.sleep(STREAM_DELAY_SECONDS)


def show_answer(answer: agent.AgentAnswer, stream: bool) -> None:
    """The answer text (streamed when new), a warning if withheld, an error if it failed."""
    if answer.error:
        st.error(answer.answer)
        return
    if answer.answer_guard == "withheld":
        st.warning(
            "The number check could not confirm the written answer, so it is withheld. "
            "These are the tool results it was based on."
        )
        st.markdown(answer.answer)
    elif stream:
        st.write_stream(words(answer.answer))
    else:
        st.markdown(answer.answer)
    for path in answer.charts:
        st.caption(f"Chart saved: {path}")


def steps_line(turn: Turn) -> str:
    """The collapsed steps line, with the model and tokens inside its opened view."""
    return chat_steps.collapsed_html(
        turn.steps, turn.answer.latency_seconds, meta_line(turn.answer)
    )


def show_turn(turn: Turn, index: int) -> None:
    """An earlier question and answer, as history."""
    st.html(style.user_message(turn.answer.question))
    with st.container(key=f"chat-answer-{index}"):
        st.html(steps_line(turn))
        show_answer(turn.answer, stream=False)


def ask_live(question: str, index: int) -> Turn:
    """Show the question, the live steps and the checked answer; return the turn."""
    st.html(style.user_message(question))
    with st.container(key=f"chat-answer-{index}"):
        live = LiveSteps(st.empty())
        live.draw()
        config = agent.AgentConfig(
            db_path=common.path(common.SALES_DB_KEY), data_end=data_clock.data_end_from_env()
        )
        answer = agent.ask(question, config, on_progress=live)  # guarded before it returns
        turn = Turn(answer, live.steps)
        live.placeholder.html(steps_line(turn))
        show_answer(answer, stream=True)
    return turn


def empty_state() -> None:
    """The hero, the trust pill and the example questions as cards; a click sends one.

    Each card is drawn in HTML; a real button (key example-N) is stretched over it by
    style.css, so the click, keyboard focus and the busy lock work as for any button.
    """
    st.html(
        '<div class="chat-welcome"><h2 class="chat-welcome-title">Ask about sales and demand'
        f"</h2>{style.trust_pill(TRUST_TEXT)}</div>"
    )
    st.html('<p class="suggest-label">Suggested questions</p>')
    # Row by row, so the two cards of a row stretch to the same height (style.css).
    for row in range(0, len(EXAMPLES), 2):
        for column, index in zip(st.columns(2), range(row, row + 2), strict=True):
            icon, title, example = EXAMPLES[index]
            with column, st.container(key=f"suggestion-{index}"):
                st.html(style.suggestion_card(icon, title, example))
                if st.button(title, key=f"example-{index}", help=example, width="stretch"):
                    start(example)


def start(question: str) -> None:
    """Take a question: lock the input and rerun, so the input is drawn disabled."""
    st.session_state[QUESTION_KEY] = question
    st.session_state[BUSY_KEY] = True
    st.rerun()


def answer_locked(question: str, history: list[Turn]) -> None:
    """Answer while the input is disabled; always release the lock, then redraw."""
    try:
        history.append(ask_live(question, len(history)))
    except llm.LLMUnavailableError:
        logger.exception("Chat question failed: the AI service is unavailable")
        st.session_state[ERROR_KEY] = UNAVAILABLE_MESSAGE
    except Exception:  # shown as a message; the page must stay usable
        logger.exception("Chat question failed")
        st.session_state[ERROR_KEY] = FAILED_MESSAGE
    finally:
        st.session_state[BUSY_KEY] = False
        st.session_state.pop(QUESTION_KEY, None)
    st.rerun()  # redraw with the input enabled again


def main() -> None:
    """Render the page."""
    history: list[Turn] = st.session_state.setdefault(HISTORY_KEY, [])
    busy = st.session_state.get(BUSY_KEY, False)
    if busy and QUESTION_KEY not in st.session_state:  # a lock with nothing to answer
        busy = st.session_state[BUSY_KEY] = False
    # page_class marks the page for CSS: the chat is a centred 760px column.
    controls = common.header(
        "Chat", "Answers from the sales and demand data.", page_class="chat-page"
    )
    if history and controls.button("Clear chat", key="clear-chat", disabled=busy):
        history.clear()
        st.rerun()
    submitted = st.chat_input(PLACEHOLDER, disabled=busy)
    if submitted and not busy:
        start(submitted)
    question = st.session_state.get(QUESTION_KEY) if busy else None
    if not history and not question:
        empty_state()
    for index, turn in enumerate(history):
        show_turn(turn, index)
    if error := st.session_state.pop(ERROR_KEY, None):
        st.error(error)
    if question:
        answer_locked(question, history)


main()
