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
    assert "&#x27;&lt;script&gt;alert(1)&lt;/script&gt;&#x27;" in html  # the SQL block
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
    """Sparkles while thinking, wrench while a tool runs, check, cross, info for notes."""
    thinking = chat_steps.row_html(chat_steps.Step(kind=chat_steps.THINKING), True)
    running = chat_steps.row_html(chat_steps.Step(tool="run_sql"), True)
    done = chat_steps.row_html(chat_steps.Step(tool="run_sql", state="ok"))
    failed = chat_steps.row_html(chat_steps.Step(tool="run_sql", state="fail"))
    note = chat_steps.row_html(chat_steps.Step(note="Fallback model", state="ok"))
    assert "i-sparkles" in thinking and "is-active step-thinking" in thinking
    assert "i-wrench" in running and "is-active step-running" in running
    assert "i-circle-check" in done and "step-ok" in done
    assert "i-circle-x" in failed and "step-fail" in failed
    assert "i-info" in note and "step-note" in note
    live = chat_steps.live_html(
        [chat_steps.Step(tool="run_sql", state="ok"), chat_steps.Step(kind=chat_steps.REVIEW)]
    )
    assert live.count("i-sparkles") == 1  # the model reviews the tool result


def test_collapsed_line_marks_a_failed_tool() -> None:
    ok = chat_steps.Step(tool="run_sql", state="ok")
    failed = chat_steps.Step(tool="get_kpis", state="fail")
    assert "steps-failed" not in chat_steps.collapsed_html([ok], 1.0)
    assert '<details class="steps steps-done steps-failed">' in chat_steps.collapsed_html(
        [ok, failed], 1.0
    )


def test_durations_are_never_shown_as_zero_seconds() -> None:
    text = chat_steps.duration_text
    assert text(None) == ""
    assert text(0.0) == text(0.0004) == "<1 ms"
    assert text(0.03) == "30 ms" and text(0.84) == "840 ms"
    assert text(0.9996) == "1.0 s"  # rounds up to a second, not "1000 ms"
    assert text(7.3) == "7.3 s" and text(42.4) == "42 s"
    assert text(75) == "1 min 15 s" and text(120) == "2 min"
    assert "0.0 s" not in {text(s) for s in (0.0, 0.0004, 0.04, 0.0499)}


def test_model_and_check_steps_have_their_own_labels_icons_and_times() -> None:
    thinking = chat_steps.Step(kind=chat_steps.THINKING, state="ok", seconds=0.84)
    review = chat_steps.Step(kind=chat_steps.REVIEW)
    check = chat_steps.Step(kind=chat_steps.CHECK, state="ok", seconds=0.003)
    assert (thinking.label(), review.label(), check.label()) == (
        "Thought",
        "Reviewing results",
        "Checked numbers",
    )
    assert (
        chat_steps.Step(kind=chat_steps.CHECK, state="fail").label() == "Checked numbers (failed)"
    )
    assert "i-shield-check" in chat_steps.row_html(chat_steps.Step(kind=chat_steps.CHECK), True)
    assert "i-pencil-line" in chat_steps.row_html(chat_steps.Step(kind=chat_steps.WRITE), True)
    assert '<span class="step-time">840 ms</span>' in chat_steps.row_html(thinking)
    assert '<span class="step-time">3 ms</span>' in chat_steps.row_html(check)
    assert '<span class="step-time"></span>' in chat_steps.row_html(review, True)  # no time yet
    # Only tool calls are counted in the summary line.
    assert chat_steps.summary_text([thinking, review, check], 1.0) == "No tools used · 1.0 s"


def test_sql_is_behind_a_closed_toggle_and_highlighted_safely() -> None:
    query = f"SELECT sum(total) FROM orders WHERE note = '{SCRIPT}' -- {IMG}"
    step = chat_steps.Step(
        tool="run_sql", state="ok", arguments=json.dumps({"query": query}), result={"rows": []}
    )
    html = chat_steps.detail_html(step)
    assert '<details class="step-sql-toggle"><summary>View SQL query</summary>' in html
    assert '<details class="step-sql-toggle" open' not in html  # closed by default
    assert '<span class="sql-kw">SELECT</span>' in html
    assert '<span class="sql-str">' in html and '<span class="sql-com">' in html
    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html and "&lt;img" in html
    # Other tools get no SQL toggle.
    other = chat_steps.Step(tool="get_kpis", state="ok", arguments=json.dumps({"query": "x"}))
    assert "step-sql-toggle" not in chat_steps.detail_html(other)
    assert chat_steps.highlight_sql("select 1") == (
        '<span class="sql-kw">select</span> <span class="sql-num">1</span>'
    )
