# Live eval, Week 6 polish

Run on 2026-10-22 on branch `week-6-dashboard`, after the dashboard polish and the
agent fixes (forecast units to one decimal, round-once `_shown` text, system prompt
rules 11 to 13). It covers the 25 questions in `tests/eval/questions.yaml` with
`DATA_END_DATE` 2026-09-30, the same way as `docs/eval_results_week6.md`:

`python scripts/run_eval.py --model openai/gpt-5.6-luna --no-fallback --max-spend <cap>`

- **Model:** `openai/gpt-5.6-luna`, fallback off, temperature 0.
- **Demand data:** the 2026-09-30 snapshot in `data/planning.duckdb` (unchanged).
- **Before the live runs:** `pytest --runslow` 529 passed; `ruff` clean.

## Result

| Run | Prompt | Score | Raw results (git-ignored) |
| --- | --- | --- | --- |
| 1 | rules 11 and 12 added | **24/25** (q21 for review) | `outputs/eval/20261022_154807_openai_gpt-5.6-luna/` |
| q21 alone, 3 times | rule 13 added | **3/3** | `outputs/eval/20261022_1556{21,31,42}_openai_gpt-5.6-luna/` |
| 2 | rules 11 to 13 | **25/25** (20/20 original, 5/5 demand) | `outputs/eval/20261022_155712_openai_gpt-5.6-luna/` |

Run 2 in detail:

| Measure | Value |
| --- | --- |
| Score | **25/25 correct** |
| Fallback used | none (fallback off) |
| Answer number guard | `ok` on all 25; nothing retried or withheld |
| HTTP 429s | 0 |
| Median / p90 latency | 6.8 s / 10.0 s |
| Cost | **$0.0196** by the script's key-balance reading |

OpenRouter reported no per-call cost (`usage.cost` missing or 0), so the per-question
cost column shows n/a. The key balance is the measured spend.

## Run 1: q21 listed 5 of 10 names

- **Question:** "Which customers are seriously overdue on their orders, and how many
  are there?"
- **Gold:** 41; the top 10 by 365-day revenue in rank order; "and 31 more".
- **Answer:** 41 and "31 more" were right and every number passed the answer number
  guard, but it named only the first 5 of the 10 customers the tool returned, and
  then said "The tool returned the top 10". The grader marked the 5 missing names.
- **Cause:** rule 9 ("answer first ... then up to 3 short supporting points") pulls
  list answers short, and nothing said a returned list must be shown in full. The
  Week 6 run had listed all 10, so the new rules 11 and 12 may have tipped it; one
  run cannot tell.
- **Fix (rule 13, commit `67f8c75`):** when the question asks which or how many
  items, name every row the tool returned, up to 10, in the tool's order, then
  "and N more" when `more_count` is above 0. The 3-point limit is for extra points,
  not for that list. Never describe the tool's results as more than the answer shows.
- **After the fix:** q21 passed 3 times alone and in run 2. Run 2's answer names all
  10 customers in rank order, then "and 31 more".

## Demand questions (run 2)

| Id | Gold | Answer |
| --- | --- | --- |
| q21 | 41; the top 10 by 365-day revenue; 31 more | 41, the same 10 names in rank order, "and 31 more" |
| q22 | WAPE 27.3%, bias 27.3% | WAPE 27.3%, bias +27.3%; forecast **500.1** vs 393 actual units |
| q23 | 15.9%; Office 17.9% | 15.9%; Office 17.9% |
| q24 | Smart Bottle Pro 327, Deluxe Bluetooth Speaker 135, Essential Cotton T-Shirt 95 | the same, in order |
| q25 | 158; Griffiths-Williams, Bird-Kelly, Kestrel Home Stores (revenue lost order) | 158; the same three in order, with revenue lost |

q22 now quotes 500.1 forecast units, which agrees with the +27.3% bias (it was 500
before the one-decimal fix).

## CLI check of rule 11

`python -m src.agent.chat --question "which product is customer buying the most?"`
(main model, fallback on, as the CLI runs):

> Taking **units sold** over the last year, **1 Oct 2025 to 30 Sep 2026**, the
> most-purchased product was **Eco Pet Bed**, with **4,444 units** sold from
> completed orders.

- The first sentence states the measure (units sold) and the period, with dates.
- Bold is on key numbers and names only.
- The answer number guard **retried once**: the first draft used the number 12, which
  no tool returned (most likely "last 12 months"; the rule 11 example in the prompt
  uses that phrase). The retry passed. Not changed here; see the follow-up below.

## Spend (key balance)

| When (PKT) | Balance | Note |
| --- | --- | --- |
| Step 0 of the session | $9.8026 | |
| 15:47:30, before run 1 | $9.7935 | |
| 15:51:21, after run 1 | $9.7778 | run 1: $0.0157 |
| 15:57:03, before run 2 | $9.7708 | q21 x 3 plus late billing |
| 16:00:27, after run 2 | $9.7533 | run 2: $0.0175 by these readings ($0.0196 by the script's own) |
| 16:04:06, 3 min after the CLI question (asked 16:00:40) | $9.7500 | the CLI question and late billing; unchanged since 16:01:49 |

- **Spend for the live checks in this session:** **$0.0435** ($9.7935 to $9.7500),
  under the $0.10 cap. This includes the two outside questions at 15:53 and 15:56,
  so the live checks themselves cost slightly less.
- **Not ours:** the $0.0091 drop before run 1 and a little of the later drops came
  from chat questions nobody in this session asked (`agent_log`: 6 example-chip
  questions between 15:22 and 15:31 PKT, then "Who is the prime minister of
  pakistan" at 15:53 and "what" at 15:56). None match the session's browser checks,
  which asked questions only in a mock with a fake agent. The dashboard listens on
  all network interfaces, so it can be reached from the local network.

## Follow-ups

- The rule 11 example ("the last 12 months") invites the number 12, which the answer
  guard rejects and retries. A wording without a number would save the retry.
- The dashboard's dev server accepts connections from the network; bind it to
  localhost for local use.
