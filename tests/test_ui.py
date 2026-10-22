"""Dashboard tests with Streamlit's AppTest (Week 6): every page loads, approvals go
through decide(), and the chat page shows what agent.ask returns.

All data is the temporary seed-42 database and planning snapshots from conftest.py;
approval tests write to their own copy of planning.duckdb. The chat agent is mocked,
and load_dotenv is replaced, so no test reads .env or reaches OpenRouter.
"""

import shutil
from datetime import date
from pathlib import Path

import dotenv
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from src.agent import agent, tools
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
    assert at.metric[0].value == net["value_shown"]
    assert "previous week" in at.metric[0].delta
    at.button_group[0].set_value("Month").run()  # day / week / month toggle
    assert not at.exception
    assert any("September 2026" in h.value for h in at.subheader)


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
    at.radio[0].set_value("Dry runs").run()
    assert not at.exception
    assert at.get("download_button")
    [page] = at.get("html")
    assert 'src="data:image/png;base64,' in page.proto.body  # the saved chart, inlined


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
    assert any(rec_id in s.value for s in at.success)
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


def fake_answer(question: str, guard: str = "ok") -> agent.AgentAnswer:
    """An answer like agent.ask returns, with one tool call."""
    call = tools.ToolOutcome(
        name="get_kpis",
        arguments='{"period_type": "week"}',
        result={"kpis": [{"kpi": "net_revenue", "current": 114286.82}]},
        success=True,
    )
    return agent.AgentAnswer(
        question_id="q",
        question=question,
        answer="Net revenue was 114,286.82.",
        model="test/model",
        tool_calls=[call],
        input_tokens=1200,
        output_tokens=80,
        cost_usd=0.0004,
        latency_seconds=1.5,
        answer_guard=guard,
    )


def test_chat_page_uses_agent_ask_one_question_at_a_time(
    app_env: None, built: dict, planning: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def ask(question: str, config: agent.AgentConfig, **kwargs: object) -> agent.AgentAnswer:
        asked.append(question)
        assert config.data_end == AS_OF and config.db_path == built["db_path"]
        return fake_answer(question, guard="withheld" if len(asked) == 2 else "ok")

    monkeypatch.setattr(agent, "ask", ask)
    at = app(built, planning, tmp_path).run().switch_page("app_pages/chat.py").run()
    at.chat_input[0].set_value("Net revenue last week?").run()
    at.chat_input[0].set_value("And the week before?").run()
    assert not at.exception
    assert asked == ["Net revenue last week?", "And the week before?"]  # single-turn
    assert len(at.session_state["chat_history"]) == 2
    markdown = " ".join(m.value for m in at.markdown)
    assert "Net revenue was 114,286.82." in markdown and "get_kpis" in markdown
    captions = " ".join(c.value for c in at.caption)
    assert "model test/model" in captions and "1,280 total" in captions and "$0.0004" in captions
    assert any("withheld" in w.value for w in at.warning)
    at.button[0].click().run()  # Clear chat
    assert at.session_state["chat_history"] == []
