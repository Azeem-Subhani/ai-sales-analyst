"""Chat with the sales analyst agent from the terminal.

    python -m src.agent.chat                                   # interactive; empty line quits
    python -m src.agent.chat --question "Net revenue last week?" # one question, then exit
    python -m src.agent.chat --model openai/gpt-6-luna         # try another model

Each question is answered on its own (no memory of earlier questions in the CLI;
the Week 6 Streamlit app keeps history). Every question is logged to agent_log in
data/audit.duckdb. Each answer spends a little of the OpenRouter credit.
"""

import argparse
import logging
import sys

from dotenv import load_dotenv

from src.agent import agent, llm
from src.reports import data_clock

QUIT_WORDS = {"", "exit", "quit", "q"}
PROMPT = "You: "


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Ask the sales analyst agent questions.")
    parser.add_argument("--question", help="ask one question and exit")
    parser.add_argument("--model", help="model ID to use instead of LLM_MODEL (for testing)")
    return parser.parse_args(argv)


def footer(answer: agent.AgentAnswer) -> str:
    """One line with model, fallback, tool calls, number guard, cost and time."""
    cost = "n/a" if answer.cost_usd is None else f"${answer.cost_usd:.4f}"
    parts = [
        f"model {answer.model or 'none'}",
        "fallback used" if answer.fallback_used else "no fallback",
        f"{len(answer.tool_calls)} tool call(s)",
        f"number guard {answer.answer_guard}",
        f"cost {cost}",
        f"{answer.latency_seconds:.1f} s",
    ]
    return "[" + ", ".join(parts) + "]"


def show(answer: agent.AgentAnswer) -> None:
    """Print the answer, any chart paths and the footer."""
    print(f"\nAnalyst: {answer.answer}\n")
    for path in answer.charts:
        print(f"Chart saved: {path}")
    print(footer(answer) + "\n")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; a missing key or bad setting prints one line and returns 1."""
    args = parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = llm.llm_settings()
        config = agent.AgentConfig(data_end=data_clock.data_end_from_env(), model=args.model)
    except (llm.LLMConfigError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.question:
        show(agent.ask(args.question, config, settings=settings))
        return 0

    print("Sales analyst. Ask a question; press Enter on an empty line to quit.")
    while True:
        try:
            question = input(PROMPT).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question.lower() in QUIT_WORDS:
            return 0
        show(agent.ask(question, config, settings=settings))


if __name__ == "__main__":
    sys.exit(main())
