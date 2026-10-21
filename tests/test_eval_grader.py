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


def test_25_questions_with_unique_ids_and_gold() -> None:
    assert len(QUESTIONS) == 25  # 20 from Week 4, 5 demand-planning questions (Week 5)
    assert len(BY_ID) == 25
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
        "demand planning",
    ]:
        assert needed in topics, needed
    assert any(q.get("require_assumption") for q in QUESTIONS)


@pytest.mark.slow
def test_gold_values_match_the_sql(real_demand_runs: dict) -> None:
    """Stored gold values equal a fresh run of every gold SQL on the seed-42 data.

    Demand questions read the 2026-09-30 snapshot with real forecasts (q24 depends on
    them), stored next to the slow tests' copy of the database.
    """
    assert fill_gold.main(["--check", "--db", str(real_demand_runs["db_path"])]) == 0


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
        (8.6, 1, "up 8.7% on last year", True),  # one unit off: a model rounding twice
        (43.2, 1, "43%", False),  # rates keep their display decimal
        (500.1, 1, "500.1 units", True),
        (500.1, 1, "500 units", False),
        (-28.5, 1, "fell 28.48%", True),  # more precise than displayed, rounds to 28.5
        (28.5, 1, "28.52%", True),
        (37.7, 1, "a 0.3768 share", False),  # a fraction is not the percentage
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


def q20_numbers() -> tuple[str, str]:
    """September 2026 net revenue and orders from q20's gold, formatted for an answer."""
    gold = {n["label"]: n["value"] for n in BY_ID["q20"]["gold"]["numbers"]}
    return f"{gold['net_revenue']:,.2f}", f"{gold['orders']:,}"


def test_last_month_means_september_and_must_be_stated() -> None:
    # Eval-style: with DATA_END_DATE 2026-09-30, "last month" is September 2026.
    revenue, orders = q20_numbers()
    stated = f"Taking last month as September 2026: net revenue {revenue} from {orders} orders."
    assert grade("q20", stated).correct is True
    silent = f"September 2026: net revenue {revenue} from {orders} orders."
    assert grade("q20", silent).correct is None  # right numbers, period not explained


def test_last_month_as_august_is_not_correct() -> None:
    # Eval-style: the old calendar reading (August 2026) no longer passes.
    august = "Taking last month as August 2026: net revenue 462,431.56 from 2,747 orders."
    assert grade("q20", august).correct is False
    assert "alternatives" not in BY_ID["q20"]


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
    assert saved["answer_guard"] == "ok"
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["correct"] == 1 and summary["total_cost_usd"] == pytest.approx(0.003)
    assert summary["answer_guard_retried"] == [] and summary["answer_guard_withheld"] == []


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
    assert "cap" in run.stopped


def test_run_eval_stops_before_crossing_the_cap(built: dict, tmp_path: Path) -> None:
    """Each fake question costs $0.003; with a $0.005 cap only one may run."""
    settings = llm.LLMSettings("test-key", "https://openrouter.ai/api/v1", "fake/m", "fb/m", 60.0)
    run = run_eval.run_eval(
        [BY_ID["q02"], BY_ID["q02"], BY_ID["q02"]],
        "fake/m",
        max_spend=0.005,
        out_dir=tmp_path,
        db_path=built["db_path"],
        audit_path=None,
        chat=AnswerFromGold(BY_ID["q02"]),
        settings=settings,
    )
    assert len(run.results) == 1
    assert run.spend < 0.005


def test_reserve_stops_before_the_first_question(built: dict, tmp_path: Path) -> None:
    run = run_eval.run_eval(
        [BY_ID["q02"]],
        "fake/m",
        max_spend=0.50,
        spent_before=0.45,
        reserve=0.06,
        out_dir=tmp_path,
        db_path=built["db_path"],
        audit_path=None,
        chat=AnswerFromGold(BY_ID["q02"]),
    )
    assert run.results == [] and "cap" in run.stopped


def test_percentile() -> None:
    values = [float(v) for v in range(1, 21)]
    assert run_eval.percentile(values, 0.9) == 18.0


class RateLimitedThenAnswer(AnswerFromGold):
    """Fails with a 429 on the first question attempt, then behaves like AnswerFromGold."""

    def __init__(self, question: dict) -> None:
        super().__init__(question)
        self.limited = False

    def __call__(self, messages: list[dict], schemas: list[dict], **kwargs: Any) -> llm.LLMResult:
        if not self.limited:
            self.limited = True
            raise llm.LLMUnavailableError("main/m failed: RateLimitError 429")
        return super().__call__(messages, schemas, **kwargs)


def test_rate_limited_question_is_asked_again(built: dict, tmp_path: Path) -> None:
    settings = llm.LLMSettings("test-key", "https://openrouter.ai/api/v1", "fake/m", "fb/m", 60.0)
    waits: list[float] = []
    run = run_eval.run_eval(
        [BY_ID["q02"]],
        "fake/m",
        out_dir=tmp_path,
        db_path=built["db_path"],
        audit_path=None,
        chat=RateLimitedThenAnswer(BY_ID["q02"]),
        settings=settings,
        rate_limit_retries=2,
        rate_limit_wait_seconds=20,
        sleep=waits.append,
    )
    assert run.correct == 1
    assert run.results[0].rate_limited == 1
    assert waits == [20]
    assert run.summary()["rate_limited_429"] == 1


# ---------------------------------------------------------------- unsupported numbers

EVIDENCE = [
    "Which product category had the lowest gross margin in September 2026?",
    '{"query": "SELECT ..."}',
    (
        '{"columns": ["category", "gross_margin_pct"], "rows": [["Electronics", 28.52], '
        '["Home & Kitchen", 43.72], ["Beauty", 60.91]], "row_count": 3}'
    ),
]


def test_supported_answer_passes() -> None:
    answer = (
        "Electronics had the lowest gross margin in September 2026 (1 to 30 September), "
        "at 28.5%. Home & Kitchen was next at 43.7%.\n\n```sql\nSELECT round(100 * x, 1)\n```"
    )
    assert eval_grader.unsupported_numbers(answer, EVIDENCE + ["2026-09-30"]) == []


def test_self_calculated_average_is_flagged() -> None:
    answer = "Electronics was lowest at 28.52%; the average of all categories was roughly 48.2%."
    assert eval_grader.unsupported_numbers(answer, EVIDENCE) == ["48.2"]


def test_fraction_turned_into_percentage_is_flagged() -> None:
    evidence = ['{"rows": [[0.3768]]}']
    assert eval_grader.unsupported_numbers("Wholesale was 37.7% of revenue.", evidence) == ["37.7"]


def test_unsupported_number_makes_a_correct_answer_reviewable() -> None:
    gold_value = BY_ID["q15"]["gold"]["numbers"][0]["value"]
    answer = f"Electronics had the lowest margin, {gold_value}%; the average was 48.2%."
    evidence = [BY_ID["q15"]["question"], f'{{"rows": [["Electronics", {gold_value}]]}}']
    result = eval_grader.grade(answer, ["run_sql"], BY_ID["q15"], evidence=evidence)
    assert result.correct is None
    assert result.unsupported == ["48.2"]
    clean = f"Electronics had the lowest gross margin in September 2026, at {gold_value}%."
    assert eval_grader.grade(clean, ["run_sql"], BY_ID["q15"], evidence=evidence).correct is True


def test_real_spend_probe_stops_the_run(built: dict, tmp_path: Path) -> None:
    """The key balance counts even when the per-response cost reads as zero."""
    run = run_eval.run_eval(
        [BY_ID["q02"]],
        "fake/m",
        max_spend=0.50,
        reserve=0.01,
        spent_probe=lambda: 0.495,
        out_dir=tmp_path,
        db_path=built["db_path"],
        audit_path=None,
        chat=AnswerFromGold(BY_ID["q02"]),
    )
    assert run.results == [] and "spent $0.4950" in run.stopped


def test_key_balance_measures_spend_and_survives_a_failed_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readings: list[float | Exception] = [10.0, 9.99, llm.LLMUnavailableError("down"), 9.97]

    def fake_remaining(settings: llm.LLMSettings) -> float:
        reading = readings.pop(0)
        if isinstance(reading, Exception):
            raise reading
        return reading

    monkeypatch.setattr(llm, "key_limit_remaining", fake_remaining)
    settings = llm.LLMSettings("test-key", "https://openrouter.ai/api/v1", "fake/m", "fb/m", 60.0)
    balance = run_eval.KeyBalance(settings)
    assert balance.spent() == pytest.approx(0.01)
    assert balance.spent() == pytest.approx(0.01)  # failed read keeps the last figure
    assert balance.spent() == pytest.approx(0.03)


@pytest.mark.parametrize(
    "period",
    ["1–31 May 2026", "1-31 May", "May 1-31, 2026", "July 1-31", "1 to 30 September"],
)
def test_date_ranges_are_not_unsupported_numbers(period: str) -> None:
    # q12-style answer (Week 5): the grader blanks dates with the answer guard's
    # patterns, so the day numbers in a month range are not flagged.
    evidence = ['{"rows": [["2026-05", 401234.56]]}']
    answer = f"Net revenue for {period} was 401,234.56."
    assert eval_grader.unsupported_numbers(answer, evidence) == []


def test_date_range_does_not_hide_a_made_up_number() -> None:
    evidence = ['{"rows": [["2026-05", 401234.56]]}']
    answer = "Net revenue for 1-31 May was 401,234.56, about 12,943 a day."
    assert eval_grader.unsupported_numbers(answer, evidence) == ["12,943"]


def test_demand_questions_use_the_planning_snapshot_only() -> None:
    demand = [q for q in QUESTIONS if q["topic"].startswith("demand planning")]
    assert len(demand) == 5
    for question in demand:
        assert "gold_planning_sql" in question and "gold_sql" not in question
        assert "DATE '2026-09-30'" in question["gold_planning_sql"]
