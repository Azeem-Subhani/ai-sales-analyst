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
