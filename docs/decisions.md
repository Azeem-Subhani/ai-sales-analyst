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
