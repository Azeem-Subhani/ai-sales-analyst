---
name: add-agent-tool
description: Use when adding or changing a tool the AI chat agent can call (src/agent/tools.py), including its JSON schema, registration, unit test and evaluation question.
---

# Add an agent tool

Tools do all calculation and data access; the model only chooses tools and
explains results (plan section 10). The agent stays read-only: no tool may
send email, change data or approve recommendations.

## Checklist

1. **Function.** Write a plain Python function in `src/agent/tools.py` with type
   hints and a docstring. It calls the analytics layer (`src/analytics/`) or
   runs read-only SQL; DuckDB is opened with `read_only=True`. Respect the
   limits: max 500 rows, 10-second timeout.
   - **Demand-planning tools** (`get_customer_alerts`, `get_forecast_accuracy`,
     `get_prebuild_recommendations`) only read stored snapshots in
     `data/planning.duckdb` via `src/demand/store.py`: the latest with as_of on
     or before `DATA_END_DATE` (`demand_snapshot(ctx)`). They never call
     `run_demand` (a run takes about 100 s, a question has 120 s). With no
     snapshot they return `NOT_COMPUTED`.
   - A tool must never change a pre-build status or import
     `src/demand/approve.py`; a test in `tests/test_demand.py` checks this.
2. **JSON schema.** Add an OpenAI-style tool schema (name, description,
   parameters with types, enums and required fields). Keep the description
   short and concrete so low-cost models pick it correctly.
3. **Registration.** Add the function and its pydantic argument model to the
   `TOOLS` registry in `src/agent/tools.py` (schemas are generated from the
   argument models). Check argument validation against the schema
   (invalid calls return a clear error, up to 2 retries).
4. **Prompt.** If the tool changes how questions should be answered, update the
   system prompt template `src/agent/prompts/system_prompt.md` ("fixed tools
   first, `run_sql` only when they don't fit"). Schemas and dates are filled in
   by `src/agent/prompts/__init__.py`; do not type them into the template.
5. **Numbers for the answer guard.** The chat agent's answer may only use
   numbers that appear in a tool result (`src/agent/answer_guard.py`). So the
   tool must return every number the answer needs (totals, changes, shares)
   already calculated. Return a pre-formatted `shown` value next to raw numbers
   when the display format matters. Percentages are rounded once, at display:
   return the unrounded value and format it with `builder.fmt_pct` /
   `builder.fmt_change`, never round and then format.
6. **Personal data.** Never return `customers.email` or any other personal
   contact column. SQL goes through `sql_guard.run_query`, which refuses them.
   A tool that reads customers with its own SQL must list its columns
   explicitly and leave email out.
7. **Unit test.** Add a test in `tests/test_tools.py` that calls the tool with
   known inputs and checks the output against a SQL result. Add a scripted
   agent test in `tests/test_agent.py` if the tool changes the loop.
8. **Evaluation question.** Add one question to `tests/eval/questions.yaml`
   with hand-written `gold_sql` and a `grade` block, then run
   `python scripts/fill_gold.py` to write its gold values (never type them).
   For a demand-planning question use `gold_planning_sql` instead: SQL on the
   stored 2026-09-30 snapshot in `planning.duckdb` (run
   `python -m src.demand.run --as-of 2026-09-30` first). Its gold check is a slow
   test (`pytest --runslow`), because the snapshot needs the real forecast fit.
   The question count (25 since Week 5) is checked in
   `tests/test_eval_grader.py`; update it and the targets (19 of the 20 sales
   questions, 4 of the 5 demand questions) with the project owner. Do not run the paid eval
   (`scripts/run_eval.py`) unless the project owner asks; it uses the $10
   OpenRouter budget.
9. Run `ruff format . && ruff check --fix .`, `pytest` and `pytest --runslow`,
   then commit.
