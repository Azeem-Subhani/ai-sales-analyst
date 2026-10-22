"""Tests for the dashboard's data functions (src/reports/views.py, src/reports/saved.py).

The dashboard shows these as they are, so they must give the same numbers as the
reports, rounded once.
"""

import csv
from datetime import date
from pathlib import Path

import pytest

from src.reports import backfill, builder, saved, views

AS_OF = date(2026, 9, 30)


@pytest.fixture(scope="module")
def reports(built: dict, tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The weekly and monthly reports, built without summaries."""
    out = tmp_path_factory.mktemp("view_reports")
    return {
        "week": builder.build_report(
            "weekly_sales_review", date(2026, 9, 27), built["db_path"], out, summary=False
        ),
        "month": builder.build_report(
            "monthly_business_review", AS_OF, built["db_path"], out, summary=False
        ),
    }


# ---------------------------------------------------------------- sales


@pytest.mark.parametrize("kind", ["week", "month"])
def test_sales_view_matches_the_report(built: dict, reports: dict, kind: str) -> None:
    view = views.sales_view(kind, AS_OF, built["db_path"])
    report = reports[kind]
    assert view["period"].label == report.numbers["period"]["label"]
    section = report.numbers["sections"][views.KPI_QUERIES[kind]]
    labels = {n: builder.COMPARISON_LABELS[n] for n in report.numbers["comparisons"]}
    assert view["kpi_cards"] == views.kpi_cards(section, labels)
    net = next(c for c in view["kpi_cards"] if c["kpi"] == "net_revenue")
    row = next(r for r in section["rows"] if r["kpi"] == "net_revenue")
    assert net["value_shown"] == builder.fmt_money(row["current"])
    first = next(iter(row["comparisons"].values()))
    assert net["comparisons"][0]["change_shown"] == builder.fmt_change(first["change"], "pct")
    rate = next(c for c in view["kpi_cards"] if c["kpi"] == "return_rate")
    assert rate["comparisons"][0]["change_shown"].endswith(" pp")
    top = view["top_products"].frame
    assert len(top) == builder.TOP_N
    report_top = report.numbers["sections"]["top_products"]["rows"]
    assert list(top["Name"]) == [r["name"] for r in report_top]
    assert list(top["Net revenue"]) == [r["net_revenue"] for r in report_top]


def test_sales_view_periods_and_trend(built: dict) -> None:
    week = views.sales_view("week", date(2026, 9, 30), built["db_path"])
    assert week["period"].label == "Week 39, 21 Sep to 27 Sep 2026"  # last complete week
    assert len(week["trend"].frame) == builder.TREND_WEEKS
    month = views.sales_view("month", AS_OF, built["db_path"])
    assert len(month["trend"].frame) == builder.TREND_MONTHS
    day = views.sales_view("day", date(2026, 9, 29), built["db_path"])
    assert day["comparison_labels"] == {"same_weekday_last_week": "same day last week"}
    with pytest.raises(ValueError):
        views.sales_view("year", AS_OF, built["db_path"])


def test_display_values_are_rounded_once() -> None:
    assert views.display_value(462431.555, "money") == 462431.56  # half-up, like the reports
    assert views.display_value(0.43149, "rate") == 43.1  # a fraction shown as a percentage
    assert views.display_value(8.6495, "change") == 8.6
    assert views.display_value(float("nan"), "money") is None
    assert views.display_value("Apparel", "text") == "Apparel"


# ---------------------------------------------------------------- demand


def test_demand_view_has_full_lists(demand_runs: dict) -> None:
    planning = demand_runs[AS_OF].planning_db
    view = views.demand_view(AS_OF, planning)
    alerts = demand_runs[AS_OF].table("customer_alerts")
    overdue = view["customer_alerts"]["Overdue"].frame
    assert len(overdue) == (alerts["alert_type"] == "overdue").sum() > 10  # not capped
    declining = view["customer_alerts"]["Declining"].frame
    lost = list(declining["Revenue lost"])
    assert lost == sorted(lost, reverse=True)
    recs = demand_runs[AS_OF].table("prebuild_recommendations")
    assert len(view["prebuild"].frame) == len(recs)
    assert len(view["pending"]) == (recs["status"] == "pending").sum()
    assert [r["rank"] for r in view["pending"]] == sorted(r["rank"] for r in view["pending"])
    assert view["prebuild"].formats()["Value at risk"] == "%.2f"


def test_demand_view_is_none_without_a_snapshot(demand_runs: dict) -> None:
    assert views.demand_view(date(2026, 9, 20), demand_runs[AS_OF].planning_db) is None


# ---------------------------------------------------------------- saved reports


def test_inline_images_swaps_saved_charts_for_data_uris(tmp_path: Path) -> None:
    (tmp_path / "trend.png").write_bytes(b"png-bytes")
    html = '<img src="trend.png"><img src="cid:trend"><img src="missing.png">'
    out = saved.inline_images(html, tmp_path)
    assert out.count('src="data:image/png;base64,cG5nLWJ5dGVz"') == 2
    assert 'src="missing.png"' in out


def test_saved_reports_and_backfill_manifest(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    for name in ("daily_sales_snapshot_2026-09-29", "weekly_sales_review_2026-09-27", "notes"):
        (reports / name).mkdir(parents=True)
        (reports / name / builder.HTML_FILENAME).write_text("<p/>", encoding="utf-8")
    found = saved.dry_run_reports(reports)
    assert [(r.report, r.as_of) for r in found] == [
        ("daily_sales_snapshot", "2026-09-29"),
        ("weekly_sales_review", "2026-09-27"),
    ]
    root = tmp_path / "backfill"
    root.mkdir()
    rows = [
        ("2026-09-28", "daily_sales_snapshot", "2026-09-27", "ok"),
        ("2026-09-28", "weekly_sales_review", "2026-09-27", "failed"),
        ("2026-10-01", "monthly_business_review", "2026-09-30", "ok"),
    ]
    with (root / backfill.MANIFEST_NAME).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=backfill.MANIFEST_COLUMNS)
        writer.writeheader()
        for run_date, report, as_of, status in rows:
            folder = f"{run_date}/{report}_{as_of}" if status == "ok" else ""
            writer.writerow(
                {
                    "run_date": run_date,
                    "report": report,
                    "as_of": as_of,
                    "status": status,
                    "output_folder": folder,
                }
            )
    assert len(saved.backfill_manifest(root)) == 3
    newest, oldest = saved.backfill_reports(root)  # newest run first; failed runs left out
    assert newest.folder == root / "2026-10-01" / "monthly_business_review_2026-09-30"
    assert newest.source == "backfill 2026-10-01" and newest.excel_path is None
    assert oldest.report == "daily_sales_snapshot"
    assert saved.backfill_manifest(tmp_path / "none") == []


def test_demand_counts_match_the_lists(demand_runs: dict) -> None:
    view = views.demand_view(AS_OF, demand_runs[AS_OF].planning_db)
    counts = view["counts"]
    assert counts["customer_alerts"] == sum(len(t.frame) for t in view["customer_alerts"].values())
    assert counts["Overdue"] == len(view["customer_alerts"]["Overdue"].frame)
    assert counts["stock_alerts"] == len(view["stock_alerts"].frame)
    assert counts["category_flagged"] == int(view["category_alerts"].frame["Flagged"].sum())
    assert counts["category_and_stock"] == counts["category_flagged"] + counts["stock_alerts"]
    assert counts["accuracy_products"] == len(view["accuracy_products"].frame)
    assert counts["pending"] == len(view["pending"]) and counts["decided"] == len(view["decided"])
    assert counts["prebuild"] == counts["pending"] + counts["decided"]


def test_backfilled_reports_carry_period_and_summary_status(tmp_path: Path) -> None:
    root = tmp_path / "backfill"
    root.mkdir()
    with (root / backfill.MANIFEST_NAME).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=backfill.MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerow(
            {"run_date": "2026-10-01", "report": "monthly_business_review", "as_of": "2026-09-30",
             "period": "September 2026", "status": "ok", "summary_status": "ok",
             "output_folder": "2026-10-01/monthly_business_review_2026-09-30"}
        )  # fmt: skip
    [report] = saved.backfill_reports(root)
    assert (report.period, report.summary_status) == ("September 2026", "ok")
    assert report.title == "Monthly business review"
