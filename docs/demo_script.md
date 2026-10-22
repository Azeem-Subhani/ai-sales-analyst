# Week 6 demo script (15 to 20 minutes)

**Audience:** the team lead. **Report:** the September 2026 monthly business review,
as of 2026-09-30, the one report that shows all 5 planted problems (see
`docs/decisions.md`, "Week 6 demo uses the September monthly business review").

**How the figures here were produced.** Every figure comes from the code's actual
output on 2026-10-22: the monthly review rebuilt with `--no-summary`, the dashboard
view functions, the eval gold values and the live eval. If the data is regenerated
or a snapshot is recomputed, check them again; see "Before the demo".

**The one-sentence story:** every number is calculated by SQL or Python; the model
only writes the summary and the chat wording, and a number guard checks both.

## Before the demo (the day before, and again 15 minutes before)

- [ ] On branch `week-6-dashboard` (or `main` after merge), virtual environment active.
- [ ] Snapshot exists:
  `python -c "from datetime import date; from src.demand import store; print(store.snapshot_exists(store.PLANNING_DB_PATH, date(2026, 9, 30)))"`
  prints `True`. If not, run `python -m src.demand.run --as-of 2026-09-30` (about 100 s).
- [ ] **SB-1042 is still pending.** A decision is final, so do not rehearse the
  approval on the real database. On the Demand page, Pre-build tab, row 1 must
  show Approve and Reject buttons.
- [ ] `REPORT_MODE=dry_run` in `.env`, so nothing is emailed during the demo.
- [ ] `DATA_END_DATE=2026-09-30` in `.env`.
- [ ] The backfilled reports exist: `outputs/backfill/index.html` lists 18 reports.
  If not, run `python -m src.reports.backfill --from 2026-09-14 --to 2026-10-01`
  (about 4.5 min, about $0.01 of summaries).
- [ ] The app starts: `streamlit run src/ui/app.py`. Open each of the 4 pages once,
  so the first (slower) loads are done before the audience arrives.
- [ ] OpenRouter balance is above $1:
  `python -c "from dotenv import load_dotenv; load_dotenv(); from src.agent import llm; print(round(llm.key_limit_remaining(llm.llm_settings()), 4))"`.
  The 5 chat questions below cost about $0.005 together.
- [ ] Close other programs that may hold `data/planning.duckdb` open, such as a
  DuckDB shell. The approval write needs it.

## Running order

| Time | Part |
| --- | --- |
| 0 to 2 min | What it is, and the core rule |
| 2 to 6 min | The monthly email (Reports page) |
| 6 to 14 min | The 5 planted problems: email, dashboard, chat |
| 14 to 17 min | Approve SB-1042 |
| 17 to 20 min | Questions |

### 1. Opening (2 min)

Open the dashboard on the **Sales** page, toggle **Month**, as of 30 Sep 2026.

- Net revenue **456,519.46**: **-1.3%** vs August, **+7.2%** vs September 2025.
- Orders **2,671**, AOV **170.92**.
- Point out that money and counts change in %, rates in percentage points (pp), the
  same comparisons the email uses.

### 2. The email (4 min)

**Reports** page, source **Backfill**, pick *monthly_business_review as of
2026-09-30 (backfill 2026-10-01)*. This is exactly what the scheduler would have
emailed at 08:00 on 1 Oct. Show the AI summary at the top, the charts, and the
**Download Excel** button (the full lists are in the Excel sheets). Mention
the backfill: 18 reports for run dates 14 Sep to 1 Oct, built as dry runs, with no
emails sent.

### 3. The 5 planted problems (8 min)

#### Problem 1: three wholesale customers stopped ordering

- **Email:** Demand alerts, "Overdue customers (58, 41 serious)". The first 3 rows:

  | Customer | Usual gap (days) | Days since last order | Revenue, last 365 days |
  | --- | --- | --- | --- |
  | Harbor & Pine Co. | 14.0 | 56 | 25,660.17 |
  | Kestrel Home Stores | 14.0 | 47 | 24,364.87 |
  | Bluegate Supplies | 14.0 | 54 | 22,691.58 |

- **Dashboard:** Demand page, **Customer alerts** tab, "Overdue (58)". Sort by
  revenue to show they lead.
- **Chat:** "Which customers are seriously overdue on their orders, and how many
  are there?"
  - Expected: **41** customers, the top 10 listed with Harbor & Pine Co., Kestrel
    Home Stores and Bluegate Supplies first, then **"and 31 more"**, with the full
    list in the dashboard and the report Excel.
  - This is eval q21, correct in the Week 6 eval.

#### Problem 2: Apparel revenue is falling

- **Email:**
  - In "Net revenue and gross margin by category", Apparel is **52,952.75**,
    **-13.4%** vs August.
  - In Demand alerts, "Category alerts" flags only Apparel: last 8 weeks
    **106,391.44** vs **141,557.14** the 8 weeks before, **-24.8%**.
- **Dashboard:** Sales page (Month), category chart and table. Then Demand page,
  **Category and stock alerts** tab: Apparel is the only flagged category.
- **Chat:** "How much has Apparel net revenue fallen from July to September 2026?"
  - Expected: July **74,035.80**, September **52,952.75**, **-28.5%**.
  - This is eval q17.

#### Problem 3: ten products ran out of stock in September

- **Email:** Demand alerts, "Stock alerts", 10 products. First rows:
  - Premium Kite (TO-1019): 3 stockout weeks, 17 on hand, 7.4 days of cover.
  - Deluxe Bluetooth Speaker (EL-1028): 2 weeks, 4 on hand, 1.3 days.
  - Premium Dog Lead (PS-1023): 2 weeks, 18 on hand, 4.8 days.
- **Dashboard:** Demand page, **Category and stock alerts** tab, "Stock alerts (10)".
- **Chat:** "Which products had zero stock on hand in September 2026, and for how
  many weeks each?"
  - Expected: the same 10 products, with Premium Kite at 3 weeks; Deluxe Bluetooth
    Speaker, Essential Dolls House and Premium Dog Lead at 2; the other six at 1.
  - Not an eval question. Run live once on 2026-10-22 with that result; the number
    guard needed one retry (`retried_ok`) that time.

#### Problem 4: the Classic Denim Jacket forecast is too high

- **Email:**
  - In Demand alerts, "Forecast-accuracy flags", row 1: Classic Denim Jacket
    (AP-1150), 393 actual units, WAPE **27.3%**, bias **+27.3%**.
  - In "Needs planner review", AP-1150 is held back from pre-build with reason
    "forecast bias +27.3% (seeded forecasts)".
- **Dashboard:** Demand page, **Forecast accuracy** tab, Products table: AP-1150 has
  393 actual vs 500.1 forecast units. Overall WAPE is 15.9%.
- **Chat:** "How accurate were the forecasts for the Classic Denim Jacket? Give WAPE
  and bias."
  - Expected: WAPE **27.3%**, bias **+27.3%** (forecast too high).
  - This is eval q22.

#### Problem 5: Smart Bottle Pro needs a pre-build

- **Email:** Demand alerts, "Pre-build recommendations, pending approval", row 1:
  Smart Bottle Pro (SB-1042), **9.1** days of cover, **327** units, value at risk
  **11,441.73**, urgent. There are 129 pending in total.
- **Dashboard:** Demand page, **Pre-build** tab, rank 1. It has 123 on hand, 0 on
  order and a 4-week lead time.
- **Chat:** "Which three products should we pre-build first, and how many units of
  each?"
  - Expected, in order: Smart Bottle Pro **327**, Deluxe Bluetooth Speaker **135**,
    Essential Cotton T-Shirt **95**.
  - The answer says a planner must approve; the agent cannot. This is eval q24.
- Point out the chat **Details** expander: the tool call, its result, model,
  tokens, cost and latency.

### 4. Approve SB-1042 (3 min)

On the Demand page, **Pre-build** tab, Approvals:

1. Type your name in **Decided by** (required) and a note, e.g. "Approved in the
   Week 6 demo".
2. Click **Approve** on row 1, *1. Smart Bottle Pro (SB-1042)*. The row changes to
   **Confirm approve** / **Cancel**.
3. Click **Confirm approve**. A green message shows
   `20260930-SB-1042: approved by <name> at <time> UTC`. The row leaves Pending and
   appears under **Decided** with status, by, when and note.
4. Say what happened underneath: the button called the same `approve.decide()` as
   `python -m src.demand.approve`. The decision is final and is kept in
   `prebuild_decisions`. The chat agent has no way to do this.

**After the demo:** SB-1042 stays approved. A later
`python -m src.demand.run --as-of 2026-09-30` is refused unless `--force`; the
decision is kept either way.

## If something goes wrong

- **A daily summary shows the placeholder:** the summary guard rejected a refusal
  twice. This is expected behaviour, and the report itself is complete. The
  2026-10-22 backfill has a summary on all 18 reports.
- **"There is no demand-planning snapshot":** run the command the page shows, then
  reload.
- **"Not saved: ... is in use by another process":** close the other program that
  holds `planning.duckdb` open, then click Approve again.
- **The chat says the model is unavailable:** show the eval results instead
  (`docs/eval_results_week6.md`, 25/25).
