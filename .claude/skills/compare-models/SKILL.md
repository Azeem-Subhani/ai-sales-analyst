---
name: compare-models
description: Use when comparing LLM candidates on OpenRouter (scripts/compare_models.py), choosing or switching the main or fallback model, and recording the result in the decisions log.
---

# Compare models

The Week 4 comparison decides which model is used (plan section 10). Switching
models is a `.env` change, not a code change.

## Checklist

1. **Confirm budget first.** The run calls the OpenRouter API and spends from
   the $10 credit limit. Get the project owner's go-ahead before running it.
2. **Check candidates** in `scripts/compare_models.py` against the plan's
   table: `xiaomi/mimo-v2.6-pro`, `deepseek/deepseek-v4-flash-0731`,
   `xiaomi/mimo-v2.6-flash`, `deepseek/deepseek-v4.1-flash`,
   `anthropic/claude-haiku-5.5`. All calls go through `src/agent/llm.py`.
3. **Run:** `python scripts/compare_models.py`. It writes
   `outputs/model_comparison.md`.
4. **Read the scores:** correct answers on the 20 test questions, valid tool
   calls, summary accuracy (every number in the summary appears in the input),
   median response time, cost per question.
5. **Decide:** best accuracy wins, speed breaks ties; the most reliable
   multi-provider runner-up becomes the fallback.
6. **Record the result.** The plan document is read-only, so add a dated entry
   to `docs/decisions.md` (create it if missing) with the scores table, the
   chosen main and fallback models, and the reason. Ask the project owner
   before changing `docs/project_plan.md`.
7. Update `LLM_MODEL` / `LLM_FALLBACK_MODEL` defaults in `.env.example` if they
   changed, then commit.
