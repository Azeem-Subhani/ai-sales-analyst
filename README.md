# ai-sales-analyst

An AI sales and demand analyst built on an e-commerce sales database. It
computes fixed sales KPIs, emails scheduled reports with a short AI-written
summary, and gives demand-planning feedback (ordering-pattern alerts,
forecast accuracy, pre-build suggestions).

**Core rule:** every number is calculated by SQL or Python; the AI model only
explains the results.

- Full plan: [docs/project_plan.md](docs/project_plan.md)
- Rules and conventions: [AGENTS.md](AGENTS.md)

Status: **Week 3**. The dummy data, KPI reports, scheduling and Gmail delivery
are done. LLM summaries, the agent, demand planning and the UI come in later
weeks.

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
pytest                                   # run all tests
ruff format . && ruff check --fix .      # format and lint
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
- **Retry:** if the guard fails, the model gets one retry. If that fails too,
  the summary is dropped.
- **Never blocks a report:** a dropped or unavailable summary is replaced by
  "Summary unavailable for this run" and the report still sends.
- **Status:** each run records `summary_status` in `report_runs`. The values are
  `ok`, `guard_failed`, `llm_unavailable`, or `skipped`.

`--no-summary` skips the model call (no spend), with status `skipped`:

```bash
python -m src.reports.builder --report weekly_sales_review --as-of 2026-09-27 --no-summary
python -m src.reports.scheduler --run-now weekly_sales_review --no-summary
```

## Chat agent

Ask questions about the sales data in plain English:

```bash
python -m src.agent.chat                                    # interactive; empty line to quit
python -m src.agent.chat --question "How did we do last month?"
```

**Tools.** The agent answers with seven read-only tools:

- Working now: `get_kpis`, `run_sql`, `make_chart` and `get_forecast`.
- Week 5: customer alerts, forecast accuracy and pre-build. These say "not
  available yet".

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
python scripts/run_eval.py --max-spend 0.40  # the 20 eval questions on LLM_MODEL, fallback on
python scripts/run_eval.py --ids q17,q20     # a subset
python scripts/compare_models.py --reference openai/gpt-5.6-luna         # free preflight + estimate
python scripts/compare_models.py --reference openai/gpt-5.6-luna --run   # live model check
```

- **Gold values:** `tests/eval/questions.yaml` holds the 20 questions. Their
  gold values come from SQL via `scripts/fill_gold.py`; nobody types them.
- **`run_eval.py` output:** results go to `outputs/eval/<timestamp>_<model>/`.
  The target is 19/20. `--max-spend` is checked against the key balance.
- **`compare_models.py`:** runs main, fallback and a reference model with
  fallback off, and writes `docs/model_comparison_week4.md`.

The latest eval results are in `docs/eval_results_week4.md`.

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
same rules (only `llm.py` imports `openai`, `.env` is not tracked). Run
`pytest` before committing work done in Cursor, and run ruff yourself.
