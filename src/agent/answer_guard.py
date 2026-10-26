"""Number guard for the chat agent's final answer (same rule as report summaries).

Every number in the written answer must come from a tool result for this question.
A number is accepted when it:
- matches a number in a tool result, exactly or rounded to fewer decimals (signs are
  ignored, "-25.3" in the data allows "fell 25.3%");
- matches a number the user wrote in the question ("top 20 products");
- is part of a date ("2026-09-27", "21 Sep 2026", "21-27 Sep", "September 2026",
  "Week 39", "in 2026");
- is a whole number from 1 to 10 (counts and ranks).

In a multi-turn chat (Week 8) a number may also come from an earlier turn's tool
results (the original, untrimmed results) or an earlier question. A number that
appears only in an earlier answer is not accepted: answers are not sources.

SQL in fenced code blocks is ignored, because the agent shows the query it ran.
The agent (src/agent/agent.py) retries once when the guard fails, and otherwise
withholds the written answer and shows the tool results instead.
"""

import json
import re
from decimal import Decimal

from src.agent import history as chat_history
from src.agent.tools import ToolOutcome
from src.reports.summary import SMALL_COUNTS, extract_numbers, number_is_allowed, to_decimal

# answer_guard values written to agent_log
GUARD_OK = "ok"  # passed first time
GUARD_RETRIED_OK = "retried_ok"  # failed, passed on the one retry
GUARD_WITHHELD = "withheld"  # failed twice; tool results shown instead
GUARD_NOT_RUN = "not_run"  # no model-written answer (error, time limit)

RETRY_PROMPT = (
    "These numbers in your answer do not appear in any tool result: {numbers}. "
    "Rewrite the answer using only numbers copied from the tool results (dates and "
    "counts from 1 to 10 are fine). Do not calculate, estimate or round anything. "
    "Keep the same plain, short style and do not use em dashes."
)
WITHHELD_NOTE = (
    "(The written answer was withheld because it contained numbers that no tool "
    "returned: {numbers}. These are the tool results it was based on.)"
)
NO_TOOL_RESULTS = "No tool returned data for this question."

MAX_ROWS_SHOWN = 20  # run_sql rows shown when the answer is withheld
MAX_JSON_CHARS = 1500  # other tool results are shown as JSON, cut at this length
# Headings for tool results in a withheld answer, for a sales manager: never the raw
# tool name. get_kpis has its own text (kpi_text) and needs no heading.
RESULT_TITLES = {
    "run_sql": "Sales data",
    "make_chart": "Chart",
    "get_forecast": "Forecast",
    "get_customer_alerts": "Customer alerts",
    "get_forecast_accuracy": "Forecast accuracy",
    "get_prebuild_recommendations": "Pre-build recommendations",
}
# Leading verbs dropped when a tool without its own title is named from its words.
NAME_VERBS = {"get", "run", "make", "list", "find", "check", "calculate"}

MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|"
    r"Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
)
DAY = r"\d{1,2}(?:st|nd|rd|th)?"
DASH = r"\s*(?:-|–|—|to)\s*"
YEAR = r"20\d{2}"
# Matched dates are blanked out before numbers are checked.
DATE_PATTERNS = [
    rf"\b{YEAR}-\d{{2}}-\d{{2}}\b",  # 2026-09-27
    rf"\b{DAY}(?:{DASH}{DAY})?\s+{MONTH}(?:,?\s+{YEAR})?\b",  # 21 Sep, 21-27 Sep 2026
    rf"\b{MONTH}\s+{DAY}(?:{DASH}{DAY})?(?:,?\s+{YEAR})?\b",  # Sep 21-27, 2026
    rf"\b{MONTH}\s+{YEAR}\b",  # September 2026
    r"\b(?:ISO\s+)?weeks?\s+\d{1,2}(?:" + DASH + r"\d{1,2})?\b",  # Week 39, weeks 38-39
    rf"\bQ[1-4]\s+{YEAR}\b",  # Q3 2026
    rf"\b(?:in|of|during|for|since|year|FY)\s+{YEAR}\b",  # in 2026
]
DATES = re.compile("|".join(DATE_PATTERNS), re.IGNORECASE)
CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)


def without_dates_and_code(text: str) -> str:
    """The answer with fenced code blocks and dates blanked out."""
    return DATES.sub(" ", CODE_BLOCK.sub(" ", text))


def allowed_numbers(
    question: str,
    outcomes: list[ToolOutcome],
    history: list[chat_history.Turn] | None = None,
) -> set[Decimal]:
    """Numbers the answer may use: the tool results, the question, and 1 to 10.

    With history, also every earlier tool result and earlier question (never an
    earlier answer).
    """
    texts = [question] + [outcome.content() for outcome in outcomes]
    texts += chat_history.carried_results(history) + chat_history.earlier_questions(history)
    return {abs(to_decimal(t)) for text in texts for t in extract_numbers(text)} | SMALL_COUNTS


def check_answer(
    answer: str,
    question: str,
    outcomes: list[ToolOutcome],
    history: list[chat_history.Turn] | None = None,
) -> list[str]:
    """Numbers in the answer that no tool returned (empty list: the answer passes)."""
    allowed = allowed_numbers(question, outcomes, history)
    failed = [
        t
        for t in extract_numbers(without_dates_and_code(answer))
        if not number_is_allowed(t, allowed)
    ]
    return list(dict.fromkeys(failed))  # unique, in order of appearance


# ---------------------------------------------------------------- withheld answer


def withheld_answer(failed: list[str], outcomes: list[ToolOutcome]) -> str:
    """The note plus every successful tool result, as plain text."""
    shown = [tool_result_text(o) for o in outcomes if o.success]
    body = "\n\n".join(shown) if shown else NO_TOOL_RESULTS
    return f"{WITHHELD_NOTE.format(numbers=', '.join(failed))}\n\n{body}"


def result_title(tool: str) -> str:
    """A readable heading for a tool's result ("get_customer_list" -> "Customer list")."""
    if tool in RESULT_TITLES:
        return RESULT_TITLES[tool]
    words = tool.split("_")
    if len(words) > 1 and words[0] in NAME_VERBS:
        words = words[1:]
    return " ".join(words).capitalize()


def tool_result_text(outcome: ToolOutcome) -> str:
    """One tool result in a form a sales manager can read, under a readable heading."""
    result = outcome.result
    if outcome.name == "get_kpis":
        return kpi_text(result)
    title = result_title(outcome.name)
    if "columns" in result and "rows" in result:
        return f"{title}:\n{rows_table(result['columns'], result['rows'])}"
    text = json.dumps(result, default=str, indent=1)
    if len(text) > MAX_JSON_CHARS:
        text = text[:MAX_JSON_CHARS] + "\n..."
    return f"{title}:\n{text}"


def kpi_text(result: dict) -> str:
    """get_kpis as 'Label: shown (change vs comparison)' lines."""
    period = result["period"]
    names = {key: c["name"] for key, c in result["comparisons"].items()}
    lines = [f"KPIs for {period['label']} ({period['start']} to {period['end']}):"]
    for row in result["kpis"]:
        changes = [f"{row[key]['change_shown']} vs {name}" for key, name in names.items()]
        suffix = f" ({'; '.join(changes)})" if changes else ""
        lines.append(f"- {row['label']}: {row['shown']}{suffix}")
    return "\n".join(lines)


def rows_table(columns: list[str], rows: list[list]) -> str:
    """A markdown table of the first MAX_ROWS_SHOWN rows."""
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    lines += ["| " + " | ".join(str(v) for v in row) + " |" for row in rows[:MAX_ROWS_SHOWN]]
    if len(rows) > MAX_ROWS_SHOWN:
        lines.append(f"(first {MAX_ROWS_SHOWN} of {len(rows)} rows)")
    return "\n".join(lines)
