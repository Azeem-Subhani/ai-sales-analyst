"""The seven read-only agent tools and their JSON schemas (plan section 10).

Tools do all data access and calculation; the model only picks tools and explains
what they return. No tool sends email, changes data or approves anything.

Each tool has a pydantic model for its arguments, and the JSON schema sent to the
model is generated from that model, so the schema and the validation cannot drift
apart. Invalid arguments are returned to the model as the tool result; each tool
gets 2 retries per question before the error becomes final (ToolSession).

Three tools are registered with their final argument schemas but are built in
Week 5; until then they return {"status": "not_available_yet", ...}. Week 5 replaces
only their function bodies.
"""

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import plotly.express as px
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.agent import sql_guard
from src.analytics import kpis
from src.reports import builder

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHARTS_DIR = PROJECT_ROOT / "outputs" / "charts"
MAX_INVALID_RETRIES = 2  # per tool, per question
FLOAT_DECIMALS = 4  # SQL floats are rounded for the model, e.g. 114286.8200000001
MAX_NAME_MATCHES = 10
DEFAULT_FORECAST_WEEKS = 8
MAX_FORECAST_WEEKS = 12
SLUG_MAX_CHARS = 40
NOT_AVAILABLE = {"status": "not_available_yet", "message": "Built in Week 5"}

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
    region: str | None = Field(default=None, description="Only customers in this region.")
    segment: str | None = Field(default=None, description="Only customers in this segment.")


class GetForecastAccuracyArgs(StrictArgs):
    weeks: int = Field(default=8, ge=1, le=26, description="Weeks to measure, most recent first.")
    sku_or_name: str | None = Field(default=None, description="One product; all if omitted.")


class GetPrebuildRecommendationsArgs(StrictArgs):
    sku_or_name: str | None = Field(default=None, description="One product; all if omitted.")
    status: Literal["proposed", "approved", "rejected"] | None = Field(
        default=None, description="Only recommendations with this status."
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
            "value": current,
            "shown": fmt(current),
        }
        for name in comparisons:
            value = builder.rounded(record[name], kind)
            change = builder.rounded(record[f"{name}_change"], "change")
            row[name] = {
                "value": value,
                "shown": fmt(value),
                "change": change,
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


def get_customer_alerts(args: GetCustomerAlertsArgs, ctx: ToolContext) -> dict:
    """Customer ordering-pattern alerts (Week 5)."""
    return dict(NOT_AVAILABLE)


def get_forecast_accuracy(args: GetForecastAccuracyArgs, ctx: ToolContext) -> dict:
    """Forecast accuracy, WAPE and bias (Week 5)."""
    return dict(NOT_AVAILABLE)


def get_prebuild_recommendations(args: GetPrebuildRecommendationsArgs, ctx: ToolContext) -> dict:
    """Pre-build recommendations, read-only; the agent cannot approve them (Week 5)."""
    return dict(NOT_AVAILABLE)


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
    """Drop pydantic's auto-generated 'title' keys; they cost tokens and add nothing."""
    if isinstance(schema, dict):
        return {k: without_titles(v) for k, v in schema.items() if k != "title"}
    if isinstance(schema, list):
        return [without_titles(v) for v in schema]
    return schema


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
            "Customer ordering-pattern alerts (overdue, declining, new high-value, segment "
            "changes). Not available until Week 5.",
            GetCustomerAlertsArgs,
            get_customer_alerts,
        ),
        Tool(
            "get_forecast_accuracy",
            "Forecast accuracy (WAPE and bias) per product. Not available until Week 5.",
            GetForecastAccuracyArgs,
            get_forecast_accuracy,
        ),
        Tool(
            "get_prebuild_recommendations",
            "Pre-build recommendations for planners (read-only). Not available until Week 5.",
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
        started = time.monotonic()
        outcome = self._execute(name, arguments)
        outcome.duration_seconds = round(time.monotonic() - started, 3)
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
    for key in ("kpis", "weeks"):
        if key in result:
            return len(result[key])
    return None
