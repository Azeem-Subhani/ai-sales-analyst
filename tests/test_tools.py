"""Agent tool tests on the seed-42 database (plan section 13).

Known figures for the week ending 2026-09-27 were hand-checked in Week 2
(docs/hand_checks/week2_weekly_2026-09-27.md): net revenue 114,286.82, 661 orders.
"""

import json
from pathlib import Path

import pytest

from src.agent import sql_guard, tools


@pytest.fixture
def session(built: dict, tmp_path: Path) -> tools.ToolSession:
    """A tool session on the test database, writing charts to a temporary folder."""
    return tools.ToolSession(tools.ToolContext(db_path=built["db_path"], charts_dir=tmp_path))


def run(session: tools.ToolSession, name: str, **args: object) -> tools.ToolOutcome:
    """Call a tool the way the agent loop does: by name with JSON arguments."""
    return session.execute(name, json.dumps(args))


def test_seven_tools_registered_with_generated_schemas() -> None:
    assert list(tools.TOOLS) == [
        "get_kpis",
        "get_forecast",
        "run_sql",
        "make_chart",
        "get_customer_alerts",
        "get_forecast_accuracy",
        "get_prebuild_recommendations",
    ]
    for schema in tools.tool_schemas():
        function = schema["function"]
        name = function["name"]
        assert function["parameters"] == tools.without_titles(
            tools.TOOLS[name].args_model.model_json_schema()
        )
        assert function["parameters"]["type"] == "object"
        assert function["description"]


def test_get_kpis_weekly_known_figures(session: tools.ToolSession) -> None:
    outcome = run(session, "get_kpis", period_type="week", as_of="2026-09-27")
    assert outcome.success
    result = outcome.result
    assert result["period"]["label"] == "Week 39, 21 Sep to 27 Sep 2026"
    assert list(result["comparisons"]) == ["previous_week", "same_week_last_year"]
    by_kpi = {row["kpi"]: row for row in result["kpis"]}
    assert by_kpi["net_revenue"]["value"] == 114286.82
    assert by_kpi["net_revenue"]["shown"] == "114,286.82"
    assert by_kpi["orders"]["value"] == 661
    assert by_kpi["aov"]["shown"] == "172.90"
    assert by_kpi["gross_margin_pct"]["shown"] == "43.1%"
    assert by_kpi["net_revenue"]["previous_week"]["change_shown"].endswith("%")
    assert by_kpi["gross_margin_pct"]["previous_week"]["change_shown"].endswith("pp")
    assert outcome.row_count == 13


def test_get_kpis_partial_week_is_a_clear_error(session: tools.ToolSession) -> None:
    outcome = run(session, "get_kpis", period_type="week", as_of="2026-09-26")
    assert not outcome.success
    assert "Sunday" in outcome.result["error"]


def test_get_kpis_after_data_end_is_a_clear_error(session: tools.ToolSession) -> None:
    outcome = run(session, "get_kpis", period_type="day", as_of="2026-10-05")
    assert "after the last order date" in outcome.result["error"]


def test_run_sql_matches_get_kpis(session: tools.ToolSession) -> None:
    outcome = run(
        session,
        "run_sql",
        query="SELECT count(*) AS orders FROM orders WHERE status = 'completed' "
        "AND order_date BETWEEN DATE '2026-09-21' AND DATE '2026-09-27'",
    )
    assert outcome.success
    assert outcome.result["rows"] == [[661]]
    assert outcome.row_count == 1


def test_run_sql_truncation_note(session: tools.ToolSession) -> None:
    outcome = run(session, "run_sql", query="SELECT order_id FROM orders")
    assert outcome.result["truncated"] is True
    assert outcome.result["row_count"] == 500
    assert "truncated" in outcome.result["note"]


def test_run_sql_guard_error_goes_back_to_the_model(session: tools.ToolSession) -> None:
    outcome = run(session, "run_sql", query="DELETE FROM orders")
    assert not outcome.success
    assert "Only SELECT" in outcome.result["error"]


def test_get_forecast_classic_denim_jacket(session: tools.ToolSession, built: dict) -> None:
    outcome = run(session, "get_forecast", sku_or_name="AP-1150", weeks=8)
    assert outcome.success
    result = outcome.result
    assert result["product"]["name"] == "Classic Denim Jacket"
    assert len(result["weeks"]) == 8
    assert result["weeks"][0]["week_start"] == "2026-08-03"
    # Check against SQL: total forecast and actual demand over the 8 weeks.
    expected = sql_guard.run_query(
        "SELECT sum(f.forecast_qty), (SELECT sum(oi.quantity) FROM orders o "
        "JOIN order_items oi USING (order_id) JOIN products p USING (product_id) "
        "WHERE p.sku = 'AP-1150' AND o.status <> 'cancelled' "
        "AND o.order_date BETWEEN DATE '2026-08-03' AND DATE '2026-09-27') "
        "FROM forecasts f JOIN products p USING (product_id) WHERE p.sku = 'AP-1150'",
        built["db_path"],
    ).rows[0]
    assert sum(w["forecast_units"] for w in result["weeks"]) == pytest.approx(float(expected[0]))
    assert sum(w["actual_units"] for w in result["weeks"]) == expected[1]
    # The planted bias: forecasts run well above actual demand.
    assert sum(w["forecast_units"] for w in result["weeks"]) > 1.15 * expected[1]


def test_get_forecast_by_exact_name_and_ambiguous_name(session: tools.ToolSession) -> None:
    by_name = run(session, "get_forecast", sku_or_name="classic denim jacket", weeks=2)
    assert by_name.success and len(by_name.result["weeks"]) == 2
    ambiguous = run(session, "get_forecast", sku_or_name="denim")
    assert not ambiguous.success
    assert "AP-1150" in ambiguous.result["error"]


def test_make_chart_writes_html_from_sql(session: tools.ToolSession, tmp_path: Path) -> None:
    outcome = run(
        session,
        "make_chart",
        query="SELECT date_trunc('month', order_date) AS month, count(*) AS orders "
        "FROM orders WHERE status = 'completed' GROUP BY 1 ORDER BY 1",
        chart_type="line",
        x="month",
        y="orders",
        title="Completed orders by month",
    )
    assert outcome.success
    path = Path(outcome.result["chart_path"])
    assert path.parent == tmp_path and path.suffix == ".html"
    assert "plotly" in path.read_text(encoding="utf-8").lower()
    assert outcome.result["row_count"] == 24


def test_make_chart_uses_the_guard_and_checks_columns(session: tools.ToolSession) -> None:
    blocked = run(
        session,
        "make_chart",
        query="SELECT * FROM read_csv('x')",
        chart_type="bar",
        x="a",
        y="b",
        title="t",
    )
    assert "not allowed" in blocked.result["error"]
    wrong = run(
        session,
        "make_chart",
        query="SELECT 1 AS a, 2 AS b",
        chart_type="bar",
        x="a",
        y="c",
        title="t",
    )
    assert "'c' is not in the query result" in wrong.result["error"]


@pytest.mark.parametrize(
    "name", ["get_customer_alerts", "get_forecast_accuracy", "get_prebuild_recommendations"]
)
def test_stub_tools_return_not_available_yet(session: tools.ToolSession, name: str) -> None:
    outcome = run(session, name)
    assert outcome.result == {"status": "not_available_yet", "message": "Built in Week 5"}


def test_stub_tools_validate_their_final_arguments(session: tools.ToolSession) -> None:
    ok = run(session, "get_customer_alerts", alert_type="overdue", region="London")
    assert ok.result["status"] == "not_available_yet"
    bad = run(session, "get_forecast_accuracy", weeks=99)
    assert bad.result["error"] == "invalid_arguments"


def test_invalid_arguments_two_retries_then_final(session: tools.ToolSession) -> None:
    first = run(session, "get_kpis", period_type="year", as_of="2026-09-27")
    second = run(session, "get_kpis", period_type="week")
    third = session.execute("get_kpis", "{not json")
    assert [o.result["error"] for o in (first, second, third)] == [
        "invalid_arguments",
        "invalid_arguments",
        "invalid_arguments_final",
    ]
    assert first.result["retries_left"] == 2 and second.result["retries_left"] == 1
    assert "period_type" in first.result["details"]
    assert "as_of" in second.result["details"]
    # Valid calls still work after the limit; only invalid ones count.
    assert run(session, "get_kpis", period_type="week", as_of="2026-09-27").success


def test_unknown_tool_and_extra_argument(session: tools.ToolSession) -> None:
    unknown = run(session, "send_email", to="x")
    assert unknown.result["error"] == "invalid_arguments"
    extra = run(session, "run_sql", query="SELECT 1", limit=5)
    assert "limit" in extra.result["details"]
