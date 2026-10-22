# ai-sales-analyst

An AI sales and demand analyst built on an e-commerce sales database. It
computes fixed sales KPIs, emails scheduled reports with a short AI-written
summary, and gives demand-planning feedback (ordering-pattern alerts,
forecast accuracy, pre-build suggestions).

**Core rule:** every number is calculated by SQL or Python; the AI model only
explains the results.

- Full plan: [docs/project_plan.md](docs/project_plan.md)
- Rules and conventions: [AGENTS.md](AGENTS.md)

Status: **Week 6**. Everything is in place:
- the dummy data and KPI reports;
- scheduling, Gmail delivery and LLM summaries;
- the chat agent and demand planning;
- the report backfill;
- the Streamlit dashboard (sales, demand with pre-build approvals, chat, saved
  reports).

The live demo is in [docs/demo_script.md](docs/demo_script.md).

## Setup

You need Python 3.12 and Git.

### macOS

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
python -m src.data.generate
pytest
```

### Windows (PowerShell)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env
python -m src.data.generate
pytest
```

If PowerShell refuses to run `Activate.ps1`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use
`.venv\Scripts\activate.bat` from Command Prompt.

After copying, open `.env` in your editor and fill in `OPENROUTER_API_KEY`,
`GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` when you reach the weeks that need
them (Week 3 for Gmail, Week 4 for the LLM). Week 1 needs none of them.
Never commit `.env`; it is git-ignored.

### What the generator does

`python -m src.data.generate` builds `data/sales.duckdb` with 24 months of fake
store data (1 Oct 2024 to 30 Sep 2026, random seed 42, so every run is
identical) and writes `data/planted_issues.json`, which describes the five
problems the AI is expected to find. It takes under a minute and prints row
counts, the date range and total revenue.

### Everyday commands

```bash
python -m src.data.generate              # rebuild the database
pytest                                   # fast tests (about 45 s: 488 pass, 13 slow skipped)
pytest --runslow                         # everything, incl. forecast fits (about 2.5 min: 501 pass)
pytest -m slow                           # only the slow tests
ruff format . && ruff check --fix .      # format and lint
python -m src.demand.run --as-of 2026-09-30   # demand-planning snapshot (about 100 s)
streamlit run src/ui/app.py              # dashboard and chat
python -m src.reports.backfill --from 2026-09-14 --to 2026-10-01   # past reports as dry runs
```

## Scheduled reports and Gmail

Three reports run on the schedules in `config/reports.yaml`, in `TIMEZONE`:

- Daily snapshot: Monday to Friday at 07:00.
- Weekly review: Monday at 08:00.
- Monthly review: the 1st at 08:00.

The weekly and monthly emails carry inline charts and an Excel attachment.

### Gmail setup (once)

Gmail needs an **App Password**, not your normal password.

1. Turn on **2-Step Verification** for the Google account you send from
   (Google Account > Security). App Passwords only exist when it is on.
2. Open <https://myaccount.google.com/apppasswords>, create an app password
   named e.g. `ai-sales-analyst`, and copy the 16 characters. Spaces don't
   matter.
3. Put it in `.env` (never in code or chat). You can revoke it on the same
   page at any time.

`.env` settings used by the scheduler (see `.env.example`):

| Key | Example | Meaning |
| --- | --- | --- |
| `GMAIL_ADDRESS` | `you@gmail.com` | The account that sends |
| `GMAIL_APP_PASSWORD` | 16 characters | App Password from step 2 |
| `REPORT_OWNER_EMAIL` | `you@company.com` | Fills `${REPORT_OWNER_EMAIL}` in `reports.yaml`, so no address is committed |
| `REPORT_MODE` | `dry_run` or `send` | `dry_run` (default) saves files to `outputs/reports/` and sends nothing |
| `TIMEZONE` | `Europe/London` | Time zone for schedules and for "yesterday" |
| `DATA_END_DATE` | `2026-09-30` | Last day of the dummy data (see below). Remove it for real data |
| `REPORT_RETRY_DELAY_SECONDS` | `300` | Wait before the single retry |

**Data clock.** The dummy data ends on 2026-09-30, so each run reports the
latest *complete* period on or before `min(yesterday, DATA_END_DATE)`:

- Daily: that day.
- Weekly: the last full Monday-to-Sunday week.
- Monthly: the last full month.

The email says which period it covers. Details are in `docs/decisions.md`
(Week 3).

**Who can receive email.** Every report recipient must be on
`delivery.allowed_recipients` in `reports.yaml`. If any one is not, nothing is
sent, and a failure notice goes to `delivery.failure_recipients`.

### Running the scheduler

```bash
python -m src.reports.scheduler --list                         # jobs and next run times
python -m src.reports.scheduler --run-now weekly_sales_review  # one report, now
python -m src.reports.scheduler                                # start; runs until Ctrl+C
python -m src.reports.scheduler --test-every 2                 # testing only: all reports every 2 min
```

The scheduler only runs while that terminal is open and the computer is awake.

If a run fails, it is retried once after `REPORT_RETRY_DELAY_SECONDS`. If the
retry fails too, a short failure notice is emailed. Config problems (missing
Gmail password, address not on the allowlist) are not retried. In send mode,
missing settings are reported at start-up as one line, not a stack trace.

### Backfill: past reports without waiting

The backfill builds every report the scheduler would have sent on a range of run
dates, as if "today" were each date:

```bash
python -m src.reports.backfill --from 2026-09-14 --to 2026-10-01               # with summaries
python -m src.reports.backfill --from 2026-09-14 --to 2026-10-01 --no-summary  # no LLM spend
```

- **Same schedule as the scheduler:** run dates come from the schedules in
  `reports.yaml` and each period from the data clock. 14 Sep to 1 Oct gives 14
  daily snapshots, 3 weekly reviews (as of 13, 20 and 27 Sep) and the September
  monthly review.
- **Never sends:** it is always a dry run, whatever `REPORT_MODE` says, and it
  never writes `report_runs`.
- **Output:**
  - reports in `outputs/backfill/<run date>/<report>_<as_of>/`;
  - `outputs/backfill/manifest.csv`, with one row per report (status, summary
    status, folder, error);
  - `outputs/backfill/index.html`, with a link to each report.

  A failed report is recorded and the rest still run.
- **Demand snapshots:** the weekly and monthly reports create any missing snapshot
  (about 100 s each).
- The 14 Sep to 1 Oct run took about 4.5 minutes and cost about $0.01 of summaries.

### Run history

Every attempt is a row in `report_runs` in `data/audit.duckdb`. The columns
are: report, period, mode, recipients, status, error, duration and UTC
timestamps. Logs go to `outputs/logs/scheduler.log`. To read the runs:

```bash
python -c "from src.reports import audit; print(audit.read_runs().tail(10).to_string())"
```

## LLM setup

The report summaries and the chat agent call models on OpenRouter. All calls
go through `src/agent/llm.py`. Put these in `.env` (see `.env.example`); never
put the key in code or chat:

| Key | Example | Meaning |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | from Tech Ops | Required. The key has a $10 credit limit |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter's OpenAI-compatible API |
| `LLM_MODEL` | `openai/gpt-5.6-luna` | Main model |
| `LLM_FALLBACK_MODEL` | `openai/gpt-6-luna` | Tried once if the main model times out, is rate limited, errors (5xx) or returns nothing |
| `LLM_TIMEOUT_SECONDS` | `60` | Per request |

Model IDs stay plain (no `:nitro`). The client asks OpenRouter for the fastest
provider instead (see `docs/decisions.md`, Week 4). If the key is rejected
(401/403), there is no fallback: check the key and its credit.

Tests never reach OpenRouter. An autouse fixture in `tests/conftest.py` blocks
the host.

**Current models** (chosen by the project owner after the Week 4 model check,
`docs/model_comparison_week4.md`):

- Main: `openai/gpt-5.6-luna`, 19/20 in the check.
- Fallback: `openai/gpt-6-luna`, 18/20.

Both are OpenAI models, so an OpenAI-wide outage is not covered. That is
accepted for the proof of concept; see `docs/decisions.md`.

**Spend.** OpenRouter reports a cost of $0 per call, so spend is measured from
the key's credit balance. Do not run the eval or the model check without the
project owner's OK.

## Report summaries

Each report opens with a 3 to 5 sentence summary written by the main model.
The model only sees the report's numbers, formatted as the email shows them,
and never the database.

- **Number guard:** every number in the summary must be one of the numbers
  sent, possibly rounded to fewer decimals, or a count from 1 to 10.
- **Not a summary:** a refusal ("I'm sorry, I cannot assist...") or a reply that
  uses no number from the data is rejected too.
- **Retry:** if the guard fails, or the summary is cut off at the output token
  cap, the model gets one retry. If that fails too, the summary is dropped.
- **Never blocks a report:** a dropped or unavailable summary is replaced by
  "Summary unavailable for this run" and the report still sends.
- **Status:** each run records `summary_status` in `report_runs`. The values are
  `ok`, `guard_failed`, `truncated`, `llm_unavailable`, or `skipped`.

`--no-summary` skips the model call (no spend), with status `skipped`:

```bash
python -m src.reports.builder --report weekly_sales_review --as-of 2026-09-27 --no-summary
python -m src.reports.scheduler --run-now weekly_sales_review --no-summary
```

## Demand planning

Demand planning finds ordering-pattern problems, checks forecast accuracy, makes
its own forecasts and suggests pre-build quantities for a planner to approve. Every
number is calculated in SQL or Python (`src/analytics/`); nothing here uses the LLM.

**Demand** means units (quantity) on all non-cancelled orders (completed or
returned), per Monday-to-Sunday week. Forecasts, forecast accuracy, safety stock and
pre-build all use this one definition. It is the same one `get_forecast` uses.

**What one run computes** (`run_demand(as_of)`; complete weeks and data on or before
as_of only):

- **Customer alerts:**
  - *Overdue* (serious above 2x the usual reorder gap, overdue above 1.5x; the
    usual gap has a 7-day floor);
  - *declining* (last 90 days more than 25% below the prior 90);
  - *new high-value*;
  - *RFM segment moves* (from Champions or Loyal to At risk or Lapsed).

  Overdue and segment moves cover wholesale customers and the top 20% of retail
  customers by revenue.
- **Category alerts:** revenue in the last 8 weeks vs the 8 before; a drop over
  15% is flagged.
- **Stock alerts:** stockout weeks in the last 4 weeks, and days of cover.
- **Forecast accuracy:** WAPE and bias of the seeded forecasts, with stockout weeks
  left out. Flagged at WAPE over 40% or bias beyond 15%.
- **Our own forecasts:** statsforecast AutoETS and SeasonalNaive, 12 weeks ahead,
  with a 26-week backtest that picks the better model per product. Products with
  under 26 weeks of history use a recent-mean fallback.
- **Pre-build:** max(0, forecast over lead time + safety stock - on hand - on
  order), ranked urgent first (cover below lead time), then by value at risk.
  Products flagged for bias go to "needs planner review" instead.

```bash
python -m src.demand.run --as-of 2026-09-30            # compute and store a snapshot
python -m src.demand.run --as-of 2026-09-30 --force    # replace one with decided recommendations
python -m src.demand.approve --id 20260930-SB-1042 --approve --by "A. Planner" --note "ok"
python -m src.demand.approve --id 20260930-AP-1004 --reject --by "A. Planner"
```

- **`config/demand.yaml`:** every threshold above. A hash of it is stored with each
  snapshot.
- **`data/planning.duckdb`:** all results, one snapshot per as_of, next to
  `sales.duckdb` (which stays read-only). A rerun replaces that as_of's snapshot. If
  any of its recommendations was approved or rejected, the rerun is refused unless
  `--force`. Decisions are kept in `prebuild_decisions` either way.
- **Approval:** only `approve.decide()` changes a status, from pending to approved
  or rejected, once. It records who, when and the note. Two places call it: the
  approval CLI and the dashboard's Demand page. The agent and the reports can never
  change a status.
- **In the reports:**
  - The weekly and monthly reports have a "Demand alerts" section: overdue and
    declining customers, category, stock and forecast-accuracy flags, pending
    pre-builds and planner review.
  - The monthly report also has "Forecast accuracy".
  - Each list shows 10 rows in the email; the Excel sheets "Demand alerts" and
    "Forecast accuracy" have every row.
  - The daily snapshot has no demand sections.
  - A report reuses the snapshot for its as_of, or computes one if none exists.
- **Week 6 demo:** use the September monthly business review (as of 2026-09-30).
  It is the one report that shows all 5 planted problems (see `docs/decisions.md`).

## Chat agent

Ask questions about the sales data in plain English:

```bash
python -m src.agent.chat                                    # interactive; empty line to quit
python -m src.agent.chat --question "How did we do last month?"
```

**Tools.** The agent answers with seven read-only tools:

- Sales: `get_kpis`, `run_sql`, `make_chart` and `get_forecast`.
- Demand planning: `get_customer_alerts`, `get_forecast_accuracy` and
  `get_prebuild_recommendations`. They read the latest stored snapshot on or
  before `DATA_END_DATE` and never compute one. With no snapshot they say to run
  `python -m src.demand.run`. They cannot approve or reject anything.
  - Each list returns at most 10 rows, with `total_count` and `more_count`, and
    the answer says "and N more". The full lists are in the dashboard and the
    report Excel.
  - Declining customers are sorted by revenue lost (prior 90 days minus last 90
    days), largest first.

**Rules that keep its numbers honest:**

- **The model never calculates.** Every number comes from a tool.
- **Answer number guard:**
  - Every number in the answer must come from a tool result for that question.
    Numbers the user wrote, dates and counts from 1 to 10 are also allowed.
  - If the guard fails, the model gets one retry. If that fails too, the
    written answer is withheld and the tool results are shown instead.
- **SQL guard:** only a single read-only SELECT on the six standard tables.
  - Max 500 rows, 10 seconds.
  - `customers.email` is personal contact data and is refused, including via
    `SELECT *` on customers.
- **Relative periods:** "last month", "this week" and similar phrases mean the
  latest complete period on or before `DATA_END_DATE`. With 2026-09-30, last
  month is September 2026.
- **Limits:** 10 tool calls and 120 seconds per question.

Each question, tool call and guard result is logged in `agent_log` and
`agent_tool_calls` in `data/audit.duckdb`.

## Eval and model check

Both scripts call OpenRouter and spend credit; pytest never does.

```bash
python scripts/fill_gold.py --check          # free: stored gold values still match the SQL
python scripts/run_eval.py --max-spend 0.40  # the 25 eval questions on LLM_MODEL, fallback on
python scripts/run_eval.py --ids q17,q20     # a subset
python scripts/compare_models.py --reference openai/gpt-5.6-luna         # free preflight + estimate
python scripts/compare_models.py --reference openai/gpt-5.6-luna --run   # live model check
```

- **Gold values:** `tests/eval/questions.yaml` holds 25 questions (20 sales,
  5 demand planning). Their gold values come from SQL via `scripts/fill_gold.py`;
  nobody types them. The demand questions use `gold_planning_sql` on the 2026-09-30
  snapshot, so run `python -m src.demand.run --as-of 2026-09-30` first.
- **`run_eval.py` output:** results go to `outputs/eval/<timestamp>_<model>/`.
  Targets: 19 of the 20 sales questions and 4 of the 5 demand questions.
  `--max-spend` is checked against the key balance.
- **`compare_models.py`:** runs main, fallback and a reference model with
  fallback off, and writes `docs/model_comparison_week4.md`.

The latest eval results are in `docs/eval_results_week6.md` (25/25, fallback off).
The grader also checks name order on ranked questions ("top", "most", "first").

## Dashboard

```bash
streamlit run src/ui/app.py
```

The dashboard opens in the browser. The sidebar **As of** date (default
`DATA_END_DATE`) applies to every page:

- **Sales:**
  - a day / week / month toggle;
  - KPI cards with the report comparisons (% for money and counts, pp for rates);
  - a trend chart (13 weeks or 12 months), the category breakdown, and the top 10
    products and customers.
- **Demand:**
  - full, sortable lists for customer alerts, category and stock alerts, forecast
    accuracy and pre-build;
  - with no snapshot for that date, it shows the `python -m src.demand.run` command
    to run.
- **Pre-build approvals** (Demand page, Pre-build tab):
  - type your name in **Decided by** and add an optional note;
  - **Approve** or **Reject**, then **Confirm**;
  - the decision goes through the same `approve.decide()` as the CLI.
- **Chat:**
  - the same agent as `python -m src.agent.chat`, one question at a time;
  - each answer has a details panel with the tool calls, model, tokens, cost and
    latency;
  - it has no approval actions, and each answer spends a little credit.
- **Reports:** the saved dry-run and backfilled reports, with charts, plus the Excel
  download.

The dashboard does no calculation and has no SQL. It shows what
`src/reports/views.py` and `src/reports/saved.py` return. Reads are read-only and
short-lived, so the CLIs can still write while it is open.

## Working with Claude Code and Cursor

The repo is set up so both tools follow the same rules without copying them.

| File | Used by | Purpose |
| --- | --- | --- |
| `AGENTS.md` | Both | **Single source of truth**: project summary, fixed decisions, design rules, folder map, commands, conventions. Edit rules here. |
| `CLAUDE.md` | Claude Code | Imports `AGENTS.md` with `@AGENTS.md`, plus Claude-only notes (skills, hooks). |
| `.claude/skills/*/SKILL.md` | Claude Code (Cursor via rule) | Step-by-step checklists: `add-report`, `add-agent-tool`, `update-dummy-data`, `compare-models`. |
| `.claude/settings.json` | Claude Code | Shared permissions and hooks (committed). |
| `.claude/settings.local.json` | Claude Code | Your personal overrides (git-ignored, create it if needed). |
| `.cursor/rules/project.mdc` | Cursor | Always on: "follow `AGENTS.md`". |
| `.cursor/rules/python.mdc` | Cursor | On `*.py` files: coding conventions and the `llm.py` rule. |
| `.cursor/rules/workflows.mdc` | Cursor | When relevant: follow the matching `SKILL.md` checklist. |

**Hooks (Claude Code only).** Small Python scripts in `.claude/hooks/`, run
with the project's `.venv` Python on macOS and Windows:

- Before an edit or shell command, `guard_edits.py` blocks:
  - adding `import openai` / `from openai` anywhere except `src/agent/llm.py`;
  - writing an OpenRouter key (`sk-or-...`) into a file;
  - shell commands that read `.env`.
- After an edit, `format_python.py` runs `ruff format` and `ruff check --fix`
  on the changed `.py` file.

Claude Code also denies reading or editing `.env` and `data/*.duckdb` directly,
`git push --force`, and `rm -rf`.

**Cursor has no copy of these hooks**, so `tests/test_guardrails.py` checks the
same rules (only `llm.py` imports `openai`, `.env` is not tracked), plus the Week 6
ones (no SQL in `src/ui`, approvals only through `decide()`, the backfill never
sends, demand-tool lists capped at 10). Run
`pytest` before committing work done in Cursor, and run ruff yourself.
