# Live eval, Week 5

Run on 2026-10-21 with `python scripts/run_eval.py --max-spend 0.45`, on branch
`week-5-demand`. It covers the 25 questions in `tests/eval/questions.yaml` (20 from
Week 4, 5 demand-planning questions) with `DATA_END_DATE` 2026-09-30.

- **Models:** main `openai/gpt-5.6-luna` (from `.env`), fallback
  `openai/gpt-6-luna`, fallback on, temperature 0.
- **Demand data:** the 2026-09-30 snapshot in `data/planning.duckdb`, computed
  with the Step 9 rules (7-day interval floor, retail top-20% filter, stockout weeks
  out of accuracy, pre-build ranked by urgency, then value at risk).
- **Code under test:** the three demand tools for the first time, plus the
  grader's date-range fix (q12 now grades correct without review).
- **Raw results:** `outputs/eval/20261021_230948_openai_gpt-5.6-luna/`
  (git-ignored).

## Result

| Measure | Value |
| --- | --- |
| Score, original 20 | **20/20 correct** (target 19 or better) |
| Score, demand questions q21 to q25 | **5/5 correct** (target 4 or better) |
| Fix rounds | none needed |
| Fallback used | none |
| Answer number guard | `ok` on 24; `retried_ok` on q07; nothing withheld |
| HTTP 429s | 0 |
| Median / p90 latency | 10.1 s / 15.2 s |
| Cost | **$0.0200**, measured from the API key's credit balance |

**Spend this session (key balance):** $9.8861 before the eval and $9.8661 after.
The cap was $0.50. The six report dry runs earlier in Week 5 (6 summary calls) ran
before the first balance read, so they are not in this figure. At Week 4 rates they
cost about $0.006.

## Demand questions

| Id | Question | Tool used | Gold | Answer |
| --- | --- | --- | --- | --- |
| q21 | Which customers are seriously overdue, and how many are there? | get_customer_alerts | 41; Harbor & Pine Co., Kestrel Home Stores, Bluegate Supplies | 41, the three named first, then every other serious customer |
| q22 | How accurate were the forecasts for the Classic Denim Jacket? | get_forecast_accuracy | WAPE 27.3%, bias 27.3% | WAPE 27.3%, bias +27.3% (forecast too high) |
| q23 | Overall WAPE, and the category with the highest WAPE? | get_forecast_accuracy | 15.9%; Office 17.9% | 15.9%; Office 17.9% |
| q24 | Which three products to pre-build first, and how many units? | get_prebuild_recommendations | Smart Bottle Pro 327, Deluxe Bluetooth Speaker 135, Essential Cotton T-Shirt 95 | the same, in order, and it says a planner must approve |
| q25 | How many customers are declining, and which three lost the most? | get_customer_alerts | 158; Griffiths-Williams, Bird-Kelly, Kestrel Home Stores | 158; the same three |

## Findings for review

- **q25 order:** the answer lists the three customers by percentage drop (Bird-Kelly
  first) instead of the tool's rank by revenue lost (Griffiths-Williams first). The
  names and numbers are right and come from the tool, so the grader marks it
  correct, but the wording "lost the most revenue" is answered by the wrong measure.
  Possible follow-up: return `revenue_lost` with its `_shown` value in
  `get_customer_alerts`, so the model can copy it instead of reordering.
- **q07 guard retry:** the first answer included 1,389,749.18, a number no tool
  returned. The guard named it, and the retry answered with tool numbers only.
- **q21 length:** the model listed all 41 serious customers (it asked for up to 50
  rows). This is correct but long; the prompt could ask for the top few by revenue.
