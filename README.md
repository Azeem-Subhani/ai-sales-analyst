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
