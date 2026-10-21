"""Run the 20 evaluation questions live against one model and grade the answers.

This calls OpenRouter and spends credit (roughly a few cents per run with low-cost
models). It is not part of pytest; pytest only tests the grader.

    python scripts/run_eval.py                          # model from LLM_MODEL, fallback on
    python scripts/run_eval.py --model openai/gpt-6-luna --no-fallback
    python scripts/run_eval.py --ids q01,q17            # a subset
    python scripts/run_eval.py --max-spend 1.00         # stop once the run passes $1

Per-question results go to outputs/eval/<timestamp>_<model>/q01.json ...; summary.json
holds the score. Answers the grader cannot decide are listed for a human to review.
"""

import argparse
import json
import math
import statistics
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # so "python scripts/run_eval.py" finds src/

from src.agent import agent, eval_grader, llm
from src.analytics import kpis
from src.reports.audit import AUDIT_DB_PATH

QUESTIONS_PATH = PROJECT_ROOT / "tests" / "eval" / "questions.yaml"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "eval"
EVAL_DATA_END = date(2026, 9, 30)  # the questions are written for this "today"
P90 = 0.9
TARGET_CORRECT = 19


class SpendLimitReached(RuntimeError):
    """The run's spend passed --max-spend; remaining questions were not asked."""


@dataclass
class QuestionResult:
    """One graded answer, as saved to outputs/eval/.../qNN.json."""

    id: str
    topic: str
    question: str
    answer: str
    correct: bool | None
    checks: dict[str, bool]
    reasons: list[str]
    alternative: str | None
    tools_used: list[str]
    tool_calls: list[dict]
    model: str | None
    fallback_used: bool
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    latency_seconds: float
    stopped_early: str | None
    error: str | None


@dataclass
class EvalRun:
    """All results of one run plus its totals."""

    model: str
    fallback: bool
    started_at: str
    results: list[QuestionResult] = field(default_factory=list)
    stopped: str | None = None  # why the run ended early, if it did

    @property
    def correct(self) -> int:
        """Questions graded correct."""
        return sum(r.correct is True for r in self.results)

    @property
    def undecided(self) -> list[QuestionResult]:
        """Questions the grader left for human review."""
        return [r for r in self.results if r.correct is None]

    @property
    def spend(self) -> float:
        """Total cost in USD of every model call in the run."""
        return sum(r.cost_usd or 0.0 for r in self.results)

    def summary(self) -> dict[str, Any]:
        """Score, latency and cost totals for summary.json and the model check."""
        latencies = [r.latency_seconds for r in self.results]
        costs = [r.cost_usd for r in self.results if r.cost_usd is not None]
        return {
            "model": self.model,
            "fallback": self.fallback,
            "started_at": self.started_at,
            "questions": len(self.results),
            "correct": self.correct,
            "wrong": sum(r.correct is False for r in self.results),
            "undecided": [r.id for r in self.undecided],
            "fallback_used": [r.id for r in self.results if r.fallback_used],
            "median_latency_seconds": round(statistics.median(latencies), 2) if latencies else None,
            "p90_latency_seconds": round(percentile(latencies, P90), 2) if latencies else None,
            "total_cost_usd": round(self.spend, 6),
            "cost_per_question_usd": round(sum(costs) / len(costs), 6) if costs else None,
            "stopped": self.stopped,
        }


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile (p90 of 20 values is the 18th smallest)."""
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def load_questions(path: Path = QUESTIONS_PATH, ids: list[str] | None = None) -> list[dict]:
    """The eval questions, all with gold values filled in by scripts/fill_gold.py."""
    questions = yaml.safe_load(path.read_text(encoding="utf-8"))
    if ids:
        questions = [q for q in questions if q["id"] in ids]
    for question in questions:
        for item in question.get("alternatives") or [question]:
            if "gold" not in item:
                raise ValueError(f"{question['id']} has no gold values; run scripts/fill_gold.py")
    return questions


def grade_answer(question: dict, answer: agent.AgentAnswer) -> QuestionResult:
    """Grade one agent answer and keep everything needed to review it."""
    tools_used = [o.name for o in answer.tool_calls if o.success]
    grade = eval_grader.grade(answer.answer, tools_used, question, error=answer.error)
    return QuestionResult(
        id=question["id"],
        topic=question["topic"],
        question=question["question"],
        answer=answer.answer,
        correct=grade.correct,
        checks=grade.checks,
        reasons=grade.reasons,
        alternative=grade.alternative,
        tools_used=tools_used,
        tool_calls=[
            {
                "name": o.name,
                "arguments": o.arguments,
                "success": o.success,
                "row_count": o.row_count,
                "error": o.error,
                "duration_seconds": o.duration_seconds,
            }
            for o in answer.tool_calls
        ],
        model=answer.model,
        fallback_used=answer.fallback_used,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
        cost_usd=answer.cost_usd,
        latency_seconds=answer.latency_seconds,
        stopped_early=answer.stopped_early,
        error=answer.error,
    )


def run_eval(
    questions: list[dict],
    model: str,
    *,
    fallback: bool = True,
    max_spend: float | None = None,
    spent_before: float = 0.0,
    out_dir: Path | None = None,
    db_path: Path = kpis.DB_PATH,
    audit_path: Path | None = AUDIT_DB_PATH,
    chat: Callable[..., llm.LLMResult] | None = None,
    settings: llm.LLMSettings | None = None,
    progress: Callable[[QuestionResult], None] | None = None,
) -> EvalRun:
    """Ask every question, grade it, and save the results; stop at max_spend.

    spent_before lets the model check apply one spend limit across several runs.
    """
    run = EvalRun(model=model, fallback=fallback, started_at=datetime.now().isoformat())
    out_dir = out_dir or OUTPUT_ROOT / f"{datetime.now():%Y%m%d_%H%M%S}_{model.replace('/', '_')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    config = agent.AgentConfig(
        db_path=db_path,
        audit_path=audit_path,
        charts_dir=out_dir / "charts",
        data_end=EVAL_DATA_END,
        model=model,
        fallback=fallback,
    )
    for question in questions:
        if max_spend is not None and spent_before + run.spend > max_spend:
            run.stopped = f"spend passed ${max_spend:.2f} before {question['id']}"
            break
        answer = agent.ask(question["question"], config, chat=chat, settings=settings)
        result = grade_answer(question, answer)
        run.results.append(result)
        (out_dir / f"{result.id}.json").write_text(
            json.dumps(asdict(result), indent=2), encoding="utf-8"
        )
        if progress:
            progress(result)
    (out_dir / "summary.json").write_text(json.dumps(run.summary(), indent=2), encoding="utf-8")
    return run


def verdict_label(correct: bool | None) -> str:
    """'correct', 'WRONG' or 'REVIEW' for the console table."""
    return {True: "correct", False: "WRONG", None: "REVIEW"}[correct]


def print_progress(result: QuestionResult) -> None:
    """One console line per question as it finishes."""
    cost = "n/a" if result.cost_usd is None else f"${result.cost_usd:.4f}"
    print(
        f"{result.id}  {verdict_label(result.correct):8} {result.latency_seconds:6.1f} s  "
        f"{cost:>9}  {', '.join(result.tools_used) or '-'}"
    )


def print_summary(run: EvalRun, out_dir: Path | None = None) -> None:
    """Score, review list and totals."""
    summary = run.summary()
    print(f"\nScore: {run.correct}/{len(run.results)} correct (target {TARGET_CORRECT}/20)")
    for result in run.results:
        if result.correct is not True:
            print(f"  {result.id} {verdict_label(result.correct)}: {'; '.join(result.reasons)}")
    if run.undecided:
        print(f"Review by hand: {', '.join(r.id for r in run.undecided)}")
    print(
        f"Median latency {summary['median_latency_seconds']} s, p90 "
        f"{summary['p90_latency_seconds']} s, total cost ${summary['total_cost_usd']:.4f}"
    )
    if run.stopped:
        print(f"Stopped early: {run.stopped}")
    if out_dir:
        print(f"Results: {out_dir}")


def main(argv: list[str] | None = None) -> int:
    """Run the eval from the command line."""
    parser = argparse.ArgumentParser(description="Run the 20 eval questions live and grade them.")
    parser.add_argument("--model", help="model ID (default: LLM_MODEL from .env)")
    parser.add_argument("--no-fallback", action="store_true", help="measure the model alone")
    parser.add_argument("--ids", help="comma-separated question ids, e.g. q01,q17")
    parser.add_argument("--max-spend", type=float, help="stop once the run passes this many USD")
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        settings = llm.llm_settings()
    except llm.LLMConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    model = args.model or settings.model
    questions = load_questions(ids=args.ids.split(",") if args.ids else None)
    out_dir = OUTPUT_ROOT / f"{datetime.now():%Y%m%d_%H%M%S}_{model.replace('/', '_')}"
    print(
        f"Running {len(questions)} questions on {model} (fallback {'off' if args.no_fallback else 'on'})"
    )
    run = run_eval(
        questions,
        model,
        fallback=not args.no_fallback,
        max_spend=args.max_spend,
        out_dir=out_dir,
        settings=settings,
        progress=print_progress,
    )
    print_summary(run, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
