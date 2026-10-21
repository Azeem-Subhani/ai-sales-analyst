# ai-sales-analyst

An AI sales and demand analyst built on an e-commerce sales database. It
computes fixed sales KPIs, emails scheduled reports with a short AI-written
summary, and gives demand-planning feedback (ordering-pattern alerts,
forecast accuracy, pre-build suggestions).

**Core rule:** every number is calculated by SQL or Python; the AI model only
explains the results.

- Full plan: [docs/project_plan.md](docs/project_plan.md)
- Rules and conventions: [AGENTS.md](AGENTS.md)

Status: **Week 1**. The AI-assistant setup, project scaffold and dummy data
generator are done. Analytics, reports, the LLM client, the agent and the UI
are placeholders for later weeks.

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
