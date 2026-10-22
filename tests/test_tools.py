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


def test_run_sql_email_block_goes_back_to_the_model(session: tools.ToolSession) -> None:
    for query in ("SELECT * FROM customers", "SELECT email FROM customers"):
        outcome = run(session, "run_sql", query=query)
        assert not outcome.success
        assert "personal contact data" in outcome.result["error"] or (
            "List the columns you need" in outcome.result["error"]
        )


def test_make_chart_cannot_read_email(session: tools.ToolSession) -> None:
    outcome = run(
        session,
        "make_chart",
        query="SELECT email, customer_id FROM customers LIMIT 3",
        chart_type="bar",
        x="email",
        y="customer_id",
        title="Emails",
    )
    assert not outcome.success and "personal contact data" in outcome.result["error"]


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


DEMAND_TOOLS = ["get_customer_alerts", "get_forecast_accuracy", "get_prebuild_recommendations"]


@pytest.mark.parametrize("name", DEMAND_TOOLS)
def test_demand_tools_say_not_computed_without_a_snapshot(tmp_path: Path, name: str) -> None:
    # No planning.duckdb next to this (missing) sales database: never compute, explain.
    session = tools.ToolSession(tools.ToolContext(db_path=tmp_path / "sales.duckdb"))
    outcome = run(session, name)
    assert outcome.result["status"] == "not_computed"
    assert "python -m src.demand.run" in outcome.result["message"]


@pytest.fixture
def demand_session(built: dict, demand_runs: dict, tmp_path: Path) -> tools.ToolSession:
    """A session whose DATA_END_DATE is 2026-09-30, with the shared snapshots stored."""
    from datetime import date

    ctx = tools.ToolContext(
        db_path=built["db_path"], charts_dir=tmp_path, data_end=date(2026, 9, 30)
    )
    return tools.ToolSession(ctx)


def test_customer_alerts_filters_and_counts(demand_session: tools.ToolSession) -> None:
    outcome = run(demand_session, "get_customer_alerts", alert_type="overdue", severity="serious")
    result = outcome.result
    assert outcome.success and result["snapshot_as_of"] == "2026-09-30"
    [overdue] = result["lists"]
    rows = overdue["rows"]
    names = [r["customer_name"] for r in rows[:3]]
    assert names == ["Harbor & Pine Co.", "Kestrel Home Stores", "Bluegate Supplies"]
    assert all(r["severity"] == "serious" for r in rows)
    assert rows[0]["revenue_365d_shown"] == "25,660.17"
    counts = {(c["alert_type"], c["severity"]): c["customers"] for c in result["counts"]}
    assert overdue["total_count"] == counts[("overdue", "serious")]
    assert not any("email" in key for r in rows for key in r)


def test_customer_alert_lists_are_capped_at_10(demand_session: tools.ToolSession) -> None:
    result = run(demand_session, "get_customer_alerts").result
    counts = {}
    for c in result["counts"]:
        counts[c["alert_type"]] = counts.get(c["alert_type"], 0) + c["customers"]
    assert {item["alert_type"] for item in result["lists"]} == set(counts)
    for item in result["lists"]:
        assert len(item["rows"]) == min(item["total_count"], tools.DEMAND_LIST_CAP)
        assert item["total_count"] == counts[item["alert_type"]]
        assert item["more_count"] == max(0, item["total_count"] - tools.DEMAND_LIST_CAP)
        assert [r["rank"] for r in item["rows"]] == list(range(1, len(item["rows"]) + 1))
    serious = run(demand_session, "get_customer_alerts", severity="serious").result["lists"][0]
    assert (serious["total_count"], len(serious["rows"]), serious["more_count"]) == (41, 10, 31)


def test_declining_alerts_are_sorted_by_revenue_lost(demand_session: tools.ToolSession) -> None:
    from src.reports import builder

    [declining] = run(demand_session, "get_customer_alerts", alert_type="declining").result["lists"]
    rows = declining["rows"]
    lost = [r["revenue_lost"] for r in rows]
    assert lost == sorted(lost, reverse=True) and lost[0] > 0
    for r in rows:
        assert r["revenue_lost_shown"] == builder.fmt_money(r["revenue_lost"])
        # revenue_lost is prior minus recent, rounded once (so within a cent of the rounded parts)
        assert abs(r["revenue_lost"] - (r["revenue_prior"] - r["revenue_recent"])) <= 0.011
    names = [r["customer_name"] for r in rows[:3]]
    assert names == ["Griffiths-Williams", "Bird-Kelly", "Kestrel Home Stores"]


def test_customer_alerts_reads_the_latest_snapshot_on_or_before_data_end(
    built: dict, demand_runs: dict, tmp_path: Path
) -> None:
    from datetime import date

    ctx = tools.ToolContext(db_path=built["db_path"], data_end=date(2026, 9, 29))
    outcome = run(tools.ToolSession(ctx), "get_customer_alerts")
    assert outcome.result["snapshot_as_of"] == "2026-09-27"


def test_forecast_accuracy_for_one_product_and_category(demand_session: tools.ToolSession) -> None:
    product = run(demand_session, "get_forecast_accuracy", sku_or_name="AP-1150").result["product"]
    assert product["key"] == "AP-1150" and product["flag_bias"] is True
    assert product["bias_shown"] == builder_change(product["bias"])
    category = run(demand_session, "get_forecast_accuracy", category="apparel").result
    assert category["category"]["key"] == "Apparel"
    assert all(p["category"] == "Apparel" for p in category["flagged_products"]["rows"])
    everything = run(demand_session, "get_forecast_accuracy").result
    assert everything["by_category"]["total_count"] == 8
    flagged = everything["flagged_products"]
    assert flagged["total_count"] >= 1 and len(flagged["rows"]) <= tools.DEMAND_LIST_CAP
    assert flagged["more_count"] == max(0, flagged["total_count"] - tools.DEMAND_LIST_CAP)
    bias = [abs(r["bias"]) for r in flagged["rows"]]
    assert bias == sorted(bias, reverse=True)
    bad = run(demand_session, "get_forecast_accuracy", category="Garden")
    assert not bad.success and "use one of" in bad.result["error"]


def test_forecast_units_are_shown_to_one_decimal(demand_session: tools.ToolSession) -> None:
    """Forecast units are not whole: shown as e.g. '500.1', so they agree with the bias."""
    product = run(demand_session, "get_forecast_accuracy", sku_or_name="AP-1150").result["product"]
    assert product["forecast_units_shown"] == f"{product['forecast_units']:,.1f}"
    assert "." in product["forecast_units_shown"]
    assert "." not in product["actual_units_shown"]  # actual units stay whole
    assert tools.fmt_units_1dp(500.09999999999997) == "500.1"
    assert tools.fmt_units_1dp(None) == "n/a"


def test_shown_text_is_rounded_once_from_the_unrounded_value() -> None:
    """0.27249996 is +27.2%; rounding to 4 decimals first (0.2725) would show +27.3%."""
    row = {"bias": 0.27249996, "wape": 0.27249996, "forecast_units": 500.04999, "weeks": 8}
    shown = tools.record(row, tools.ACCURACY_FIELDS)
    assert shown["bias"] == 0.2725  # the raw value for the model keeps 4 decimals
    assert shown["bias_shown"] == "+27.2%"
    assert shown["wape_shown"] == "27.2%"
    assert shown["forecast_units_shown"] == "500.0"
    assert shown["weeks_shown"] == "8"
    nan = tools.record({"bias": float("nan")}, {"bias": tools.ACCURACY_FIELDS["bias"]})
    assert nan == {"bias": None, "bias_shown": "n/a"}
    money = tools.record({"revenue_lost": 1234.5649}, tools.CUSTOMER_ALERT_FIELDS)
    assert money["revenue_lost"] == 1234.56 and money["revenue_lost_shown"] == "1,234.56"


def builder_change(bias: float) -> str:
    """Bias fraction shown as a signed percentage, like the reports."""
    from src.reports import builder

    return builder.fmt_change(bias * 100, "pct")


def test_prebuild_recommendations_are_read_only(demand_session: tools.ToolSession) -> None:
    result = run(demand_session, "get_prebuild_recommendations", status="pending").result
    recs = result["recommendations"]
    assert recs["total_count"] > tools.DEMAND_LIST_CAP and len(recs["rows"]) == 10
    assert recs["more_count"] == recs["total_count"] - 10
    assert [r["rank"] for r in recs["rows"]] == list(range(1, 11))
    assert all(r["status"] == "pending" for r in recs["rows"])
    review = result["needs_planner_review"]
    assert review["total_count"] >= 1 and len(review["rows"]) <= tools.DEMAND_LIST_CAP
    assert "cannot change a status" in result["definitions"]
    assert not hasattr(tools, "approve")


def test_demand_tools_validate_arguments(demand_session: tools.ToolSession) -> None:
    bad = run(demand_session, "get_customer_alerts", severity="critical")
    assert bad.result["error"] == "invalid_arguments"
    bad = run(demand_session, "get_prebuild_recommendations", status="proposed")
    assert bad.result["error"] == "invalid_arguments"
    bad = run(demand_session, "get_forecast_accuracy", weeks=8)  # not an argument
    assert bad.result["error"] == "invalid_arguments"
    for name in ("get_customer_alerts", "get_prebuild_recommendations"):  # lists are capped at 10
        assert run(demand_session, name, limit=50).result["error"] == "invalid_arguments"


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


def test_make_chart_schema_keeps_its_title_argument() -> None:
    """Regression: stripping pydantic 'title' metadata once removed the title argument."""
    parameters = tools.TOOLS["make_chart"].schema()["function"]["parameters"]
    assert "title" in parameters["properties"]
    assert set(parameters["required"]) <= set(parameters["properties"])
    for schema in tools.tool_schemas():
        params = schema["function"]["parameters"]
        assert set(params.get("required", [])) <= set(params["properties"])


def test_sql_error_is_a_clear_message(session: tools.ToolSession) -> None:
    outcome = run(session, "run_sql", query="SELECT no_such_column FROM orders")
    assert not outcome.success
    assert outcome.result["error"].startswith("The query failed:")
