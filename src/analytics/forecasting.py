"""Forecast accuracy, our own weekly forecasts and their backtest (Week 5).

Actuals are always the demand series from src/analytics/demand.py (units on
non-cancelled orders, Monday-to-Sunday weeks), the same definition get_forecast uses.

- WAPE = sum |actual - forecast| / sum actual
- bias = (sum forecast - sum actual) / sum actual (positive = forecast too high)
Both are stored as unrounded fractions and rounded once, at display.
"""

from datetime import date

import duckdb
import pandas as pd

from src.analytics import demand

SEEDED_FORECASTS_SQL = """
SELECT product_id, week_start, forecast_qty
FROM forecasts
WHERE model = $model
  AND week_start + INTERVAL 6 DAY <= $as_of   -- the week is complete
  AND created_at <= $as_of                     -- the forecast existed on as_of
"""


# ---------------------------------------------------------------- accuracy of the seeded forecasts


def wape_and_bias(
    actual: float, forecast: float, abs_error: float
) -> tuple[float | None, float | None]:
    """(WAPE, bias) as fractions; None for both when there is no actual demand."""
    if actual <= 0:
        return None, None
    return abs_error / actual, (forecast - actual) / actual


def seeded_forecasts(con: duckdb.DuckDBPyConnection, as_of: date, model: str) -> pd.DataFrame:
    """Forecast rows of one model for weeks complete on as_of."""
    frame = con.execute(SEEDED_FORECASTS_SQL, {"as_of": as_of, "model": model}).df()
    frame["week_start"] = pd.to_datetime(frame["week_start"]).dt.date
    return frame


def accuracy_rows(level: str, key: str, group: pd.DataFrame, model: str) -> dict:
    """One accuracy row from forecast/actual pairs."""
    actual = float(group["actual"].sum())
    forecast = float(group["forecast_qty"].sum())
    abs_error = float((group["actual"] - group["forecast_qty"]).abs().sum())
    wape, bias = wape_and_bias(actual, forecast, abs_error)
    return {
        "level": level,
        "key": key,
        "model": model,
        "first_week": group["week_start"].min(),
        "last_week": group["week_start"].max(),
        "weeks": int(group["week_start"].nunique()),
        "actual_units": actual,
        "forecast_units": forecast,
        "abs_error": abs_error,
        "wape": wape,
        "bias": bias,
    }


def forecast_accuracy(
    con: duckdb.DuckDBPyConnection,
    inputs: demand.DemandInputs,
    series: pd.DataFrame,
    settings: dict,
) -> pd.DataFrame:
    """WAPE and bias per product, per category and overall, with product flags.

    A product is flagged for WAPE above wape_flag or |bias| above bias_flag, unless its
    actual units over the weeks measured are below min_actual_units (its figures are
    still stored). Category and overall rows are totals and are never flagged.
    """
    model = settings["model"]
    forecasts = seeded_forecasts(con, inputs.as_of, model)
    if forecasts.empty:
        return pd.DataFrame()
    actuals = series[["product_id", "week_start", "units"]].rename(columns={"units": "actual"})
    actuals = actuals.assign(week_start=pd.to_datetime(actuals["week_start"]).dt.date)
    pairs = forecasts.merge(actuals, on=["product_id", "week_start"], how="left")
    pairs["actual"] = pairs["actual"].fillna(0).astype(float)
    products = inputs.products.set_index("product_id")[["sku", "name", "category"]]
    pairs = pairs.join(products, on="product_id")

    rows = []
    for product_id, group in pairs.groupby("product_id"):
        row = accuracy_rows("product", group["sku"].iloc[0], group, model)
        row.update(
            product_id=product_id, name=group["name"].iloc[0], category=group["category"].iloc[0]
        )
        below = row["actual_units"] < settings["min_actual_units"]
        row["below_min_units"] = below
        row["flag_wape"] = (
            not below and row["wape"] is not None and row["wape"] > settings["wape_flag"]
        )
        row["flag_bias"] = (
            not below and row["bias"] is not None and abs(row["bias"]) > settings["bias_flag"]
        )
        rows.append(row)
    totals = {"below_min_units": False, "flag_wape": False, "flag_bias": False}
    for category, group in pairs.groupby("category"):
        rows.append(
            {**accuracy_rows("category", category, group, model), **totals, "category": category}
        )
    rows.append({**accuracy_rows("overall", "all", pairs, model), **totals})

    table = pd.DataFrame(rows)
    table["flagged"] = table["flag_wape"] | table["flag_bias"]
    return table
