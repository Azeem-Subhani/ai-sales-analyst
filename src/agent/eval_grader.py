"""Grades agent answers against the gold values in tests/eval/questions.yaml.

Checks for one question:
- numbers: each gold number must appear in the answer within display rounding. The
  answer may round to fewer decimals (114,287 for 114,286.82; money of 1,000 or more
  may drop all decimals; other numbers must keep their display decimals), and may be
  off by one unit in its last digit (the reports round twice, e.g. 8.65 -> "8.7").
  Signs are ignored ("fell 28.5%" matches -28.5).
- names: each gold name must appear (case and punctuation ignored, "&" = "and").
- chart: make_chart must have succeeded.
- assumption: for ambiguous questions, the answer must say what it assumed.
- no unsupported numbers: every number in the answer (outside SQL code) must match
  a number in the question, the tool arguments or the tool results, within
  rounding; whole numbers 1 to 10 are allowed as counts. This catches numbers the
  model worked out itself (an average, a gap, a fraction turned into a percentage).

Result: correct (True) when every check passes; wrong (False) when fewer than half
pass, or the agent errored; undecided (None) otherwise, for a human to review.
Questions with alternatives (none in the current set) are graded against each
alternative and the best one counts.
"""

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from src.reports.summary import extract_numbers, to_decimal

MONEY_DROP_DECIMALS_FROM = Decimal(1000)
PASS_THRESHOLD = 0.5
ASSUMPTION_WORDS = re.compile(
    r"\b(assum\w*|taking|taken|interpret\w*|i(?:'ve| have) used|i used|treating|"
    r"means|meaning|read (?:this|that|it) as|refers? to|i(?:'ll| will) use|based on)\b",
    re.IGNORECASE,
)
NON_WORD = re.compile(r"[^a-z0-9]+")
CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`]*`")
SMALL_COUNTS = {Decimal(n) for n in range(1, 11)}


@dataclass
class Grade:
    """The verdict for one answer, with every check spelled out for review."""

    correct: bool | None
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    alternative: str | None = None
    unsupported: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- single checks


def number_present(gold: float, decimals: int, answer: str) -> bool:
    """True if the answer contains the gold number within display rounding.

    Counts (0 decimals) must match exactly. A number written with fewer decimals
    than displayed must be the gold value rounded (within half a unit). A number
    written at display precision may be one unit off, for the reports' double rounding.
    """
    target = abs(Decimal(str(gold)))
    min_decimals = 0 if target >= MONEY_DROP_DECIMALS_FROM else decimals
    for token in extract_numbers(answer):
        value = to_decimal(token)
        token_decimals = len(token.split(".")[1]) if "." in token else 0
        if token_decimals < min_decimals:
            continue
        if token_decimals > decimals:  # more precise than displayed: round it first
            value = value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
            token_decimals = decimals
        unit = Decimal(1).scaleb(-token_decimals)
        if decimals == 0:
            tolerance = Decimal(0)
        elif token_decimals < decimals:
            tolerance = unit / 2
        else:
            tolerance = unit
        if abs(value - target) <= tolerance:
            return True
    return False


def normalise(text: str) -> str:
    """Lower case, '&' as 'and', punctuation as spaces."""
    return " " + NON_WORD.sub(" ", text.lower().replace("&", " and ")).strip() + " "


def name_present(name: str, answer: str) -> bool:
    """True if the name appears in the answer as whole words."""
    return normalise(name) in normalise(answer)


def states_assumption(answer: str) -> bool:
    """True if the answer says how it read an ambiguous question."""
    return bool(ASSUMPTION_WORDS.search(answer))


def evidence_numbers(evidence: list[str]) -> set[Decimal]:
    """Every number written in the question, tool arguments and tool results."""
    return {abs(to_decimal(t)) for text in evidence for t in extract_numbers(text)} | SMALL_COUNTS


def supported(token: str, allowed: set[Decimal]) -> bool:
    """True if a number in the answer matches an evidence number within rounding.

    Whole numbers may be rounded (half a unit); numbers with decimals may also be
    one unit off, for the reports' double rounding.
    """
    value = to_decimal(token)
    decimals = len(token.split(".")[1]) if "." in token else 0
    unit = Decimal(1).scaleb(-decimals)
    tolerance = unit / 2 if decimals == 0 else unit
    return any(abs(value - number) <= tolerance for number in allowed)


def unsupported_numbers(answer: str, evidence: list[str]) -> list[str]:
    """Numbers in the answer (SQL code excluded) that no evidence supports."""
    prose = INLINE_CODE.sub(" ", CODE_BLOCK.sub(" ", answer))
    allowed = evidence_numbers(evidence)
    found = [t for t in extract_numbers(prose) if not supported(t, allowed)]
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------- whole question


def grade_against(
    answer: str,
    tools_used: list[str],
    gold: dict,
    require_assumption: bool,
    unsupported: list[str] | None = None,
) -> Grade:
    """Grade one answer against one gold block (unsupported: None = not checked)."""
    checks: dict[str, bool] = {}
    for number in gold.get("numbers", []):
        key = f"number {number['label']}={number['value']}"
        checks[key] = number_present(number["value"], number["decimals"], answer)
    for name in gold.get("names", []):
        checks[f"name {name}"] = name_present(name, answer)
    if gold.get("chart"):
        checks["chart made"] = "make_chart" in tools_used
    if require_assumption:
        checks["assumption stated"] = states_assumption(answer)
    if unsupported is not None:
        checks["no unsupported numbers"] = not unsupported
    result = verdict(checks)
    result.unsupported = list(unsupported or [])
    if result.unsupported:
        result.reasons.append(f"numbers no tool returned: {', '.join(result.unsupported)}")
    return result


def verdict(checks: dict[str, bool]) -> Grade:
    """True if all checks pass, False if fewer than half do, else undecided."""
    if not checks:
        return Grade(None, checks, ["no checks defined"])
    passed = sum(checks.values())
    failed = [name for name, ok in checks.items() if not ok]
    if passed == len(checks):
        return Grade(True, checks)
    if passed / len(checks) < PASS_THRESHOLD:
        return Grade(False, checks, [f"failed: {', '.join(failed)}"])
    return Grade(None, checks, [f"partly right; failed: {', '.join(failed)}"])


def grade(
    answer: str,
    tools_used: list[str],
    question: dict,
    error: str | None = None,
    evidence: list[str] | None = None,
) -> Grade:
    """Grade an agent answer for one eval question (with gold values filled in).

    evidence: the question, tool arguments and tool results as text; when given,
    numbers in the answer that none of it supports fail the check.
    """
    if error:
        return Grade(False, {}, [f"agent error: {error}"])
    require_assumption = bool(question.get("require_assumption"))
    unsupported = None if evidence is None else unsupported_numbers(answer, evidence)
    alternatives = question.get("alternatives")
    if not alternatives:
        return grade_against(answer, tools_used, question["gold"], require_assumption, unsupported)
    best: Grade | None = None
    for alternative in alternatives:
        result = grade_against(
            answer, tools_used, alternative["gold"], require_assumption, unsupported
        )
        result.alternative = alternative["label"]
        if best is None or rank(result) > rank(best):
            best = result
    assert best is not None
    return best


def rank(result: Grade) -> tuple[int, int]:
    """Order verdicts: correct > undecided > wrong, then by checks passed."""
    order = {True: 2, None: 1, False: 0}[result.correct]
    return order, sum(result.checks.values())


# ---------------------------------------------------------------- gold from SQL


def gold_from_rows(spec: dict, columns: list[str], rows: list[list[Any]]) -> dict:
    """Turn a grade spec and the gold query's rows into gold numbers and names."""
    gold: dict[str, Any] = {"numbers": [], "names": []}
    for number in spec.get("numbers", []):
        for index in selected_rows(number, rows):
            value = rows[index][columns.index(number["column"])]
            label = number["column"] if len(rows) == 1 else f"{number['column']}[{index}]"
            value = int(value) if number["decimals"] == 0 else float(value)
            gold["numbers"].append({"label": label, "value": value, "decimals": number["decimals"]})
    for name in spec.get("names", []):
        if "literal" in name:
            gold["names"].append(name["literal"])
            continue
        for index in selected_rows(name, rows):
            gold["names"].append(str(rows[index][columns.index(name["column"])]))
    if spec.get("chart"):
        gold["chart"] = True
    return {key: value for key, value in gold.items() if value}


def selected_rows(item: dict, rows: list[list[Any]]) -> list[int]:
    """Row indexes a spec item refers to: 'all', a list, or just the first row."""
    chosen = item.get("rows", [0])
    return list(range(len(rows))) if chosen == "all" else list(chosen)
