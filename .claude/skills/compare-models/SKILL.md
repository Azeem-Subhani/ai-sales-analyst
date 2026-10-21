---
name: compare-models
description: Use when comparing LLM candidates on OpenRouter (scripts/compare_models.py), choosing or switching the main or fallback model, and recording the result in the decisions log.
---

# Compare models

The model check decides which models are used (plan section 10). The project
owner chooses the models; the check measures them. Switching models is a
`.env` change (`LLM_MODEL`, `LLM_FALLBACK_MODEL`), not a code change, and only
the owner edits `.env`.

Current choice (2026-10-21): main `openai/gpt-5.6-luna` (19/20), fallback
`openai/gpt-6-luna` (18/20). See `docs/decisions.md`, Week 4 and Week 4 wrap-up.

## Checklist

1. **Confirm budget first.** The live run spends from the $10 OpenRouter key.
   Run the free preflight, show the owner its cost estimate, and wait for an OK.
   ```bash
   python scripts/compare_models.py --reference <model-id>         # free
   ```
2. **Run** with the agreed cap. Main and fallback come from `.env`; each model
   answers the 20 questions in `tests/eval/questions.yaml` with fallback off.
   ```bash
   python scripts/compare_models.py --reference <model-id> --run --max-spend 0.50
   ```
3. **Measure spend from the key balance.** OpenRouter reports `usage.cost = 0`,
   so per-call cost is not reliable. The script reads the balance before and
   after each model; quote that figure.
4. **Read the results** in `docs/model_comparison_week4.md` (rename for a later
   check): correct / review / wrong, median and p90 latency, cost per question,
   HTTP 429s, number of providers. Review every "review" answer by hand.
   - A number the model calculated itself (a sum, an average, a change no tool
     returned) makes the answer wrong: it breaks the core rule.
   - At run time the answer number guard (`src/agent/answer_guard.py`) retries
     and then withholds such answers. The eval output records `answer_guard`
     per question, so count retries and withheld answers too.
5. **Owner's rules:** recommend a change only if the main model scores below
   19/20 while another model reaches 19, or the fallback scores below 17/20.
   Note provider diversity: a fallback from the same company as the main model
   does not cover a company-wide outage.
6. **Record the result.** The plan document is read-only, so add a dated entry
   to `docs/decisions.md` with the scores table, the chosen main and fallback
   models, the reason, and the measured spend.
7. **After a switch:** the owner updates `.env` and `.env.example`. Then run
   the final eval on the new main model with fallback on, with the owner's OK:
   ```bash
   python scripts/run_eval.py --max-spend <cap>
   ```
   Record it in `docs/eval_results_<week>.md`.
8. Run `ruff format . && ruff check --fix .` and `pytest`, then commit.
