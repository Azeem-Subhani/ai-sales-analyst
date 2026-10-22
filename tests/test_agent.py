"""Agent loop tests with a scripted fake model, plus prompt and example checks.

The fake replaces llm.chat_with_tools, so no test reaches OpenRouter.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from src.agent import agent, agent_log, chat, llm, prompts, sql_guard, tools

SETTINGS = llm.LLMSettings("test-key", "https://openrouter.ai/api/v1", "main/m", "fb/m", 60.0)
WEEK_ARGS = {"period_type": "week", "as_of": "2026-09-27"}
DATA_END = date(2026, 9, 30)


def tool_call(name: str, args: dict | str, call_id: str = "c1") -> llm.ToolCall:
    """A tool call as the model would send it."""
    raw = args if isinstance(args, str) else json.dumps(args)
    return llm.ToolCall(call_id, name, raw)


def calls(*tool_calls: llm.ToolCall) -> llm.LLMResult:
    """A model turn that asks for tools."""
    return llm.LLMResult(
        text=None,
        tool_calls=list(tool_calls),
        model="main/m",
        input_tokens=50,
        output_tokens=5,
        cost_usd=0.001,
    )


def text(answer: str, fallback_used: bool = False) -> llm.LLMResult:
    """A model turn that answers."""
    return llm.LLMResult(
        text=answer,
        model="fb/m" if fallback_used else "main/m",
        fallback_used=fallback_used,
        input_tokens=80,
        output_tokens=20,
        cost_usd=0.002,
    )


class ScriptedChat:
    """Plays back model turns in order and records what it was sent."""

    def __init__(self, *turns: llm.LLMResult | Exception) -> None:
        self.turns = list(turns)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, messages: list[dict], schemas: list[dict], **kwargs: Any) -> llm.LLMResult:
        self.requests.append({"messages": [dict(m) for m in messages], **kwargs})
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


@pytest.fixture
def config(built: dict, tmp_path: Path) -> agent.AgentConfig:
    """Agent settings on the test database, logging to a temporary audit file."""
    return agent.AgentConfig(
        db_path=built["db_path"],
        audit_path=tmp_path / "audit.duckdb",
        charts_dir=tmp_path / "charts",
        data_end=DATA_END,
    )


def ask(question: str, config: agent.AgentConfig, fake: ScriptedChat) -> agent.AgentAnswer:
    """Run the agent with the fake model and dummy settings."""
    return agent.ask(question, config, chat=fake, settings=SETTINGS)


def test_tool_call_then_answer(config: agent.AgentConfig) -> None:
    fake = ScriptedChat(
        calls(tool_call("get_kpis", WEEK_ARGS)), text("Net revenue was 114,286.82.")
    )
    answer = ask("Net revenue last week?", config, fake)
    assert answer.answer == "Net revenue was 114,286.82."
    assert [o.name for o in answer.tool_calls] == ["get_kpis"]
    assert answer.tool_calls[0].success
    # The tool result went back to the model, linked to the call id.
    tool_message = fake.requests[1]["messages"][-1]
    assert tool_message["role"] == "tool" and tool_message["tool_call_id"] == "c1"
    assert '"shown": "114,286.82"' in tool_message["content"]
    assert (answer.input_tokens, answer.output_tokens) == (130, 25)
    assert answer.cost_usd == pytest.approx(0.003)
    assert answer.stopped_early is None


def test_ten_tool_call_limit_then_final_answer(config: agent.AgentConfig) -> None:
    query = {"query": "SELECT 1 AS x"}
    many = [calls(tool_call("run_sql", query, f"c{i}")) for i in range(10)]
    fake = ScriptedChat(*many, text("Here is what I found."))
    answer = ask("Keep going", config, fake)
    assert len(answer.tool_calls) == 10
    assert answer.stopped_early == "tool_limit"
    assert answer.answer.startswith("(Stopped early: reached the limit of 10 tool calls.)")
    assert answer.answer.endswith("Here is what I found.")
    final = fake.requests[-1]
    assert final["tool_choice"] == "none"
    assert "used all 10 tool calls" in final["messages"][-1]["content"]


def test_calls_beyond_limit_in_one_turn_are_not_run(config: agent.AgentConfig) -> None:
    query = {"query": "SELECT 1 AS x"}
    fake = ScriptedChat(
        calls(*[tool_call("run_sql", query, f"c{i}") for i in range(12)]), text("Done.")
    )
    answer = ask("Twelve at once", config, fake)
    assert len(answer.tool_calls) == 10
    tool_replies = [m for m in fake.requests[1]["messages"] if m["role"] == "tool"]
    assert len(tool_replies) == 12  # every call id gets a reply
    assert "tool_call_limit" in tool_replies[-1]["content"]


def test_invalid_args_then_successful_retry(config: agent.AgentConfig) -> None:
    fake = ScriptedChat(
        calls(tool_call("get_kpis", {"period_type": "fortnight", "as_of": "2026-09-27"})),
        calls(tool_call("get_kpis", WEEK_ARGS, "c2")),
        text("661 orders."),
    )
    answer = ask("Orders last week?", config, fake)
    assert [o.success for o in answer.tool_calls] == [False, True]
    first_reply = json.loads(fake.requests[1]["messages"][-1]["content"])
    assert first_reply["error"] == "invalid_arguments"
    assert answer.answer == "661 orders."


def test_invalid_args_final_error_after_two_retries(config: agent.AgentConfig) -> None:
    bad = {"period_type": "year", "as_of": "2026-09-27"}
    fake = ScriptedChat(
        *[calls(tool_call("get_kpis", bad, f"c{i}")) for i in range(3)], text("I could not.")
    )
    ask("?", config, fake)
    third_reply = json.loads(fake.requests[3]["messages"][-1]["content"])
    assert third_reply["error"] == "invalid_arguments_final"


def test_llm_unavailable_message(config: agent.AgentConfig) -> None:
    fake = ScriptedChat(llm.LLMUnavailableError("Both models failed."))
    answer = ask("Net revenue?", config, fake)
    assert answer.answer == agent.UNAVAILABLE_ANSWER
    assert "Both models failed" in answer.error
    row = agent_log.read_questions(config.audit_path).iloc[0]
    assert row["final_answer"] == agent.UNAVAILABLE_ANSWER
    assert "Both models failed" in row["error"]


def test_auth_error_message(config: agent.AgentConfig) -> None:
    fake = ScriptedChat(llm.LLMAuthError("OpenRouter rejected the API key (401)."))
    answer = ask("Net revenue?", config, fake)
    assert "rejected the API key" in answer.answer


def test_time_limit(config: agent.AgentConfig) -> None:
    ticks = iter([0.0, 0.0, 200.0, 200.0, 200.0])
    config.clock = lambda: next(ticks)
    fake = ScriptedChat(calls(tool_call("get_kpis", WEEK_ARGS)), text("never sent"))
    answer = ask("Slow question", config, fake)
    assert answer.stopped_early == "time_limit"
    assert answer.answer == agent.TIME_LIMIT_ANSWER
    assert len(fake.requests) == 1


def test_request_timeout_is_capped_by_time_left(config: agent.AgentConfig) -> None:
    ticks = iter([0.0, 100.0, 100.0])
    config.clock = lambda: next(ticks)
    fake = ScriptedChat(text("ok"))
    ask("?", config, fake)
    assert fake.requests[0]["settings"].timeout_seconds == pytest.approx(20.0)


def test_fallback_flag_and_model_override(config: agent.AgentConfig) -> None:
    config.model, config.fallback = "other/m", False
    fake = ScriptedChat(text("ok", fallback_used=True))
    answer = ask("?", config, fake)
    assert answer.fallback_used is True
    assert fake.requests[0]["model"] == "other/m" and fake.requests[0]["fallback"] is False


def test_agent_log_rows_written(config: agent.AgentConfig) -> None:
    fake = ScriptedChat(
        calls(
            tool_call("get_kpis", WEEK_ARGS, "c1"),
            tool_call("run_sql", {"query": "DELETE FROM orders"}, "c2"),
        ),
        text("Net revenue was 114,286.82."),
    )
    answer = ask("Net revenue last week?", config, fake)
    questions = agent_log.read_questions(config.audit_path)
    assert len(questions) == 1
    row = questions.iloc[0]
    assert row["question_id"] == answer.question_id
    assert row["question"] == "Net revenue last week?"
    assert (row["model"], bool(row["fallback_used"])) == ("main/m", False)
    assert (row["tool_call_count"], row["total_tokens"]) == (2, 155)
    assert row["cost_usd"] == pytest.approx(0.003)
    assert row["final_answer"] == "Net revenue was 114,286.82."
    assert row["answer_guard"] == "ok" and row["answer_guard_failed"] is None
    calls_logged = agent_log.read_tool_calls(config.audit_path)
    assert list(calls_logged["tool_name"]) == ["get_kpis", "run_sql"]
    assert list(calls_logged["success"]) == [True, False]
    assert calls_logged.iloc[0]["row_count"] == 13
    assert "Only SELECT" in calls_logged.iloc[1]["error"]
    assert set(calls_logged["question_id"]) == {answer.question_id}


def test_agent_db_connection_cannot_see_audit_tables(config: agent.AgentConfig) -> None:
    ask("?", config, ScriptedChat(text("ok")))  # creates agent_log in the audit file
    outcome = tools.ToolSession(tools.ToolContext(db_path=config.db_path)).execute(
        "run_sql", json.dumps({"query": "SELECT * FROM agent_log"})
    )
    assert not outcome.success and "not allowed" in outcome.result["error"]


# ---------------------------------------------------------------- answer number guard


def test_answer_guard_passes_numbers_from_tools(config: agent.AgentConfig) -> None:
    fake = ScriptedChat(
        calls(tool_call("get_kpis", WEEK_ARGS)),
        text("Week 39 (21-27 Sep 2026): net revenue 114,287 from 661 orders, top 3 KPIs."),
    )
    answer = ask("How did we do last week?", config, fake)
    assert answer.answer_guard == "ok" and answer.answer_guard_failed == []
    assert len(fake.requests) == 2  # no retry


def test_answer_guard_retry_succeeds(config: agent.AgentConfig) -> None:
    fake = ScriptedChat(
        calls(tool_call("get_kpis", WEEK_ARGS)),
        text("Net revenue was about 115,000, roughly 172.9 per order."),
        text("Net revenue was 114,286.82 from 661 orders."),
    )
    answer = ask("Net revenue last week?", config, fake)
    assert answer.answer == "Net revenue was 114,286.82 from 661 orders."
    assert answer.answer_guard == "retried_ok"
    assert answer.answer_guard_failed == ["115,000"]
    retry = fake.requests[-1]
    assert retry["tool_choice"] == "none"
    assert "115,000" in retry["messages"][-1]["content"]
    assert retry["messages"][-2]["role"] == "assistant"
    assert answer.cost_usd == pytest.approx(0.005)  # the retry is counted


def test_answer_guard_withholds_after_second_failure(config: agent.AgentConfig) -> None:
    fake = ScriptedChat(
        calls(tool_call("get_kpis", WEEK_ARGS)),
        text("Net revenue was 115,000."),
        text("Net revenue was 120,000."),
    )
    answer = ask("Net revenue last week?", config, fake)
    assert answer.answer_guard == "withheld"
    assert answer.answer_guard_failed == ["120,000"]
    assert answer.answer.startswith("(The written answer was withheld")
    assert "120,000" in answer.answer.splitlines()[0]
    assert "- Net revenue: 114,286.82 (" in answer.answer  # the tool result instead
    row = agent_log.read_questions(config.audit_path).iloc[0]
    assert (row["answer_guard"], row["answer_guard_failed"]) == ("withheld", "120,000")


def test_answer_guard_not_run_without_model_text(config: agent.AgentConfig) -> None:
    answer = ask("?", config, ScriptedChat(llm.LLMUnavailableError("down")))
    assert answer.answer_guard == "not_run"


def test_agent_log_gets_guard_columns_on_an_old_file(config: agent.AgentConfig) -> None:
    # An audit.duckdb created before the guard has no answer_guard columns.
    import duckdb

    old_create = agent_log.CREATE_AGENT_LOG.split("    answer_guard ")[0].rstrip().rstrip(",")
    with duckdb.connect(str(config.audit_path)) as con:
        con.execute(old_create + "\n)")
    ask("?", config, ScriptedChat(text("ok")))
    row = agent_log.read_questions(config.audit_path).iloc[0]
    assert row["answer_guard"] == "ok"


# ---------------------------------------------------------------- prompt


def test_system_prompt_contents(built: dict) -> None:
    prompt = prompts.build_system_prompt(built["db_path"], DATA_END)
    assert "Today is 2026-09-30" in prompt
    for table in sql_guard.STANDARD_TABLES:
        assert f"- {table}(" in prompt
    assert "order_date DATE" in prompt  # generated from the database
    assert "email VARCHAR" not in prompt  # blocked column is not advertised
    assert "| Net revenue | Sum of line values for orders with status `completed`" in prompt
    assert "Return rate | Returned orders / (completed + returned orders)" in prompt
    assert "orders.status: cancelled, completed, returned" in prompt
    assert "not_computed" in prompt and "get_prebuild_recommendations" in prompt
    assert prompt.count("```sql") == 5
    assert "{" not in prompt.split("## KPI definitions")[0]  # template fully filled


def test_prompt_last_month_and_last_week_at_data_end(built: dict) -> None:
    # Eval-style: DATA_END_DATE 2026-09-30 -> last month September, last week 21-27 Sep.
    prompt = prompts.build_system_prompt(built["db_path"], DATA_END)
    assert '"last month", "latest month": September 2026 (2026-09-01 to 2026-09-30)' in prompt
    assert '"last week", "latest week": 21 Sep to 27 Sep 2026 (2026-09-21 to 2026-09-27)' in prompt


def test_relative_periods_mid_month_use_previous_complete_month() -> None:
    lines = prompts.relative_periods(date(2026, 9, 15))  # a Tuesday
    assert "August 2026 (2026-08-01 to 2026-08-31)" in lines
    assert "07 Sep to 13 Sep 2026" in lines


def test_today_is_the_real_date_without_data_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATA_END_DATE", raising=False)
    assert prompts.today() == date.today()


@pytest.mark.parametrize("example", prompts.load_examples(), ids=lambda e: e["question"][:40])
def test_prompt_examples_run_and_match(built: dict, example: dict) -> None:
    result = sql_guard.run_query(example["sql"], built["db_path"])
    row = dict(zip(result.columns, result.rows[example["check"]["row"]], strict=True))
    for column, expected in example["check"]["values"].items():
        value = tools.json_value(row[column])
        if isinstance(expected, float):
            assert value == pytest.approx(expected)
        else:
            assert str(value) == str(expected)


# ---------------------------------------------------------------- chat CLI


def test_chat_cli_missing_key_is_one_line(capsys: pytest.CaptureFixture, monkeypatch) -> None:
    monkeypatch.setattr(chat, "load_dotenv", lambda: None)
    assert chat.main(["--question", "hi"]) == 1
    err = capsys.readouterr().err.strip()
    assert err.startswith("Error: OPENROUTER_API_KEY")
    assert "\n" not in err


def test_chat_cli_one_shot(
    capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch, config: agent.AgentConfig
) -> None:
    monkeypatch.setattr(chat, "load_dotenv", lambda: None)
    monkeypatch.setattr(chat.llm, "llm_settings", lambda: SETTINGS)
    seen: dict = {}

    def fake_ask(question: str, cfg: agent.AgentConfig, **kwargs: Any) -> agent.AgentAnswer:
        seen.update(question=question, model=cfg.model)
        return agent.AgentAnswer("q1", question, "661 orders.", model="other/m")

    monkeypatch.setattr(chat.agent, "ask", fake_ask)
    assert chat.main(["--question", "Orders last week?", "--model", "other/m"]) == 0
    out = capsys.readouterr().out
    assert "Analyst: 661 orders." in out and "model other/m" in out
    assert seen == {"question": "Orders last week?", "model": "other/m"}


# ---------------------------------------------------------------- progress events (Week 6)


def kinds(events: list[agent.ProgressEvent]) -> list[str]:
    """The event kinds, in order."""
    return [e.kind for e in events]


def test_progress_events_follow_each_step(config: agent.AgentConfig) -> None:
    events: list[agent.ProgressEvent] = []
    fake = ScriptedChat(
        calls(tool_call("get_kpis", WEEK_ARGS)),
        text("Net revenue was about 115,000."),
        text("Net revenue was 114,286.82.", fallback_used=True),
    )
    answer = agent.ask(
        "Net revenue last week?", config, chat=fake, settings=SETTINGS, on_progress=events.append
    )
    assert answer.answer == "Net revenue was 114,286.82."
    assert kinds(events) == [
        "thinking", "thinking_done", "tool_start", "tool_done",
        "thinking", "thinking_done", "guard_retry", "thinking", "thinking_done", "fallback",
    ]  # fmt: skip
    tool_done = events[3]
    assert (tool_done.tool, tool_done.success) == ("get_kpis", True)
    assert json.loads(tool_done.arguments) == WEEK_ARGS
    assert tool_done.result == answer.tool_calls[0].result
    assert all(e.seconds is not None and e.seconds >= 0 for e in events if e.kind.endswith("done"))
    assert "fb/m" in events[-1].label
    # The final (checked) answer is never in an event: it only comes back from ask().
    assert not any("114,286.82." in (e.label or "") for e in events)


def test_withheld_answer_is_reported(config: agent.AgentConfig) -> None:
    events: list[agent.ProgressEvent] = []
    fake = ScriptedChat(
        calls(tool_call("get_kpis", WEEK_ARGS)),
        text("Net revenue was 115,000."),
        text("Net revenue was 120,000."),
    )
    agent.ask("Net revenue?", config, chat=fake, settings=SETTINGS, on_progress=events.append)
    assert kinds(events)[-3:] == ["thinking", "thinking_done", "withheld"]


def test_a_failing_callback_never_loses_the_answer(config: agent.AgentConfig) -> None:
    def broken(event: agent.ProgressEvent) -> None:
        raise RuntimeError("display failed")

    fake = ScriptedChat(calls(tool_call("get_kpis", WEEK_ARGS)), text("Net revenue 114,286.82."))
    answer = agent.ask("Net?", config, chat=fake, settings=SETTINGS, on_progress=broken)
    assert answer.answer == "Net revenue 114,286.82." and answer.answer_guard == "ok"


def test_model_failure_is_reported_as_a_failed_step(config: agent.AgentConfig) -> None:
    events: list[agent.ProgressEvent] = []
    fake = ScriptedChat(llm.LLMUnavailableError("both down"))
    answer = agent.ask("Net?", config, chat=fake, settings=SETTINGS, on_progress=events.append)
    assert answer.answer == agent.UNAVAILABLE_ANSWER
    assert kinds(events) == ["thinking", "thinking_done"] and events[1].success is False


def test_without_a_callback_the_answer_is_unchanged(config: agent.AgentConfig) -> None:
    turns = (calls(tool_call("get_kpis", WEEK_ARGS)), text("Net revenue was 114,286.82."))
    plain = ask("Net revenue last week?", config, ScriptedChat(*turns))
    seen: list[agent.ProgressEvent] = []
    watched = agent.ask(
        "Net revenue last week?",
        config,
        chat=ScriptedChat(*turns),
        settings=SETTINGS,
        on_progress=seen.append,
    )
    assert (plain.answer, plain.answer_guard) == (watched.answer, watched.answer_guard)
    assert seen
