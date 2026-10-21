"""Week 5 acceptance tests: the 5 planted problems, seen by demand planning (no LLM calls).

This is the only test file that reads the planted issues (data/planted_issues.json;
the shared `built` fixture holds the same list the generator writes to that file).
Alert and forecast code never read it; test_planted_issues_are_not_read_by_src checks.

Dates (docs/decisions.md, Week 5):
- Problems 1 to 4 as of 2026-09-27 (the weekly sales review), problem 1 also in the
  September monthly report (2026-09-30).
- Problem 5 as of 2026-09-30: its low-cover stock is in the 2026-09-28 snapshot,
  which an as_of of 2026-09-27 cannot see. Pre-build ranks urgent first, then by
  value at risk.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from src.reports import builder

WEEKLY_AS_OF = date(2026, 9, 27)
MONTH_END = date(2026, 9, 30)
BIAS_RANGE = (0.25, 0.30)
MAX_PREBUILD_RANK = 2

pytestmark = pytest.mark.slow  # real forecast fits (conftest.real_demand_runs)


def planted(built: dict, problem_id: int) -> dict:
    """One planted problem from the generator's list."""
    return next(issue for issue in built["issues"] if issue["id"] == problem_id)


@pytest.fixture(scope="module")
def weekly_report(built: dict, real_demand_runs: dict, tmp_path_factory: pytest.TempPathFactory):
    """The weekly sales review for 2026-09-27, without an LLM summary."""
    out = tmp_path_factory.mktemp("acceptance")
    return builder.build_report(
        "weekly_sales_review",
        WEEKLY_AS_OF,
        db_path=real_demand_runs["db_path"],
        output_root=out,
        summary=False,
    )


@pytest.fixture(scope="module")
def monthly_report(built: dict, real_demand_runs: dict, tmp_path_factory: pytest.TempPathFactory):
    """The September 2026 monthly business review, without an LLM summary."""
    out = tmp_path_factory.mktemp("acceptance_monthly")
    return builder.build_report(
        "monthly_business_review",
        MONTH_END,
        db_path=real_demand_runs["db_path"],
        output_root=out,
        summary=False,
    )


def demand_group(report: builder.Report, key: str) -> dict:
    """One list of the report's Demand alerts section."""
    groups = report.numbers["sections"]["demand_alerts"]["groups"]
    return next(g for g in groups if g["key"] == key)


def serious_overdue(result) -> set[str]:
    """Names of customers with a serious overdue alert in a snapshot."""
    alerts = result.table("customer_alerts")
    rows = alerts[(alerts["alert_type"] == "overdue") & (alerts["severity"] == "serious")]
    return set(rows["customer_name"])


# ---------------------------------------------------------------- problem 1: wholesale churn


def test_churned_wholesale_customers_are_serious_overdue_on_month_end(
    built: dict, real_demand_runs: dict
) -> None:
    names = set(planted(built, 1)["customers"])
    assert names <= serious_overdue(real_demand_runs[MONTH_END])


def test_september_monthly_report_shows_the_churned_customers(
    built: dict, monthly_report: builder.Report
) -> None:
    names = planted(built, 1)["customers"]
    overdue = demand_group(monthly_report, "overdue")
    shown = [row["customer_name"] for row in overdue["rows"][: overdue["email_rows"]]]
    text = builder_text(monthly_report)
    for name in names:
        assert name in shown, name
        assert name in text, name


def test_weekly_review_flags_the_churned_customers(
    built: dict, weekly_report: builder.Report
) -> None:
    overdue = demand_group(weekly_report, "overdue")
    shown = {row["customer_name"]: row for row in overdue["rows"][: overdue["email_rows"]]}
    for name in planted(built, 1)["customers"]:
        assert shown[name]["severity"] == "serious", name


# ---------------------------------------------------------------- problem 2: Apparel decline


def test_weekly_review_flags_the_apparel_decline(
    built: dict, weekly_report: builder.Report
) -> None:
    category = planted(built, 2)["category"]
    flagged = demand_group(weekly_report, "category")["rows"]
    assert [row["category"] for row in flagged] == [category]
    assert flagged[0]["revenue_change_pct"] < -15


# ---------------------------------------------------------------- problem 3: stockouts


def test_weekly_review_flags_exactly_the_10_stockout_products(
    built: dict, weekly_report: builder.Report
) -> None:
    products = planted(built, 3)["products"]
    stock = demand_group(weekly_report, "stock")
    assert stock["count"] == len(products) == 10
    by_sku = {row["sku"]: row for row in stock["rows"]}
    assert set(by_sku) == {p["sku"] for p in products}
    for product in products:
        assert by_sku[product["sku"]]["stockout_weeks"] == product["weeks"], product["sku"]


# ---------------------------------------------------------------- problem 4: forecast bias


def test_weekly_review_flags_the_denim_jacket_bias(
    built: dict, weekly_report: builder.Report, real_demand_runs: dict
) -> None:
    sku = planted(built, 4)["sku"]
    flags = {row["key"]: row for row in demand_group(weekly_report, "forecast_flags")["rows"]}
    assert sku in flags
    accuracy = real_demand_runs[WEEKLY_AS_OF].table("forecast_accuracy")
    row = accuracy[(accuracy["level"] == "product") & (accuracy["key"] == sku)].iloc[0]
    assert row["flag_bias"] and BIAS_RANGE[0] <= row["bias"] <= BIAS_RANGE[1]
    assert row["weeks"] == len(planted(built, 4)["weeks"])


# ---------------------------------------------------------------- problem 5: pre-build need


def test_smart_bottle_gets_a_prebuild_ranked_first_or_second(
    built: dict, real_demand_runs: dict
) -> None:
    sku = planted(built, 5)["sku"]
    recs = real_demand_runs[MONTH_END].table("prebuild_recommendations")
    row = recs[recs["sku"] == sku]
    assert not row.empty, f"no pre-build recommendation for {sku}"
    assert row.iloc[0]["recommended_units"] > 0
    assert row.iloc[0]["rank"] <= MAX_PREBUILD_RANK, (
        f"{sku} ranks {row.iloc[0]['rank']}; "
        f"ahead of it: {recs.head(int(row.iloc[0]['rank']) - 1)['sku'].tolist()}"
    )


def test_smart_bottle_uses_the_month_end_stock_snapshot(
    built: dict, real_demand_runs: dict
) -> None:
    issue = planted(built, 5)
    recs = real_demand_runs[MONTH_END].table("prebuild_recommendations")
    row = recs[recs["sku"] == issue["sku"]].iloc[0]
    assert pd.Timestamp(row["snapshot_date"]).date().isoformat() == issue["latest_snapshot"]
    assert row["on_hand"] == issue["latest_on_hand"] and row["on_order"] == issue["latest_on_order"]


# ---------------------------------------------------------------- isolation


def test_planted_issues_are_not_read_by_src() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    for path in root.rglob("*.py"):
        if path.name == "generate.py":  # the generator writes the file
            continue
        text = path.read_text(encoding="utf-8")
        assert "planted_issues" not in text and "ISSUES_PATH" not in text, path


def builder_text(report: builder.Report) -> str:
    """The email as the reader sees it."""
    import html
    import re

    return html.unescape(re.sub(r"<[^>]+>", " ", report.html))
