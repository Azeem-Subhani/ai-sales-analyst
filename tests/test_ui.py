"""Dashboard tests with Streamlit's AppTest (Week 6): every page loads, approvals go
through decide(), and the chat page shows what agent.ask returns.

All data is the temporary seed-42 database and planning snapshots from conftest.py;
approval tests write to their own copy of planning.duckdb. The chat agent is mocked,
and load_dotenv is replaced, so no test reads .env or reaches OpenRouter.
"""

import re
import shutil
from datetime import date
from pathlib import Path

import dotenv
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from src.agent import agent, llm, tools
from src.demand import approve, store
from src.reports import builder, views
from src.ui import common

APP = Path(__file__).resolve().parents[1] / "src" / "ui" / "app.py"
AS_OF = date(2026, 9, 30)
TIMEOUT = 60  # seconds per run; the first sales view builds the order_lines view
PAGES = ["app_pages/sales.py", "app_pages/demand.py", "app_pages/chat.py", "app_pages/reports.py"]


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No .env, DATA_END_DATE 2026-09-30, and an empty dashboard cache."""
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("DATA_END_DATE", AS_OF.isoformat())
    st.cache_data.clear()


@pytest.fixture
def planning(demand_runs: dict, tmp_path: Path) -> Path:
    """A writable copy of the test planning.duckdb (snapshots of 27 and 30 Sep)."""
    target = tmp_path / "planning.duckdb"
    shutil.copy(demand_runs[AS_OF].planning_db, target)
    return target


@pytest.fixture
def saved_reports(tmp_path: Path) -> Path:
    """One saved dry-run report with a chart and an Excel file."""
    folder = tmp_path / "reports" / "weekly_sales_review_2026-09-27"
    folder.mkdir(parents=True)
    (folder / builder.HTML_FILENAME).write_text(
        '<p>Weekly</p><img src="revenue_trend_13w.png">', encoding="utf-8"
    )
    (folder / "revenue_trend_13w.png").write_bytes(b"\x89PNG fake")
    (folder / "weekly_sales_review_2026-09-27.xlsx").write_bytes(b"xlsx")
    return tmp_path / "reports"


def app(built: dict, planning: Path, tmp_path: Path, reports_root: Path | None = None) -> AppTest:
    """The dashboard pointed at the temporary files."""
    at = AppTest.from_file(str(APP), default_timeout=TIMEOUT)
    at.session_state[common.SALES_DB_KEY] = str(built["db_path"])
    at.session_state[common.PLANNING_DB_KEY] = str(planning)
    at.session_state[common.REPORTS_ROOT_KEY] = str(reports_root or tmp_path / "none")
    at.session_state[common.BACKFILL_ROOT_KEY] = str(tmp_path / "no_backfill")
    return at


# ---------------------------------------------------------------- every page loads


@pytest.mark.parametrize("page", PAGES)
def test_every_page_loads_without_exceptions(
    app_env: None, built: dict, planning: Path, saved_reports: Path, tmp_path: Path, page: str
) -> None:
    at = app(built, planning, tmp_path, saved_reports).run()
    assert not at.exception
    assert at.session_state[common.AS_OF_KEY] == AS_OF
    at.switch_page(page).run()
    assert not at.exception, [e.value for e in at.exception]


def test_sales_page_shows_report_numbers(
    app_env: None, built: dict, planning: Path, tmp_path: Path
) -> None:
    at = app(built, planning, tmp_path).run()
    view = views.sales_view("week", date(2026, 9, 27), built["db_path"])
    net = next(card for card in view["kpi_cards"] if card["kpi"] == "net_revenue")
    cards = html_text(at)
    assert f'<div class="card-value">{net["value_shown"]}</div>' in cards  # the KPI card
    first = net["comparisons"][0]
    assert f'{first["change_shown"]} <span class="vs">vs prev. week</span>' in cards
    assert f"tone-{first['tone']}" in cards
    at.button_group[0].set_value("Month").run()  # day / week / month toggle
    assert not at.exception
    assert '<div class="section-title">September 2026</div>' in html_text(at)


def test_demand_page_without_a_snapshot_shows_the_command(
    app_env: None, built: dict, planning: Path, tmp_path: Path
) -> None:
    at = app(built, planning, tmp_path)
    at.session_state[common.AS_OF_KEY] = date(2026, 9, 20)
    at.run().switch_page("app_pages/demand.py").run()
    assert not at.exception
    assert at.code[0].value == "python -m src.demand.run --as-of 2026-09-20"
    assert not at.button  # no button creates a snapshot


def test_reports_page_inlines_charts(
    app_env: None, built: dict, planning: Path, saved_reports: Path, tmp_path: Path
) -> None:
    at = app(built, planning, tmp_path, saved_reports).run()
    at.switch_page("app_pages/reports.py").run()
    at.button_group[0].set_value("Dry runs").run()
    assert not at.exception
    assert "Weekly sales review" in html_text(at)  # its card
    assert (
        at.button(key=f"view-{saved_reports / 'weekly_sales_review_2026-09-27'}").label == "Viewing"
    )
    assert at.get("download_button")
    bodies = [h.proto.body for h in at.get("html")]
    assert any('src="data:image/png;base64,' in body for body in bodies)  # chart inlined


def test_demand_tabs_show_counts_and_severity_tags(
    app_env: None, built: dict, planning: Path, tmp_path: Path
) -> None:
    at = app(built, planning, tmp_path).run().switch_page("app_pages/demand.py").run()
    assert not at.exception
    counts = views.demand_view(AS_OF, planning)["counts"]
    labels = [tab.label for tab in at.tabs]
    assert f":material/groups: Customer alerts \u00b7 {counts['customer_alerts']}" in labels
    assert any(f"Pre-build \u00b7 {counts['pending']}" in label for label in labels)
    overdue = at.dataframe[0].value
    assert set(overdue["Severity"]) <= {"\U0001f534 serious", "\U0001f7e0 overdue"}


def test_demand_forecast_units_have_one_decimal(planning: Path) -> None:
    """The dashboard shows forecast units to one decimal; actual units stay whole."""
    table = views.demand_view(AS_OF, planning)["accuracy_products"]
    assert table.formats()["Forecast units"] == "%.1f"
    assert table.formats()["Actual units"] == "%d"
    assert views.display_value(500.09999999999997, "units_1dp") == 500.1


# ---------------------------------------------------------------- approvals


def first_pending(planning: Path) -> str:
    """The rank-1 pending recommendation of 2026-09-30."""
    recs = store.read_table(planning, "prebuild_recommendations", AS_OF)
    return recs[recs["status"] == "pending"].sort_values("rank").iloc[0]["rec_id"]


def demand_page(built: dict, planning: Path, tmp_path: Path) -> AppTest:
    """The Demand page at 2026-09-30."""
    at = app(built, planning, tmp_path).run()
    return at.switch_page("app_pages/demand.py").run()


def test_approve_goes_through_decide_after_a_confirm_click(
    app_env: None, built: dict, planning: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple] = []
    real_decide = approve.decide

    def spy(*args: object, **kwargs: object) -> approve.Decision:
        calls.append((args, kwargs))
        return real_decide(*args, **kwargs)

    monkeypatch.setattr(approve, "decide", spy)
    rec_id = first_pending(planning)
    at = demand_page(built, planning, tmp_path)
    at.text_input(key="decided_by").set_value("Planner One")
    at.text_input(key="decision_note").set_value("demo check")
    at.button(key=f"approve-{rec_id}").click().run()
    assert not calls  # the first click only asks for confirmation
    at.button(key=f"confirm-{rec_id}").click().run()
    assert not at.exception
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:4] == (rec_id, "approve", "Planner One", "demo check")
    assert kwargs["planning_db"] == planning
    recs = store.read_table(planning, "prebuild_recommendations", AS_OF).set_index("rec_id")
    assert recs.loc[rec_id, "status"] == "approved"
    assert recs.loc[rec_id, "decided_by"] == "Planner One"
    assert any(rec_id in t.proto.body for t in at.toast)  # success toast
    assert at.session_state["decided_by"] == "Planner One"  # remembered for the session
    assert not [b for b in at.button if b.key and b.key.endswith(rec_id)]  # decided: no buttons


def test_reject_and_cancel(app_env: None, built: dict, planning: Path, tmp_path: Path) -> None:
    rec_id = first_pending(planning)
    at = demand_page(built, planning, tmp_path)
    at.text_input(key="decided_by").set_value("Planner Two")
    at.button(key=f"reject-{rec_id}").click().run()
    at.button(key=f"cancel-{rec_id}").click().run()
    recs = store.read_table(planning, "prebuild_recommendations", AS_OF).set_index("rec_id")
    assert recs.loc[rec_id, "status"] == "pending"
    at.button(key=f"reject-{rec_id}").click().run()
    at.button(key=f"confirm-{rec_id}").click().run()
    recs = store.read_table(planning, "prebuild_recommendations", AS_OF).set_index("rec_id")
    assert recs.loc[rec_id, "status"] == "rejected"


def test_decided_by_is_required(
    app_env: None, built: dict, planning: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(approve, "decide", pytest.fail)
    rec_id = first_pending(planning)
    at = demand_page(built, planning, tmp_path)
    at.button(key=f"approve-{rec_id}").click().run()
    at.button(key=f"confirm-{rec_id}").click().run()
    assert not at.exception
    assert any("Decided by" in e.value for e in at.error)


def test_a_failed_write_shows_a_clear_error(
    app_env: None, built: dict, planning: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def locked(*args: object, **kwargs: object) -> None:
        raise store.PlanningStoreError("planning.duckdb is in use by another process")

    monkeypatch.setattr(approve, "decide", locked)
    rec_id = first_pending(planning)
    at = demand_page(built, planning, tmp_path)
    at.text_input(key="decided_by").set_value("Planner One")
    at.button(key=f"approve-{rec_id}").click().run()
    at.button(key=f"confirm-{rec_id}").click().run()
    assert not at.exception
    assert any("in use by another process" in e.value for e in at.error)


# ---------------------------------------------------------------- chat

CHECKED = "Net revenue was 114,286.82."


def fake_answer(question: str, guard: str = "ok") -> agent.AgentAnswer:
    """An answer like agent.ask returns, with one tool call."""
    call = tools.ToolOutcome(
        name="get_kpis",
        arguments='{"period_type": "week"}',
        result={"kpis": [{"kpi": "net_revenue", "current": 114286.82}]},
        success=True,
        duration_seconds=0.2,
    )
    return agent.AgentAnswer(
        question_id="q",
        question=question,
        answer=CHECKED
        if guard != "withheld"
        else "(The written answer was withheld.) Net revenue: 114,286.82",
        model="test/model",
        tool_calls=[call],
        input_tokens=1200,
        output_tokens=80,
        cost_usd=0.0004,
        latency_seconds=1.5,
        answer_guard=guard,
    )


class FakeAgent:
    """Stands in for agent.ask: plays the progress events, then returns the answer."""

    def __init__(self, config_check: object = None) -> None:
        self.asked: list[str] = []
        self.events_seen_before_answer: list[str] = []
        self.config_check = config_check

    def __call__(self, question: str, config: agent.AgentConfig, **kwargs: object):
        self.asked.append(question)
        if self.config_check:
            self.config_check(config)
        guard = "withheld" if "withhold" in question else "ok"
        answer = fake_answer(question, guard)
        emit = kwargs["on_progress"]
        events = [
            agent.ProgressEvent(agent.EVENT_THINKING, agent.THINKING_LABEL),
            agent.ProgressEvent(
                agent.EVENT_THINKING_DONE, agent.THINKING_LABEL, seconds=0.8, success=True
            ),
            agent.ProgressEvent(agent.EVENT_TOOL_START, "get_kpis", tool="get_kpis"),
            agent.ProgressEvent(
                agent.EVENT_TOOL_DONE,
                "get_kpis",
                tool="get_kpis",
                seconds=0.2,
                success=True,
                arguments='{"period_type": "week"}',
                result=answer.tool_calls[0].result,
            ),
            agent.ProgressEvent(agent.EVENT_THINKING, agent.THINKING_LABEL),
            agent.ProgressEvent(
                agent.EVENT_THINKING_DONE, agent.THINKING_LABEL, seconds=0.5, success=True
            ),
        ]
        if guard == "withheld":
            events += [
                agent.ProgressEvent(agent.EVENT_GUARD_RETRY, agent.GUARD_RETRY_LABEL),
                agent.ProgressEvent(agent.EVENT_WITHHELD, agent.WITHHELD_LABEL),
            ]
        for event in events:
            emit(event)
            self.events_seen_before_answer.append(event.kind)
        return answer


def chat_page(built: dict, planning: Path, tmp_path: Path) -> AppTest:
    """The Chat page, empty."""
    return app(built, planning, tmp_path).run().switch_page("app_pages/chat.py").run()


def html_text(at: AppTest) -> str:
    """Every st.html body on the page, joined."""
    return " ".join(h.proto.body for h in at.get("html"))


def test_chat_shows_live_steps_then_the_checked_answer(
    app_env: None, built: dict, planning: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def check(config: agent.AgentConfig) -> None:
        assert config.data_end == AS_OF and config.db_path == built["db_path"]

    fake = FakeAgent(check)
    monkeypatch.setattr(agent, "ask", fake)
    at = chat_page(built, planning, tmp_path)
    at.chat_input[0].set_value("Net revenue last week?").run()
    assert not at.exception, [e.value for e in at.exception]
    assert fake.asked == ["Net revenue last week?"]
    markdown = " ".join(m.value for m in at.markdown)
    assert CHECKED in markdown
    html = html_text(at)
    assert '<div class="user-bubble">Net revenue last week?</div>' in html
    assert "test/model" in html and "1,280 tokens" in html and "$0.0004" in html
    # The steps collapse to one line: the tool's past-tense label and the total time,
    # opening to show the step's duration and its Data.
    assert "<summary>Calculated KPIs \u00b7 1.5 s</summary>" in html
    assert '<span class="step-time">0.2 s</span>' in html
    assert '<details class="step-data"><summary>Data</summary>' in html
    assert not at.get("status") and not at.get("spinner")  # no Streamlit status widgets
    assert len(at.session_state["chat_history"]) == 1


def test_chat_withheld_answer_is_a_warning(
    app_env: None, built: dict, planning: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "ask", FakeAgent())
    at = chat_page(built, planning, tmp_path)
    at.chat_input[0].set_value("Please withhold this").run()
    assert not at.exception
    assert any("withheld" in w.value for w in at.warning)
    assert "Number check failed" in html_text(at)
    assert CHECKED not in " ".join(m.value for m in at.markdown)


def test_chat_example_chip_sends_the_question(
    app_env: None, built: dict, planning: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAgent()
    monkeypatch.setattr(agent, "ask", fake)
    at = chat_page(built, planning, tmp_path)
    assert "Every number in an answer comes from a tool" in html_text(at)
    chips = [b for b in at.button if (b.key or "").startswith("example-")]
    assert len(chips) == 4
    at.button(key="example-1").click().run()  # "Stockouts in September"
    assert not at.exception
    assert fake.asked == ["Which products ran out of stock in September 2026?"]
    assert not [b for b in at.button if (b.key or "").startswith("example-")]  # no longer empty


def test_chat_history_and_clear(
    app_env: None, built: dict, planning: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAgent()
    monkeypatch.setattr(agent, "ask", fake)
    at = chat_page(built, planning, tmp_path)
    at.chat_input[0].set_value("First?").run()
    at.chat_input[0].set_value("Second?").run()
    assert not at.exception
    assert fake.asked == ["First?", "Second?"]  # single-turn: one call per question
    assert html_text(at).count('<details class="steps steps-done">') == 2  # history too
    next(b for b in at.button if b.label == "Clear chat").click().run()
    assert at.session_state["chat_history"] == []


def test_chat_page_shows_the_data_end_instead_of_the_date_picker(
    app_env: None, built: dict, planning: Path, tmp_path: Path
) -> None:
    at = chat_page(built, planning, tmp_path)
    assert not at.sidebar.date_input
    assert "Data through 30 Sep 2026" in html_text(at)  # the sidebar footer
    assert not any("Answers use data" in m.value for m in at.sidebar.markdown)
    at.switch_page("app_pages/sales.py").run()
    assert at.sidebar.date_input[0].value == AS_OF


# ---------------------------------------------------------------- input lock

BUSY_KEY = "chat_busy"


def test_chat_input_is_locked_while_answering_and_unlocked_after(
    app_env: None, built: dict, planning: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeAgent()
    busy_during_run: list[bool] = []

    def ask(question: str, config: agent.AgentConfig, **kwargs: object) -> agent.AgentAnswer:
        busy_during_run.append(st.session_state.get(BUSY_KEY, False))
        return fake(question, config, **kwargs)

    monkeypatch.setattr(agent, "ask", ask)
    at = chat_page(built, planning, tmp_path)
    at.chat_input[0].set_value("Net revenue last week?").run()
    assert not at.exception, [e.value for e in at.exception]
    assert busy_during_run == [True]  # the input was drawn disabled during the run
    assert at.session_state[BUSY_KEY] is False
    assert not at.chat_input[0].disabled  # enabled again with the answer shown
    assert len(at.session_state["chat_history"]) == 1


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (RuntimeError("tool crashed"), "Something went wrong"),
        (llm.LLMUnavailableError("OpenRouter timed out"), "The AI service isn't responding"),
    ],
    ids=["exception", "llm_unavailable"],
)
def test_chat_input_is_unlocked_when_answering_fails(
    app_env: None,
    built: dict,
    planning: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    message: str,
) -> None:
    def failing_ask(question: str, config: agent.AgentConfig, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(agent, "ask", failing_ask)
    at = chat_page(built, planning, tmp_path)
    at.chat_input[0].set_value("Net revenue last week?").run()
    assert not at.exception, [e.value for e in at.exception]  # a message, not a traceback
    assert at.session_state[BUSY_KEY] is False  # the finally block released the lock
    assert not at.chat_input[0].disabled
    [shown] = [e.value for e in at.error]
    assert shown.startswith(message)
    assert at.session_state["chat_history"] == []
    # The page still works: the next question is answered.
    monkeypatch.setattr(agent, "ask", FakeAgent())
    at.chat_input[0].set_value("Second try?").run()
    assert not at.exception
    assert len(at.session_state["chat_history"]) == 1


# ---------------------------------------------------------------- escaping and the meta line


def test_chat_question_with_markup_is_shown_as_text(
    app_env: None, built: dict, planning: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "ask", FakeAgent())
    at = chat_page(built, planning, tmp_path)
    at.chat_input[0].set_value('Hi <script>alert(1)</script> <img src=x onerror="x()">').run()
    assert not at.exception
    html = html_text(at)
    assert "<script>" not in html and "<img" not in html  # never placed as markup
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=&quot;x()&quot;&gt;" in html


@pytest.mark.parametrize("cost", [None, 0.0])
def test_chat_meta_line_hides_a_missing_or_zero_cost(
    app_env: None,
    built: dict,
    planning: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cost: float | None,
) -> None:
    fake = FakeAgent()

    def ask(question: str, config: agent.AgentConfig, **kwargs: object) -> agent.AgentAnswer:
        answer = fake(question, config, **kwargs)
        answer.cost_usd = cost
        return answer

    monkeypatch.setattr(agent, "ask", ask)
    at = chat_page(built, planning, tmp_path)
    at.chat_input[0].set_value("Net revenue last week?").run()
    body = next(h.proto.body for h in at.get("html") if 'class="chat-meta"' in h.proto.body)
    # The model and tokens line is inside the collapsed steps view only (Week 7).
    assert body.startswith('<details class="steps steps-done">')
    meta = re.search(r'<div class="chat-meta">(.*?)</div>', body).group(1)
    assert "test/model" in meta and "1,280 tokens" in meta and "1.5 s" in meta
    assert "$" not in meta and "cost" not in meta


def test_chat_empty_state_shows_four_suggestion_cards(
    app_env: None, built: dict, planning: Path, tmp_path: Path
) -> None:
    at = chat_page(built, planning, tmp_path)
    html = html_text(at)
    assert "Ask about sales and demand" in html
    assert html.count('<div class="suggestion">') == 4
    assert "Which wholesale customers have stopped ordering?" in html  # the full question
    assert not at.get("chat_message")  # no avatars: answers are plain text


def test_ok_after_retry_summary_card_is_green_like_ok(tmp_path: Path) -> None:
    from src.reports import saved
    from src.ui.app_pages import reports

    def card(status: str) -> str:
        report = saved.SavedReport(
            "backfill 2026-09-14", "daily_sales_snapshot", "2026-09-13", tmp_path,
            summary_status=status,
        )  # fmt: skip
        return reports.card_html(report)

    assert "tag-good" in card("ok") and "tag-good" in card("ok_after_retry")
    assert "summary ok_after_retry" in card("ok_after_retry")
