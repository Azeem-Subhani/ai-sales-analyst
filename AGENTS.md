# AGENTS.md

Single source of truth for anyone (human or AI assistant) working in this repo.
Claude Code reads it through `CLAUDE.md`; Cursor reads it directly and through
`.cursor/rules/`. Edit rules here, not in those files.

Full project plan: `docs/project_plan.md` (do not modify; it is the reference
document). If anything here conflicts with the plan, flag it before acting.

## Project summary

`ai-sales-analyst` is a Python proof of concept: an AI sales and demand analyst
on top of an e-commerce sales database (generated dummy data in v1).

1. **Sales reporting:** fixed KPI definitions (net revenue, orders, AOV, units,
   gross margin, new vs returning customers, cancellation and return rates,
   top products and customers).
2. **Scheduled reports:** daily, weekly and monthly reports emailed via Gmail,
   each starting with a short LLM-written summary of the computed numbers.
3. **Demand planning:** customer ordering-pattern alerts, weekly forecasts,
   forecast accuracy (WAPE and bias), and pre-build recommendations that a
   human approves.

**Core rule: every number is calculated by SQL or Python. The LLM only
explains results.** It never does arithmetic, and never receives database access
when writing report summaries.

How the core rule is enforced (keep these in place; see `docs/decisions.md`):

- **No LLM-calculated numbers.**
  - Prompts tell the model to copy numbers, never to sum, average, round or
    work out changes.
  - Totals, differences, shares and percentage changes come from SQL or
    Python tools.
- **Summary number guard** (`src/reports/summary.py`): every number in a report
  summary must be in the numbers sent, or a count from 1 to 10. On failure:
  one retry, then the summary is dropped. A summary that passes only on the retry
  is `ok_after_retry` (shown like `ok`), with the first failure's reason in
  `summary_retry_reason` (`report_runs` and the backfill manifest).
- **Answer number guard** (`src/agent/answer_guard.py`):
  - Every number in a chat answer must come from a tool result for that
    question, a carried tool result from an earlier turn, the user's question
    or an earlier question, a date, or be a count from 1 to 10. A number found
    only in an earlier answer is rejected.
  - On failure: one retry. Then the written answer is withheld and the tool
    results are shown instead.
  - The result is logged in `agent_log.answer_guard`.
- **The dashboard chat is multi-turn; the CLI and eval are not** (Week 8):
  - `agent.ask(history=...)` sends earlier questions, answers and their exact
    tool messages; size limits and pairing live in `src/agent/history.py`.
  - 20 answered turns per chat (`src/ui/chat_session.py`); history is in
    `st.session_state` only, never on disk.
  - `agent_log.history_turns` and `history_chars` record what was carried.
- **SQL guard blocks personal contact data** (`src/agent/sql_guard.py`):
  - `customers.email` can never be queried.
  - When a query reads `customers`, these are refused too: `SELECT *`, `c.*`,
    `COLUMNS(...)`, positional references, the whole row as a value, and
    renamed columns.
  - A new personal contact column (phone, address) must be added to
    `PERSONAL_DATA_COLUMNS` in the same change that adds it to the schema.
- **Demand has one definition:** units (quantity) on all non-cancelled orders
  (completed or returned), per Monday-to-Sunday week. Forecasts, forecast
  accuracy, safety stock and pre-build all use it, and so does `get_forecast`. It is
  not the "Units sold" KPI, which counts completed orders only.
- **Demand results live in `data/planning.duckdb`:**
  - It sits next to `sales.duckdb`; its schema is created by
    `src/demand/store.py`.
  - `sales.duckdb` stays read-only.
  - `run_sql` and `make_chart` still see only the six standard tables.
  - One snapshot per as_of, computed by `run_demand(as_of)`. Thresholds come
    from `config/demand.yaml`, never from code.
- **The agent stays on topic** (system prompt rule 14, prompt only): questions
  outside this business's sales and demand data get a short redirect and no tool
  call. Eval q26 and q27 check this.
- **Tests never spend credit or send email:** `tests/conftest.py` blocks
  OpenRouter and Gmail for every test, with no opt-out (fake secrets, no `.env`
  loading, the real client and key read raise, sockets refuse both hosts). Mock
  `llm.complete` or pass a fake client instead.
- **The agent never changes an approval status.** Only `decide()` in
  `src/demand/approve.py` changes a pre-build status. Two places call it: the
  approval CLI and the dashboard's Demand page (`src/ui/app_pages/demand.py`).
  Nothing in `src/agent`, `src/reports` or the rest of `src/ui` (the chat page
  included) imports it. `tests/test_demand.py` and `tests/test_guardrails.py`
  check this.
- **Demand-tool lists are capped at 10 rows** (`DEMAND_LIST_CAP`), each with
  `total_count` and `more_count`; the model says "and N more". There is no limit
  argument. The full lists are in the dashboard and the report Excel.
- **The dashboard never calculates** (`src/ui`):
  - It shows what `src/reports/views.py` and `src/reports/saved.py` return, and
    those reuse the report sections.
  - It has no SQL and opens no database itself; a guardrail test checks this.
  - New numbers go in a `src/` function with a test, never in a page.
- **The backfill never sends** (`src/reports/backfill.py`):
  - It replays the scheduler's triggers as forced dry runs into
    `outputs/backfill/`.
  - It never calls `send_report` or writes `report_runs`.
- **`data/planted_issues.json` is for tests only.** Alert, forecast and report code
  never read it. Only `tests/test_demand_acceptance.py` uses the planted issues.
- **Round once.**
  - Percentages, rates and changes are calculated from unrounded values.
  - They are stored unrounded and rounded once, when shown (`fmt_pct` /
    `fmt_change`, Excel number formats).
  - Never round a value and then round it again for display.

## Fixed decisions (do not change without asking the project owner)

- Python 3.12, `venv` + `pip`, pinned `requirements.txt`.
- Dev-only tools in `requirements-dev.txt`: pytest, ruff (format + lint).
- Database: DuckDB, single file `data/sales.duckdb`, standard SQL only.
- Dummy data: Faker + NumPy, fixed random seed 42.
- Libraries: pandas, statsforecast (AutoETS + SeasonalNaive), matplotlib
  (email charts), Plotly + Streamlit (UI), Jinja2 (email HTML), openpyxl
  (Excel), APScheduler, Gmail SMTP with an App Password, python-dotenv, PyYAML.
- LLM: OpenRouter via the `openai` Python SDK (OpenAI-compatible API).
  - `LLM_BASE_URL`, `OPENROUTER_API_KEY`, `LLM_MODEL`
    (`openai/gpt-5.6-luna`), `LLM_FALLBACK_MODEL` (`openai/gpt-6-luna`),
    all from `.env`. Chosen by the project owner after the Week 4 model
    check (`docs/model_comparison_week4.md`).
  - **Every model call goes through `src/agent/llm.py`. No other file imports
    `openai` or calls the API.**
- Not allowed in v1: the `anthropic` SDK, agent frameworks (LangChain,
  LangGraph, etc.), web frameworks (FastAPI, Django, etc.).
- The AI agent and reports open DuckDB **read-only**. Nothing writes to
  business data except the data generator and the `forecasts` table.
  Demand-planning results are written only to `data/planning.duckdb`
  (`src/demand/store.py`), and approvals only by `src/demand/approve.py`.
- Budget: the OpenRouter key has a $10 credit limit. Do not call the API
  unless the task explicitly needs it.
- Ask before adding any package not listed above.

## Design rules for future growth (plan section 14)

1. **Standard data model.** Analytics, reports and the agent only read the six
   standard tables (`customers`, `products`, `orders`, `order_items`,
   `inventory`, `forecasts`). The dummy generator is "connector #1"; real
   sources later get their own connector that writes the same tables.
2. **One email function.** All sending goes through
   `send_report(report, recipients)` in `src/reports/emailer.py`.
3. **One LLM client.** Every model call goes through `src/agent/llm.py`. Model
   names come from `.env`.

## Folder map

```
AGENTS.md, CLAUDE.md          assistant context (this file is the source)
.claude/                      Claude Code settings, hooks, skills
.cursor/rules/                Cursor rules (point back to this file)
config/reports.yaml           report schedules, queries, recipients
config/demand.yaml            demand-planning thresholds (Week 5)
data/                         sales.duckdb, planning.duckdb, audit.duckdb and
                              planted_issues.json (all generated, git-ignored)
docs/project_plan.md          full project plan (read-only)
scripts/compare_models.py     Week 4 model comparison
src/data/generate.py          dummy data generator (connector #1)
src/analytics/                KPI SQL views, demand series, customer alerts and RFM,
                              forecast accuracy and forecasts, pre-build
src/demand/                   run_demand + CLI, approval CLI, planning.duckdb store,
                              config loader
src/agent/                    llm.py (only LLM client), tools, prompts, agent loop,
                              chat history, SQL guard, answer number guard, eval grader
src/reports/                  report builder, demand sections, emailer, scheduler,
                              Jinja2 templates
src/reports/views.py, saved.py  dashboard data: sales and demand views, saved reports
src/reports/backfill.py       replay the schedule as dry runs (Week 6)
src/ui/app.py                 Streamlit dashboard and chat; pages in src/ui/app_pages/
outputs/reports, outputs/logs dry-run reports and logs (git-ignored)
outputs/backfill              backfilled reports, manifest.csv, index.html (git-ignored)
tests/                        pytest suite
```

## Run commands

Run from the repo root with the virtual environment active.

```
python -m src.data.generate              # build data/sales.duckdb (seed 42)
pytest                                   # fast tests (slow ones skipped)
pytest --runslow                         # all tests, incl. forecast fits and acceptance
python -m src.demand.run --as-of 2026-09-30   # demand snapshot (about 100 s)
python -m src.demand.approve --id <id> --approve|--reject --by <name> [--note "..."]
ruff format . && ruff check --fix .      # format and lint
python -m src.reports.scheduler          # start scheduled reports (Week 3+)
python -m src.agent.chat                 # chat agent in the terminal (spends credit)
python scripts/run_eval.py --max-spend 0.40   # live eval (spends credit; ask first)
python -m src.reports.backfill --from 2026-09-14 --to 2026-10-01 [--no-summary]
streamlit run src/ui/app.py              # dashboard and chat (Week 6)
```

## Coding conventions

- Type hints on all function signatures.
- Small functions that do one thing; docstring on every module and public function.
- No magic numbers: name constants at the top of the module (`SEED = 42`).
- SQL lives in `.sql` files or clearly named module-level constants, never
  built by string concatenation from user input.
- Use `logging`, not `print`, in library code (`print` is fine for CLI summaries).
- Open DuckDB with `read_only=True` everywhere except the generator, the forecast
  writer and the planning store (`src/demand/store.py`, `approve.py`).
- Read secrets with `os.environ` / python-dotenv; never hard-code them.
- Keep code readable for a developer with some Python experience: plain
  Python, clear names, comments for intent rather than mechanics.
- Formatting and linting: ruff (config in `pyproject.toml`).

## Working rules

- Show a short plan and wait for approval before large changes.
- Work in small steps; commit after each completed task with a clear message.
  Work on a branch, never push directly to `main`.
- Never commit `.env` or real secrets, and never print API keys.
- Never read `.env` or open `data/*.duckdb` directly; use the project's Python
  scripts.
- Finish every task by running `ruff` and `pytest` and fixing failures. Before a
  PR, run `pytest --runslow` too (the acceptance tests are slow).
- Report real command output; never invent results.
- When adding a report, an agent tool, changing the dummy data or comparing
  models, follow the matching checklist in `.claude/skills/*/SKILL.md`.
