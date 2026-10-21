"""Tests for the eval set and its grader, with fixed fake answers (no live model calls).

The live eval (scripts/run_eval.py) is not part of pytest; it spends OpenRouter credit.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import fill_gold, run_eval
from src.agent import eval_grader, llm

QUESTIONS = run_eval.load_questions()
BY_ID = {q["id"]: q for q in QUESTIONS}


# ---------------------------------------------------------------- the question set


def test_twenty_questions_with_unique_ids_and_gold() -> None:
    assert len(QUESTIONS) == 20
    assert len(BY_ID) == 20
    for question in QUESTIONS:
        for item in question.get("alternatives") or [question]:
            gold = item["gold"]
            assert gold.get("numbers") or gold.get("names") or gold.get("chart"), question["id"]


def test_coverage_of_required_topics() -> None:
    topics = " | ".join(q["topic"] for q in QUESTIONS)
    for needed in [
        "day",
        "week",
        "month",
        "comparison",
        "category",
        "channel",
        "region",
        "customer-type",
        "top-N products",
        "top-N customers",
        "trend",
        "returns",
        "cancellations",
        "gross margin",
        "new customers",
        "Apparel decline",
        "Classic Denim Jacket",
        "chart",
        "ambiguous",
    ]:
        assert needed in topics, needed
    assert any(q.get("require_assumption") for q in QUESTIONS)


def test_gold_values_match_the_sql(built: dict) -> None:
    """Stored gold values equal a fresh run of every gold_sql on the seed-42 data."""
    assert fill_gold.main(["--check", "--db", str(built["db_path"])]) == 0


def test_known_gold_values() -> None:
    q02 = {n["label"]: n["value"] for n in BY_ID["q02"]["gold"]["numbers"]}
    assert q02 == {"net_revenue": 114286.82, "orders": 661, "aov": 172.9}
    q17 = {n["label"]: n["value"] for n in BY_ID["q17"]["gold"]["numbers"]}
    assert q17["change_pct"] == -28.5


# ---------------------------------------------------------------- number matching


@pytest.mark.parametrize(
    ("gold", "decimals", "answer", "expected"),
    [
        (114286.82, 2, "Net revenue was 114,286.82.", True),
        (114286.82, 2, "about 114,287", True),  # money >= 1,000 may drop decimals
        (114286.82, 2, "114286.82", True),  # no thousands separator
        (114286.82, 2, "114,286.83", True),  # one unit off at display precision
        (114286.82, 2, "114,286.85", False),
        (114286.82, 2, "114k", False),
        (661, 0, "661 orders", True),
        (661, 0, "662 orders", False),  # counts must be exact
        (-28.5, 1, "fell 28.5%", True),  # sign ignored
        (8.6, 1, "up 8.7% on last year", True),  # the reports' double rounding
        (43.2, 1, "43%", False),  # rates keep their display decimal
        (500.1, 1, "500.1 units", True),
        (500.1, 1, "500 units", False),
    ],
)
def test_number_present(gold: float, decimals: int, answer: str, expected: bool) -> None:
    assert eval_grader.number_present(gold, decimals, answer) is expected


def test_name_matching_ignores_case_punctuation_and_ampersand() -> None:
    assert eval_grader.name_present("Home & Kitchen", "home and kitchen led")
    assert eval_grader.name_present("Stokes, Robinson and Gough", "STOKES ROBINSON AND GOUGH")
    assert not eval_grader.name_present("web", "website")  # whole words only


# ---------------------------------------------------------------- verdicts


def grade(qid: str, answer: str, tools: list[str] | None = None, error: str | None = None):
    """Grade a fake answer for one question."""
    return eval_grader.grade(answer, tools or [], BY_ID[qid], error=error)


def test_correct_answer() -> None:
    result = grade("q02", "Week 39: net revenue 114,286.82 from 661 orders, AOV 172.90.")
    assert result.correct is True
    assert all(result.checks.values())


def test_wrong_number() -> None:
    result = grade("q02", "Week 39: net revenue 120,000.00 from 700 orders, AOV 171.43.")
    assert result.correct is False


def test_partly_right_is_left_for_review() -> None:
    result = grade("q02", "Net revenue 114,286.82 from 661 orders; AOV 171.43.")
    assert result.correct is None
    assert "aov" in result.reasons[0]


def test_missing_name_in_list_answer() -> None:
    gold = BY_ID["q11"]["gold"]
    names = gold["names"]
    top = gold["numbers"][0]["value"]
    complete = f"Top 3: {names[0]} ({top:,.2f}), {names[1]} and {names[2]}."
    assert grade("q11", complete).correct is True
    missing_two = f"The top customer was {names[0]} with {top:,.2f}."
    assert grade("q11", missing_two).correct is None  # 2 of 4 checks pass


def test_chart_needs_make_chart() -> None:
    assert grade("q19", "Here is the chart.", ["make_chart"]).correct is True
    assert grade("q19", "Here is the chart.", ["run_sql"]).correct is False


def test_ambiguous_question_needs_numbers_and_assumption() -> None:
    august = {n["label"]: n["value"] for n in BY_ID["q20"]["alternatives"][0]["gold"]["numbers"]}
    revenue, orders = f"{august['net_revenue']:,.2f}", f"{august['orders']:,}"
    stated = f"Assuming you mean August 2026: net revenue {revenue} from {orders} orders."
    result = grade("q20", stated)
    assert result.correct is True and result.alternative == "August 2026"
    silent = f"August 2026: net revenue {revenue} from {orders} orders."
    assert grade("q20", silent).correct is None  # right numbers, no stated assumption


def test_agent_error_is_wrong() -> None:
    assert grade("q01", "", error="Both models failed").correct is False


# ---------------------------------------------------------------- the runner, offline


class AnswerFromGold:
    """A fake model: one get_kpis call, then an answer quoting the gold numbers."""

    def __init__(self, question: dict) -> None:
        self.question = question
        self.turn = 0

    def __call__(self, messages: list[dict], schemas: list[dict], **kwargs: Any) -> llm.LLMResult:
        self.turn += 1
        if self.turn == 1:
            call = llm.ToolCall("c1", "get_kpis", '{"period_type": "week", "as_of": "2026-09-27"}')
            return llm.LLMResult(text=None, tool_calls=[call], model="fake/m", cost_usd=0.001)
        numbers = self.question["gold"]["numbers"]
        text = "; ".join(f"{n['label']} {n['value']:,.{n['decimals']}f}" for n in numbers)
        return llm.LLMResult(text=f"Week 39: {text}.", model="fake/m", cost_usd=0.002)


def test_run_eval_offline_writes_results(built: dict, tmp_path: Path) -> None:
    question = BY_ID["q02"]
    settings = llm.LLMSettings("test-key", "https://openrouter.ai/api/v1", "fake/m", "fb/m", 60.0)
    run = run_eval.run_eval(
        [question],
        "fake/m",
        fallback=False,
        out_dir=tmp_path,
        db_path=built["db_path"],
        audit_path=tmp_path / "audit.duckdb",
        chat=AnswerFromGold(question),
        settings=settings,
    )
    assert run.correct == 1
    saved = json.loads((tmp_path / "q02.json").read_text())
    assert saved["correct"] is True and saved["tools_used"] == ["get_kpis"]
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["correct"] == 1 and summary["total_cost_usd"] == pytest.approx(0.003)


def test_run_eval_stops_at_spend_limit(built: dict, tmp_path: Path) -> None:
    questions = [BY_ID["q02"], BY_ID["q02"]]
    settings = llm.LLMSettings("test-key", "https://openrouter.ai/api/v1", "fake/m", "fb/m", 60.0)
    run = run_eval.run_eval(
        questions,
        "fake/m",
        max_spend=0.001,
        out_dir=tmp_path,
        db_path=built["db_path"],
        audit_path=None,
        chat=AnswerFromGold(BY_ID["q02"]),
        settings=settings,
    )
    assert len(run.results) == 1
    assert "spend passed" in run.stopped


def test_percentile() -> None:
    values = [float(v) for v in range(1, 21)]
    assert run_eval.percentile(values, 0.9) == 18.0
