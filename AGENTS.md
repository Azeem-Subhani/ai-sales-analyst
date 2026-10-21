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
data/                         generated database + planted_issues.json (git-ignored)
docs/project_plan.md          full project plan (read-only)
scripts/compare_models.py     Week 4 model comparison
src/data/generate.py          dummy data generator (connector #1)
src/analytics/                KPI SQL views, customer patterns, forecasting, pre-build
src/agent/                    llm.py (only LLM client), tools, prompts, agent loop
src/reports/                  report builder, emailer, scheduler, Jinja2 templates
src/app/streamlit_app.py      chat + dashboard
outputs/reports, outputs/logs dry-run reports and logs (git-ignored)
tests/                        pytest suite
```

## Run commands

Run from the repo root with the virtual environment active.

```
python -m src.data.generate              # build data/sales.duckdb (seed 42)
pytest                                   # run all tests
ruff format . && ruff check --fix .      # format and lint
python -m src.reports.scheduler          # start scheduled reports (Week 3+)
streamlit run src/app/streamlit_app.py   # chat and dashboard (Week 6)
```

## Coding conventions

- Type hints on all function signatures.
- Small functions that do one thing; docstring on every module and public function.
- No magic numbers: name constants at the top of the module (`SEED = 42`).
- SQL lives in `.sql` files or clearly named module-level constants, never
  built by string concatenation from user input.
- Use `logging`, not `print`, in library code (`print` is fine for CLI summaries).
- Open DuckDB with `read_only=True` everywhere except the generator and the
  forecast writer.
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
- Finish every task by running `ruff` and `pytest` and fixing failures.
- Report real command output; never invent results.
- When adding a report, an agent tool, changing the dummy data or comparing
  models, follow the matching checklist in `.claude/skills/*/SKILL.md`.
