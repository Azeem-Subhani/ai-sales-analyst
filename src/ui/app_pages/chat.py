"""Chat page: the same agent as python -m src.agent.chat, one question at a time.

How a question is shown:
1. The question appears as a bubble straight away.
2. A status block shows each step live (model calls, tools, a fallback model, a number
   check retry) from agent.ask's on_progress events, then collapses to one line.
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
from src.ui import common, style

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
THINKING_ICON = "\U0001f9e0"  # brain
NOTE_ICON = "⚠️"  # warning sign
# Tool name -> (icon, what the step says). Text only; the numbers come from the tools.
TOOL_STEPS = {
    "get_kpis": ("\U0001f4c8", "Getting KPIs"),
    "run_sql": ("\U0001f5c4️", "Running SQL"),
    "make_chart": ("\U0001f4ca", "Making a chart"),
    "get_forecast": ("\U0001f52e", "Getting the forecast"),
    "get_customer_alerts": ("\U0001f465", "Checking customer alerts"),
    "get_forecast_accuracy": ("\U0001f3af", "Checking forecast accuracy"),
    "get_prebuild_recommendations": ("\U0001f4e6", "Checking pre-build recommendations"),
}
ASSISTANT_AVATAR = str(style.LOGO_PATH)  # the logo mark, as in the sidebar
NOTE_KINDS = {agent.EVENT_FALLBACK, agent.EVENT_GUARD_RETRY, agent.EVENT_WITHHELD}


@dataclass
class Step:
    """One line of the progress block."""

    icon: str
    label: str
    state: str = "running"  # running | ok | fail | note
    seconds_shown: str = ""
    tool: str | None = None
    arguments: str | None = None
    result: dict | None = None

    def html(self) -> str:
        """The step as one line of HTML."""
        return style.step_line(self.icon, self.label, self.seconds_shown, self.state)


@dataclass
class Turn:
    """One question and its answer, with the steps it took."""

    answer: agent.AgentAnswer
    steps: list[Step] = field(default_factory=list)


class LiveSteps:
    """agent.ask's on_progress callback: draws each step as it starts and finishes."""

    def __init__(self, container: object) -> None:
        self.container = container
        self.steps: list[Step] = []
        self.slots: list = []

    def __call__(self, event: agent.ProgressEvent) -> None:
        if event.kind == agent.EVENT_THINKING:
            self.start(Step(THINKING_ICON, f"{agent.THINKING_LABEL}…"))
        elif event.kind == agent.EVENT_TOOL_START:
            icon, label = TOOL_STEPS.get(event.tool or "", ("\U0001f527", "Using a tool"))
            self.start(Step(icon, f"{label} ({event.tool})", tool=event.tool))
        elif event.kind in (agent.EVENT_THINKING_DONE, agent.EVENT_TOOL_DONE):
            self.finish(event)
        elif event.kind in NOTE_KINDS:
            self.start(Step(NOTE_ICON, event.label, state="note"))

    def start(self, step: Step) -> None:
        """Add a step line."""
        self.steps.append(step)
        slot = self.container.empty()
        self.slots.append(slot)
        slot.html(step.html())

    def finish(self, event: agent.ProgressEvent) -> None:
        """Turn the latest running step of that kind into a tick or a cross."""
        wanted_tool = event.kind == agent.EVENT_TOOL_DONE
        for step, slot in zip(reversed(self.steps), reversed(self.slots), strict=True):
            if step.state == "running" and (step.tool is not None) == wanted_tool:
                step.state = "ok" if event.success else "fail"
                step.seconds_shown = seconds_text(event.seconds)
                step.arguments, step.result = event.arguments, event.result
                slot.html(step.html())
                return


def seconds_text(seconds: float | None) -> str:
    """'1.2 s' for a step's duration."""
    return "" if seconds is None else f"{seconds:.1f} s"


def summary_label(turn: Turn) -> str:
    """The collapsed status line, e.g. 'Used 3 tools · 4.2 s'."""
    count = len(turn.answer.tool_calls)
    tools_text = "No tools used" if count == 0 else f"Used {count} tool{'s' * (count != 1)}"
    return f"{tools_text} · {turn.answer.latency_seconds:.1f} s"


def status_state(answer: agent.AgentAnswer) -> str:
    """complete, or error when the model failed or the answer was withheld."""
    return "error" if answer.error or answer.answer_guard == "withheld" else "complete"


def tool_details(steps: list[Step]) -> None:
    """Arguments and results of every tool call, inside the status block."""
    for step in steps:
        if step.tool and step.state in ("ok", "fail"):
            st.caption(f"{step.icon} {step.tool}: arguments, then result")
            st.json(step.arguments or "{}", expanded=False)
            st.json(step.result or {}, expanded=False)


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
        with st.status(summary_label(turn), state=status_state(turn.answer), expanded=False):
            for step in turn.steps:
                st.html(step.html())
            tool_details(turn.steps)
        show_answer(turn.answer, stream=False)


def ask_live(question: str) -> Turn:
    """Show the question, the live steps and the checked answer; return the turn."""
    st.html(style.user_message(question))
    with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
        status = st.status("Working…", expanded=True)
        live = LiveSteps(status)
        config = agent.AgentConfig(
            db_path=common.path(common.SALES_DB_KEY), data_end=data_clock.data_end_from_env()
        )
        answer = agent.ask(question, config, on_progress=live)  # guarded before it returns
        turn = Turn(answer, live.steps)
        with status:
            tool_details(turn.steps)
        status.update(label=summary_label(turn), state=status_state(answer), expanded=False)
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
