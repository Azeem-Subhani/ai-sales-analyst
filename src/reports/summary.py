"""The LLM-written summary at the top of each report (plan section 8, Week 4).

The model never sees the database and never calculates. It gets the report's numbers
already formatted exactly as the email shows them, e.g.

    Net revenue: 114,286.82 (+4.2% vs previous week; +9.8% vs same week last year)

and is asked for 3 to 5 sentences. A number guard then pulls every number out of the
summary and checks it against the numbers that were sent (allowing commas, % signs and
rounding to fewer decimals). Whole numbers 1 to 10 are allowed as counts or ranks; the
period's dates and week number are part of the input, so they are allowed too. If any
number fails, or the model is cut off at the token cap (finish_reason "length"), the
model gets one retry; if that fails too, the summary is dropped. A missing summary
never stops a report.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from src.agent import llm
from src.reports import builder

logger = logging.getLogger(__name__)

# summary_status values written to report_runs
STATUS_OK = "ok"
STATUS_GUARD_FAILED = "guard_failed"
STATUS_LLM_UNAVAILABLE = "llm_unavailable"
STATUS_SKIPPED = "skipped"
STATUS_TRUNCATED = "truncated"  # cut off at MAX_SUMMARY_TOKENS on both attempts

MAX_ATTEMPTS = 2  # the first try plus one retry after a guard failure or a cut-off
# Output token cap per summary call. Reasoning models count their hidden thinking
# against this cap, so it must leave room for that as well as the 3 to 5 sentences
# (about 150 tokens). 400 cut off a monthly summary mid-sentence (Week 4 wrap-up).
MAX_SUMMARY_TOKENS = 2000
SMALL_COUNTS = {Decimal(n) for n in range(1, 11)}  # "three regions", "top 5", "2nd"
TOP_ROWS = 3  # products and customers listed in the input

# 114,286.82 | 661 | 4.2 ; not digits glued to letters or codes (B2B, AP-1150)
NUMBER = re.compile(r"(?<![\w.])(?<![A-Za-z]-)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")

SYSTEM_PROMPT = """You write the opening summary of a sales report for business readers.

Rules:
- Use only the numbers in the data, copied exactly as they are written there.
- Do not do any arithmetic: no sums, differences, averages, ratios or new percentages.
- Do not add any number that is not in the data.
- Lead with the most important change.
- Write 3 to 5 sentences of plain business English.
- No greeting, no heading, no bullet points, no sign-off."""

RETRY_PROMPT = (
    "These numbers in your summary are not in the data: {numbers}. Rewrite the summary "
    "using only numbers that appear in the data, copied exactly. Follow all the rules."
)
TRUNCATED_PROMPT = (
    "Your summary was cut off before it finished. Write it again, complete, in at most "
    "5 short sentences. Follow all the rules."
)


@dataclass
class SummaryOutcome:
    """The summary text (None when unavailable) and how it went."""

    text: str | None
    status: str
    attempts: int = 0
    failed_numbers: list[str] = field(default_factory=list)
    model: str | None = None
    cost_usd: float | None = None


# ---------------------------------------------------------------- inputs


def summary_inputs(numbers: dict) -> list[str]:
    """The report's numbers as pre-formatted lines, exactly as the email shows them."""
    period = numbers["period"]
    comparisons = numbers["comparisons"]
    lines = [
        f"Report: {numbers['title']}",
        f"Period: {period['label']} ({period['start']} to {period['end']})",
        "Compared with: "
        + "; ".join(
            f"{c['name']} ({c['label']}, {c['start']} to {c['end']})" for c in comparisons.values()
        ),
    ]
    for section in numbers["sections"].values():
        if section["type"] == "kpis":
            lines += kpi_lines(section, comparisons)
        elif section["type"] == "breakdown":
            lines += breakdown_lines(section, comparisons)
        elif section["type"] == "top":
            lines += top_lines(section, comparisons)
        elif section["type"] == "groups":
            from src.reports import demand_sections  # imports builder, like this module

            lines += demand_sections.summary_lines(section)
    return lines


def kpi_lines(section: dict, comparisons: dict) -> list[str]:
    """'Net revenue: 114,286.82 (+4.2% vs previous week; ...)' for each KPI row."""
    lines = ["", f"{section['title']}:"]
    for row in section["rows"]:
        value = builder.FORMATTERS[row["kind"]](row["current"])
        unit = builder.change_unit(row["kind"])
        changes = "; ".join(
            f"{builder.fmt_change(row['comparisons'][name]['change'], unit)} "
            f"vs {comparisons[name]['name']}"
            for name in comparisons
        )
        lines.append(f"- {row['label']}: {value} ({changes})")
    return lines


def breakdown_lines(section: dict, comparisons: dict) -> list[str]:
    """Net revenue per category/region/channel/customer type with its change."""
    label = comparisons[section["compared_to"]]["name"]
    lines = ["", f"{section['title']} (change vs {label}):"]
    for row in section["rows"]:
        line = (
            f"- {row['value']}: net revenue {builder.fmt_money(row['net_revenue'])} "
            f"({builder.fmt_change(row['net_revenue_change'], 'pct')}), "
            f"share {builder.fmt_pct(row['share'])}"
        )
        if section["show_margin"]:
            line += (
                f", gross margin {builder.fmt_pct(row['gross_margin_pct'])} "
                f"({builder.fmt_change(row['gross_margin_change'], 'pp')})"
            )
        lines.append(line)
    return lines


def top_lines(section: dict, comparisons: dict) -> list[str]:
    """The first few rows of a top-products or top-customers table."""
    label = comparisons[section["compared_to"]]["name"]
    lines = ["", f"Top {section['entity']} by net revenue (change vs {label}):"]
    for row in section["rows"][:TOP_ROWS]:
        lines.append(
            f"- Rank {row['rank']}: {row['name']} ({row['detail']}), net revenue "
            f"{builder.fmt_money(row['net_revenue'])} "
            f"({builder.fmt_change(row['net_revenue_change'], 'pct')})"
        )
    return lines


# ---------------------------------------------------------------- number guard


def extract_numbers(text: str) -> list[str]:
    """Every number written in text, as written (signs and % are not part of the match)."""
    return NUMBER.findall(text)


def to_decimal(token: str) -> Decimal:
    """'114,286.82' -> Decimal('114286.82')."""
    return Decimal(token.replace(",", ""))


def allowed_numbers(input_lines: list[str]) -> set[Decimal]:
    """Numbers the summary may use: everything in the input, plus 1 to 10."""
    return {to_decimal(t) for line in input_lines for t in extract_numbers(line)} | SMALL_COUNTS


def number_is_allowed(token: str, allowed: set[Decimal]) -> bool:
    """True if token equals an allowed number, or one rounded to the decimals written.

    '114,287' and '114,286.8' both match 114,286.82; '43%' matches 43.1. Abbreviations
    such as '114k' do not match, because 114 is not 114,286.82 rounded.
    """
    value = to_decimal(token)
    decimals = len(token.split(".")[1]) if "." in token else 0
    step = Decimal(1).scaleb(-decimals)
    return any(
        value == number or value == number.quantize(step, rounding=ROUND_HALF_UP)
        for number in allowed
    )


def check_numbers(summary: str, allowed: set[Decimal]) -> list[str]:
    """The numbers in summary that are not allowed (empty list: the summary passes)."""
    failed = [t for t in extract_numbers(summary) if not number_is_allowed(t, allowed)]
    return list(dict.fromkeys(failed))  # unique, in order of appearance


# ---------------------------------------------------------------- writing


def write_summary(
    numbers: dict,
    *,
    complete: Callable[..., llm.LLMResult] | None = None,
    model: str | None = None,
) -> SummaryOutcome:
    """Ask the LLM for the summary, guard its numbers, retry once, else drop it.

    Never raises for LLM problems: an unavailable model gives status llm_unavailable.
    complete defaults to llm.complete (tests pass a fake).
    """
    complete = complete or llm.complete
    lines = summary_inputs(numbers)
    allowed = allowed_numbers(lines)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Data:\n" + "\n".join(lines)},
    ]
    outcome = SummaryOutcome(text=None, status=STATUS_GUARD_FAILED, cost_usd=0.0)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        outcome.attempts = attempt
        try:
            result = complete(messages, model=model, max_tokens=MAX_SUMMARY_TOKENS)
        except (llm.LLMUnavailableError, llm.LLMAuthError, llm.LLMConfigError) as error:
            logger.warning("Summary for %s unavailable: %s", numbers["report"], error)
            outcome.status = STATUS_LLM_UNAVAILABLE
            outcome.text = None
            return outcome
        outcome.model = result.model
        if result.cost_usd is not None:
            outcome.cost_usd = (outcome.cost_usd or 0.0) + result.cost_usd
        text = " ".join((result.text or "").split())  # one paragraph, no stray newlines
        if result.truncated:
            # A cut-off summary must never be sent, even if its numbers pass.
            logger.warning(
                "Summary for %s attempt %d was cut off at %d output tokens (%d reasoning)",
                numbers["report"],
                attempt,
                result.output_tokens,
                result.reasoning_tokens,
            )
            outcome.status, outcome.failed_numbers = STATUS_TRUNCATED, []
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": TRUNCATED_PROMPT},
            ]
            continue
        failed = check_numbers(text, allowed)
        if not failed:
            outcome.text, outcome.status, outcome.failed_numbers = text, STATUS_OK, []
            return outcome
        outcome.status, outcome.failed_numbers = STATUS_GUARD_FAILED, failed
        logger.warning(
            "Summary for %s attempt %d used numbers not in the data: %s",
            numbers["report"],
            attempt,
            ", ".join(failed),
        )
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": RETRY_PROMPT.format(numbers=", ".join(failed))},
        ]
    outcome.text = None
    return outcome  # guard_failed or truncated (the last attempt's problem): dropped


def skipped() -> SummaryOutcome:
    """The outcome for --no-summary."""
    return SummaryOutcome(text=None, status=STATUS_SKIPPED)
