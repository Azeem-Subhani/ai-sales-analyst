"""Weekly demand series, stockout weeks, stock position and lead times (Week 5).

Demand = units (quantity) on all non-cancelled orders (completed or returned), per
Monday-to-Sunday week. This is the same definition get_forecast uses, and it is the
only demand definition in demand planning: forecasts, forecast accuracy, safety stock
and pre-build all use the series built here.

Every function takes an explicit as_of date. Only weeks that are complete on as_of
(their Sunday is on or before it) are in the series, and only orders and inventory
snapshots on or before as_of are read.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd

DAYS_IN_WEEK = 7

DEMAND_LINES_SQL = """
SELECT o.order_id, o.customer_id, o.order_date, oi.product_id, oi.quantity
FROM orders o JOIN order_items oi USING (order_id)
WHERE o.status <> 'cancelled' AND o.order_date <= $as_of
"""
PRODUCTS_SQL = """
SELECT product_id, sku, name, category, launch_date, lead_time_days
FROM products ORDER BY product_id
"""
INVENTORY_SQL = """
SELECT product_id, snapshot_date, on_hand, on_order
FROM inventory WHERE snapshot_date <= $as_of
"""
FIRST_ORDER_SQL = "SELECT min(order_date) FROM orders"
LAST_ORDER_SQL = "SELECT max(order_date) FROM orders"


@dataclass
class DemandInputs:
    """The rows demand planning reads from the sales database, cut at as_of."""

    as_of: date
    lines: pd.DataFrame  # non-cancelled order lines on or before as_of
    products: pd.DataFrame
    inventory: pd.DataFrame  # snapshots on or before as_of
    first_order_date: date


def monday_of(day: date) -> date:
    """The Monday that starts day's week."""
    return day - timedelta(days=day.weekday())


def last_complete_week(as_of: date) -> date:
    """Start (Monday) of the latest Monday-to-Sunday week that ends on or before as_of."""
    monday = monday_of(as_of)
    is_sunday = as_of - monday == timedelta(days=DAYS_IN_WEEK - 1)
    return monday if is_sunday else monday - timedelta(days=DAYS_IN_WEEK)


def first_full_week(first_order_date: date) -> date:
    """The first Monday on or after the first order date (the first week with all 7 days)."""
    monday = monday_of(first_order_date)
    return monday if monday == first_order_date else monday + timedelta(days=DAYS_IN_WEEK)


def load_inputs(con: duckdb.DuckDBPyConnection, as_of: date) -> DemandInputs:
    """Read the order lines, products and inventory that demand planning needs."""
    last_order = con.execute(LAST_ORDER_SQL).fetchone()[0]
    if as_of > last_order:
        raise ValueError(f"as_of {as_of} is after the last order date {last_order}.")
    params = {"as_of": as_of}
    lines = con.execute(DEMAND_LINES_SQL, params).df()
    lines["order_date"] = pd.to_datetime(lines["order_date"]).dt.date
    inventory = con.execute(INVENTORY_SQL, params).df()
    inventory["snapshot_date"] = pd.to_datetime(inventory["snapshot_date"]).dt.date
    products = con.execute(PRODUCTS_SQL).df()
    products["launch_date"] = pd.to_datetime(products["launch_date"]).dt.date
    first_order = con.execute(FIRST_ORDER_SQL).fetchone()[0]
    return DemandInputs(as_of, lines, products, inventory, first_order)


def lead_time_weeks(products: pd.DataFrame, default_weeks: float) -> pd.Series:
    """Lead time in weeks per product_id (lead_time_days / 7; the default if missing)."""
    weeks = products.set_index("product_id")["lead_time_days"] / DAYS_IN_WEEK
    return weeks.fillna(default_weeks).astype(float)


def weekly_series(inputs: DemandInputs, impute_weeks: int) -> pd.DataFrame:
    """Weekly demand per product, from its launch week to the last complete week.

    Columns: product_id, sku, week_start, units, is_stockout, units_model, is_imputed.
    - A stockout week is one whose Monday inventory snapshot shows on_hand = 0.
    - units_model is the value forecasts are fitted on: units, except in stockout weeks,
      where it is the mean of the previous impute_weeks non-stockout weeks (0 if none).
    - The series starts at the later of the launch week and the first full data week.
    """
    last_week = last_complete_week(inputs.as_of)
    data_start = first_full_week(inputs.first_order_date)
    lines = inputs.lines.assign(week_start=inputs.lines["order_date"].map(monday_of))
    units = lines.groupby(["product_id", "week_start"])["quantity"].sum()
    stockouts = set(
        inputs.inventory.loc[
            inputs.inventory["on_hand"] == 0, ["product_id", "snapshot_date"]
        ].itertuples(index=False, name=None)
    )

    frames = []
    for product in inputs.products.itertuples(index=False):
        start = max(monday_of(product.launch_date), data_start)
        if start > last_week:
            continue
        weeks = pd.date_range(start, last_week, freq="7D").date
        frame = pd.DataFrame({"week_start": weeks})
        frame["product_id"], frame["sku"] = product.product_id, product.sku
        frame["units"] = [int(units.get((product.product_id, w), 0)) for w in weeks]
        frame["is_stockout"] = [(product.product_id, w) in stockouts for w in weeks]
        frame["units_model"] = impute_stockouts(frame["units"], frame["is_stockout"], impute_weeks)
        frame["is_imputed"] = frame["is_stockout"]
        frames.append(frame)
    columns = ["product_id", "sku", "week_start", "units", "is_stockout", "units_model"]
    if not frames:
        return pd.DataFrame(columns=[*columns, "is_imputed"])
    return pd.concat(frames, ignore_index=True)[[*columns, "is_imputed"]]


def impute_stockouts(units: pd.Series, is_stockout: pd.Series, weeks: int) -> list[float]:
    """Replace each stockout week with the mean of the previous `weeks` non-stockout weeks."""
    values: list[float] = []
    history: list[float] = []  # non-stockout weeks seen so far
    for value, stockout in zip(units, is_stockout, strict=True):
        if stockout:
            recent = history[-weeks:]
            values.append(float(np.mean(recent)) if recent else 0.0)
        else:
            values.append(float(value))
            history.append(float(value))
    return values


def latest_stock(inputs: DemandInputs) -> pd.DataFrame:
    """On hand and on order per product from the latest snapshot on or before as_of.

    Only products with a row in that snapshot are returned: a product with no current
    stock record (discontinued, or not launched yet) has no stock position to plan on.
    """
    inventory = inputs.inventory
    if inventory.empty:
        return pd.DataFrame(columns=["product_id", "snapshot_date", "on_hand", "on_order"])
    latest = inventory["snapshot_date"].max()
    current = inventory[inventory["snapshot_date"] == latest]
    return current[["product_id", "snapshot_date", "on_hand", "on_order"]].reset_index(drop=True)


def average_daily_demand(series: pd.DataFrame, weeks: int) -> pd.Series:
    """Mean daily units per product over the last `weeks` weeks, stockout weeks excluded.

    Products whose last `weeks` weeks are all stockout weeks get NaN (no usable demand).
    """
    recent = series.groupby("product_id", group_keys=False).tail(weeks)
    usable = recent[~recent["is_stockout"]]
    daily = usable.groupby("product_id")["units"].mean() / DAYS_IN_WEEK
    return daily.reindex(series["product_id"].unique())


def days_of_cover(on_hand: pd.Series, daily_demand: pd.Series) -> pd.Series:
    """on hand / average daily demand; NaN when demand is zero or unknown."""
    demand = daily_demand.reindex(on_hand.index)
    cover = on_hand.astype(float) / demand.where(demand > 0)
    return cover


# ---------------------------------------------------------------- category alerts

CATEGORY_REVENUE_SQL = """
SELECT p.category,
       SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)) AS revenue
FROM orders o
JOIN order_items oi USING (order_id)
JOIN products p USING (product_id)
WHERE o.status = 'completed' AND o.order_date BETWEEN $start AND $end
GROUP BY p.category
"""
CATEGORIES_SQL = "SELECT DISTINCT category FROM products ORDER BY category"


def category_alerts(con: duckdb.DuckDBPyConnection, as_of: date, settings: dict) -> pd.DataFrame:
    """Net revenue per category, last N complete weeks vs the N before; every category.

    flagged = the drop is greater than settings["drop"] (a fraction). The change is
    stored in percent and unrounded, like the KPI changes.
    """
    weeks = settings["weeks"]
    current_end = last_complete_week(as_of) + timedelta(days=DAYS_IN_WEEK - 1)
    current_start = current_end - timedelta(weeks=weeks) + timedelta(days=1)
    prior_end = current_start - timedelta(days=1)
    prior_start = current_start - timedelta(weeks=weeks)

    def revenue(start: date, end: date) -> dict[str, float]:
        rows = con.execute(CATEGORY_REVENUE_SQL, {"start": start, "end": end}).fetchall()
        return {category: float(value) for category, value in rows}

    current, prior = revenue(current_start, current_end), revenue(prior_start, prior_end)
    rows = []
    for (category,) in con.execute(CATEGORIES_SQL).fetchall():
        now, before = current.get(category, 0.0), prior.get(category, 0.0)
        change = (now / before - 1) * 100 if before else None
        rows.append(
            {
                "category": category,
                "current_start": current_start,
                "current_end": current_end,
                "prior_start": prior_start,
                "prior_end": prior_end,
                "current_revenue": now,
                "prior_revenue": before,
                "revenue_change_pct": change,
                "flagged": change is not None and change < -settings["drop"] * 100,
            }
        )
    return pd.DataFrame(rows).sort_values(["revenue_change_pct", "category"]).reset_index(drop=True)


# ---------------------------------------------------------------- stock alerts


def cover_table(inputs: DemandInputs, series: pd.DataFrame, cover_weeks: int) -> pd.DataFrame:
    """Stock position, average daily demand and days of cover for products in stock records."""
    stock = latest_stock(inputs).set_index("product_id")
    daily = average_daily_demand(series, cover_weeks)
    stock["avg_daily_demand"] = daily.reindex(stock.index)
    stock["days_of_cover"] = days_of_cover(stock["on_hand"], stock["avg_daily_demand"])
    return stock


def stock_alerts(inputs: DemandInputs, series: pd.DataFrame, settings: dict) -> pd.DataFrame:
    """Products with at least one stockout week in the last N complete weeks.

    Ranked by stockout weeks (most first), then days of cover (lowest first), then SKU.
    """
    recent = series.groupby("product_id", group_keys=False).tail(settings["weeks"])
    out = recent[recent["is_stockout"]].groupby("product_id")
    flagged = pd.DataFrame(
        {"stockout_weeks": out.size(), "last_stockout_week": out["week_start"].max()}
    )
    if flagged.empty:
        return pd.DataFrame(columns=["product_id", "rank", "stockout_weeks"])
    cover = cover_table(inputs, series, settings["cover_weeks"])
    products = inputs.products.set_index("product_id")[["sku", "name", "category"]]
    alerts = flagged.join(products).join(cover).rename_axis("product_id").reset_index()
    alerts = alerts.sort_values(
        ["stockout_weeks", "days_of_cover", "sku"],
        ascending=[False, True, True],
        na_position="last",
    ).reset_index(drop=True)
    alerts["rank"] = range(1, len(alerts) + 1)
    return alerts
