# Week 6 wrap-up: live verification (2026-10-22)

Model `openai/gpt-5.6-luna`, fallback off, DATA_END_DATE 2026-09-30. Times PKT.
Spend is measured from the API key's credit balance (`llm.key_limit_remaining()`);
OpenRouter reported no per-call cost on this run.

## Eval: 26/27

| Measure | Result |
| --- | --- |
| Score | **26/27** (q01 to q25: 24/25; off-topic q26, q27: 2/2) |
| Answer number guard | `ok` on all 27; nothing retried or withheld |
| Fallback used | none (fallback off) |
| HTTP 429s | 0 |
| Median / p90 latency | 6.56 s / 10.41 s |
| Cost | $0.0161 by the script's own balance reading |

| Id | Verdict | Time | Tools |
| --- | --- | --- | --- |
| q01 | correct | 4.4 s | get_kpis |
| q02 | correct | 4.8 s | get_kpis |
| q03 | correct | 7.7 s | get_kpis |
| q04 | correct | 6.0 s | get_kpis |
| q05 | correct | 4.6 s | get_kpis |
| q06 | correct | 6.6 s | run_sql |
| q07 | correct | 7.8 s | run_sql |
| q08 | correct | 6.1 s | run_sql |
| q09 | correct | 10.4 s | run_sql |
| q10 | correct | 9.0 s | run_sql |
| q11 | correct | 8.4 s | run_sql |
| q12 | correct | 8.6 s | run_sql |
| q13 | correct | 5.4 s | get_kpis |
| q14 | correct | 6.3 s | get_kpis |
| q15 | correct | 8.2 s | run_sql |
| q16 | correct | 5.4 s | get_kpis |
| q17 | correct | 10.6 s | run_sql |
| q18 | **wrong** | 10.9 s | get_forecast |
| q19 | correct | 9.5 s | make_chart |
| q20 | correct | 5.6 s | get_kpis |
| q21 | correct | 7.0 s | get_customer_alerts |
| q22 | correct | 7.8 s | get_forecast_accuracy |
| q23 | correct | 7.1 s | get_forecast_accuracy |
| q24 | correct | 5.5 s | get_prebuild_recommendations |
| q25 | correct | 5.9 s | get_customer_alerts |
| q26 | correct | 2.3 s | none |
| q27 | correct | 2.0 s | none |

### q18 (not tuned; needs the project owner's call)
- Question: total forecast units and total actual demand for the Classic Denim
  Jacket (AP-1150) across the 8 forecast weeks. Gold: 500.1 and 393.
- The model called `get_forecast` (weekly values), not `get_forecast_accuracy`,
  which returns the 8-week totals. It then declined to add the weeks up, as rule 1
  requires: "The forecast result provides weekly values but not an 8-week
  aggregate, so I can't state the totals ...".
- Grader: failed `forecast_units=500.1` and `actual_units=393`; no unsupported
  number. It passed in the Week 6 and polish evals, so this is a tool-choice
  change, probably from the longer prompt (rule 14). Not tuned, as instructed.

### Off-topic answers
- q26: "I can only help with this business's sales and demand data. You can ask, for
  example, "What was net revenue last month?" or "Which products need planner
  review?""
- q27: "I can only help with this business's sales and demand data, not weather. You
  can ask about tomorrow's sales forecast or product demand instead."

## Backfill, 14 Sep to 1 Oct

The earlier output was kept in `outputs/backfill_before_wrapup/`.

| Measure | Result |
| --- | --- |
| Reports | 18 built (14 daily, 3 weekly, 1 monthly), 0 failed |
| Summary shown | **18/18** |
| `ok` | 12 |
| `ok_after_retry` | 6, all with reason `content_filter` (Azure): daily as of 13, 17, 20, 22, 27 and 28 Sep |
| Dropped | 0 |
| Cost | $0.0065 |

## CLI check

`python -m src.agent.chat --question "which product is customer buying the most?"`

- First sentence: "By units sold across all completed orders from **1 Oct 2024 to
  30 Sep 2026**, the top product is **Eco Pet Bed**, with **8,561 units**."
- Number guard `ok` on the first attempt (no retry this time; the rule 11 example
  no longer offers a "12").
- **Not as expected:** it stated the measure and period (rule 11), but chose all the
  data instead of the latest full year, so the figure is 8,561, not 4,444. Rule 11's
  new example says "the latest full year of data"; the model read that as the whole
  data range. Not tuned.

## Spend (key balance)

| When | Balance | Difference | What |
| --- | --- | --- | --- |
| 16:42:40, before the first live call | $9.7498 | | session start |
| 16:44:41, after the refusal probes | $9.7490 | $0.0008 | Step 3: 10 calls |
| 16:54:19, before the backfill | $9.7490 | $0.0000 | no late billing |
| 16:56:18, after the backfill | $9.7425 | $0.0065 | Step 7 backfill |
| 16:59:47, after the eval | $9.7217 | $0.0208 | eval ($0.0161 by the script's reading) plus late backfill billing |
| 17:00:40, after the CLI question | $9.7182 | $0.0035 | CLI question plus late eval billing |
| 17:04:05, about 3 min after the CLI question | $9.7131 | $0.0051 | late billing plus a question this session did not ask (17:01:46, "Give me a list of products that i have in my inventory") |

Total by the balance: **$0.0367** of the $0.15 cap ($9.7498 to $9.7131). That
includes at least one question this session did not ask (17:01:46), so the session's
own spend is slightly less. Every block stayed within its sub-budget (investigation
$0.0008 of $0.03, backfill $0.0065 of $0.04, eval and CLI $0.0294 of $0.07 including
late billing). A second outside question came at 17:04:46, after the last reading.
