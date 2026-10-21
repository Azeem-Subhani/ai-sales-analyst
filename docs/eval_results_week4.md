# Final eval, Week 4 wrap-up

Run on 2026-10-21 with `python scripts/run_eval.py --max-spend 0.40`, on branch
`week-4-wrapup`. It covers the 20 questions in `tests/eval/questions.yaml` with
`DATA_END_DATE` 2026-09-30.

- **Models:** main `openai/gpt-5.6-luna` (from `.env`), fallback
  `openai/gpt-6-luna`, fallback on, temperature 0.
- **Code under test:** this run is the first with the answer number guard, the
  SQL guard's personal-data block, the round-once fix, and "last month" =
  September 2026.
- **Raw results:** `outputs/eval/20261021_202303_openai_gpt-5.6-luna/`
  (git-ignored).

## Result

| Measure | Value |
| --- | --- |
| Score (grader) | **19/20 correct**, 0 wrong, 1 review (q12) |
| Score after hand review | 20/20 (q12 is correct; see below) |
| Target | 19/20, met on the first run; no fix rounds were needed |
| Fallback used | none (every question answered by `openai/gpt-5.6-luna`) |
| Answer number guard | `ok` on all 20; no retries, nothing withheld |
| HTTP 429s | 0 |
| Median / p90 latency | 12.3 s / 18.3 s |
| Cost | **$0.0077**, measured from the API key's credit balance (about $0.00038 per question) |

OpenRouter again reported `usage.cost = 0` per call, so the per-question cost
column in the raw results reads n/a. The balance figure is the one to use.

## Failures

| Question | Grader verdict | Finding | Hand verdict |
| --- | --- | --- | --- |
| q12 "What was net revenue in each month from April to September 2026?" | review | All six monthly values match the gold exactly. The one flag is "31", from the date range "May 2026 (1–31 May)", the same grader false positive noted in the model check ("July 1-31"). The runtime answer guard recognises the range as a date and passed it. | Correct |

The grader's unsupported-number check does not treat day ranges such as
"1–31 May" as dates; the runtime guard does. Aligning the two is a small
follow-up. It has not been done here because it changes the grader.

## Model per question

| Question | Verdict | Model | Fallback | Answer guard | Tools | Latency (s) |
| --- | --- | --- | --- | --- | --- | --- |
| q01 | correct | openai/gpt-5.6-luna | no | ok | get_kpis | 8.5 |
| q02 | correct | openai/gpt-5.6-luna | no | ok | get_kpis | 12.1 |
| q03 | correct | openai/gpt-5.6-luna | no | ok | get_kpis | 23.0 |
| q04 | correct | openai/gpt-5.6-luna | no | ok | get_kpis | 10.2 |
| q05 | correct | openai/gpt-5.6-luna | no | ok | get_kpis | 7.0 |
| q06 | correct | openai/gpt-5.6-luna | no | ok | run_sql | 12.8 |
| q07 | correct | openai/gpt-5.6-luna | no | ok | run_sql | 10.1 |
| q08 | correct | openai/gpt-5.6-luna | no | ok | run_sql | 13.4 |
| q09 | correct | openai/gpt-5.6-luna | no | ok | run_sql | 15.5 |
| q10 | correct | openai/gpt-5.6-luna | no | ok | run_sql | 7.8 |
| q11 | correct | openai/gpt-5.6-luna | no | ok | run_sql | 7.7 |
| q12 | review → correct | openai/gpt-5.6-luna | no | ok | run_sql | 18.3 |
| q13 | correct | openai/gpt-5.6-luna | no | ok | get_kpis | 12.6 |
| q14 | correct | openai/gpt-5.6-luna | no | ok | get_kpis | 13.5 |
| q15 | correct | openai/gpt-5.6-luna | no | ok | run_sql | 24.4 |
| q16 | correct | openai/gpt-5.6-luna | no | ok | get_kpis | 7.9 |
| q17 | correct | openai/gpt-5.6-luna | no | ok | run_sql | 13.9 |
| q18 | correct | openai/gpt-5.6-luna | no | ok | get_forecast, run_sql | 12.8 |
| q19 | correct | openai/gpt-5.6-luna | no | ok | make_chart | 12.1 |
| q20 | correct | openai/gpt-5.6-luna | no | ok | get_kpis | 10.9 |

q20 ("How did we do last month?") answered for September 2026 and said which
month it used, under the new relative-period rule.
