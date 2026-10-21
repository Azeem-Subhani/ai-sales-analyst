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
2. **JSON schema.** Add an OpenAI-style tool schema (name, description,
   parameters with types, enums and required fields). Keep the description
   short and concrete so low-cost models pick it correctly.
3. **Registration.** Add the function and schema to the tool registry used by
   `src/agent/agent.py`. Check argument validation against the schema
   (invalid calls return a clear error, up to 2 retries).
4. **Prompt.** If the tool changes how questions should be answered, update the
   system prompt in `src/agent/prompts.py` ("fixed tools first, `run_sql` only
   when they don't fit").
5. **Unit test.** Add a test that calls the function directly with known inputs
   and checks the output against a SQL result.
6. **Evaluation question.** Add one new question with a known answer to the
   agent evaluation (`tests/test_agent_questions.py`). Do not run the paid
   evaluation unless the project owner asks; it uses the $10 OpenRouter budget.
7. Run `ruff format . && ruff check --fix .` and `pytest`, then commit.
