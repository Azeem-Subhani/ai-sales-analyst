"""Chat step display and dashboard HTML (Week 6 polish): labels for every tool, and every
dynamic string escaped before it goes into HTML. No Streamlit run, no API calls."""

import json
from pathlib import Path

from src.agent import tools
from src.ui import chat_steps, style

SCRIPT = "<script>alert(1)</script>"
IMG = '<img src=x onerror="alert(2)">'
STYLE_CSS = Path(__file__).resolve().parents[1] / "src" / "ui" / "style.css"


def test_step_labels_cover_every_agent_tool() -> None:
    assert set(chat_steps.STEP_LABELS) == set(tools.TOOLS)
    assert len(chat_steps.STEP_LABELS) == 7
    for running, done in chat_steps.STEP_LABELS.values():
        assert running.endswith("ing") or running.split()[0].endswith("ing"), running
        assert running != done


def test_step_label_is_present_tense_while_running_and_past_when_done() -> None:
    step = chat_steps.Step(tool="get_forecast_accuracy")
    assert step.label() == "Checking forecast accuracy"
    step.state = "ok"
    assert step.label() == "Checked forecast accuracy"
    step.state = "fail"
    assert step.label() == "Checked forecast accuracy (failed)"


def test_summary_line_for_one_and_several_steps() -> None:
    one = [chat_steps.Step(tool="get_forecast_accuracy", state="ok", seconds=7.0)]
    assert chat_steps.summary_text(one, 7.3) == "Checked forecast accuracy · 7.3 s"
    three = [chat_steps.Step(tool="run_sql", state="ok") for _ in range(3)]
    note = chat_steps.Step(note="Number check failed, asking the model again", state="ok")
    assert chat_steps.summary_text([*three, note], 9.1) == "3 steps · 9.1 s"  # notes not counted


def test_user_question_markup_is_escaped() -> None:
    html = style.user_message(f"Top products {SCRIPT} {IMG}")
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=&quot;alert(2)&quot;&gt;" in html


def test_sql_tool_result_and_labels_are_escaped() -> None:
    step = chat_steps.Step(
        tool="run_sql",
        state="ok",
        seconds=1.2,
        arguments=json.dumps({"query": f"SELECT '{SCRIPT}' AS x"}),
        result={"columns": ["x"], "rows": [[IMG]]},
    )
    note = chat_steps.Step(note=f"Main model failed {SCRIPT}", state="ok")
    html = chat_steps.collapsed_html([step, note], 1.5)
    assert "<script>" not in html and "<img" not in html
    assert "SELECT &#x27;&lt;script&gt;" in html  # the SQL block
    assert "&lt;img src=x onerror=" in html  # the Data block
    assert "Main model failed &lt;script&gt;" in html  # a note


def test_data_view_shows_at_most_20_rows_then_a_count() -> None:
    result = {"rows": [[n] for n in range(30)], "row_count": 30}
    shown = chat_steps.preview(result)
    assert len(shown["rows"]) == 21 and shown["rows"][-1] == "… 10 more rows"
    assert chat_steps.preview({"rows": [[1]] * 20})["rows"] == [[1]] * 20  # no count line


def test_style_css_has_no_angle_brackets() -> None:
    """Streamlit drops the whole stylesheet when the CSS contains '<' (even in a comment)."""
    assert "<" not in STYLE_CSS.read_text(encoding="utf-8")


def test_every_tool_gets_a_readable_label_never_the_raw_name() -> None:
    """Known tools use STEP_LABELS; any other tool name is turned into words."""
    for name in tools.TOOLS:
        running, done = chat_steps.readable_labels(name)
        assert "_" not in running and "_" not in done, name
    assert chat_steps.readable_labels("get_customer_list") == (
        "Getting customer list",
        "Got customer list",
    )
    step = chat_steps.Step(tool="get_customer_list", state="ok")
    assert step.label() == "Got customer list"
    assert chat_steps.readable_labels("frobnicate") == chat_steps.UNKNOWN_TOOL_LABELS


def test_step_rows_show_one_icon_per_state() -> None:
    """Brain while thinking, wrench while a tool runs, check, cross, info for notes."""
    thinking = chat_steps.row_html(chat_steps.Step(note=chat_steps.THINKING_LABEL), True)
    running = chat_steps.row_html(chat_steps.Step(tool="run_sql"), True)
    done = chat_steps.row_html(chat_steps.Step(tool="run_sql", state="ok"))
    failed = chat_steps.row_html(chat_steps.Step(tool="run_sql", state="fail"))
    note = chat_steps.row_html(chat_steps.Step(note="Fallback model", state="ok"))
    assert "i-brain" in thinking and "is-active step-thinking" in thinking
    assert "i-wrench" in running and "is-active step-running" in running
    assert "i-circle-check" in done and "step-ok" in done
    assert "i-circle-x" in failed and "step-fail" in failed
    assert "i-info" in note and "step-note" in note
    live = chat_steps.live_html([chat_steps.Step(tool="run_sql", state="ok")], thinking=True)
    assert live.count("i-brain") == 1  # the model works again after the tool


def test_collapsed_line_marks_a_failed_tool() -> None:
    ok = chat_steps.Step(tool="run_sql", state="ok")
    failed = chat_steps.Step(tool="get_kpis", state="fail")
    assert "steps-failed" not in chat_steps.collapsed_html([ok], 1.0)
    assert '<details class="steps steps-done steps-failed">' in chat_steps.collapsed_html(
        [ok, failed], 1.0
    )
