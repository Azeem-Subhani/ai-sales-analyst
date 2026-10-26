"""Unit tests for the chat agent's answer number guard (no model calls)."""

import json

import pytest

from src.agent import answer_guard, tools
from src.agent.tools import ToolOutcome


def outcome(result: dict, name: str = "run_sql") -> ToolOutcome:
    """A successful tool call with this result."""
    return ToolOutcome(name, "{}", result, True)


SALES = outcome(
    {
        "columns": ["category", "net_revenue", "change_pct"],
        "rows": [["Apparel", 21083.05, -25.3], ["Electronics", 31481.36, 8.6495]],
    }
)


@pytest.mark.parametrize(
    "answer",
    [
        "Apparel fell 25.3% to 21,083.05.",  # sign ignored, commas allowed
        "Electronics led with 31,481 (up 8.6%).",  # rounded to fewer decimals
        "The top 3 categories, week 39 (21-27 Sep 2026).",  # count and dates
        "Between 2026-09-21 and 2026-09-27, September 2026, Q3 2026, in 2026.",
        "Sep 21-27, 2026 and 21 to 27 September.",
        "Here are the top 20 categories.",  # 20 is in the question
        "Apparel 21,083.05.\n```sql\nSELECT 1234 LIMIT 99\n```",  # SQL is ignored
    ],
)
def test_guard_accepts(answer: str) -> None:
    assert answer_guard.check_answer(answer, "Show the top 20 categories", [SALES]) == []


def test_guard_accepts_a_markdown_table_with_full_dates() -> None:
    """A table answer is checked like prose: pipes add no numbers, dates are blanked."""
    answer = (
        "These categories changed in the 30 September 2026 report:\n\n"
        "| Category | Net revenue | Change |\n"
        "|---|---|---|\n"
        "| Apparel | 21,083.05 | -25.3% |\n"
        "| Electronics | 31,481.36 | +8.6% |\n\n"
        "Last order dates run from 5 August 2026 to 14 Aug 2026."
    )
    assert answer_guard.check_answer(answer, "Revenue by category", [SALES]) == []


def test_retry_prompt_keeps_the_answer_style() -> None:
    assert "plain, short style" in answer_guard.RETRY_PROMPT
    assert "em dashes" in answer_guard.RETRY_PROMPT


@pytest.mark.parametrize(
    ("answer", "failed"),
    [
        ("Total revenue was 52,564.41.", ["52,564.41"]),  # a sum the model made
        ("Electronics led by about 10,398.", ["10,398"]),  # a gap the model made
        ("Up 8.7%.", ["8.7"]),  # rounded twice
        ("Apparel was 21k.", ["21"]),  # an abbreviation is not a rounding
        ("Orders rose 12 times, 12 times.", ["12"]),  # listed once
    ],
)
def test_guard_rejects(answer: str, failed: list[str]) -> None:
    assert answer_guard.check_answer(answer, "Revenue by category", [SALES]) == failed


def test_failed_tool_results_are_not_shown_when_withheld() -> None:
    failed_call = ToolOutcome("run_sql", "{}", {"error": "bad"}, False, error="bad")
    text = answer_guard.withheld_answer(["99"], [failed_call, SALES])
    assert text.startswith("(The written answer was withheld")
    assert "| Apparel | 21083.05 | -25.3 |" in text
    assert "bad" not in text


def test_withheld_rows_are_capped() -> None:
    rows = [[i] for i in range(answer_guard.MAX_ROWS_SHOWN + 5)]
    text = answer_guard.withheld_answer(["99"], [outcome({"columns": ["n"], "rows": rows})])
    assert f"(first {answer_guard.MAX_ROWS_SHOWN} of {len(rows)} rows)" in text


def test_withheld_with_no_tool_results() -> None:
    assert answer_guard.NO_TOOL_RESULTS in answer_guard.withheld_answer(["99"], [])


def test_other_tools_shown_as_json() -> None:
    forecast = outcome({"product": {"sku": "AP-1"}, "weeks": []}, name="get_forecast")
    text = answer_guard.withheld_answer(["99"], [forecast])
    assert "Forecast:" in text and json.dumps("AP-1") in text
    assert "get_forecast" not in text  # a readable heading, never the raw tool name


def test_withheld_headings_are_readable_for_every_tool() -> None:
    """Every agent tool has a heading without underscores; others are named from words."""
    for name in tools.TOOLS:
        assert "_" not in answer_guard.result_title(name), name
    assert answer_guard.result_title("get_customer_list") == "Customer list"
    rows = outcome({"columns": ["n"], "rows": [[1]]}, name="run_sql")
    text = answer_guard.withheld_answer(["99"], [rows])
    assert "Sales data:" in text and "run_sql" not in text
