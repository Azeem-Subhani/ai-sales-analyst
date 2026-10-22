# Live eval, Week 6

Run on 2026-10-22 with
`python scripts/run_eval.py --model openai/gpt-5.6-luna --no-fallback --max-spend 0.20`,
on branch `week-6-dashboard`. It covers the 25 questions in `tests/eval/questions.yaml`
with `DATA_END_DATE` 2026-09-30.

- **Model:** `openai/gpt-5.6-luna`, fallback off (as in the Week 5 check), temperature 0.
- **Demand data:** the 2026-09-30 snapshot in `data/planning.duckdb`, recomputed
  on 2026-10-22 so `customer_alerts` has the new `revenue_lost` column. No
  recommendation had been decided, so no `--force` was needed.
- **Code under test:**
  - `revenue_lost` in declining alerts;
  - every demand-tool list capped at 10 rows, with `total_count` and `more_count`
    (the `limit` argument is gone);
  - the grader's new ranked-order check;
  - the updated q21 gold (10 names plus "31 more").
- **Raw results:** `outputs/eval/20261022_122342_openai_gpt-5.6-luna/` (git-ignored).

## Result

| Measure | Value |
| --- | --- |
| Score | **25/25 correct** (20/20 original, 5/5 demand) |
| Ranked-order check | passed on q08, q10, q11, q24, q25 (q06, q15, q23 have one name) |
| Fix rounds | none |
| Fallback used | none (fallback off) |
| Answer number guard | `ok` on all 25; nothing retried or withheld |
| HTTP 429s | 0 |
| Median / p90 latency | 6.3 s / 8.8 s |
| Cost | **$0.0160**, from the API key's credit balance |

OpenRouter did not report a per-call cost on this run (`usage.cost` missing), so the
per-question cost column shows n/a. The key-balance figure is the measured spend.

**Spend so far this session (key balance):** $9.8661 after the Week 5 eval, $9.8419
after this eval: **$0.0242** of the $0.50 cap. Of that, $0.0083 was not the eval. A
first draft of the backfill test called `load_dotenv()`, which put the real LLM keys
into the pytest process. The module-scoped report fixture in `test_reports.py` then
wrote three real summaries before the OpenRouter block applied. The test now
replaces `load_dotenv`, and a full fast run afterwards left the balance unchanged
(see `docs/decisions.md`).

## Demand questions

| Id | Gold | Answer |
| --- | --- | --- |
| q21 | 41; the top 10 by 365-day revenue; 31 more | 41, the same 10 names in rank order, "and 31 more" in the dashboard and report Excel |
| q22 | WAPE 27.3%, bias 27.3% | WAPE 27.3%, bias +27.3% |
| q23 | 15.9%; Office 17.9% | 15.9%; Office 17.9% |
| q24 | Smart Bottle Pro 327, Deluxe Bluetooth Speaker 135, Essential Cotton T-Shirt 95 | the same, in order; says a planner must approve |
| q25 | 158; Griffiths-Williams, Bird-Kelly, Kestrel Home Stores (revenue lost order) | 158; the same three in order, with revenue lost 5,460.84, 4,838.91 and 4,716.62 |

## Findings for review

- **q25 is fixed.** In Week 5 the model ordered by percentage drop. With
  `revenue_lost_shown` in the tool result and the order stated in the tool
  description, it now lists the customers by revenue lost and quotes the amounts.
- **q21 is shorter.** The answer lists 10 customers and says "and 31 more", where in
  Week 5 it listed all 41.
