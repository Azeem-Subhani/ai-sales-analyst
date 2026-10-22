"""Chat page: the same agent as python -m src.agent.chat, one question at a time.

Each question is answered on its own with agent.ask (single-turn, as in the eval);
earlier questions and answers are only shown as history. The agent's tools are
read-only, and this page has no approval actions.
"""

import json

import streamlit as st

from src.agent import agent, chat
from src.reports import data_clock
from src.ui import common

HISTORY_KEY = "chat_history"


def details(answer: agent.AgentAnswer) -> None:
    """Tool calls and results, then model, tokens, cost and latency."""
    with st.expander(f"Details: {len(answer.tool_calls)} tool call(s)"):
        st.caption(chat.footer(answer))
        st.caption(
            f"Tokens: {answer.input_tokens:,} in, {answer.output_tokens:,} out, "
            f"{answer.total_tokens:,} total"
        )
        if answer.error:
            st.error(answer.error)
        for number, call in enumerate(answer.tool_calls, start=1):
            status = "ok" if call.success else f"failed: {call.error or 'error'}"
            st.markdown(f"**{number}. {call.name}** ({status})")
            try:
                st.json(json.loads(call.arguments or "{}"), expanded=False)
            except json.JSONDecodeError:
                st.code(call.arguments)
            st.json(call.result, expanded=False)


def show(answer: agent.AgentAnswer) -> None:
    """One question and its answer, as the CLI shows them (withheld answers included)."""
    with st.chat_message("user"):
        st.markdown(answer.question)
    with st.chat_message("assistant"):
        if answer.answer_guard == "withheld":
            st.warning("The written answer was withheld by the number guard; tool results below.")
        st.markdown(answer.answer)
        for path in answer.charts:
            st.caption(f"Chart saved: {path}")
        details(answer)


def main() -> None:
    """Render the page."""
    st.title("Chat")
    st.caption(
        "Ask about sales or demand. Each question is answered on its own, measured from "
        "DATA_END_DATE (not the sidebar date); numbers come from the tools, never from the "
        "model. Each answer spends a little OpenRouter credit."
    )
    history: list[agent.AgentAnswer] = st.session_state.setdefault(HISTORY_KEY, [])
    if st.button("Clear chat", disabled=not history):
        history.clear()
        st.rerun()
    for answer in history:
        show(answer)
    question = st.chat_input("Ask a question")
    if question:
        config = agent.AgentConfig(
            db_path=common.path(common.SALES_DB_KEY),
            data_end=data_clock.data_end_from_env(),
        )
        with st.spinner("Thinking..."):
            answer = agent.ask(question, config)
        history.append(answer)
        show(answer)


main()
