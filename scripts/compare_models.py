"""Week 4 model check: the main, fallback and one reference model on the 20 eval questions.

The project owner chose the models. Main and fallback come from LLM_MODEL and
LLM_FALLBACK_MODEL in .env, and the reference model is passed on the command line.
This script measures them. It never changes .env.

    python scripts/compare_models.py --reference openai/gpt-5.6-luna         # free: preflight + estimate
    python scripts/compare_models.py --reference openai/gpt-5.6-luna --run   # live: spends credit

Preflight (free catalog GETs) checks that each model exists, supports tool calling,
and reports its price and number of providers. If the main or fallback model fails,
the script stops. The live run asks every question once per model with fallback
off, so each model is measured on its own. Across all models it stops before the
next question could take the total spend to --max-spend (default $0.50, the owner's
cap; the key has a $10 limit), judged by the costliest question so far. Results go
to docs/model_comparison_week4.md.
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))  # so "python scripts/compare_models.py" finds src/

from scripts import run_eval
from src.agent import llm, prompts, tools

REPORT_PATH = PROJECT_ROOT / "docs" / "model_comparison_week4.md"
DEFAULT_MAX_SPEND_USD = 0.50  # owner's cap for the Week 4 check (key limit is $10)
TOKENS_PER_MILLION = 1_000_000
CHARS_PER_TOKEN = 4  # rough average for English text and SQL
# Typical question: 3 model calls (2 tool calls, then the answer). Worst case: 11
# calls (the 10-call limit plus the final answer). Tool results add roughly this
# many tokens per call, and reasoning models may add hidden output tokens.
TYPICAL_CALLS, WORST_CALLS = 3, 11
TOOL_RESULT_TOKENS_PER_CALL = 1_000
TYPICAL_OUTPUT_TOKENS, WORST_OUTPUT_TOKENS = 1_500, 6_000
MAIN_TARGET, OTHER_TARGET, FALLBACK_MINIMUM = 19, 19, 17
RATE_LIMIT_RETRIES = 2  # measurement only: a 429 is not a wrong answer
RATE_LIMIT_WAIT_SECONDS = 20.0


@dataclass
class ModelCheck:
    """Preflight result for one model."""

    role: str  # main | fallback | reference
    model: str
    listed: bool
    tools: bool
    input_price: float | None  # USD per 1M tokens (OpenRouter's list price)
    output_price: float | None
    max_input_price: float | None  # most expensive endpoint; sort-by-throughput may pick it
    max_output_price: float | None
    providers: int
    endpoints: int

    @property
    def ok(self) -> bool:
        """Listed, supports tools and has at least one provider."""
        return self.listed and self.tools and self.endpoints > 0


def per_million(price: str | float | None) -> float | None:
    """OpenRouter's per-token price string as USD per 1M tokens."""
    return None if price in (None, "") else float(price) * TOKENS_PER_MILLION


def check_model(role: str, model: str, catalog: dict, settings: llm.LLMSettings) -> ModelCheck:
    """Look one model up in the catalog and list its provider endpoints."""
    entry = catalog.get(model)
    endpoints = llm.model_endpoints(model, settings) if entry else []
    supported = set((entry or {}).get("supported_parameters") or [])
    pricing = (entry or {}).get("pricing") or {}
    endpoint_prices = [e.get("pricing") or {} for e in endpoints]
    return ModelCheck(
        role=role,
        model=model,
        listed=entry is not None,
        tools={"tools", "tool_choice"} <= supported,
        input_price=per_million(pricing.get("prompt")),
        output_price=per_million(pricing.get("completion")),
        max_input_price=max(
            (per_million(p.get("prompt")) or 0 for p in endpoint_prices), default=None
        ),
        max_output_price=max(
            (per_million(p.get("completion")) or 0 for p in endpoint_prices), default=None
        ),
        providers=len({e.get("provider_name") for e in endpoints}),
        endpoints=len(endpoints),
    )


def base_prompt_tokens() -> int:
    """Approximate tokens sent on every call: system prompt plus tool schemas."""
    chars = len(prompts.build_system_prompt(data_end=run_eval.EVAL_DATA_END))
    chars += len(json.dumps(tools.tool_schemas()))
    return math.ceil(chars / CHARS_PER_TOKEN)


def question_tokens(base: int, calls: int, output: int) -> tuple[int, int]:
    """(input, output) tokens for one question: each call resends the growing history."""
    history = TOOL_RESULT_TOKENS_PER_CALL * calls * (calls - 1) // 2
    return base * calls + history, output


def estimate_cost(check: ModelCheck, questions: int, base: int, worst: bool) -> float:
    """Estimated USD for one model on all questions (worst: most expensive endpoint too)."""
    calls, output = (
        (WORST_CALLS, WORST_OUTPUT_TOKENS) if worst else (TYPICAL_CALLS, TYPICAL_OUTPUT_TOKENS)
    )
    tokens_in, tokens_out = question_tokens(base, calls, output)
    price_in = (check.max_input_price if worst else check.input_price) or 0.0
    price_out = (check.max_output_price if worst else check.output_price) or 0.0
    return questions * (tokens_in * price_in + tokens_out * price_out) / TOKENS_PER_MILLION


def money(value: float | None, digits: int = 4) -> str:
    """'$0.0123' or 'n/a'."""
    return "n/a" if value is None else f"${value:.{digits}f}"


def print_preflight(checks: list[ModelCheck], questions: int, base: int) -> tuple[float, float]:
    """The preflight table and cost estimate; returns (typical, worst) totals."""
    print(
        f"{'role':10} {'model':30} {'listed':7} {'tools':6} {'in $/M':>8} {'out $/M':>8} "
        f"{'providers':>9} {'endpoints':>9}"
    )
    for c in checks:
        print(
            f"{c.role:10} {c.model:30} {c.listed!s:7} {c.tools!s:6} "
            f"{c.input_price or 0:8.3f} {c.output_price or 0:8.3f} {c.providers:9} {c.endpoints:9}"
        )
    typical_in, typical_out = question_tokens(base, TYPICAL_CALLS, TYPICAL_OUTPUT_TOKENS)
    worst_in, worst_out = question_tokens(base, WORST_CALLS, WORST_OUTPUT_TOKENS)
    print(
        f"\nEstimate per question: typical {typical_in:,} in / {typical_out:,} out tokens; "
        f"worst case {worst_in:,} in / {worst_out:,} out (base prompt ~{base:,} tokens)."
    )
    typical_total = worst_total = 0.0
    for c in checks:
        typical = estimate_cost(c, questions, base, worst=False)
        worst = estimate_cost(c, questions, base, worst=True)
        typical_total += typical
        worst_total += worst
        print(
            f"  {c.model:30} {questions} questions: typical {money(typical)}, worst {money(worst)}"
        )
    print(
        f"Total for {len(checks)} models: typical {money(typical_total)}, "
        f"worst case {money(worst_total)}"
    )
    return typical_total, worst_total


# ---------------------------------------------------------------- live run and report


def recommendation(scores: dict[str, int], roles: dict[str, str]) -> str:
    """Only recommend a change under the owner's two rules; otherwise keep the choice."""
    main = next(m for m, r in roles.items() if r == "main")
    fallback = next(m for m, r in roles.items() if r == "fallback")
    notes = []
    better = [m for m, s in scores.items() if m != main and s >= OTHER_TARGET]
    if scores.get(main, 0) < MAIN_TARGET and better:
        notes.append(
            f"Main model scored {scores[main]}/20 (below {MAIN_TARGET}) while "
            f"{', '.join(better)} reached {OTHER_TARGET}+. Consider switching the main model."
        )
    if scores.get(fallback, 0) < FALLBACK_MINIMUM:
        notes.append(
            f"Fallback scored {scores.get(fallback, 0)}/20, below {FALLBACK_MINIMUM}. "
            "Consider a different fallback."
        )
    return " ".join(notes) or "Keep the owner's choice: neither change rule is met."


def cost_per_question(actual: float | None, summary: dict) -> str:
    """Measured spend divided by questions asked, else the estimate."""
    if actual is not None and summary["questions"]:
        return money(actual / summary["questions"], 5)
    return money(summary["cost_per_question_usd"], 5) + " (estimate)"


def write_report(
    checks: list[ModelCheck],
    runs: dict[str, run_eval.EvalRun],
    estimated: float,
    actual: dict[str, float | None],
) -> str:
    """Markdown results table for docs/model_comparison_week4.md."""
    roles = {c.model: c.role for c in checks}
    scores = {m: r.correct for m, r in runs.items()}
    lines = [
        "# Model check, Week 4",
        "",
        (
            f"Run on {date.today().isoformat()} with `scripts/compare_models.py`: the 20 questions in "
            "`tests/eval/questions.yaml`, once per model, fallback off, temperature 0, "
            "provider sort by throughput. Main and fallback were chosen by the project owner; "
            "the reference model is for comparison only."
        ),
        "",
        (
            "| Role | Model | Correct | Review | Wrong | Median latency (s) | p90 latency (s) "
            "| Cost per question | HTTP 429s | Providers (endpoints) |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for c in checks:
        run = runs.get(c.model)
        if run is None:
            lines.append(
                f"| {c.role} | `{c.model}` | not run | | | | | | | {c.providers} ({c.endpoints}) |"
            )
            continue
        s = run.summary()
        lines.append(
            f"| {c.role} | `{c.model}` | {s['correct']}/{s['questions']} | {len(s['undecided'])} "
            f"| {s['wrong']} | {s['median_latency_seconds']} | {s['p90_latency_seconds']} "
            f"| {cost_per_question(actual.get(c.model), s)} | {s['rate_limited_429']} "
            f"| {c.providers} ({c.endpoints}) |"
        )
    real = sum(v for v in actual.values() if v is not None)
    lines += [
        "",
        (
            f"Spend for the check: {money(real)}, measured from the API key's credit balance "
            f"before and after each model (upper-bound estimate used for the cap: {money(estimated)}). "
            "Cost per question is that measured spend divided by the questions asked."
        ),
        (
            "HTTP 429s: rate-limit responses; each question that hit one was asked again "
            f"after {RATE_LIMIT_WAIT_SECONDS:g} s, up to {RATE_LIMIT_RETRIES} times."
        ),
        "",
    ]
    lines += ["Questions not graded correct:", ""]
    for model, run in runs.items():
        misses = [
            f"{r.id} ({'review' if r.correct is None else 'wrong'})"
            for r in run.results
            if r.correct is not True
        ]
        lines.append(f"- `{model}`: {', '.join(misses) or 'none'}")
    if any(r.stopped for r in runs.values()):
        lines += [
            "",
            "Stopped early: "
            + "; ".join(f"{m}: {r.stopped}" for m, r in runs.items() if r.stopped),
        ]
    lines += ["", f"**Recommendation:** {recommendation(scores, roles)}", ""]
    lines += [
        (
            "Questions marked review were left undecided by the grader and need a human look; "
            "per-question answers are in `outputs/eval/`."
        ),
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Preflight and estimate (free); with --run, the live check."""
    parser = argparse.ArgumentParser(description="Week 4 model check.")
    parser.add_argument("--reference", required=True, help="reference model ID (comparison only)")
    parser.add_argument("--run", action="store_true", help="run the live check (spends credit)")
    parser.add_argument(
        "--max-spend", type=float, default=DEFAULT_MAX_SPEND_USD, help="total cap in USD"
    )
    parser.add_argument(
        "--spent-so-far",
        type=float,
        default=0.0,
        help="USD already spent this week; counts toward --max-spend",
    )
    args = parser.parse_args(argv)

    load_dotenv()
    try:
        settings = llm.llm_settings()
    except llm.LLMConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    roles = [
        ("main", settings.model),
        ("fallback", settings.fallback_model),
        ("reference", args.reference),
    ]
    catalog = llm.list_models(settings)
    checks = [check_model(role, model, catalog, settings) for role, model in roles]
    questions = run_eval.load_questions()
    print_preflight(checks, len(questions), base_prompt_tokens())

    failed = [c for c in checks if c.role in ("main", "fallback") and not c.ok]
    if failed:
        for c in failed:
            print(
                f"Error: {c.role} model {c.model} failed the check (listed={c.listed}, "
                f"tools={c.tools}, endpoints={c.endpoints}).",
                file=sys.stderr,
            )
        return 1
    if not args.run:
        print("\nPreflight only; nothing was spent. Add --run to run the live check.")
        return 0

    # OpenRouter has reported usage.cost = 0, so costs are estimated from the most
    # expensive endpoint's price: an upper bound, which keeps the cap safe.
    for c in checks:
        if c.max_input_price is not None and c.max_output_price is not None:
            llm.PRICE_TABLE[c.model] = (c.max_input_price, c.max_output_price)
    key_before = key_start = llm.key_limit_remaining(settings)
    actual: dict[str, float | None] = {}

    def real_spend() -> float:
        """Spend so far this week: earlier spend plus the key balance drop since start."""
        now = llm.key_limit_remaining(settings)
        drop = 0.0 if key_start is None or now is None else key_start - now
        return args.spent_so_far + drop

    runs: dict[str, run_eval.EvalRun] = {}
    spent, costliest = args.spent_so_far, 0.0
    for c in checks:
        if not c.ok:
            print(f"\nSkipping {c.model}: it failed the preflight check.")
            continue
        if max(spent, real_spend()) + costliest >= args.max_spend:
            print(
                f"\nSpend {money(spent)} is too close to the {money(args.max_spend, 2)} cap; stopping."
            )
            break
        print(f"\n== {c.role}: {c.model} (fallback off)")
        run = run_eval.run_eval(
            questions,
            c.model,
            fallback=False,
            max_spend=args.max_spend,
            spent_before=spent,
            reserve=costliest,
            spent_probe=real_spend,
            settings=settings,
            progress=run_eval.print_progress,
            rate_limit_retries=RATE_LIMIT_RETRIES,
            rate_limit_wait_seconds=RATE_LIMIT_WAIT_SECONDS,
        )
        key_after = llm.key_limit_remaining(settings)
        actual[c.model] = (
            None if key_before is None or key_after is None else round(key_before - key_after, 6)
        )
        key_before = key_after
        spent += run.spend
        costliest = max([costliest] + [r.cost_usd or 0.0 for r in run.results])
        runs[c.model] = run
        run_eval.print_summary(run)
    real = sum(v for v in actual.values() if v is not None)
    REPORT_PATH.write_text(
        write_report(checks, runs, spent - args.spent_so_far, actual), encoding="utf-8"
    )
    print(
        f"\nSpend: {money(real)} by the key's credit balance (estimate used for the cap: "
        f"{money(spent)}). Table written to {REPORT_PATH}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
