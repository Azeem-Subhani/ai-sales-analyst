"""Report sections built from the demand-planning snapshot (Week 5).

- demand_alerts (weekly and monthly): overdue and declining customers, category and
  stock alerts, forecast-accuracy flags, pending pre-build recommendations and the
  products that need planner review.
- forecast_accuracy (monthly): seeded-forecast WAPE and bias overall, by category,
  and the flagged products.

The numbers come from the snapshot for the report's as_of (src.demand.run.get_or_run,
which computes one only if none is stored). Nothing here calculates: values are
copied from the snapshot and rounded once, at display. The email shows at most
max_rows_per_type rows per list with "and N more"; the Excel sheets have every row.
"""

from collections.abc import Callable
from datetime import date, datetime

import pandas as pd

from src.demand import config as demand_config
from src.demand import run as demand_run
from src.reports import builder

GROUPS_TYPE = "groups"
MORE_TEXT = "and {more} more (full list in the Excel file)"
NONE_TEXT = "None flagged."

# Column kinds -> how a value is stored and shown.
STORED: dict[str, Callable] = {
    "money": lambda v: builder.rounded(v, "money"),
    "count": lambda v: builder.rounded(v, "count"),
    "rate": lambda v: builder.rounded(v, "rate"),
    "change": lambda v: builder.rounded(v, "change"),
    "days": lambda v: None if v is None or pd.isna(v) else float(v),
}
SHOWN: dict[str, Callable] = {
    "money": builder.fmt_money,
    "count": builder.fmt_count,
    "rate": builder.fmt_pct,
    "change": lambda v: builder.fmt_change(v, "pct"),
    "days": builder.fmt_days,
}
EXCEL_KINDS = {
    "money": "money",
    "count": "count",
    "rate": "rate",
    "change": "change",
    "days": "days",
}

# (key, header, kind) per list. Text columns are left-aligned in the email.
OVERDUE_COLUMNS = [
    ("customer_name", "Customer", "text"),
    ("customer_type", "Type", "text"),
    ("severity", "Severity", "text"),
    ("usual_interval_days", "Usual gap (days)", "days"),
    ("days_since_last_order", "Days since last order", "count"),
    ("revenue_365d", "Revenue, last 365 days", "money"),
]
DECLINING_COLUMNS = [
    ("customer_name", "Customer", "text"),
    ("customer_type", "Type", "text"),
    ("revenue_prior", "Prior 90 days", "money"),
    ("revenue_recent", "Last 90 days", "money"),
    ("revenue_change_pct", "Change", "change"),
]
NEW_HIGH_VALUE_COLUMNS = [
    ("customer_name", "Customer", "text"),
    ("customer_type", "Type", "text"),
    ("first_order_date", "First order", "date"),
    ("revenue_recent", "Last 90 days", "money"),
]
SEGMENT_COLUMNS = [
    ("customer_name", "Customer", "text"),
    ("customer_type", "Type", "text"),
    ("segment_before", "Segment 28 days earlier", "text"),
    ("segment_now", "Segment now", "text"),
    ("revenue_365d", "Revenue, last 365 days", "money"),
]
CATEGORY_COLUMNS = [
    ("category", "Category", "text"),
    ("prior_revenue", "Prior 8 weeks", "money"),
    ("current_revenue", "Last 8 weeks", "money"),
    ("revenue_change_pct", "Change", "change"),
]
STOCK_COLUMNS = [
    ("name", "Product", "text"),
    ("sku", "SKU", "text"),
    ("stockout_weeks", "Stockout weeks (last 4)", "count"),
    ("on_hand", "On hand", "count"),
    ("days_of_cover", "Days of cover", "days"),
]
ACCURACY_FLAG_COLUMNS = [
    ("name", "Product", "text"),
    ("key", "SKU", "text"),
    ("actual_units", "Actual units", "count"),
    ("wape", "WAPE", "rate"),
    ("bias_pct", "Bias", "change"),
]
PREBUILD_COLUMNS = [
    ("rank", "Rank", "count"),
    ("name", "Product", "text"),
    ("sku", "SKU", "text"),
    ("priority", "Reason", "text"),
    ("days_of_cover", "Days of cover", "days"),
    ("lead_time_weeks", "Lead time (weeks)", "days"),
    ("on_hand", "On hand", "count"),
    ("on_order", "On order", "count"),
    ("recommended_units", "Pre-build units", "count"),
    ("unit_price", "Unit price", "money"),
    ("value_at_risk", "Value at risk", "money"),
    ("rec_id", "Id", "text"),
]
# The email shows only these; the Excel sheet keeps every column above.
PREBUILD_EMAIL_COLUMNS = ["name", "days_of_cover", "recommended_units", "value_at_risk", "priority"]

REVIEW_COLUMNS = [
    ("name", "Product", "text"),
    ("sku", "SKU", "text"),
    ("reason", "Reason", "text"),
]
ACCURACY_TOTAL_COLUMNS = [
    ("key", "Category", "text"),
    ("actual_units", "Actual units", "count"),
    ("forecast_units", "Forecast units", "count"),
    ("wape", "WAPE", "rate"),
    ("bias_pct", "Bias", "change"),
]


# ---------------------------------------------------------------- snapshot


def snapshot(ctx: "builder.Context") -> demand_run.DemandResult:
    """The demand snapshot for the report's as_of, loaded once per report."""
    if ctx.demand is None:
        ctx.demand = demand_run.get_or_run(ctx.as_of, sales_db=ctx.db_path)
    return ctx.demand


def plain(value: object) -> object:
    """A DataFrame cell as a JSON-safe value (dates as ISO text, NaN as None)."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp | datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


def group(
    key: str,
    title: str,
    frame: pd.DataFrame,
    columns: list,
    email: bool = True,
    email_columns: list[str] | None = None,
) -> dict:
    """One list: every row (rounded for storage), its columns and its count."""
    rows = []
    for record in frame.to_dict("records"):
        row = {}
        for column, _, kind in columns:
            value = plain(record.get(column))
            row[column] = STORED[kind](value) if kind in STORED else value
        rows.append(row)
    limit = max_rows()
    return {
        "key": key,
        "title": title,
        "columns": [{"key": c, "label": label, "kind": kind} for c, label, kind in columns],
        "rows": rows,
        "count": len(rows),
        "email": email,
        "email_columns": email_columns or [c for c, _, _ in columns],
        "email_rows": min(len(rows), limit),
        "more": max(0, len(rows) - limit),  # the "and N more" in the email
    }


def with_bias_pct(frame: pd.DataFrame) -> pd.DataFrame:
    """Bias as a signed percentage for display (the stored value is a fraction)."""
    return frame.assign(bias_pct=frame["bias"] * 100) if not frame.empty else frame


def flagged_products(accuracy: pd.DataFrame) -> pd.DataFrame:
    """Flagged product rows, largest bias (either sign) first, ties by SKU."""
    flagged = with_bias_pct(accuracy[(accuracy["level"] == "product") & accuracy["flagged"]])
    if flagged.empty:
        return flagged
    order = flagged.assign(size=flagged["bias_pct"].abs()).sort_values(
        ["size", "key"], ascending=[False, True]
    )
    return order.drop(columns="size")


# ---------------------------------------------------------------- sections


def demand_alerts_section(ctx: "builder.Context") -> dict:
    """The Demand alerts section (weekly and monthly reports)."""
    result = snapshot(ctx)
    alerts = result.table("customer_alerts")
    by_type = {t: alerts[alerts["alert_type"] == t] for t in ("overdue", "declining")}
    overdue = by_type["overdue"]
    serious = int((overdue["severity"] == "serious").sum())
    categories = result.table("category_alerts")
    flagged = flagged_products(result.table("forecast_accuracy"))
    prebuild = result.table("prebuild_recommendations")
    pending = prebuild[prebuild["status"] == "pending"].sort_values("rank")
    groups = [
        group(
            "overdue",
            f"Overdue customers ({len(overdue)}, {serious} serious)",
            overdue,
            OVERDUE_COLUMNS,
        ),
        group(
            "declining",
            f"Declining customers ({len(by_type['declining'])})",
            by_type["declining"],
            DECLINING_COLUMNS,
        ),
        group("category", "Category alerts", categories[categories["flagged"]], CATEGORY_COLUMNS),
        group(
            "stock", "Stock alerts", result.table("stock_alerts").sort_values("rank"), STOCK_COLUMNS
        ),
        group("forecast_flags", "Forecast-accuracy flags", flagged, ACCURACY_FLAG_COLUMNS),
        group(
            "prebuild",
            "Pre-build recommendations, pending approval (urgent first, then value at risk)",
            pending,
            PREBUILD_COLUMNS,
            email_columns=PREBUILD_EMAIL_COLUMNS,
        ),
        group(
            "planner_review",
            "Needs planner review (forecast bias)",
            result.table("planner_review"),
            REVIEW_COLUMNS,
        ),
        group(
            "new_high_value",
            "New high-value customers",
            alerts[alerts["alert_type"] == "new_high_value"],
            NEW_HIGH_VALUE_COLUMNS,
            email=False,
        ),
        group(
            "segment_change",
            "Customers moving to an at-risk segment",
            alerts[alerts["alert_type"] == "segment_change"],
            SEGMENT_COLUMNS,
            email=False,
        ),
    ]
    return {
        "type": GROUPS_TYPE,
        "name": "demand_alerts",
        "title": "Demand alerts",
        "snapshot_as_of": result.as_of.isoformat(),
        "note": f"Demand-planning snapshot as of {result.as_of.isoformat()}. Pre-build "
        "recommendations need a planner's approval before anything is ordered.",
        "groups": groups,
        "counts": {g["key"]: g["count"] for g in groups},
        "serious_overdue": serious,
    }


def forecast_accuracy_section(ctx: "builder.Context") -> dict:
    """The Forecast accuracy section (monthly report): seeded forecasts vs demand."""
    result = snapshot(ctx)
    accuracy = with_bias_pct(result.table("forecast_accuracy"))
    if accuracy.empty:
        return builder.placeholder_section("Forecast accuracy", "No seeded forecasts to measure.")
    overall = accuracy[accuracy["level"] == "overall"].assign(key="All products")
    categories = accuracy[accuracy["level"] == "category"].sort_values("key")
    flagged = flagged_products(accuracy)
    products = accuracy[accuracy["level"] == "product"].sort_values("key")
    settings = demand_config.load_config()["forecast_accuracy"]
    first = plain(overall["first_week"].iloc[0])
    last_week = pd.Timestamp(overall["last_week"].iloc[0]).date()
    groups = [
        group("overall", "Overall", overall, ACCURACY_TOTAL_COLUMNS),
        group("by_category", "By category", categories, ACCURACY_TOTAL_COLUMNS),
        group("flagged", f"Flagged products ({len(flagged)})", flagged, ACCURACY_FLAG_COLUMNS),
        group("all_products", "All products", products, ACCURACY_FLAG_COLUMNS, email=False),
    ]
    return {
        "type": GROUPS_TYPE,
        "name": "forecast_accuracy",
        "title": "Forecast accuracy",
        "snapshot_as_of": result.as_of.isoformat(),
        "note": (
            f"Seeded forecasts for the weeks {first} to {last_week.isoformat()}, against "
            "demand (units on non-cancelled orders). Bias above 0 means the forecast was "
            f"too high. A product is flagged for WAPE above {settings['wape_flag'] * 100:.0f}% "
            f"or bias beyond {settings['bias_flag'] * 100:.0f}%, if it had at least "
            f"{settings['min_actual_units']} actual units."
        ),
        "groups": groups,
        "counts": {g["key"]: g["count"] for g in groups},
    }


# ---------------------------------------------------------------- email views


def max_rows() -> int:
    """Rows per list in the email."""
    return int(demand_config.load_config()["reports"]["max_rows_per_type"])


def shown(value: object, kind: str) -> str:
    """One cell as text."""
    if kind in SHOWN:
        return SHOWN[kind](value)
    return "n/a" if value is None else str(value)


def group_views(section: dict) -> list[dict]:
    """Template blocks for a groups section: one table per list, cut at max_rows."""
    views = [{"title": section["title"], "note": section["note"], "header": [], "rows": []}]
    for g in section["groups"]:
        if not g["email"]:
            continue
        if not g["rows"]:
            views.append({"title": g["title"], "placeholder": NONE_TEXT, "minor": True})
            continue
        by_key = {c["key"]: c for c in g["columns"]}
        columns = [by_key[key] for key in g["email_columns"]]
        rows = [
            [
                builder.cell(
                    shown(row[c["key"]], c["kind"]),
                    "left" if c["kind"] in ("text", "date") else "right",
                )
                for c in columns
            ]
            for row in g["rows"][: g["email_rows"]]
        ]
        view = {
            "title": g["title"],
            "header": [c["label"] for c in columns],
            "rows": rows,
            "minor": True,
        }
        if g["more"]:
            view["footer"] = MORE_TEXT.format(more=builder.fmt_count(g["more"]))
        views.append(view)
    return views


# ---------------------------------------------------------------- Excel


def excel_frame(section: dict) -> tuple[str, pd.DataFrame, dict[str, str]]:
    """(sheet name, table, formats): every list stacked, one column set, a List column."""
    records, formats = [], {}
    for g in section["groups"]:
        for row in g["rows"]:
            record = {"List": g["title"]}
            for c in g["columns"]:
                record[c["label"]] = row[c["key"]]
                if c["kind"] in EXCEL_KINDS:
                    formats[c["label"]] = EXCEL_KINDS[c["kind"]]
            records.append(record)
    sheet = "Demand alerts" if section["name"] == "demand_alerts" else "Forecast accuracy"
    return sheet, pd.DataFrame(records), formats


# ---------------------------------------------------------------- summary input


def summary_lines(section: dict) -> list[str]:
    """The section as text lines for the LLM summary: counts and the top 3 of each list."""
    lines = ["", f"{section['title']} ({section['note']}):"]
    for g in section["groups"]:
        lines.append(f"- {g['title']}: {builder.fmt_count(g['count'])} rows")
        for row in g["rows"][:3]:
            parts = [
                f"{c['label']} {shown(row[c['key']], c['kind'])}"
                for c in g["columns"]
                if c["key"] != "rec_id"  # ids are for planners, not the summary
            ]
            lines.append("  - " + "; ".join(parts))
    return lines
