# Decisions log

Running log of decisions made during the build. `docs/project_plan.md` section 16
holds the original v1 decisions and is not edited; entries here clarify or extend it.
Owner: project owner (Azeem Subhani). Newest entries at the bottom of each section.

## Week 1 follow-ups

### Exact dependency lock file
- **Decision:** `requirements-lock.txt` holds the full `pip freeze` of a working
  Python 3.12 venv. `requirements.txt` and `requirements-dev.txt` stay the edited
  source of truth.
- **Why:** pinned top-level packages still let transitive dependencies drift; the lock
  file reproduces the exact tested environment.
- **How to update:** after changing a requirements file, reinstall and run
  `python -m pip freeze > requirements-lock.txt`, then restore the header comment.

### Run and agent logs live in `data/audit.duckdb`
- **Decision:** the `report_runs` table (Week 3) and `agent_log` table (Week 4) are
  written to a separate DuckDB file, `data/audit.duckdb` (git-ignored via `data/*`).
  `data/sales.duckdb` stays read-only for reports, the agent and the app.
- **Why:** DuckDB allows one read-write process *or* many read-only processes on a
  file, not both, so logging into `sales.duckdb` would lock out Streamlit and the
  agent while the scheduler writes. The generator also replaces `sales.duckdb` as a
  whole file on rebuild, which would wipe the logs. Keeping them apart also keeps the
  AGENTS.md rule intact: only the generator and the forecast writer touch business data.
- **Considered:** same database with its own tables (rejected for the reasons above).

### Generator constants stay in `generate.py` for now
- **Decision:** moving catalog constants (categories, product nouns, countries) into
  `src/data/catalog.py` is deferred.
- **Why:** Week 2 does not need it, and touching the generator risks seed-42 output.
  Revisit if a second connector needs the same lists.

## Week 2: KPI definitions (clarified version of plan section 7)

All KPIs are calculated in SQL (`src/analytics/kpis.sql`). Differences from the plan
text are marked **(clarified)**.

| KPI | Definition |
| --- | --- |
| Line value | `quantity * unit_price * (1 - discount_pct)` (order_items prices, not list price) |
| Net revenue | Sum of line values for orders with status `completed`. Returned and cancelled orders are excluded **(clarified: the plan said "minus returned orders"; returns are now excluded and reported separately)** |
| Returns | Sum of line values for orders with status `returned` |
| Orders | Count of completed orders |
| AOV | Net revenue / orders |
| Units sold | Sum of quantity on completed orders |
| Cost of goods | Sum of `quantity * products.unit_cost` on completed orders |
| Gross margin % | (Net revenue - cost of goods) / net revenue |
| Cancellation rate | Cancelled orders / all orders |
| Return rate | Returned orders / (completed + returned orders) **(clarified: the plan said / completed orders)** |
| New customer | The customer's first-ever non-cancelled order falls in the period; otherwise returning **(clarified: cancelled orders do not count as a first order)**. Population: customers with at least one non-cancelled order in the period (assumption, open for confirmation) |
| Top products / customers | Top 10 by net revenue, with change vs the report's first comparison period, plus the list of last period's top 10 that dropped out |
| Breakdowns | Net revenue by category, region, channel and customer_type |

### Periods and comparisons
- Day; week = Monday to Sunday; month = calendar month.
- Every function takes an explicit `as_of` date; nothing defaults to today (the data
  ends 2026-09-30). The report covers the day / week / month **ending on** `as_of`:
  weekly reports need a Sunday, monthly reports the last day of a month. An `as_of`
  after the last order date is rejected. The Week 3 scheduler passes "yesterday".
- Daily: vs same weekday last week (`as_of - 7 days`).
- Weekly: vs previous week and the same ISO week last year
  (`date.fromisocalendar(year - 1, week, 1)`; ISO week 53 falls back to week 52).
- Monthly: vs previous month and the same month last year.
- Changes: percent for money and counts, percentage points for rates. When the
  comparison value is zero the change is empty and shown as "n/a".
- Money is shown with thousands separators and 2 decimals, no currency symbol
  (assumption: the dummy data has no currency column).

## Week 5 acceptance criterion: wholesale churn (planted problem 1)

- **Criterion:** as of 2026-09-30, the overdue-customer alerts must flag
  Harbor & Pine Co., Bluegate Supplies and Kestrel Home Stores as serious (more than
  2x their usual interval), and the September monthly report must show them in its
  demand-alerts section. (The monthly report has no demand-alerts section yet;
  Week 5 adds it.)
- **Why not in Week 2:** the original Week 2 expectation (the August monthly report
  shows the three dropping out of top customers or declining) was wrong. A monthly
  sales report cannot show churn that starts mid-month among customers outside the
  top 10, so the check was dropped rather than weakened or marked xfail.
- **Context (measured on the seed-42 data with `src/analytics/kpis.py`, Week 2):**

  | Customer | July 2026 net revenue (rank) | August 2026 net revenue (rank) | Change |
  | --- | --- | --- | --- |
  | Harbor & Pine Co. | 2,588.32 (11) | 1,358.74 (60) | -47.5% |
  | Kestrel Home Stores | 1,113.63 (106) | 1,795.54 (33) | +61.2% |
  | Bluegate Supplies | 970.67 (123) | 256.87 (543) | -73.5% |

  Ranked by revenue drop from July to August, Harbor & Pine is 18th and Bluegate 56th;
  normal wholesale reorder cycles (2 to 6 weeks) produce larger swings. Kestrel rises in
  August because its last orders fall in the first half of the month. In September all
  three have zero net revenue.

## Week 3: scheduling and delivery

### Data clock (`DATA_END_DATE`)
- **Decision:** a scheduled run reports the latest *complete* period on or before a
  reference date. The reference date is `min(yesterday, DATA_END_DATE)`, with
  "yesterday" taken in `TIMEZONE`.
  - Daily: the reference date itself.
  - Weekly: the last full Monday-to-Sunday week ending on or before it.
  - Monthly: the last full calendar month ending on or before it.

  With `DATA_END_DATE=2026-09-30`, every run reports the 30 Sep 2026 daily report,
  week 39 (21 to 27 Sep 2026) and September 2026. The email states which period it
  covers and the data end date. Code: `src/reports/data_clock.py`.
- **Replaces:** the Week 2 line "The Week 3 scheduler passes yesterday". The builder
  is unchanged: it still takes an explicit `as_of` and rejects partial periods.
- **Why:** the dummy data ends on 2026-09-30, but the scheduler runs on real dates,
  so "yesterday" has no data and would raise on every run.
- **Real data later:** remove `DATA_END_DATE` from `.env`. The reference date then
  becomes plain yesterday, with no code change.

### Recipients come from `.env`, and the allowlist refuses the whole send
- **Decision:** `config/reports.yaml` writes addresses as `${REPORT_OWNER_EMAIL}`, and
  the value is read from `.env`. The top-level `delivery` block holds
  `allowed_recipients` and `failure_recipients`.
  - If any recipient is not on the allowlist, `send_report` refuses the whole send
    and logs the refused address.
  - The run is marked failed with no retry, and a failure notice goes to
    `failure_recipients`.
- **Why:** this keeps personal addresses out of git (Cinnova: no PII in committed
  code). Refusing the whole send catches config typos instead of silently mailing
  a subset.
- **Considered:** literal addresses in reports.yaml (rejected: PII in git history).
  Dropping the unknown address and sending to the rest (rejected: hides mistakes).

### Retries
- **Decision:** a failed run is retried once after `REPORT_RETRY_DELAY_SECONDS`
  (default 300). If the retry also fails, a failure notice is sent.
  - Config errors (allowlist, missing Gmail credentials, unset `${VAR}`, bad
    `REPORT_MODE`) are not retried, because retrying cannot fix them.
  - Missing Gmail credentials also mean no notice can be emailed. In that case
    the error is only logged and written to `report_runs`.
- Every attempt is one row in `report_runs` in `data/audit.duckdb` (see Week 1
  follow-ups).

## Week 4: LLM client, summaries and agent

### Base branch is `main` (no `dev` yet)
- **Decision:** `week-4-agent` was created from `main` (992d404, the merge of
  `week-3-delivery`). Week 4 is merged back into `main` through a PR.
- **Why:** the Week 4 brief says to branch from `dev`, but `origin` had no `dev`
  branch on 2026-10-21. `week-3-delivery` was confirmed merged into `origin/main`.
- **How to update:** if a `dev` branch is created later, rebase or merge
  `week-4-agent` onto it and update this entry.
- **Update (2026-10-21):** `week-4-agent` was merged into `main` (PR #4), and
  `week-4-wrapup` was branched from `main`. There is still no `dev` branch.

### Fast provider routing: sort by throughput, not `:nitro`
- **Decision:** every OpenRouter call sends
  `extra_body={"provider": {"sort": "throughput"}}`. `.env` holds plain model IDs
  with no `:nitro` suffix.
- **Why:** this keeps model IDs clean and switching models a `.env` change.
- **Difference from `:nitro` (OpenRouter provider-routing docs, checked
  2026-10-21):** `:nitro` is a superset of `sort: "throughput"`. It also makes
  priority-service-tier endpoints eligible, which `sort` alone does not. Setting
  `sort` also turns off OpenRouter's default price-weighted load balancing, so
  providers are tried fastest first. Chosen by the project owner.

### Report summaries: pre-formatted numbers and a number guard
- **Decision:** `src/reports/summary.py` sends the model the report's numbers as
  text lines, formatted exactly as the email shows them. The model writes 3 to 5
  sentences. A guard extracts every number from the summary and accepts it only
  if it matches a number that was sent, the same number rounded to fewer
  decimals, or a whole number from 1 to 10. If any number fails, the model gets
  one retry with the failing numbers named. After a second failure the summary
  is dropped.
- **Never blocks a report:** if the LLM is unavailable or the summary is
  dropped, the report still builds and sends with "Summary unavailable for this
  run". `report_runs.summary_status` records `ok`, `guard_failed`,
  `llm_unavailable` or `skipped` (`--no-summary`). The column is added to an
  existing `audit.duckdb` on first use.
- **Known limit:** the guard checks values, not direction. Signs are ignored, so
  "fell 2.5%" for a +2.5% change passes the guard. The prompt asks the model to
  copy the data; reviewers should check direction in the Week 6 dry-run review.
- **Scheduler start-up:** a missing `OPENROUTER_API_KEY` is logged as a one-line
  warning, not an error, because reports must still send without a summary.

### pydantic is pinned (15th pin)
- **Decision:** `pydantic==2.14.1` is added to `requirements.txt` as the 15th
  top-level pin. It was already installed as a dependency of `openai`, so
  `requirements-lock.txt` was regenerated with no change.
- **Why:** the agent tools import pydantic directly, for argument models and
  for generating the tool JSON schemas. A direct import should be a direct pin,
  so an `openai` upgrade cannot silently change it.

### SQL guard reads DuckDB's parse tree
- **Decision:** `src/agent/sql_guard.py` accepts a query only if all of these
  hold:
  1. `duckdb.extract_statements` finds exactly one statement, and it is a SELECT.
  2. The text starts with SELECT or WITH.
  3. No blocked keyword appears outside strings and comments.
  4. Every table, CTE and function in `json_serialize_sql`'s parse tree passes.
     Only the six standard tables and the query's own CTEs may be read. Table
     functions other than `range`, `generate_series` and `unnest` are refused,
     and so are file and system functions.
- **Replaces:** the plan's idea of reading table names from `EXPLAIN`. The
  parse tree also catches `read_csv(...)` and `'file.csv'` scans, and it
  needs no database.
- **Why the start-keyword check:** DuckDB reports `PRAGMA`, `DESCRIBE`, `SHOW`
  and `SUMMARIZE` as SELECT statements.
- **Second layer:** the agent's connection is read-only with
  `SET enable_external_access = false`. This is done with SET, not a
  connect-time config, because DuckDB refuses two in-process connections to one
  file with different configs. The setting applies to the whole in-process
  database until it closes; nothing in the project reads files through DuckDB.
  `audit.duckdb` is never attached.
- Limits: 500 rows (501 are fetched to detect truncation), 10 seconds via
  `interrupt()`.

### get_forecast compares with demand, not "units sold"
- **Decision:** `get_forecast` puts actual units next to each forecast week.
  Actual units are units on non-cancelled orders (completed or returned), per
  Monday-to-Sunday week.
- **Why:** the generator seeded the forecasts from that demand series. The
  "Units sold" KPI counts completed orders only, so comparing against it would
  overstate forecast error by the return rate.
- **Week 5:** forecast accuracy (WAPE and bias) should use the same demand
  definition. Confirm with the project owner.

### Agent loop, prompt and agent_log
- **Loop:** `src/agent/agent.py` is a plain loop with no framework.
  - Limits per question: 10 tool calls and 120 seconds.
  - At the tool limit, the model is asked once more with `tool_choice="none"`.
    The answer then starts with "(Stopped early: ...)".
  - Each model request's timeout is capped by the time left for the question.
- **Prompt:** `src/agent/prompts/` replaces the `prompts.py` stub. It holds
  `system_prompt.md` (the template) and `examples.yaml` (5 question/SQL pairs).
  - Table schemas and column values are read from the database.
  - KPI definitions are copied from this file's Week 2 section.
  - "Today" is `DATA_END_DATE`, or the real date when it is unset.
  - A test runs every example query through the SQL guard and checks its value.
- **Logging:** `agent_log` (one row per question) and `agent_tool_calls` (one row
  per tool call) are in `data/audit.duckdb`, linked by `question_id`.
- **Chat CLI:** each question is answered on its own, with no conversation
  history. The Week 6 Streamlit app keeps history.
- **Email addresses:** `customers.email` is in the schema. The prompt tells the
  model never to show email addresses. Blocking the column in the SQL guard is
  an open item before real data arrives.
  - **Closed (2026-10-21):** see "SQL guard blocks personal contact data" in the
    Week 4 wrap-up section.

### Evaluation set and grading rules
- **Decision:** `tests/eval/questions.yaml` holds 20 questions. Each one has
  hand-written gold SQL. `scripts/fill_gold.py` runs that SQL through the SQL
  guard and writes the gold values into the file; nobody types them.
  `pytest` checks that the stored values still match the SQL.
- **Grading (`src/agent/eval_grader.py`):**
  - Numbers must match within display rounding:
    - Money of 1,000 or more may drop its decimals.
    - Other values keep their display decimals.
    - Counts must match exactly.
    - At display precision, one unit off in the last digit is allowed.
    - Signs are ignored.
  - Names must be present (case and punctuation ignored).
  - A chart answer needs a successful `make_chart` call.
  - The ambiguous question ("How did we do last month?") accepts August or
    September 2026, as long as the answer states its assumption.
- **Verdicts:** all checks pass → correct. Fewer than half pass → wrong.
  Anything in between is marked for human review.
- **Why one unit of tolerance:** the builder rounds changes to 2 decimals and
  then displays 1, so a true 8.6495% shows as 8.7%. This also affects the Week 2
  reports and is listed as an open item.
  - **Closed (2026-10-21):** see "Percentages are rounded once, at display" in the
    Week 4 wrap-up section. The tolerance stays, for models that round twice.

### Model check: main and fallback chosen by the project owner
- **Decision:** the project owner chose the models. The main model is
  `deepseek/deepseek-v4-flash` and the fallback is `openai/gpt-6-luna`.
  `openai/gpt-5.6-luna` was run as a reference only. `.env` was not changed by
  the check.
- **Results (2026-10-21):** see `docs/model_comparison_week4.md`. All runs used
  fallback off, temperature 0 and throughput routing.

  | Model | Correct | Median / p90 latency | Cost per question | Providers |
  | --- | --- | --- | --- | --- |
  | main | 13/20 | 7.2 / 12.7 s | $0.00083 | 15 |
  | fallback | 18/20 | 7.6 / 16.6 s | $0.00042 | 3 |
  | reference | 19/20 | 8.8 / 10.1 s | $0.00072 | 3 |

- **Owner's rules:** a recommendation is made only if the main model scores
  below 19/20 while another model reaches 19, or the fallback scores below
  17/20. The first rule is met: the main model scored 13 and the reference 19.
  So a change of main model is recommended for the owner to decide; nothing was
  switched. The fallback passed its rule with 18.
- **Main model's misses:** most are numbers it calculated itself (sums, medians,
  month-on-month changes, made-up thresholds), against the prompt rule.
- **Cost reporting:** OpenRouter reported `usage.cost = 0` on every response.
  Spend is therefore measured from the API key's credit balance. Week 4 spend
  is about $0.089 against the owner's $0.50 cap. That covers three model-check
  runs: the first invalid, the second cut short, the third reported here.
- **Grader limit:** a date range written as "July 1-31" can be flagged as an
  unsupported number. Such flags go to review, not to wrong.

### Main model switched to openai/gpt-5.6-luna
- **Decision (project owner, 2026-10-21):** after the model check, the main
  model is switched from `deepseek/deepseek-v4-flash` to `openai/gpt-5.6-luna`.
  The fallback stays `openai/gpt-6-luna`.
- **Why:** `gpt-5.6-luna` was the only model at 19/20, with the lowest p90
  latency (10.1 s), at $0.00072 per question. DeepSeek scored 13/20, mostly by
  adding numbers it calculated itself. The fallback passed its rule with 18/20.
- **How:** the owner changes `LLM_MODEL` in `.env` and `.env.example`; Claude
  Code cannot edit those files. No code change is needed.
- **Not re-measured:** at the owner's request, the eval was not run again after
  the switch. The 19/20 figure comes from the model check run with fallback
  off. Step 7's final eval with the `.env` main model is therefore not run;
  this is an open item.
  - **Closed (2026-10-21):** the final eval with the `.env` main model and
    fallback on scored 19/20 (20/20 after hand review) for $0.0077; see
    `docs/eval_results_week4.md`.
- **Trade-off:** 3 providers instead of DeepSeek's 15. A provider-wide outage
  at OpenAI is covered only partly, because the fallback is also an OpenAI model
  (served by OpenAI, Azure and Amazon Bedrock).

## Week 4 wrap-up

### Main model is openai/gpt-5.6-luna
- **Decision (project owner, 2026-10-21):** the main model is
  `openai/gpt-5.6-luna`, replacing `deepseek/deepseek-v4-flash`.
- **Why:** in the Week 4 model check `gpt-5.6-luna` scored 19/20 and
  `deepseek-v4-flash` 13/20. Most of DeepSeek's misses were numbers it
  calculated itself (sums, medians, month-on-month changes, made-up
  thresholds), which breaks the core rule.
- Details: "Main model switched to openai/gpt-5.6-luna" above and
  `docs/model_comparison_week4.md`.

### Fallback stays openai/gpt-6-luna
- **Decision (project owner, 2026-10-21):** the fallback stays
  `openai/gpt-6-luna`. It scored 18/20, above the 17/20 bar.
- **Accepted risk:** the main and fallback models are both OpenAI models, so an
  OpenAI-wide outage is not covered (both are served by OpenAI, Azure and Amazon
  Bedrock, which helps only with single-provider outages). This is accepted for
  the proof of concept and will be revisited before Phase 2.

### LLM spend is measured from the key balance
- **Decision (2026-10-21):** spend is measured from the OpenRouter API key's
  credit balance, read before and after each eval or model-check run.
- **Why:** OpenRouter reported `usage.cost = 0` on every response, so the
  per-call cost cannot be trusted. Per-call `cost_usd` in `agent_log` and the
  eval output may therefore read 0 or be missing; the balance difference is
  the number to use.

### SQL guard blocks personal contact data
- **Decision (2026-10-21):** `src/agent/sql_guard.py` refuses any query that
  can return personal contact data, for `run_sql` and `make_chart`.
- **Blocked columns:** `customers.email`, which is the only contact column in the
  schema (the six tables have no phone or address columns).
- **Also refused when the query reads `customers`**, because each can return
  email without naming it: `SELECT *`, `c.*`, `COLUMNS(...)`, positional
  references (`#3`), the whole row as a value (`SELECT c FROM customers c`,
  `to_json(c)`) and renamed columns (`customers t(a, b, c)`). A CTE named
  `customers` counts as the table.
- **Not blocked:** `customers.name`. For wholesale customers it is a company
  name, and reports and "top customers" answers need it. Retail customer names
  are personal names; revisit this before real customer data arrives (Phase 2).
- The prompt's schema section no longer lists `email`. Error messages tell the
  model to list the columns it needs, so it can recover.

### Answer number guard on the chat agent
- **Decision (2026-10-21):** the chat agent's final answer gets the same number
  rule as report summaries. Every number must be one of these:
  - a number in a tool result for that question, exactly or rounded to fewer
    decimals, with the sign ignored;
  - a number the user wrote in the question (approved by the project owner);
  - part of a date (ISO date, "21 Sep 2026", "21-27 Sep", "September 2026",
    "Week 39", "in 2026");
  - a whole number from 1 to 10.
- **On failure:** the model gets one retry with the failing numbers named. If
  that fails too, the written answer is withheld. The user sees a note and the
  tool results as plain tables.
- **Logging:** `agent_log.answer_guard` (`ok`, `retried_ok`, `withheld` or
  `not_run`) and `agent_log.answer_guard_failed`. The columns are added to an
  existing `audit.duckdb` on first use. The eval output records the same.
- **Code:** `src/agent/answer_guard.py`. SQL in fenced code blocks is not
  checked, because the agent shows the query it ran.
- **Known limit:** like the summary guard, it checks values, not direction or
  meaning. A copied number attached to the wrong KPI passes.

### Relative periods mean the latest complete period
- **Decision (project owner, 2026-10-21):** "last month", "this week", "yesterday"
  and similar phrases are measured from `DATA_END_DATE` and mean the latest
  complete period. With 2026-09-30, last month and this month are September
  2026, and last week and this week are 21 to 27 Sep 2026.
- The system prompt states these periods as dates, worked out in Python from
  the data clock, so the model does no date arithmetic. Eval q20 ("How did we do
  last month?") now accepts September 2026 only, and the answer must say which
  month it used.

### Percentages are rounded once, at display
- **Decision (2026-10-21):** rates, shares, margins and percentage changes are
  calculated from unrounded values (as before, in `src/analytics/kpis.py`). They
  are now also stored unrounded in `Report.numbers`, and rounded once, when
  shown. This applies to HTML, plain-text email, Excel number formats, chart
  labels, summary lines and `get_kpis`' shown values. Money is still stored at
  2 decimals.
- **Why:** `builder.rounded()` stored changes at 2 decimals and rates at 4, and
  display rounded again. So August 2026 net revenue vs August 2025 (+8.6495%)
  showed +8.7%.
- **Effect:** 12 displayed figures in the weekly review (2026-09-27) and 7 in
  the monthly review (2026-08-31) move by 0.1. Apparel vs the previous week is
  now -25.3% (was -25.4%). The daily snapshot is unchanged. The hand-checked
  figures (114,286.82, 661, 172.90, 43.1%, Electronics 31,481.36, Stokes,
  Robinson and Gough 2,767.45) are unchanged.
- **Known limit (money, not changed):** money is rounded by Python, which works on
  the binary value. August 2026 net revenue is 462,431.555 before rounding. The
  report shows 462,431.55, while SQL `ROUND` in the eval gold gives 462,431.56.
  The eval grader's one-unit tolerance covers it. Switching to decimal half-up
  rounding is a possible later change.

### Week 5: forecast accuracy uses the get_forecast demand definition
- **Note for Week 5 (2026-10-21):** forecast accuracy (WAPE and bias) must use
  the same demand definition as `get_forecast`: units on all non-cancelled
  orders (completed or returned), per Monday-to-Sunday week. It must not use
  the "Units sold" KPI, which counts completed orders only.

### Cut-off report summaries are retried, then dropped
- **Problem (2026-10-21):** the August 2026 monthly summary was cut off
  mid-sentence ("Returns rose 42.7% vs"), but was still marked `ok`. All its
  numbers were real, so the number guard passed it, and the code did not check
  why the model stopped.
- **Cause:** `openai/gpt-5.6-luna` is a reasoning model, and its hidden reasoning
  counts against `max_tokens`. On the rebuild the summary used 602 output tokens,
  434 of them reasoning, so the old cap of 400 could not fit a complete summary.
- **Decision:**
  - `src/agent/llm.py` records `finish_reason` and reasoning tokens on every
    result.
  - A summary with `finish_reason` `length` counts as a failed attempt even if
    its numbers pass. The model gets one retry, asking for a complete, shorter
    summary.
  - A second cut-off drops the summary with the new `summary_status` value
    `truncated`, and the report sends with the placeholder.
  - `MAX_SUMMARY_TOKENS` rises from 400 to 2000.
- **Check:** the monthly report rebuilt once with summaries on finished with
  `stop` at 602 tokens, and the summary is `ok` and complete.
- **Not changed:** the chat agent sets no `max_tokens`, so its answers are not
  cut by our own cap. Its `finish_reason` is recorded but not acted on.

## Week 5: demand planning

### main is the only base branch; dev is dropped
- **Decision (project owner, 2026-10-21):** every week branches from `main` and
  merges back into `main` through a PR with tests. There will be no `dev` branch.
  This replaces the "if a `dev` branch is created later" note in the Week 4 entry
  "Base branch is `main` (no `dev` yet)".
- **Why:** there is one developer, every week still goes through a reviewed PR with
  the full test suite, and the dev/main split caused confusion in Weeks 3 and 4.

### Money is rounded half-up, once, from the exact sum
- **Decision (2026-10-21):** closes the Week 4 wrap-up "Known limit (money)". Money
  is still summed in SQL from unrounded line values (DuckDB DECIMAL, exact) and
  rounded once when it enters the Report. `builder.round_money` now rounds the exact
  decimal half-up, like SQL `ROUND`, instead of Python's `round()` on the float.
- **Cause:** pandas returns the DECIMAL sum as a float. August 2026 net revenue is
  exactly 462,431.555, but the float is just below it, so `round()` gave 462,431.55.
  `str()` of the float gives back the exact decimal, because these sums have at most
  15 significant digits.
- **Effect:** August 2026 net revenue shows 462,431.56, matching SQL. The Week 2
  hand-checked figures are unchanged (covered by a test).

### Eval grader skips dates like the answer guard
- **Decision (2026-10-21):** the grader's "no unsupported numbers" check blanks
  dates with `answer_guard.without_dates_and_code`, so "1–31 May" or "July 1-31" no
  longer sends a q12-style answer to review. This closes the Week 4 "Grader limit".

### Demand data layer and data/planning.duckdb
- **Decision (2026-10-21):** demand-planning results go in `data/planning.duckdb`,
  next to the sales database it was computed from (tests write it next to their
  temporary sales database). The schema is created by `src/demand/store.py`.
  `sales.duckdb` stays read-only, and `run_sql` still sees only the six tables.
- **One snapshot per as_of:** every table has an `as_of` column. A rerun replaces
  all of that as_of's rows in one transaction. A snapshot whose pre-build
  recommendations were approved or rejected is only replaced with `--force`
  (project owner, 2026-10-21), and `prebuild_decisions` is never deleted.
- **Tables beyond the eight in the brief:**
  - `demand_weekly`: the weekly series with stockout and imputed flags, so reports,
    tests and the agent read the same numbers the forecasts used.
  - `customer_rfm`: the RFM scores now and 28 days earlier, needed for segment moves.
  - `planner_review`: products held back from pre-build because of forecast bias.
  - `prebuild_decisions`: the append-only approval history.
- **Demand series:** units on non-cancelled orders per Monday-to-Sunday week (the
  `get_forecast` definition), complete weeks only (the week's Sunday is on or before
  as_of). It starts at the later of the product's launch week and the first full data
  week (2024-10-07; the data starts on Tuesday 2024-10-01).
- **Stockout week:** the week's Monday inventory snapshot shows `on_hand = 0`
  (inventory snapshots are taken on Mondays).
- **Lead time:** `products.lead_time_days` exists, so it is used (weeks = days / 7).
  The 4-week default in `config/demand.yaml` applies only to a missing value.
- **Stock position:** on hand and on order come from the latest inventory snapshot
  on or before as_of. A product with no row in that snapshot (discontinued) has no
  stock position, so it gets no stock alert and no pre-build recommendation.

### Demand alert rules decided in Week 5
- **Decision (2026-10-21):** rules the brief left open, as built in
  `src/analytics/customers.py` and `src/analytics/demand.py`:
  - **Revenue** in customer and category alerts is net revenue (completed orders),
    the KPI definition, summed exactly in SQL. **Orders** and order dates use all
    non-cancelled orders (the demand definition).
  - **Windows** are inclusive and end on as_of: the last 365 days is
    as_of - 364 to as_of; the last 90 days is as_of - 89 to as_of, and the prior 90
    days are the 90 days before that.
  - **Overdue:** the usual interval is the median gap between distinct order dates
    in the window (two orders on one day count as one date). Serious and overdue
    customers are ranked together by last-365-day revenue.
  - **Declining** customers are ranked by revenue lost (prior minus recent).
  - **New high-value:** the bar is the 90th percentile of last-90-day revenue over
    all customers with revenue in that window. A new customer at or above the bar is
    flagged.
  - **Category alerts:** the last 8 complete weeks are the 8 Monday-to-Sunday weeks
    ending on or before as_of (to 2026-09-27 for both 09-27 and 09-30). All
    categories are stored, with `flagged` true for a drop greater than 15%.
  - **Stock alerts** are ranked by stockout weeks (most first), then days of cover
    (lowest first). Days of cover is n/a when the last 4 weeks are all stockout
    weeks or have no demand.
- **RFM segment map:** R, F and M are scored 1 to 5 by percentile rank (ties share
  a score) over customers with a non-cancelled order in the 365 days to the scoring
  date. R = days since the last order (fewer is better), F = orders in the window,
  M = net revenue in the window. First match wins:

  | Segment | Rule |
  | --- | --- |
  | Champions | R >= 4, F >= 4 and M >= 4 |
  | Loyal | R >= 3 and F >= 3 |
  | Promising | R >= 4 |
  | Needs attention | R = 3 |
  | At risk | F >= 3 (R is 1 or 2) |
  | Lapsed | everyone else (R is 1 or 2, F is 1 or 2) |

  The top segments are Champions and Loyal; the at-risk ones are At risk and Lapsed.
  A customer in a top segment 28 days earlier with no order in the window now
  counts as Lapsed.
- **Alert counts on the seed-42 data (as of 2026-09-30):** overdue 609 (482
  serious, 127 overdue; 603 of them retail), declining 158, new high-value 1,
  segment moves 197, category 1 (Apparel), stock 10. The overdue list is long
  because many retail customers order every 2 to 3 days, so 5 days without an order
  is already over 2x their interval. This is reported to the project owner and not
  changed; reports show the top 10 by revenue.

### Forecast accuracy of the seeded forecasts
- **Decision (2026-10-21):** accuracy uses the `forecasts` rows with model `seeded`
  whose week is complete on as_of and that were created on or before it. Actuals
  are the demand series (the `get_forecast` definition), so a stockout week counts
  as 0 actual units.
- Category and overall rows are totals (all product-weeks summed, then divided) and
  are never flagged. WAPE and bias are stored as unrounded fractions.
- **Result on the seed-42 data (as of 2026-09-27 and 2026-09-30, the same 8 weeks):**
  overall WAPE 16.9%, bias +0.6%. 24 products are flagged: 24 for bias, 3 also for
  WAPE. 18 products are under 20 units and not flagged. AP-1150 Classic Denim Jacket
  has bias +27.3%. 8 of the 10 stockout products are among the flagged, because the
  seeded forecast did not know about the stockout and actual demand was 0 in those
  weeks. Being flagged for bias keeps them out of pre-build ("needs planner review").

### Our own forecasts and backtest
- **Decision (2026-10-21):** `src/analytics/forecasting.py` fits statsforecast AutoETS
  and SeasonalNaive (season length 52), weekly, 12 weeks ahead, on every product with
  at least 26 weeks of history. Rules decided in the build:
  - **Fallback (project owner, 2026-10-21):** products with fewer than 26 weeks (3 on
    the seed data, including SB-1042 Smart Bottle Pro) get the mean of their last 4
    non-stockout weeks, held flat, stored as model `recent_mean` with
    `is_fallback = true`.
  - Products with fewer than 52 weeks get no SeasonalNaive forecast (it needs a full
    season), so AutoETS is used. AutoETS treats a season of 52 as non-seasonal
    (statsforecast handles seasons up to 24).
  - Forecasts are clipped at 0, because a falling ETS trend can go negative.
  - **Backtest windows:** 6 windows of 4 weeks, every 4 weeks, with the newest
    ending on the last complete week, so all of them lie inside the last 26 weeks
    ((26 - 4) / 4 + 1 = 6). A product is skipped in a window with fewer than 13
    training weeks. Stockout weeks are imputed for fitting and **not scored**,
    because they have no real demand to compare with.
  - The model with the lower backtest WAPE is chosen per product; AutoETS on a tie
    or when SeasonalNaive has no score.
  - statsforecast runs in one process (`n_jobs=1`). A process pool crashed under
    macOS spawn, and reports build inside scheduler threads.
  - Fits are cached in the process by series content, so as_of dates with the same
    complete weeks (2026-09-27 and 2026-09-30) fit once.
- **Result on the seed-42 data (as of 2026-09-30):** backtest WAPE, on the 242
  products scored by both models: AutoETS 40.5%, SeasonalNaive 53.7%; chosen mix
  40.3%. Chosen: AutoETS 226, SeasonalNaive 21, recent_mean 3. A full
  `run_demand` takes about 98 seconds, almost all of it statsforecast.

### Pre-build recommendations and approval
- **Decision (2026-10-21):** `src/analytics/prebuild.py` follows the brief's formula
  with our own chosen forecast (Step 5) and the latest stock snapshot on or before
  as_of. Rules decided in the build:
  - **Lead time** in weeks is `lead_time_days / 7`. A part week counts in proportion
    (30 days = 4 full forecast weeks plus 2/7 of the fifth).
  - **Safety stock sd** is the sample sd (n - 1) of weekly units over the last 26
    weeks with stockout weeks left out. It is 0 when fewer than 2 weeks remain.
  - **Rounding:** the formula result is stored unrounded (`prebuild_raw`), and the
    recommended units are that result rounded up to a whole unit.
  - **Planner review:** every product flagged for bias in forecast accuracy goes
    to `planner_review`, whatever its pre-build result, and gets no recommendation.
  - **Order:** by days of cover (on hand / average daily demand of the last 4
    non-stockout weeks), lowest first; unknown cover last; ties by SKU.
  - **Id:** `YYYYMMDD-SKU` (for example `20260930-SB-1042`), so a rerun keeps the id.
- **Approval:** `python -m src.demand.approve --id <id> --approve|--reject --by
  <name> [--note]`. Only a pending recommendation can be decided, once. The status
  update and the `prebuild_decisions` row are written in one transaction. No code
  in `src/agent`, `src/reports` or `src/app` imports the approval module (a test
  checks this).
- **Result on the seed-42 data:** 122 recommendations and 24 products for planner
  review as of 2026-09-30; 96 and 24 as of 2026-09-27. SB-1042 Smart Bottle Pro:
  327 units, 9.1 days of cover, **ranked 5th** as of 2026-09-30. Four smaller
  products have less cover (AP-1007 5.0, AP-1002 5.3, AP-1003 7.5 and BE-1015 8.1
  days). This is reported to the project owner at the acceptance step and no rule
  is changed.

### Demand sections in the reports
- **Decision (2026-10-21):** `src/reports/demand_sections.py` builds the sections from
  the snapshot for the report's own as_of (`get_or_run`: reused if stored, otherwise
  computed, which adds about 100 seconds to that report). The scheduler is unchanged.
  - **Weekly and monthly, "Demand alerts":** overdue customers (serious and overdue
    together, by 365-day revenue), declining customers, flagged categories, stock
    alerts, forecast-accuracy flags (largest bias first), pending pre-build
    recommendations (by days of cover) and the planner-review list. The email
    shows 10 rows per list and "and N more". The "Demand alerts" Excel sheet has
    every row of every list in one table, with a List column, plus the new
    high-value and segment-change lists, which are not in the email.
  - **Monthly, "Forecast accuracy":** overall, by category and flagged products in
    the email; the Excel sheet adds every product.
  - The daily snapshot has no demand sections.
  - **Summaries:** each list's count and first 3 rows are added to the summary
    input as text lines. The number guard is unchanged; it now also accepts the
    numbers in those lines.
  - The template gained one optional element, a muted footer line under a table
    (the "and N more").
- **Test time:** the report and demand tests fit forecasts for 2026-08-31 and
  2026-09-27/30 (about 100 seconds each), so `pytest` now takes about 4 minutes.

### Demand-planning agent tools
- **Decision (2026-10-21):** `get_customer_alerts`, `get_forecast_accuracy` and
  `get_prebuild_recommendations` read the latest snapshot with as_of on or before
  `DATA_END_DATE`. With none they return `status: not_computed` and the command to
  run; they never compute (a question has 120 seconds; a run takes about 100).
- **Arguments differ from the Week 4 stubs** (the stubs' region, segment, weeks and
  "proposed" are gone), following the Week 5 brief:
  - customer alerts: `alert_type`, `severity`, `limit`;
  - forecast accuracy: `sku_or_name` or `category`;
  - pre-build: `status` (pending, approved or rejected) and `limit`.
- Every number is returned with a `_shown` text for the answer guard. The tools
  return no email address, and they have no way to change a status.
- **Eval:** 5 demand questions (q21 to q25) use `gold_planning_sql`, run read-only
  by `scripts/fill_gold.py` on the 2026-09-30 snapshot in `planning.duckdb`.

### Overdue and segment-move alerts: interval floor and retail filter
- **Decision (project owner, 2026-10-21):** two rules cut the noise in the overdue
  and segment-move lists. The declining rule is unchanged.
  - **The usual interval has a floor of 7 days** (`min_usual_interval_days`). A
    customer who orders every 2 days is not overdue after 5 days without an order.
  - **Retail customers count only if they are in the top 20% of all customers by
    365-day revenue** (`retail_top_share`; the bar is the 80th percentile over
    customers with revenue in the window). Wholesale customers always count.
    This applies to overdue and segment-move alerts only.
- **Why:** with the Week 5 first version, overdue had 609 customers (482 serious)
  as of 2026-09-30, almost all retail daily buyers, so the list was too long to act on.
- **New counts:**

  | As of | Overdue (serious + overdue) | Serious: retail / wholesale | Overdue: retail / wholesale | Segment moves: retail / wholesale |
  | --- | --- | --- | --- | --- |
  | 2026-09-27 | 62 | 32 / 5 | 24 / 1 | 9 / 14 |
  | 2026-09-30 | 58 | 36 / 5 | 16 / 1 | 7 / 18 |

  Harbor & Pine Co., Kestrel Home Stores and Bluegate Supplies are serious and
  rank 1 to 3 on both dates.

### Forecast accuracy leaves out stockout weeks
- **Decision (project owner, 2026-10-21):** a product's stockout weeks are left out of
  its WAPE and bias, and of the category and overall totals. This is the same
  treatment as the Step 5 backtest. `weeks` is the number of weeks measured. A product
  under 20 actual units after the exclusion is stored but not flagged.
- **Why:** sales in a stockout week are not real demand. Counting them as 0 actual
  units flagged 8 of the 10 stockout products for bias, which kept them out of
  pre-build exactly when they need stock.
- **Result (2026-09-27 and 2026-09-30):** 17 products flagged, all for bias (was 24).
  Overall WAPE is 15.9%, bias -0.4%. AP-1150 Classic Denim Jacket is still +27.3%.
  9 of the 10 stockout products are no longer flagged and are back in the pre-build
  calculation. HK-1011 Ultra Mixing Bowls is still flagged: its bias over its 7
  non-stockout weeks is +16.3%.
- This replaces the result paragraph in "Forecast accuracy of the seeded forecasts".

### Pre-build ranking: urgent first, then value at risk
- **Decision (project owner, 2026-10-21):** this replaces the "Order: by days of cover"
  rule. Pre-build recommendations are ranked in two groups:
  1. **urgent:** days of cover below the lead time in days (stock runs out before
     a new order could arrive);
  2. **normal:** everything else, including unknown cover.

  Within each group they are ranked by **value at risk** = pre-build units x unit
  price, largest first; ties by SKU. `value_at_risk`, `unit_price` and `priority`
  are stored on `prebuild_recommendations` (added to an existing planning.duckdb on
  first use) and shown in the reports.
- **Why:** a planner prioritises by urgency, then by money at stake, not by days of
  cover alone. Days of cover alone put four small products (10 to 55 units) ahead of
  SB-1042 Smart Bottle Pro (327 units).
- **Result as of 2026-09-30:** 129 recommendations (72 urgent, 57 normal). The top 5:
  SB-1042 (11,441.73), EL-1028 (6,613.65), AP-1004 (3,704.05), EL-1024 (3,179.70),
  TO-1005 (2,849.50), all urgent.

### Email pre-build table: 5 columns
- **Decision (project owner, 2026-10-21):** the email's pre-build table shows only
  product, days of cover, pre-build units, value at risk and reason (urgent or
  normal), so it fits a phone screen. The "Demand alerts" Excel sheet keeps every
  column, including the id the approval command needs.

### Slow tests are skipped by default
- **Decision (project owner, 2026-10-21):** tests that need the real statsforecast fit
  (the seed-data forecast and backtest checks, the gold-value check, which q24
  depends on, and every Week 5 acceptance test) are marked `slow`.
  - `pytest` skips them.
  - `pytest -m slow` runs only them; `pytest --runslow` runs everything.
  - The marker is registered in `pyproject.toml`; the skip logic is in
    `tests/conftest.py`.
- **How:** default tests replace the fit with a recent-mean stand-in (session fixture
  `fast_forecasts`). Slow tests use `real_demand_runs`, which runs the real fit on its
  own copy of the sales database, so fast and real snapshots never share a
  `planning.duckdb`. The forecasting unit test fits a 3-product subset for real
  (3.4 s), so the default run still exercises statsforecast.
- **Times (2026-10-21):** default `pytest` about 39 s (443 passed, 12 skipped);
  `pytest -m slow` about 101 s (12 tests).

### Two changes from the approved Week 5 plan
- **Code layout (2026-10-21):** the approved plan put the calculations in new files
  under `src/demand/`. They live in `src/analytics/` instead, filling the empty Week 1
  files the folder map already reserved:
  - `customers.py`: customer alerts and RFM;
  - `forecasting.py`: accuracy, forecasts and backtest;
  - `prebuild.py`: pre-build;
  - `demand.py` (new): the demand series, stock position, category and stock alerts.

  `src/demand/` holds only the store (`store.py`), the CLIs (`run.py`, `approve.py`)
  and the config loader (`config.py`).
- **Single process (2026-10-21):** forecasting runs in one process (`n_jobs=1`)
  because the statsforecast process pool crashed on macOS. A full `run_demand` takes
  about 98 seconds.

### Week 6 demo uses the September monthly business review
- **Decision (project owner, 2026-10-21):** the Week 6 demo uses the September 2026
  monthly business review (as of 2026-09-30). It is the one report that shows all 5
  planted problems:
  - the 3 churned wholesale customers, serious overdue and ranked 1 to 3;
  - the Apparel decline;
  - the 10 stockout products;
  - the AP-1150 bias;
  - the SB-1042 Smart Bottle Pro pre-build, ranked 1st.
- **Why not the weekly review (as of 2026-09-27):** it cannot show the SB-1042
  pre-build. The latest inventory snapshot on or before 27 Sep is the 21 Sep one
  (329 on hand, 202 on order), which still covers the product's 4-week lead time.
  The low stock (123 on hand, none on order) appears only in the 28 Sep snapshot.

## Week 6: agent fixes, backfill, dashboard

### revenue_lost is stored with declining alerts
- **Decision (2026-10-22):** `declining_alerts` already ranked by revenue lost
  (prior 90 days minus last 90 days) but dropped the column. It is now kept and
  stored as `customer_alerts.revenue_lost`, added to existing files by a
  `MIGRATIONS` entry. The 2026-09-27 and 2026-09-30 snapshots were recomputed.
  None of their recommendations had been decided, so no `--force` was needed.
- **Why:** the rank and the number the agent quotes come from one calculation. The
  alternative, the tool subtracting the stored revenues, would have computed the
  same figure in a second place.
- `get_customer_alerts` returns `revenue_lost` and `revenue_lost_shown`, rounded
  once like the other money fields. Older snapshots show it as empty until rerun.

### Demand-tool lists are capped at 10 rows
- **Decision (2026-10-22):** every list from `get_customer_alerts` (one per alert
  type), `get_forecast_accuracy` (`overall`, `flagged_products`, `by_category`) and
  `get_prebuild_recommendations` (`recommendations`, `needs_planner_review`) has at
  most 10 rows (`DEMAND_LIST_CAP`), plus `total_count` and `more_count`. The `limit`
  argument (default 10, max 50) was removed, so the model cannot ask for more.
- The tool descriptions tell the model to say "and N more" and that the full list is
  in the dashboard and the report Excel. `run_sql` keeps its 500-row limit.
- `get_forecast_accuracy` lost `flagged_count`; it is now
  `flagged_products.total_count`.

### Ranked questions are graded in order
- **Decision (2026-10-22):** a grade spec can say `ordered: true`. The gold names the
  answer mentions must then appear in gold order (first mention counts). A missing
  name is left to its own name check, so it is not counted twice.
- Set on q06, q08, q10, q11, q15, q23, q24 and q25, the questions that ask for "top",
  "most", "highest", "lowest" or "first". It only bites where there are 2 or more
  names: q08, q10, q11, q24, q25.
- q21's gold is now the capped list: 41 serious customers, the first 10 by rank and
  `more_customers` 31. q25's gold SQL orders by `revenue_lost DESC` (the same order
  as the rank). Gold values were regenerated with `scripts/fill_gold.py`.
- Live eval: 25/25 on `openai/gpt-5.6-luna` with fallback off
  (`docs/eval_results_week6.md`).

### Tests never load .env
- **Decision (2026-10-22):** a test that calls a CLI `main()` must replace
  `load_dotenv` first. The autouse OpenRouter block is function-scoped, and a
  module-scoped fixture (the reports in `test_reports.py`) is built before it
  applies. Once real keys are in `os.environ`, that fixture calls the model.
- **What happened:** a first draft of the backfill test did this and spent $0.0083
  (three report summaries). It was fixed before any commit, and a full run afterwards
  left the key balance unchanged.

### Backfill instead of two live weeks of scheduler runs
- **Decision (project owner, 2026-10-22):** `python -m src.reports.backfill --from
  2026-09-14 --to 2026-10-01` builds every report the scheduler would have sent on
  those run dates. 1 Oct is included for the September monthly review.
- **Same rules as the scheduler:**
  - Run dates come from the scheduler's own `job_specs` CronTriggers
    (`get_next_fire_time`, in `TIMEZONE`), and each as_of from
    `data_clock.reporting_as_of` with that run date as "today".
  - Nothing is hard-coded. A test moves the weekly review to Tuesday and the plan
    follows.
  - Result: 14 daily snapshots, 3 weekly reviews (as of 13, 20 and 27 Sep) and the
    September monthly review. As in the scheduler, Saturdays have no daily snapshot,
    because the Monday run reports Sunday.
- **Always a dry run:** the backfill calls only `scheduler.build_once` with
  `mode="dry_run"`. It never calls `deliver_once`, `send_report` or
  `audit.record_run`, so `report_runs` stays the record of real runs.
- **Output:**
  - The reports go to `outputs/backfill/<run_date>/<report>_<as_of>/`, in the normal
    dry-run format.
  - `manifest.csv` has `run_date, report, as_of, period, status, summary_status,
    summary_cost_usd, output_folder, error`. `summary_cost_usd` is an extra column;
    OpenRouter reports $0, so it reads 0.0 and spend is measured from the key balance.
  - `index.html` links every report.
  - One failed report is recorded and the rest still run.
- **Demand snapshots:** the weekly reports created the missing 2026-09-13 and
  2026-09-20 snapshots through the builder's existing `get_or_run`.
- `Report` now carries `summary_cost_usd`. It was calculated before but dropped.
- **Run (2026-10-22):** 18 of 18 ok, every summary `ok`, 4 min 22 s, $0.0079 by the
  key balance.

### Dashboard: src/ui, read-only, numbers from src/
- **Path (2026-10-22):** the dashboard is `src/ui/app.py` (`streamlit run
  src/ui/app.py`), as the Week 6 brief asked. The one-line stub
  `src/app/streamlit_app.py` was removed. `docs/project_plan.md` still names the
  old path; it is read-only, so this entry records the change.
- **Pages live in `src/ui/app_pages/`, not `pages/`.** Streamlit treats a `pages/`
  folder next to the main script as old-style auto pages. On a rerun it then ran a
  page without `app.py`, so the sidebar and shared as_of were missing.
- **No numbers in the UI:**
  - The pages call `src/reports/views.py` (sales and demand) and
    `src/reports/saved.py` (saved reports). They have no SQL and open no database;
    a guardrail test checks this.
  - `views` reuses the builder's sections and comparisons and returns tables rounded
    once for display: money half-up to cents, rates as percentages to 1 decimal.
    Tables stay sortable because the values are numbers, not text.
- **Sales periods:** the Sales page shows the latest complete day, week or month
  ending on or before the sidebar date (the data clock's rule), with the matching
  report's `compare_to`.
- **Demand page:**
  - It needs a snapshot for exactly the sidebar date. If there is none, it shows the
    `python -m src.demand.run --as-of ...` command and has no button to create one.
  - Lists are full, with no 10-row cap. Declining customers show revenue lost.
- **Approvals:**
  - Each Approve or Reject needs a second Confirm click. "Decided by" is required
    and kept for the session; the note is optional.
  - Every decision goes through `approve.decide()` with the dashboard's
    `planning.duckdb` path, exactly as the CLI does.
  - `ApprovalError`, `PlanningStoreError` and any other write failure (such as a
    locked file) are shown as an error. After a decision the read cache is cleared.
  - `tests/test_demand.py`'s approval-import check now covers `src/agent` and
    `src/reports`. The guardrail tests cover `src/ui`: only the Demand page may call
    `decide()`.
- **Chat page:**
  - It calls `agent.ask`, the same function the CLI uses; no refactor was needed,
    since the CLI's `main()` only parses arguments.
  - Each question is single-turn and history is display only.
  - Answers are measured from `DATA_END_DATE`, not the sidebar date, as in the
    CLI and the eval.
  - The details expander shows the tool calls and results, and the CLI footer plus
    tokens.
- **Reports page:**
  - It lists `outputs/reports` folders and the backfill manifest.
  - The saved HTML is shown with `st.html`, after `saved.inline_images` swaps
    each chart for a data URI. `st.html` sanitizes the HTML.
  - `st.iframe` (the replacement for the deprecated `components.html`) runs the page
    with access to the app, and the report includes LLM-written summary text.
- **Short-lived reads:** every read opens DuckDB read-only and closes it, and is
  cached with `st.cache_data` per as_of and file path. The CLIs can still write
  while the dashboard is open.

### Week 6 guardrail tests
- **Decision (2026-10-22):** `tests/test_guardrails.py` also checks:
  - `src/ui` uses no `openai`, `duckdb`, `connect` or `read_only`, so it opens no
    database itself;
  - `src/ui` has no SQL in any string;
  - only `approve.py` writes a pre-build status (`UPDATE prebuild_recommendations`,
    `INSERT INTO prebuild_decisions`), and only `approve.py` and the Demand page use
    `decide`;
  - importing the agent, its tools, the chat CLI and `src.ui.common` in a fresh
    process never loads `src.demand.approve`, and no file in `src/agent` or the chat
    page names it;
  - `backfill.py` uses none of `send_report`, `deliver_once`, `notify_failure`,
    `run_report`, `record_run`, `emailer` or `audit`;
  - every argument combination of the three demand tools returns at most 10 rows per
    list. The fixture must have at least one longer list, so the cap is really
    exercised.
- The name checks use the code's AST (names, attributes, imports), so comments and
  docstrings that mention these words do not trip them.

### The summary guard rejects refusals and number-free summaries
- **Finding (2026-10-22):** 6 of the 14 backfilled daily snapshots had the summary
  "I'm sorry, but I cannot assist with that request.": those as of 13, 17, 20, 22,
  27 and 28 Sep. Their `summary_status` was `ok`, because the number guard only
  checks numbers and a refusal has none. The weekly and monthly summaries were
  normal.
- **Decision (project owner, 2026-10-22):** `summary.not_a_summary()` rejects two
  kinds of reply:
  - a refusal (narrow `REFUSAL` pattern: "I'm sorry", "I cannot assist/help",
    "I am unable to", "as an AI");
  - a reply that uses no number from the input. The 1 to 10 count allowance does
    not count as using one.

  Either gets the one retry, with `NOT_A_SUMMARY_PROMPT`. If the retry fails too,
  the summary is dropped: status `guard_failed`, the placeholder in the email, and
  the report still sends.
- Ordinary wording such as "unable to compare" is not a refusal (tested).
- Why the daily input draws refusals is not yet known; the guard stops them
  reaching an email either way.
- **Live check (2026-10-22):** I reran the backfill for 14 Sep to 1 Oct. All 18
  reports built and all 18 summaries ended `ok`:
  - 12 passed on the first attempt.
  - 6 were refusals on the first attempt and passed on the retry. They were the
    same six dailies as before (as of 13, 17, 20, 22, 27 and 28 Sep).
  - None was dropped.

  It made 24 model calls and cost $0.0074 by the key balance (9.8282 to 9.8208).
  No backfilled report now contains a refusal.
- The Reports page lists backfilled reports newest first, so it opens on the
  September monthly review.

### Dashboard redesign (Week 6 follow-up)
- **Decision (project owner, 2026-10-22):** a modern look for the whole dashboard,
  with the chat first. It uses native Streamlit and CSS only, with no new packages.
- **Progress callback:**
  - `agent.ask(..., on_progress=None)` calls back with a `ProgressEvent` for each
    model call (`thinking` / `thinking_done`, with its duration), tool call
    (`tool_start` / `tool_done`, with arguments and result), `fallback` (seen after
    the call returns), `guard_retry` and `withheld`.
  - A failing callback is logged and ignored.
  - Steps are timed with `time.monotonic`, so `config.clock` and the 120-second
    limit work exactly as before.
  - The CLI passes no callback.
- **Never stream unchecked text:**
  - Events carry no answer text. The page gets the answer only from `ask()`'s return
    value, after the guard, and only then reveals it word by word with
    `st.write_stream`.
  - A withheld answer is shown as a warning plus the tool results, without the
    typewriter effect.
- **Chat date:** the Chat page hides the sidebar date picker and shows "Answers use
  data up to <DATA_END_DATE>". The picked date is kept in session state, not in the
  widget, so it survives a visit to Chat.
- **Theme and styling:**
  - `.streamlit/config.toml` has one indigo accent (`#4F46E5`, `#818CF8` in dark
    mode), a system font stack (nothing is downloaded) and rounded corners, with
    matching light and dark themes.
  - `src/ui/style.py` adds the CSS for the chat bubbles, the pill input, cards and
    tags. It uses semi-transparent colours, so one CSS block works in both modes.
  - The spinner keyframes use `0%` / `100%`, because the word `from` would trip the
    guardrail test that looks for SQL in `src/ui`.
- **Numbers stay outside the UI:**
  - The Demand tab badges come from a new `counts` entry in `views.demand_view`.
  - Report cards read `period` and `summary_status` from the backfill manifest
    (`saved.SavedReport`).
  - KPI cards only lay out the text `views.kpi_cards` already formatted, with an
    arrow from the change's sign and green or red from the report's `tone`.
- **Tags in sortable tables:** severity, priority, status and "flagged" show as
  coloured emoji text. That keeps `st.dataframe` sorting; a pandas Styler would turn
  the numbers into text.
- **Checked live (2026-10-22):** I asked the 4 example questions in the browser.
  They found the churned wholesale customers, the September stockouts, AP-1150 at
  +27.3% bias, and SB-1042 at 327 units. Each passed the guard on the first
  attempt. No page scrolls horizontally at 375 px width.

### Week 6 polish: look, chat steps, input lock, rounding (2026-10-22)
- **UI stack (project owner):** the v1 dashboard stays Streamlit. React with FastAPI
  comes in Phase 3. No new packages and no Streamlit version change for this polish.
- **Accent:** a blue-to-cyan gradient (`#2563EB` to `#06B6D4`) replaces the indigo
  accent of the redesign above.
  - `primaryColor` is `#2563EB` in light mode and `#3B82F6` in dark mode, where it
    reads better. The gradient is the same in both.
  - The gradient is used in four places only: the logo mark, the active sidebar
    item, user chat bubbles and the send button. Never on cards, headings, text or
    page backgrounds.
  - Green and red stay only for up/down comparisons on the KPI cards. Severity and
    priority tags keep their meaning colours. Email charts and report HTML are
    unchanged; the restyle is UI only.
  - `toolbarMode = "minimal"` hides the developer toolbar and the Deploy button.
- **Custom HTML and CSS for the chat steps and the sidebar:**
  - The tool steps are our own HTML (`src/ui/chat_steps.py`) instead of
    `st.status`, which drew spinners and could not show muted, borderless lines. One
    `STEP_LABELS` mapping covers the 7 tools, present tense while running and past
    tense when done.
  - The finished steps collapse to one `<details>` line ("Checked forecast accuracy ·
    7.3 s" or "3 steps · 9.1 s"). Opened, each step shows its duration, the SQL for
    `run_sql`, and a nested "Data" view of the escaped tool result. That view is the
    proof in the demo that the numbers come from SQL or Python.
  - The sidebar is built in `app.py` (`st.navigation(position="hidden")` plus
    `st.page_link`), because Streamlit's own menu cannot have a brand block above it.
    It is deep navy (`#0B1220`) in both modes, with status chips for the data end and
    the main model.
  - `st.html` strips inline `<svg>` and SVG data URIs, so the sidebar logo mark is
    drawn in CSS; `src/ui/logo.svg` is the same mark as a file, used as the assistant
    avatar.
- **CSS rules:**
  - All dashboard CSS is in one file, `src/ui/style.css`, loaded once from `app.py`.
  - It hooks into `data-testid` attributes, not Streamlit's generated class names.
    One exception: the active menu item is found by the `st-key-nav-active-...` class
    that its container's key creates, because Streamlit marks the active link only
    with a generated class.
  - `style.css` must not contain `<`, even in a comment: Streamlit then drops the
    whole stylesheet. A test checks this.
- **Escaping:** every dynamic string placed into HTML (the question, labels, notes,
  SQL, tool results, the model name) goes through `html.escape`. Tests check that a
  question with `<script>` or `<img onerror=...>` is shown as text.
- **Input lock:** the chat input is disabled while a question is answered. The lock
  is released in a `finally` block, so an exception or a timeout never leaves it
  disabled. `LLMUnavailableError` shows "The AI service isn't responding right now.
  Please try again in a minute."; other errors show a generic message.
- **Meta line:** the cost is left out when it is missing or zero (OpenRouter reports
  $0; spend is measured from the key balance).
- **Exception to "the UI computes no numbers":** the UI may count rows it displays
  (`len`, and the rows it hides, as in "3 steps" or "… 10 more rows" in the Data
  view). It never calculates business figures. Step durations come from the agent's
  progress events and the total from `latency_seconds`.
- **Forecast units to one decimal (display only):**
  - Finding: the most-biased answer paired 500 forecast units with +27.3% bias for
    AP-1150. The stored forecast is 500.1 against 393 actual units, so the bias
    (+27.25%) was right; `get_forecast_accuracy` showed the forecast with `fmt_count`,
    with no decimals.
  - The chat tool (`forecast_units_shown`) and the dashboard's Demand page
    forecast-accuracy table now show forecast units with one decimal. Actual units
    stay whole. The bias calculation is unchanged.
  - Reports keep whole numbers: their product table shows no forecast units, and the
    overall and category bias figures already agree with the whole numbers.
- **Round once in the demand tools:** `record()`, the helper behind every demand
  tool's `*_shown` text, formatted the value after it was cut to 4 decimals for the
  model. The text now comes from the unrounded value (0.27249996 shows +27.2%, not
  +27.3%); the raw number for the model keeps 4 decimals.
- **System prompt rules 11 and 12:** when a question does not say which measure
  (units or revenue) or which period, the first sentence says what was chosen. Bold
  only key numbers and names, never a whole line. Not yet checked live; the live eval
  rerun goes in `docs/eval_results_week6_polish.md`.
