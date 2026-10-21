# Model check, Week 4

Run on 2026-10-21 with `scripts/compare_models.py`: the 20 questions in `tests/eval/questions.yaml`, once per model, fallback off, temperature 0, provider sort by throughput. Main and fallback were chosen by the project owner; the reference model is for comparison only.

| Role | Model | Correct | Review | Wrong | Median latency (s) | p90 latency (s) | Cost per question | HTTP 429s | Providers (endpoints) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| main | `deepseek/deepseek-v4-flash` | 13/20 | 6 | 1 | 7.15 | 12.71 | $0.00083 | 0 | 15 (15) |
| fallback | `openai/gpt-6-luna` | 18/20 | 1 | 1 | 7.55 | 16.64 | $0.00042 | 0 | 3 (7) |
| reference | `openai/gpt-5.6-luna` | 19/20 | 1 | 0 | 8.77 | 10.06 | $0.00072 | 0 | 3 (7) |

Spend for the check: $0.0396, measured from the API key's credit balance before and after each model (upper-bound estimate used for the cap: $0.2442). Cost per question is that measured spend divided by the questions asked.
HTTP 429s: rate-limit responses; each question that hit one was asked again after 20 s, up to 2 times.

Questions not graded correct:

- `deepseek/deepseek-v4-flash`: q06 (review), q10 (review), q15 (review), q17 (review), q18 (wrong), q19 (review), q20 (review)
- `openai/gpt-6-luna`: q17 (wrong), q20 (review)
- `openai/gpt-5.6-luna`: q20 (review)

**Recommendation:** Main model scored 13/20 (below 19) while openai/gpt-5.6-luna reached 19+. Consider switching the main model.

Questions marked review were left undecided by the grader and need a human look; per-question answers are in `outputs/eval/`.
## Hand review of the undecided answers (by Claude Code; owner to confirm)

| Model | Question | Finding | Proposed verdict |
| --- | --- | --- | --- |
| all three | q20 "How did we do last month?" | Correct September 2026 numbers and the period is named, but no answer says it *interpreted* "last month" | Not correct under the agreed rule (assumption must be stated) |
| deepseek-v4-flash | q06 | Adds "each contributed less than 53,000", a threshold no tool returned | Wrong (core rule) |
| deepseek-v4-flash | q10 | "nearly £24k": rounds a value and adds a currency symbol the data does not have | Wrong (core rule) |
| deepseek-v4-flash | q15 | "median gross margin ... roughly 50.4%", calculated by the model | Wrong (core rule) |
| deepseek-v4-flash | q17 | Month-on-month changes -17.4% and -13.4%, calculated by the model | Wrong (core rule) |
| deepseek-v4-flash | q19 | "Total net revenue ... 1,357,133.24", a sum done by the model | Wrong (core rule) |
| deepseek-v4-flash | q18 | One request hit the 60 s timeout; with fallback on, the fallback model would have answered | Wrong in this measurement |
| gpt-6-luna | q17 | Gives the 21,083.05 drop and 28.5% but not the July and September values; the "31" flag is a grader false positive (the date "July 1-31") | Wrong under the agreed rule |

With this review: main 13/20, fallback 18/20, reference 19/20. The main model's misses
are mostly numbers it calculated itself despite the prompt rule; the OpenAI models did
not do this once.

Earlier runs on 2026-10-21 (before the fixes in commit 217306b) are not counted: the
first had a schema bug that hid `make_chart`'s title argument, a grader bug, cost
reported as 0, and 13 HTTP 429s on the main model; the second was cut short by a
dropped connection on a balance check.
