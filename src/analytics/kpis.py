"""KPI queries and period comparisons using the SQL in kpis.sql (plan section 7).

Every number here comes from the SQL in `kpis.sql`; this module only picks periods,
runs the named queries and lines up the current period against its comparisons.
All functions take explicit dates: nothing defaults to today, because the data ends
on 2026-09-30.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "sales.duckdb"
SQL_PATH = Path(__file__).with_name("kpis.sql")

PERIOD_KINDS = ("day", "week", "month")
SUNDAY = 6  # date.weekday() value
DAYS_IN_WEEK = 7
LAST_ISO_WEEK_FALLBACK = 52  # used when last year has no ISO week 53
TOP_N_DEFAULT = 10

# Which comparisons make sense for which period kind.
COMPARISONS = {
    "same_weekday_last_week": "day",
    "previous_week": "week",
    "same_week_last_year": "week",
    "previous_month": "month",
    "same_month_last_year": "month",
}

# KPI key -> (label, kind). Kind drives formatting and how change is measured:
# "money" and "count" change in percent, "rate" changes in percentage points.
KPIS = {
    "net_revenue": ("Net revenue", "money"),
    "orders": ("Orders", "count"),
    "aov": ("Average order value", "money"),
    "units": ("Units sold", "count"),
    "cost_of_goods": ("Cost of goods", "money"),
    "gross_margin_pct": ("Gross margin", "rate"),
    "returns_value": ("Returns", "money"),
    "cancelled_orders": ("Cancelled orders", "count"),
    "cancellation_rate": ("Cancellation rate", "rate"),
    "returned_orders": ("Returned orders", "count"),
    "return_rate": ("Return rate", "rate"),
    "new_customers": ("New customers", "count"),
    "returning_customers": ("Returning customers", "count"),
}
BREAKDOWN_DIMENSIONS = ("category", "region", "channel", "customer_type")


@dataclass(frozen=True)
class Period:
    """An inclusive date range of one kind (day, week or month)."""

    kind: str
    start: date
    end: date

    @property
    def label(self) -> str:
        """Human-readable name, e.g. 'Week 39, 21 Sep to 27 Sep 2026'."""
        if self.kind == "day":
            return self.end.strftime("%a %d %b %Y")
        if self.kind == "week":
            week = self.start.isocalendar().week
            return f"Week {week}, {self.start:%d %b} to {self.end:%d %b %Y}"
        return self.start.strftime("%B %Y")

    def params(self) -> dict[str, date]:
        """Named parameters for the SQL queries."""
        return {"start": self.start, "end": self.end}


def open_db(db_path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    """Open the sales database read-only and create the order_lines temp view."""
    con = duckdb.connect(str(db_path), read_only=True)
    con.execute(load_queries()["order_lines"])
    return con


@cache
def load_queries() -> dict[str, str]:
    """Read kpis.sql and split it into {query name: SQL} at '-- name:' lines."""
    text = SQL_PATH.read_text(encoding="utf-8")
    parts = re.split(r"^-- name: (\w+)\s*$", text, flags=re.MULTILINE)
    # parts = [header, name1, sql1, name2, sql2, ...]
    return {name: sql.strip() for name, sql in zip(parts[1::2], parts[2::2], strict=True)}


def run_query(
    con: duckdb.DuckDBPyConnection, name: str, period: Period | None = None
) -> pd.DataFrame:
    """Run a named query from kpis.sql, with the period's dates as parameters."""
    sql = load_queries()[name]
    if period is None:
        return con.execute(sql).df()
    return con.execute(sql, period.params()).df()


# ---------------------------------------------------------------- periods


def period_for(kind: str, as_of: date) -> Period:
    """Return the day, week or month that ends on as_of.

    Raises ValueError if as_of is not the last day of a week (Sunday) or month,
    so a report never covers a partial period by accident.
    """
    if kind == "day":
        return Period("day", as_of, as_of)
    if kind == "week":
        if as_of.weekday() != SUNDAY:
            raise ValueError(f"Weekly reports need a Sunday as_of; {as_of} is a {as_of:%A}.")
        return Period("week", as_of - timedelta(days=DAYS_IN_WEEK - 1), as_of)
    if kind == "month":
        last_day = calendar.monthrange(as_of.year, as_of.month)[1]
        if as_of.day != last_day:
            raise ValueError(f"Monthly reports need the last day of a month; got {as_of}.")
        return Period("month", as_of.replace(day=1), as_of)
    raise ValueError(f"Unknown period kind {kind!r}; expected one of {PERIOD_KINDS}.")


def month_period(year: int, month: int) -> Period:
    """Return the calendar month as a Period."""
    last_day = calendar.monthrange(year, month)[1]
    return Period("month", date(year, month, 1), date(year, month, last_day))


def comparison_period(period: Period, name: str) -> Period:
    """Return the comparison period called `name` for `period` (see COMPARISONS)."""
    if COMPARISONS.get(name) != period.kind:
        raise ValueError(f"Comparison {name!r} does not apply to a {period.kind} period.")
    if name == "same_weekday_last_week":
        day = period.end - timedelta(days=DAYS_IN_WEEK)
        return Period("day", day, day)
    if name == "previous_week":
        return Period(
            "week",
            period.start - timedelta(days=DAYS_IN_WEEK),
            period.end - timedelta(days=DAYS_IN_WEEK),
        )
    if name == "same_week_last_year":
        iso = period.start.isocalendar()
        try:
            start = date.fromisocalendar(iso.year - 1, iso.week, 1)
        except ValueError:  # this year has week 53, last year did not
            start = date.fromisocalendar(iso.year - 1, LAST_ISO_WEEK_FALLBACK, 1)
        return Period("week", start, start + timedelta(days=DAYS_IN_WEEK - 1))
    if name == "previous_month":
        previous_end = period.start - timedelta(days=1)
        return month_period(previous_end.year, previous_end.month)
    # same_month_last_year
    return month_period(period.start.year - 1, period.start.month)


def last_order_date(con: duckdb.DuckDBPyConnection) -> date:
    """Return the latest order date in the database."""
    value = run_query(con, "last_order_date").iloc[0, 0]
    return pd.Timestamp(value).date()


# ---------------------------------------------------------------- KPIs


def change(current: float | None, previous: float | None, kind: str) -> float | None:
    """Change from previous to current.

    Percent for money and counts, percentage points for rates (both as plain numbers,
    e.g. 12.5 means +12.5% or +12.5 pp). None when it cannot be measured.
    """
    if current is None or previous is None or pd.isna(current) or pd.isna(previous):
        return None
    if kind == "rate":
        return (current - previous) * 100
    if previous == 0:
        return None
    return (current - previous) / previous * 100


def kpi_values(con: duckdb.DuckDBPyConnection, period: Period) -> dict[str, float | None]:
    """Return every KPI in KPIS for one period as {key: value}."""
    row = run_query(con, "kpi_summary", period).iloc[0].to_dict()
    row.update(run_query(con, "new_returning", period).iloc[0].to_dict())
    return {key: none_if_nan(row[key]) for key in KPIS}


def none_if_nan(value: object) -> float | None:
    """Turn NaN/NA into None and numpy numbers into plain floats."""
    if value is None or pd.isna(value):
        return None
    return float(value)


def kpi_table(
    con: duckdb.DuckDBPyConnection, period: Period, comparisons: dict[str, Period]
) -> pd.DataFrame:
    """KPIs for a period next to each comparison period.

    Columns: kpi, label, kind, current, then <comparison> and <comparison>_change
    for each comparison name.
    """
    current = kpi_values(con, period)
    others = {name: kpi_values(con, p) for name, p in comparisons.items()}
    rows = []
    for key, (label, kind) in KPIS.items():
        row = {"kpi": key, "label": label, "kind": kind, "current": current[key]}
        for name, values in others.items():
            row[name] = values[key]
            row[f"{name}_change"] = change(current[key], values[key], kind)
        rows.append(row)
    return pd.DataFrame(rows)


def breakdown(
    con: duckdb.DuckDBPyConnection,
    dimension: str,
    period: Period,
    comparison: Period | None = None,
) -> pd.DataFrame:
    """Net revenue, cost and margin by one dimension, with change vs a comparison.

    Columns: value, net_revenue, cost_of_goods, gross_margin_pct, units, orders, share,
    and when a comparison is given: previous_net_revenue, previous_gross_margin_pct,
    net_revenue_change (%), gross_margin_change (pp).
    """
    if dimension not in BREAKDOWN_DIMENSIONS:
        raise ValueError(f"Unknown dimension {dimension!r}; expected {BREAKDOWN_DIMENSIONS}.")
    current = _one_dimension(con, dimension, period)
    if comparison is not None:
        previous = _one_dimension(con, dimension, comparison)[
            ["value", "net_revenue", "gross_margin_pct"]
        ].rename(
            columns={
                "net_revenue": "previous_net_revenue",
                "gross_margin_pct": "previous_gross_margin_pct",
            }
        )
        current = current.merge(previous, on="value", how="outer")
        current["net_revenue"] = current["net_revenue"].fillna(0.0)
        current["net_revenue_change"] = [
            change(c, p, "money")
            for c, p in zip(current["net_revenue"], current["previous_net_revenue"], strict=True)
        ]
        current["gross_margin_change"] = [
            change(c, p, "rate")
            for c, p in zip(
                current["gross_margin_pct"], current["previous_gross_margin_pct"], strict=True
            )
        ]
    return current.sort_values(["net_revenue", "value"], ascending=[False, True]).reset_index(
        drop=True
    )


def _one_dimension(con: duckdb.DuckDBPyConnection, dimension: str, period: Period) -> pd.DataFrame:
    """Rows of the breakdowns query for one dimension, with each value's revenue share."""
    df = run_query(con, "breakdowns", period)
    df = df[df["dimension"] == dimension].drop(columns="dimension").reset_index(drop=True)
    total = df["net_revenue"].sum()
    df["share"] = df["net_revenue"] / total if total else None
    return df


def _top(
    con: duckdb.DuckDBPyConnection,
    query: str,
    key: str,
    period: Period,
    comparison: Period | None,
    n: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Shared logic for top products and top customers.

    Returns (top n this period with change vs comparison, comparison's top n that
    dropped out of this period's top n).
    """
    current = _ranked(run_query(con, query, period))
    if comparison is None:
        return current.head(n), current.iloc[0:0]
    previous = _ranked(run_query(con, query, comparison))
    prev_cols = previous[[key, "net_revenue", "rank"]].rename(
        columns={"net_revenue": "previous_net_revenue", "rank": "previous_rank"}
    )
    top = current.head(n).merge(prev_cols, on=key, how="left")
    top["net_revenue_change"] = [
        change(c, p, "money")
        for c, p in zip(top["net_revenue"], top["previous_net_revenue"], strict=True)
    ]
    dropped = previous.head(n)
    dropped = dropped[~dropped[key].isin(top[key])]
    now = current[[key, "net_revenue", "rank"]].rename(
        columns={"net_revenue": "current_net_revenue", "rank": "current_rank"}
    )
    dropped = dropped.rename(
        columns={"net_revenue": "previous_net_revenue", "rank": "previous_rank"}
    ).merge(now, on=key, how="left")
    dropped["current_net_revenue"] = dropped["current_net_revenue"].fillna(0.0)
    dropped["net_revenue_change"] = [
        change(c, p, "money")
        for c, p in zip(
            dropped["current_net_revenue"], dropped["previous_net_revenue"], strict=True
        )
    ]
    return top, dropped.reset_index(drop=True)


def _ranked(df: pd.DataFrame) -> pd.DataFrame:
    """Sort by net revenue (ties by first text column) and add a 1-based rank."""
    tie_breaker = df.columns[1]  # sku or customer_name
    df = df.sort_values(["net_revenue", tie_breaker], ascending=[False, True])
    df = df.reset_index(drop=True)
    df["rank"] = df.index + 1
    return df


def top_products(
    con: duckdb.DuckDBPyConnection,
    period: Period,
    comparison: Period | None = None,
    n: int = TOP_N_DEFAULT,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Top n products by net revenue, and the comparison's top n that dropped out."""
    return _top(con, "revenue_by_product", "product_id", period, comparison, n)


def top_customers(
    con: duckdb.DuckDBPyConnection,
    period: Period,
    comparison: Period | None = None,
    n: int = TOP_N_DEFAULT,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Top n customers by net revenue, and the comparison's top n that dropped out."""
    return _top(con, "revenue_by_customer", "customer_id", period, comparison, n)


def revenue_trend(
    con: duckdb.DuckDBPyConnection, bucket: str, end: date, buckets: int
) -> pd.DataFrame:
    """Net revenue for the `buckets` weeks or months ending with the one containing `end`.

    Missing buckets are filled with 0. Columns: bucket_start, net_revenue.
    """
    if bucket == "week":
        last_start = end - timedelta(days=end.weekday())
        starts = [last_start - timedelta(weeks=i) for i in range(buckets - 1, -1, -1)]
        span = Period("week", starts[0], last_start + timedelta(days=DAYS_IN_WEEK - 1))
        query = "revenue_by_week"
    elif bucket == "month":
        starts = []
        year, month = end.year, end.month
        for _ in range(buckets):
            starts.append(date(year, month, 1))
            year, month = (year, month - 1) if month > 1 else (year - 1, 12)
        starts.reverse()
        span = Period("month", starts[0], month_period(end.year, end.month).end)
        query = "revenue_by_month"
    else:
        raise ValueError(f"Unknown bucket {bucket!r}; expected 'week' or 'month'.")
    df = run_query(con, query, span)
    pairs = zip(df["bucket_start"], df["net_revenue"], strict=True)
    by_start = {pd.Timestamp(s).date(): v for s, v in pairs}
    return pd.DataFrame(
        {"bucket_start": starts, "net_revenue": [by_start.get(s, 0.0) for s in starts]}
    )
