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
