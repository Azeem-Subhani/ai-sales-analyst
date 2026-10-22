"""Chat page: the same agent as python -m src.agent.chat, one question at a time.

How a question is shown:
1. The question appears as a bubble straight away.
2. Inside the assistant bubble, small grey lines show each step live (tools, a fallback
   model, a number-check retry) from agent.ask's on_progress events, then collapse to
   one line that opens with <details> (src/ui/chat_steps.py).
3. The answer only arrives when agent.ask returns, after the answer number guard, and
   is then revealed word by word. Unchecked text is never shown. A withheld answer is
   shown as a warning with the tool results instead.

Each question is answered on its own (single-turn, as in the eval); earlier questions
and answers are only shown as history. The agent's tools are read-only, and this page
has no approval actions.
"""

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from html import escape

import streamlit as st

from src.agent import agent
from src.reports import data_clock
from src.ui import chat_steps, common, style

HISTORY_KEY = "chat_history"
PENDING_KEY = "chat_pending"  # an example question clicked on the empty page
STREAM_DELAY_SECONDS = 0.012  # per word of the typewriter effect
EXAMPLES = [  # (chip label, the question it sends)
    (
        "Wholesale customers who stopped ordering",
        "Which wholesale customers have stopped ordering?",
    ),
    ("Stockouts in September", "Which products ran out of stock in September 2026?"),
    ("Most biased forecast", "Which product's forecast is the most biased?"),
    (
        "Top pre-build recommendation",
        "What is the top pre-build recommendation, and how many units?",
    ),
]
ASSISTANT_AVATAR = str(style.LOGO_PATH)  # the logo mark, as in the sidebar
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
    """model · tokens · cost · time, as a small grey line.

    The cost is left out when OpenRouter reports none or $0 (it often does; the real
    spend is measured from the key balance).
    """
    parts = [answer.model or "no model", f"{answer.total_tokens:,} tokens"]
    if answer.fallback_used:
        parts.insert(1, "fallback model")
    if answer.cost_usd:  # None or 0.0: no cost shown
        parts.append(f"${answer.cost_usd:.4f}")
    parts.append(f"{answer.latency_seconds:.1f} s")
    return '<div class="chat-meta">' + escape(" · ".join(parts)) + "</div>"


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
            "These are the tool results it was based on.",
            icon=":material/gpp_maybe:",
        )
        st.markdown(answer.answer)
    elif stream:
        st.write_stream(words(answer.answer))
    else:
        st.markdown(answer.answer)
    for path in answer.charts:
        st.caption(f"Chart saved: {path}")
    st.html(meta_line(answer))


def show_turn(turn: Turn) -> None:
    """An earlier question and answer, as history."""
    st.html(style.user_message(turn.answer.question))
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        st.html(chat_steps.collapsed_html(turn.steps, turn.answer.latency_seconds))
        show_answer(turn.answer, stream=False)


def ask_live(question: str) -> Turn:
    """Show the question, the live steps and the checked answer; return the turn."""
    st.html(style.user_message(question))
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        live = LiveSteps(st.empty())
        live.draw()
        config = agent.AgentConfig(
            db_path=common.path(common.SALES_DB_KEY), data_end=data_clock.data_end_from_env()
        )
        answer = agent.ask(question, config, on_progress=live)  # guarded before it returns
        turn = Turn(answer, live.steps)
        live.placeholder.html(chat_steps.collapsed_html(turn.steps, answer.latency_seconds))
        show_answer(answer, stream=True)
    return turn


def empty_state() -> None:
    """A welcome line and example questions; a click sends one."""
    st.html(
        '<div class="chat-welcome">Ask about sales, customers, stock or forecasts. '
        "Every number in an answer comes from a tool, never from the model.</div>"
    )
    columns = st.columns(2)
    for index, (label, example) in enumerate(EXAMPLES):
        with columns[index % 2]:
            if st.button(
                label,
                key=f"example-{index}",
                help=example,
                icon=":material/chat_bubble:",
                width="stretch",
            ):
                st.session_state[PENDING_KEY] = example
                st.rerun()


def main() -> None:
    """Render the page."""
    history: list[Turn] = st.session_state.setdefault(HISTORY_KEY, [])
    title, clear = st.columns([5, 1], vertical_alignment="bottom")
    title.title(":material/forum: Chat")
    if clear.button("Clear chat", icon=":material/delete_sweep:", disabled=not history):
        history.clear()
        st.rerun()
    question = st.chat_input("Ask about sales or demand…") or st.session_state.pop(
        PENDING_KEY, None
    )
    if not history and not question:
        empty_state()
    for turn in history:
        show_turn(turn)
    if question:
        history.append(ask_live(question))


main()
