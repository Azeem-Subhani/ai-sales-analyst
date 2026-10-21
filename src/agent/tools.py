"""The seven read-only agent tools and their JSON schemas (plan section 10).

Tools do all data access and calculation; the model only picks tools and explains
what they return. No tool sends email, changes data or approves anything.

Each tool has a pydantic model for its arguments, and the JSON schema sent to the
model is generated from that model, so the schema and the validation cannot drift
apart. Invalid arguments are returned to the model as the tool result; each tool
gets 2 retries per question before the error becomes final (ToolSession).

The three demand-planning tools (Week 5) only read stored snapshots in
data/planning.duckdb: the latest one with as_of on or before DATA_END_DATE. They never
compute one (an agent question has 120 seconds; a run takes longer), and they cannot
change an approval status. With no snapshot they say how to create one.
"""

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import plotly.express as px
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agent import sql_guard
from src.analytics import kpis
from src.demand import store
from src.reports import builder, data_clock

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHARTS_DIR = PROJECT_ROOT / "outputs" / "charts"
MAX_INVALID_RETRIES = 2  # per tool, per question
FLOAT_DECIMALS = 4  # SQL floats are rounded for the model, e.g. 114286.8200000001
MAX_NAME_MATCHES = 10
DEFAULT_FORECAST_WEEKS = 8
MAX_FORECAST_WEEKS = 12
SLUG_MAX_CHARS = 40
DEFAULT_DEMAND_ROWS = 10
MAX_DEMAND_ROWS = 50
NOT_COMPUTED = {
    "status": "not_computed",
    "message": "Demand planning has not been computed yet. Ask the planner to run "
    "python -m src.demand.run --as-of <YYYY-MM-DD>.",
}

# The standard comparisons for each period kind, as in the scheduled reports.
STANDARD_COMPARISONS: dict[str, list[str]] = {
    kind: [name for name, k in kpis.COMPARISONS.items() if k == kind] for kind in kpis.PERIOD_KINDS
}

FORECAST_SQL = """
SELECT f.week_start, f.forecast_qty, f.model, f.created_at,
       COALESCE(a.units, 0) AS actual_units,
       f.week_start + INTERVAL 6 DAY <= $last_order AS has_actual  -- week complete
FROM forecasts f
LEFT JOIN (
    SELECT oi.product_id,
           date_trunc('week', o.order_date)::DATE AS week_start,
           SUM(oi.quantity) AS units
    FROM orders o JOIN order_items oi USING (order_id)
    WHERE o.status <> 'cancelled'
    GROUP BY ALL
) a ON a.product_id = f.product_id AND a.week_start = f.week_start
WHERE f.product_id = $product_id
ORDER BY f.week_start DESC
LIMIT $weeks
"""
PRODUCT_BY_SKU_SQL = (
    "SELECT product_id, sku, name, category FROM products WHERE lower(sku) = lower($text)"
)
PRODUCT_BY_NAME_SQL = """
SELECT product_id, sku, name, category FROM products
WHERE lower(name) LIKE '%' || lower($text) || '%'
ORDER BY name
LIMIT $limit
"""
LAST_ORDER_SQL = "SELECT max(order_date) FROM orders"


@dataclass
class ToolContext:
    """Where tools read from and write to (tests pass temporary paths)."""

    db_path: Path = kpis.DB_PATH
    charts_dir: Path = CHARTS_DIR
    sql_timeout_seconds: float = sql_guard.DEFAULT_TIMEOUT_SECONDS
    data_end: date | None = None  # DATA_END_DATE; demand tools read snapshots up to it

    @property
    def planning_db(self) -> Path:
        """planning.duckdb next to the sales database."""
        return store.planning_path_for(self.db_path)


class ToolError(Exception):
    """An expected problem (unknown product, partial week); the message goes to the model."""


# ---------------------------------------------------------------- argument models


class StrictArgs(BaseModel):
    """Unknown argument names are an error, so typos reach the model as feedback."""

    model_config = ConfigDict(extra="forbid")


class RunSqlArgs(StrictArgs):
    query: str = Field(
        description="One read-only SELECT (WITH ... SELECT allowed) on the six standard tables."
    )


class GetKpisArgs(StrictArgs):
    period_type: Literal["day", "week", "month"] = Field(
        description="day, week (Monday to Sunday) or month (calendar month)."
    )
    as_of: date = Field(
        description="Last day of the period, YYYY-MM-DD. A week must end on a Sunday, "
        "a month on its last day."
    )


class GetForecastArgs(StrictArgs):
    sku_or_name: str = Field(description="Product SKU (e.g. AP-1150) or part of its name.")
    weeks: int = Field(
        default=DEFAULT_FORECAST_WEEKS,
        ge=1,
        le=MAX_FORECAST_WEEKS,
        description="Number of most recent forecast weeks to return.",
    )


class MakeChartArgs(StrictArgs):
    query: str = Field(description="SELECT that returns the chart data (same rules as run_sql).")
    chart_type: Literal["line", "bar"] = Field(description="line for trends, bar for comparisons.")
    x: str = Field(description="Column of the query result for the x axis.")
    y: str = Field(description="Numeric column of the query result for the y axis.")
    title: str = Field(min_length=1, max_length=120, description="Chart title.")


class GetCustomerAlertsArgs(StrictArgs):
    alert_type: Literal["overdue", "declining", "new_high_value", "segment_change"] | None = Field(
        default=None, description="Only this kind of alert; all kinds if omitted."
    )
    severity: Literal["serious", "overdue", "warning", "info"] | None = Field(
        default=None,
        description="Only this severity: serious or overdue (overdue alerts), warning "
        "(declining, segment_change), info (new_high_value).",
    )
    limit: int = Field(
        default=DEFAULT_DEMAND_ROWS, ge=1, le=MAX_DEMAND_ROWS, description="Rows to return."
    )


class GetForecastAccuracyArgs(StrictArgs):
    sku_or_name: str | None = Field(default=None, description="One product (SKU or name).")
    category: str | None = Field(default=None, description="One category, e.g. Apparel.")


class GetPrebuildRecommendationsArgs(StrictArgs):
    status: Literal["pending", "approved", "rejected"] | None = Field(
        default=None, description="Only recommendations with this status; all if omitted."
    )
    limit: int = Field(
        default=DEFAULT_DEMAND_ROWS, ge=1, le=MAX_DEMAND_ROWS, description="Rows to return."
    )


# ---------------------------------------------------------------- helpers


def json_value(value: Any) -> Any:
    """A SQL value as JSON-safe data the model can read."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return None if pd.isna(value) else round(value, FLOAT_DECIMALS)
    if isinstance(value, Decimal):
        return round(float(value), FLOAT_DECIMALS)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def query_payload(result: sql_guard.QueryResult) -> dict:
    """run_sql's answer: columns, rows, row count, and whether rows were cut at 500."""
    payload: dict[str, Any] = {
        "columns": result.columns,
        "rows": [[json_value(v) for v in row] for row in result.rows],
        "row_count": result.row_count,
        "truncated": result.truncated,
    }
    if result.truncated:
        payload["note"] = (
            f"truncated: only the first {sql_guard.MAX_ROWS} rows are shown; "
            "aggregate or filter the query."
        )
    return payload


def find_product(con: Any, text: str) -> dict:
    """One product by exact SKU or by a unique name match; else a ToolError with candidates."""
    rows = con.execute(PRODUCT_BY_SKU_SQL, {"text": text.strip()}).fetchall()
    if not rows:
        rows = con.execute(
            PRODUCT_BY_NAME_SQL, {"text": text.strip(), "limit": MAX_NAME_MATCHES}
        ).fetchall()
    if not rows:
        raise ToolError(f"No product matches {text!r}. Try the SKU or a shorter part of the name.")
    exact = [r for r in rows if r[2].lower() == text.strip().lower()]
    if len(rows) > 1 and len(exact) != 1:
        options = [f"{r[1]} {r[2]}" for r in rows]
        raise ToolError(f"{text!r} matches several products: {options}. Ask with the SKU.")
    product_id, sku, name, category = (exact or rows)[0]
    return {"product_id": product_id, "sku": sku, "name": name, "category": category}


def slug(text: str) -> str:
    """'Weekly revenue, 2026!' -> 'weekly_revenue_2026' (for chart file names)."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:SLUG_MAX_CHARS] or "chart"


# ---------------------------------------------------------------- tools


def run_sql(args: RunSqlArgs, ctx: ToolContext) -> dict:
    """Run one guarded, read-only SELECT; max 500 rows, 10-second timeout."""
    result = sql_guard.run_query(args.query, ctx.db_path, timeout_seconds=ctx.sql_timeout_seconds)
    return query_payload(result)


def get_kpis(args: GetKpisArgs, ctx: ToolContext) -> dict:
    """The 13 standard KPIs for one period with the reports' standard comparisons."""
    try:
        period = kpis.period_for(args.period_type, args.as_of)
    except ValueError as error:
        raise ToolError(str(error)) from error
    comparisons = {
        name: kpis.comparison_period(period, name)
        for name in STANDARD_COMPARISONS[args.period_type]
    }
    con = kpis.open_db(ctx.db_path)
    try:
        last = kpis.last_order_date(con)
        if args.as_of > last:
            raise ToolError(f"as_of {args.as_of} is after the last order date {last}.")
        table = kpis.kpi_table(con, period, comparisons)
    finally:
        con.close()

    rows = []
    for record in table.to_dict("records"):
        kind = record["kind"]
        fmt = builder.FORMATTERS[kind]
        unit = builder.change_unit(kind)
        current = builder.rounded(record["current"], kind)
        row = {
            "kpi": record["kpi"],
            "label": record["label"],
            "value": json_value(current),
            "shown": fmt(current),
        }
        for name in comparisons:
            value = builder.rounded(record[name], kind)
            change = builder.rounded(record[f"{name}_change"], "change")
            row[name] = {
                "value": json_value(value),
                "shown": fmt(value),
                # Shown text comes from the unrounded change (rounded once); the
                # number is cut to FLOAT_DECIMALS only to keep the JSON readable.
                "change": json_value(change),
                "change_shown": builder.fmt_change(change, unit),
            }
        rows.append(row)
    return {
        "period": {
            "label": period.label,
            "start": period.start.isoformat(),
            "end": period.end.isoformat(),
        },
        "comparisons": {
            name: {
                "label": p.label,
                "start": p.start.isoformat(),
                "end": p.end.isoformat(),
                "name": builder.COMPARISON_LABELS[name],
            }
            for name, p in comparisons.items()
        },
        "change_units": "percent for money and counts, percentage points (pp) for rates",
        "kpis": rows,
    }


def get_forecast(args: GetForecastArgs, ctx: ToolContext) -> dict:
    """Seeded weekly forecasts for one product, with actual demand for the same weeks."""
    with sql_guard.open_readonly(ctx.db_path) as con:
        product = find_product(con, args.sku_or_name)
        last_order = con.execute(LAST_ORDER_SQL).fetchone()[0]
        rows = con.execute(
            FORECAST_SQL,
            {"product_id": product["product_id"], "weeks": args.weeks, "last_order": last_order},
        ).fetchall()
    if not rows:
        raise ToolError(f"There are no forecasts for {product['sku']} {product['name']}.")
    weeks = [
        {
            "week_start": json_value(week_start),
            "forecast_units": json_value(forecast),
            "actual_units": int(actual) if has_actual else None,
            "model": model,
            "created_at": json_value(created_at),
        }
        for week_start, forecast, model, created_at, actual, has_actual in reversed(rows)
    ]
    return {
        "product": {k: product[k] for k in ("sku", "name", "category")},
        "actual_units_definition": "units on non-cancelled orders (completed or returned), "
        "Monday-to-Sunday weeks; this is demand, not the 'units sold' KPI",
        "weeks": weeks,
    }


def make_chart(args: MakeChartArgs, ctx: ToolContext) -> dict:
    """Chart the rows of a guarded query; the data always comes from SQL, never the model."""
    result = sql_guard.run_query(args.query, ctx.db_path, timeout_seconds=ctx.sql_timeout_seconds)
    for column in (args.x, args.y):
        if column not in result.columns:
            raise ToolError(f"Column {column!r} is not in the query result {result.columns}.")
    if result.row_count == 0:
        raise ToolError("The query returned no rows, so there is nothing to chart.")
    frame = pd.DataFrame(result.rows, columns=result.columns)
    draw = px.line if args.chart_type == "line" else px.bar
    figure = draw(frame, x=args.x, y=args.y, title=args.title)
    ctx.charts_dir.mkdir(parents=True, exist_ok=True)
    path = ctx.charts_dir / f"{slug(args.title)}_{datetime.now():%Y%m%d_%H%M%S_%f}.html"
    figure.write_html(path, include_plotlyjs=True)  # self-contained: opens offline
    return {
        "chart_path": str(path),
        "chart_type": args.chart_type,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "data_preview": query_payload(result)["rows"][:5],
    }


# ---------------------------------------------------------------- demand-planning tools

CUSTOMER_ALERT_FIELDS = {
    "customer_name": None,
    "customer_type": None,
    "region": None,
    "severity": None,
    "last_order_date": None,
    "days_since_last_order": builder.fmt_count,
    "usual_interval_days": builder.fmt_days,
    "revenue_365d": builder.fmt_money,
    "revenue_recent": builder.fmt_money,
    "revenue_prior": builder.fmt_money,
    "revenue_change_pct": lambda v: builder.fmt_change(v, "pct"),
    "first_order_date": None,
    "segment_before": None,
    "segment_now": None,
}
ACCURACY_FIELDS = {
    "level": None,
    "key": None,
    "name": None,
    "category": None,
    "weeks": builder.fmt_count,
    "actual_units": builder.fmt_count,
    "forecast_units": builder.fmt_count,
    "wape": builder.fmt_pct,
    "bias": lambda v: builder.fmt_change(None if v is None else v * 100, "pct"),
    "flagged": None,
    "flag_wape": None,
    "flag_bias": None,
    "below_min_units": None,
}
PREBUILD_FIELDS = {
    "rec_id": None,
    "rank": None,
    "sku": None,
    "name": None,
    "category": None,
    "status": None,
    "recommended_units": builder.fmt_count,
    "days_of_cover": builder.fmt_days,
    "on_hand": builder.fmt_count,
    "on_order": builder.fmt_count,
    "lead_time_weeks": builder.fmt_days,
    "forecast_lead_time": builder.fmt_count,
    "safety_stock": builder.fmt_count,
    "forecast_model": None,
    "is_fallback": None,
    "decided_by": None,
    "decided_at_utc": None,
}
REVIEW_FIELDS = {"sku": None, "name": None, "category": None, "reason": None}
ROUNDED_MONEY = {"revenue_365d", "revenue_recent", "revenue_prior"}


def demand_snapshot(ctx: ToolContext) -> date | None:
    """The as_of of the latest stored snapshot on or before DATA_END_DATE (None if none)."""
    end = ctx.data_end or data_clock.data_end_from_env()
    return store.latest_snapshot(ctx.planning_db, end)


def read_planning(ctx: ToolContext, table: str, as_of: date) -> pd.DataFrame:
    """One table of one snapshot, read-only."""
    return store.read_table(ctx.planning_db, table, as_of)


def record(row: dict, fields: dict[str, Callable | None]) -> dict:
    """A row with JSON-safe values plus "<field>_shown" text for every number."""
    out: dict[str, Any] = {}
    for name, fmt in fields.items():
        value = json_value(row.get(name))
        if name in ROUNDED_MONEY and value is not None:
            value = builder.rounded(value, "money")
        out[name] = value
        if fmt is not None:
            out[f"{name}_shown"] = fmt(value)
    return out


def snapshot_header(as_of: date) -> dict:
    """Where the numbers come from."""
    return {
        "snapshot_as_of": as_of.isoformat(),
        "source": "demand-planning snapshot in planning.duckdb (computed by python -m "
        "src.demand.run); numbers are stored results, not live queries",
    }


def get_customer_alerts(args: GetCustomerAlertsArgs, ctx: ToolContext) -> dict:
    """Customer ordering-pattern alerts from the latest demand snapshot."""
    as_of = demand_snapshot(ctx)
    if as_of is None:
        return dict(NOT_COMPUTED)
    alerts = read_planning(ctx, "customer_alerts", as_of)
    counts = alerts.groupby(["alert_type", "severity"]).size()
    if args.alert_type:
        alerts = alerts[alerts["alert_type"] == args.alert_type]
    if args.severity:
        alerts = alerts[alerts["severity"] == args.severity]
    alerts = alerts.sort_values(["alert_type", "rank"])
    rows = [
        {"alert_type": r["alert_type"], "rank": int(r["rank"]), **record(r, CUSTOMER_ALERT_FIELDS)}
        for r in alerts.head(args.limit).to_dict("records")
    ]
    return {
        **snapshot_header(as_of),
        "definitions": "overdue: 4+ orders in 365 days and days since last order over 1.5x "
        "the usual (median) gap, serious over 2x, ranked by 365-day revenue; declining: "
        "last-90-day revenue more than 25% below the prior 90 days (prior at least 1,000); "
        "new_high_value: first order in the last 90 days and top 10% of 90-day revenue; "
        "segment_change: RFM segment moved from Champions/Loyal to At risk/Lapsed vs 28 "
        "days earlier. Revenue is net revenue (completed orders).",
        "counts": [
            {"alert_type": t, "severity": sev, "customers": int(n)}
            for (t, sev), n in counts.items()
        ],
        "matching": len(alerts),
        "returned": len(rows),
        "rows": rows,
    }


def get_forecast_accuracy(args: GetForecastAccuracyArgs, ctx: ToolContext) -> dict:
    """Seeded-forecast WAPE and bias from the latest demand snapshot."""
    as_of = demand_snapshot(ctx)
    if as_of is None:
        return dict(NOT_COMPUTED)
    table = read_planning(ctx, "forecast_accuracy", as_of)
    overall = table[table["level"] == "overall"]
    result: dict[str, Any] = {
        **snapshot_header(as_of),
        "definitions": "WAPE = sum |actual - forecast| / sum actual; bias = (sum forecast - "
        "sum actual) / sum actual, positive = forecast too high. Actual = units on "
        "non-cancelled orders. Flagged: WAPE over 40% or bias beyond 15%, products with "
        "at least 20 actual units.",
        "overall": [record(r, ACCURACY_FIELDS) for r in overall.to_dict("records")],
    }
    if args.sku_or_name:
        with sql_guard.open_readonly(ctx.db_path) as con:
            product = find_product(con, args.sku_or_name)
        rows = table[(table["level"] == "product") & (table["key"] == product["sku"])]
        if rows.empty:
            raise ToolError(f"No seeded-forecast accuracy for {product['sku']} {product['name']}.")
        result["product"] = record(rows.iloc[0].to_dict(), ACCURACY_FIELDS)
    if args.category:
        rows = table[
            (table["level"] == "category")
            & (table["key"].str.lower() == args.category.strip().lower())
        ]
        if rows.empty:
            options = sorted(table.loc[table["level"] == "category", "key"])
            raise ToolError(f"No category {args.category!r}; use one of {options}.")
        result["category"] = record(rows.iloc[0].to_dict(), ACCURACY_FIELDS)
    if not args.sku_or_name:
        flagged = table[(table["level"] == "product") & table["flagged"]]
        if args.category:
            flagged = flagged[flagged["category"].str.lower() == args.category.strip().lower()]
        flagged = flagged.reindex(flagged["bias"].abs().sort_values(ascending=False).index)
        result["flagged_count"] = len(flagged)
        result["flagged_products"] = [
            record(r, ACCURACY_FIELDS) for r in flagged.head(MAX_DEMAND_ROWS).to_dict("records")
        ]
        if not args.category:
            categories = table[table["level"] == "category"].sort_values("key")
            result["by_category"] = [
                record(r, ACCURACY_FIELDS) for r in categories.to_dict("records")
            ]
    return result


def get_prebuild_recommendations(args: GetPrebuildRecommendationsArgs, ctx: ToolContext) -> dict:
    """Pre-build recommendations from the latest demand snapshot, read-only.

    The agent can never approve or reject: only a planner can, with the approval CLI.
    """
    as_of = demand_snapshot(ctx)
    if as_of is None:
        return dict(NOT_COMPUTED)
    recs = read_planning(ctx, "prebuild_recommendations", as_of).sort_values("rank")
    status_counts = recs["status"].value_counts().to_dict()
    if args.status:
        recs = recs[recs["status"] == args.status]
    review = read_planning(ctx, "planner_review", as_of).sort_values("sku")
    return {
        **snapshot_header(as_of),
        "definitions": "pre-build = max(0, forecast over lead time + safety stock - on hand "
        "- on order); safety stock = 1.65 x sd of weekly demand (26 weeks) x sqrt(lead "
        "time in weeks). Sorted by days of cover, lowest first. Only planners approve or "
        "reject; the agent cannot change a status.",
        "status_counts": {k: int(v) for k, v in status_counts.items()},
        "matching": len(recs),
        "returned": min(len(recs), args.limit),
        "rows": [record(r, PREBUILD_FIELDS) for r in recs.head(args.limit).to_dict("records")],
        "needs_planner_review_count": len(review),
        "needs_planner_review": [
            record(r, REVIEW_FIELDS) for r in review.head(args.limit).to_dict("records")
        ],
    }


# ---------------------------------------------------------------- registry


@dataclass(frozen=True)
class Tool:
    """A tool the model can call: its name, description, argument model and function."""

    name: str
    description: str
    args_model: type[StrictArgs]
    function: Callable[[Any, ToolContext], dict]

    def schema(self) -> dict:
        """OpenAI-style tool schema, generated from the pydantic argument model."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": without_titles(self.args_model.model_json_schema()),
            },
        }


def without_titles(schema: Any) -> Any:
    """Drop pydantic's auto-generated 'title' keys; they cost tokens and add nothing.

    Keys inside "properties" are argument names, not metadata, so an argument that
    is itself called "title" (make_chart) is kept.
    """
    if isinstance(schema, list):
        return [without_titles(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    cleaned = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            cleaned[key] = {name: without_titles(prop) for name, prop in value.items()}
        elif key != "title":
            cleaned[key] = without_titles(value)
    return cleaned


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in [
        Tool(
            "get_kpis",
            "Standard KPIs (net revenue, orders, AOV, units, gross margin, returns, "
            "cancellations, new and returning customers) for one day, week or month, with "
            "the standard comparisons. Use this first for headline numbers.",
            GetKpisArgs,
            get_kpis,
        ),
        Tool(
            "get_forecast",
            "Weekly forecast units for one product, with actual demand units for the same "
            "weeks where the week is complete.",
            GetForecastArgs,
            get_forecast,
        ),
        Tool(
            "run_sql",
            "Run one read-only SELECT on the six standard tables when no other tool fits "
            "(breakdowns, top lists, trends). Max 500 rows, 10 seconds.",
            RunSqlArgs,
            run_sql,
        ),
        Tool(
            "make_chart",
            "Draw a line or bar chart from a SELECT and save it as an HTML file. Use only "
            "when the user asks for a chart.",
            MakeChartArgs,
            make_chart,
        ),
        Tool(
            "get_customer_alerts",
            "Customer ordering-pattern alerts from the stored demand-planning snapshot: "
            "overdue (serious/overdue), declining, new high-value and RFM segment changes, "
            "with counts per type and the top customers.",
            GetCustomerAlertsArgs,
            get_customer_alerts,
        ),
        Tool(
            "get_forecast_accuracy",
            "Forecast accuracy (WAPE and bias) of the seeded forecasts: overall, by category, "
            "flagged products, or one product or category.",
            GetForecastAccuracyArgs,
            get_forecast_accuracy,
        ),
        Tool(
            "get_prebuild_recommendations",
            "Pre-build recommendations (read-only) with their approval status, and products "
            "that need planner review. The agent cannot approve or reject.",
            GetPrebuildRecommendationsArgs,
            get_prebuild_recommendations,
        ),
    ]
}


def tool_schemas() -> list[dict]:
    """Schemas of every registered tool, in registry order."""
    return [tool.schema() for tool in TOOLS.values()]


# ---------------------------------------------------------------- running a call


@dataclass
class ToolOutcome:
    """The result of one tool call, as sent to the model and as logged in agent_log."""

    name: str
    arguments: str
    result: dict
    success: bool
    row_count: int | None = None
    error: str | None = None
    duration_seconds: float = 0.0
    called_at_utc: datetime | None = None

    def content(self) -> str:
        """The tool result message content sent back to the model."""
        return json.dumps(self.result, default=str)


@dataclass
class ToolSession:
    """Runs tool calls for one question and counts invalid-argument retries per tool."""

    ctx: ToolContext = field(default_factory=ToolContext)
    invalid_counts: dict[str, int] = field(default_factory=dict)

    def execute(self, name: str, arguments: str) -> ToolOutcome:
        """Validate the arguments and run the tool; never raises for expected problems."""
        called_at = datetime.now(UTC).replace(tzinfo=None)
        started = time.monotonic()
        outcome = self._execute(name, arguments)
        outcome.duration_seconds = round(time.monotonic() - started, 3)
        outcome.called_at_utc = called_at
        return outcome

    def _execute(self, name: str, arguments: str) -> ToolOutcome:
        tool = TOOLS.get(name)
        if tool is None:
            return self._invalid(
                name, arguments, f"Unknown tool {name!r}; use one of {list(TOOLS)}."
            )
        try:
            raw = json.loads(arguments or "{}")
            args = tool.args_model.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as error:
            return self._invalid(name, arguments, compact_validation_error(error))
        try:
            result = tool.function(args, self.ctx)
        except (ToolError, sql_guard.SQLGuardError, sql_guard.SQLTimeoutError) as error:
            return ToolOutcome(name, arguments, {"error": str(error)}, False, error=str(error))
        except Exception as error:
            logger.exception("Tool %s failed", name)
            message = f"{type(error).__name__}: {error}"
            return ToolOutcome(name, arguments, {"error": message}, False, error=message)
        return ToolOutcome(name, arguments, result, True, row_count=result_row_count(result))

    def _invalid(self, name: str, arguments: str, message: str) -> ToolOutcome:
        """Return the validation error; after 2 retries for this tool, a final error."""
        count = self.invalid_counts.get(name, 0) + 1
        self.invalid_counts[name] = count
        if count > MAX_INVALID_RETRIES:
            result = {
                "error": "invalid_arguments_final",
                "details": message,
                "message": f"Arguments for {name} were invalid {count} times; do not call it "
                "again for this question. Answer with what you have and say what failed.",
            }
        else:
            result = {
                "error": "invalid_arguments",
                "details": message,
                "retries_left": MAX_INVALID_RETRIES - count + 1,
            }
        return ToolOutcome(name, arguments, result, False, error=message)


def compact_validation_error(error: Exception) -> str:
    """A short, model-readable description of invalid arguments."""
    if isinstance(error, json.JSONDecodeError):
        return f"Arguments are not valid JSON: {error.msg}."
    assert isinstance(error, ValidationError)
    parts = [
        f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in error.errors()
    ]
    return "; ".join(parts)


def result_row_count(result: dict) -> int | None:
    """Rows in a tool result, for agent_log."""
    if "row_count" in result:
        return int(result["row_count"])
    for key in ("kpis", "weeks", "rows", "flagged_products"):
        if key in result:
            return len(result[key])
    return None
