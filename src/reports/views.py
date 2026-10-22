"""Read-only views of the sales and demand numbers for the Streamlit dashboard (Week 6).

The dashboard only lays these out: every number is calculated here or below (SQL in
src/analytics, the report sections in builder, the stored demand snapshot), with the
same comparisons as the reports. Tables come back as DataFrames whose numbers are
already rounded once for display (money half-up to cents, rates as percentages with
one decimal), so the dashboard can sort them without formatting or rounding anything.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from src.analytics import kpis
from src.demand import store
from src.reports import builder, data_clock, demand_sections

PERIOD_REPORTS = {
    "day": "daily_sales_snapshot",
    "week": "weekly_sales_review",
    "month": "monthly_business_review",
}
KPI_QUERIES = {"day": "kpis_day", "week": "kpis_week", "month": "kpis_month"}
# Trend chart per period kind: (bucket, number of buckets), as in the report charts.
TRENDS = {
    "day": ("week", builder.TREND_WEEKS),
    "week": ("week", builder.TREND_WEEKS),
    "month": ("month", builder.TREND_MONTHS),
}
PCT_DECIMALS = 1
DAYS_DECIMALS = 1

# Kind of each display column -> the Streamlit number format the dashboard uses.
DISPLAY_FORMATS = {
    "money": "%.2f",
    "count": "%d",
    "rate": "%.1f%%",
    "change": "%+.1f%%",
    "pp": "%+.1f pp",
    "days": "%.1f",
}

CATEGORY_COLUMNS = [
    ("value", "Category", "text"),
    ("net_revenue", "Net revenue", "money"),
    ("share", "Share", "rate"),
    ("net_revenue_change", "Change", "change"),
    ("gross_margin_pct", "Gross margin", "rate"),
    ("gross_margin_change", "Margin change", "pp"),
]
TOP_COLUMNS = [
    ("rank", "Rank", "count"),
    ("name", "Name", "text"),
    ("detail", "Type", "text"),
    ("net_revenue", "Net revenue", "money"),
    ("volume", "Volume", "count"),
    ("net_revenue_change", "Change", "change"),
]
ALERT_TYPES = {
    "overdue": ("Overdue", demand_sections.OVERDUE_COLUMNS),
    "declining": (
        "Declining",
        [*demand_sections.DECLINING_COLUMNS, ("revenue_lost", "Revenue lost", "money")],
    ),
    "new_high_value": ("New high-value", demand_sections.NEW_HIGH_VALUE_COLUMNS),
    "segment_change": ("Segment moves", demand_sections.SEGMENT_COLUMNS),
}
CATEGORY_ALERT_COLUMNS = [
    *demand_sections.CATEGORY_COLUMNS,
    ("flagged", "Flagged", "text"),
]
ACCURACY_PRODUCT_COLUMNS = [
    ("name", "Product", "text"),
    ("key", "SKU", "text"),
    ("category", "Category", "text"),
    ("actual_units", "Actual units", "count"),
    ("forecast_units", "Forecast units", "count"),
    ("wape", "WAPE", "rate"),
    ("bias_pct", "Bias", "change"),
    ("flagged", "Flagged", "text"),
]
DECISION_COLUMNS = [
    ("status", "Status", "text"),
    ("decided_by", "Decided by", "text"),
    ("decided_at_utc", "Decided at (UTC)", "date"),
    ("note", "Note", "text"),
]


@dataclass
class Table:
    """A display table: rounded values, column labels and each column's kind."""

    frame: pd.DataFrame
    kinds: dict[str, str]  # column label -> money | count | rate | change | pp | days | ...

    def formats(self) -> dict[str, str]:
        """Number formats for the numeric columns, by label."""
        return {
            label: DISPLAY_FORMATS[kind]
            for label, kind in self.kinds.items()
            if kind in DISPLAY_FORMATS
        }


# ---------------------------------------------------------------- rounding for display


def display_value(value: object, kind: str) -> object:
    """One value rounded once for display, by kind (rates become percentages)."""
    value = kpis.none_if_nan(value) if isinstance(value, float) else value
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if kind == "money":
        return builder.round_money(float(value))
    if kind == "count":
        return round(float(value))
    if kind == "rate":
        return round(float(value) * 100, PCT_DECIMALS)
    if kind in ("change", "pp"):
        return round(float(value), PCT_DECIMALS)
    if kind == "days":
        return round(float(value), DAYS_DECIMALS)
    return value


def table(rows: list[dict], columns: list[tuple[str, str, str]]) -> Table:
    """A display Table from records and (key, label, kind) column specs."""
    data = [
        {label: display_value(row.get(key), kind) for key, label, kind in columns} for row in rows
    ]
    frame = pd.DataFrame(data, columns=[label for _, label, _ in columns])
    return Table(frame, {label: kind for _, label, kind in columns})


# ---------------------------------------------------------------- sales


def period_ending(kind: str, as_of: date) -> kpis.Period:
    """The latest complete period of a kind ending on or before as_of."""
    if kind == "week":
        return kpis.period_for(kind, data_clock.last_sunday_on_or_before(as_of))
    if kind == "month":
        return kpis.period_for(kind, data_clock.last_month_end_on_or_before(as_of))
    return kpis.period_for(kind, as_of)


def kpi_cards(section: dict, comparison_labels: dict[str, str]) -> list[dict]:
    """KPI cards from a report KPI section: value and change text per comparison."""
    cards = []
    for row in section["rows"]:
        unit = builder.change_unit(row["kind"])
        lower_is_better = row["kpi"] in builder.LOWER_IS_BETTER
        cards.append(
            {
                "kpi": row["kpi"],
                "label": row["label"],
                "value_shown": builder.FORMATTERS[row["kind"]](row["current"]),
                "comparisons": [
                    {
                        "label": comparison_labels[name],
                        "value_shown": builder.FORMATTERS[row["kind"]](cmp["value"]),
                        "change_shown": builder.fmt_change(cmp["change"], unit),
                        "tone": builder.tone(cmp["change"], lower_is_better),
                    }
                    for name, cmp in row["comparisons"].items()
                ],
            }
        )
    return cards


def sales_view(
    kind: str,
    as_of: date,
    db_path: Path = kpis.DB_PATH,
    config_path: Path = builder.CONFIG_PATH,
) -> dict:
    """KPI cards, trend, category breakdown and top 10 lists for the period ending as_of.

    Uses the matching report's comparisons (reports.yaml compare_to). Raises
    ValueError for an unknown kind or an as_of after the last order.
    """
    if kind not in PERIOD_REPORTS:
        raise ValueError(f"Unknown period kind {kind!r}; expected one of {list(PERIOD_REPORTS)}.")
    block = builder.load_config(config_path)[PERIOD_REPORTS[kind]]
    period = period_ending(kind, as_of)
    comparisons = {name: kpis.comparison_period(period, name) for name in block["compare_to"]}
    labels = {name: builder.COMPARISON_LABELS[name] for name in comparisons}
    con = kpis.open_db(db_path)
    try:
        last = kpis.last_order_date(con)
        if period.end > last:
            raise ValueError(f"{period.label} ends after the last order date {last}.")
        ctx = builder.Context(con, period, comparisons, as_of=period.end, db_path=db_path)
        kpi_section = builder.QUERY_SECTIONS[KPI_QUERIES[kind]](ctx)
        bucket, count = TRENDS[kind]
        trend = kpis.revenue_trend(con, bucket, period.end, count)
        categories = builder.breakdown_section(ctx, "category", "", show_margin=True)
        products = builder.top_section(ctx, "products", builder.TOP_N)
        customers = builder.top_section(ctx, "customers", builder.TOP_N)
    finally:
        con.close()
    primary = labels[ctx.primary]
    return {
        "period": period,
        "comparison_labels": labels,
        "primary_comparison": primary,
        "kpi_cards": kpi_cards(kpi_section, labels),
        "trend": table(
            [
                {"bucket_start": pd.Timestamp(s).date(), "net_revenue": v}
                for s, v in zip(trend["bucket_start"], trend["net_revenue"], strict=True)
            ],
            [("bucket_start", "Period start", "date"), ("net_revenue", "Net revenue", "money")],
        ),
        "categories": table(categories["rows"], CATEGORY_COLUMNS),
        "top_products": table(products["rows"], TOP_COLUMNS),
        "top_customers": table(customers["rows"], TOP_COLUMNS),
    }


# ---------------------------------------------------------------- demand


def demand_view(as_of: date, planning_db: Path = store.PLANNING_DB_PATH) -> dict | None:
    """Full (uncapped) demand lists for the snapshot of exactly as_of; None if none exists."""
    if not store.snapshot_exists(planning_db, as_of):
        return None
    tables = {name: store.read_table(planning_db, name, as_of) for name in DEMAND_TABLES}
    alerts = tables["customer_alerts"].sort_values(["alert_type", "rank"])
    accuracy = demand_sections.with_bias_pct(tables["forecast_accuracy"])
    totals = accuracy[accuracy["level"] != "product"].sort_values(["level", "key"])
    products = accuracy[accuracy["level"] == "product"].sort_values("key")
    recs = tables["prebuild_recommendations"].sort_values("rank")
    return {
        "as_of": as_of,
        "customer_alerts": {
            label: table(alerts[alerts["alert_type"] == alert_type].to_dict("records"), columns)
            for alert_type, (label, columns) in ALERT_TYPES.items()
        },
        "category_alerts": table(
            tables["category_alerts"].sort_values("category").to_dict("records"),
            CATEGORY_ALERT_COLUMNS,
        ),
        "stock_alerts": table(
            tables["stock_alerts"].sort_values("rank").to_dict("records"),
            demand_sections.STOCK_COLUMNS,
        ),
        "accuracy_totals": table(
            totals.to_dict("records"),
            [("level", "Level", "text"), *demand_sections.ACCURACY_TOTAL_COLUMNS],
        ),
        "accuracy_products": table(products.to_dict("records"), ACCURACY_PRODUCT_COLUMNS),
        "prebuild": table(
            recs.to_dict("records"), [*demand_sections.PREBUILD_COLUMNS, *DECISION_COLUMNS]
        ),
        "pending": prebuild_rows(recs, "pending"),
        "decided": prebuild_rows(recs, None),
        "planner_review": table(
            tables["planner_review"].sort_values("sku").to_dict("records"),
            demand_sections.REVIEW_COLUMNS,
        ),
    }


DEMAND_TABLES = [
    "customer_alerts",
    "category_alerts",
    "stock_alerts",
    "forecast_accuracy",
    "prebuild_recommendations",
    "planner_review",
]


def prebuild_rows(recs: pd.DataFrame, status: str | None) -> list[dict]:
    """Pre-build rows for the approval list: pending ones, or (status None) decided ones."""
    chosen = recs[recs["status"] == status] if status else recs[recs["status"] != "pending"]
    rows = []
    for rec in chosen.to_dict("records"):
        rows.append(
            {
                "rec_id": rec["rec_id"],
                "rank": int(rec["rank"]),
                "name": rec["name"],
                "sku": rec["sku"],
                "status": rec["status"],
                "recommended_units_shown": builder.fmt_count(rec["recommended_units"]),
                "value_at_risk_shown": builder.fmt_money(
                    display_value(rec["value_at_risk"], "money")
                ),
                "days_of_cover_shown": builder.fmt_days(kpis.none_if_nan(rec["days_of_cover"])),
                "priority": rec["priority"],
                "decided_by": rec["decided_by"],
                "decided_at_utc": None
                if pd.isna(rec["decided_at_utc"])
                else str(rec["decided_at_utc"]),
                "note": None if pd.isna(rec["note"]) else rec["note"],
            }
        )
    return rows


def default_as_of(db_path: Path = kpis.DB_PATH) -> date:
    """DATA_END_DATE if set, else the last order date in the sales database."""
    data_end = data_clock.data_end_from_env()
    if data_end:
        return data_end
    con = kpis.open_db(db_path)
    try:
        return kpis.last_order_date(con)
    finally:
        con.close()
