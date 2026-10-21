"""Forecast accuracy, our own weekly forecasts and their backtest (Week 5).

Actuals are always the demand series from src/analytics/demand.py (units on
non-cancelled orders, Monday-to-Sunday weeks), the same definition get_forecast uses.

- WAPE = sum |actual - forecast| / sum actual
- bias = (sum forecast - sum actual) / sum actual (positive = forecast too high)
Both are stored as unrounded fractions and rounded once, at display.
"""

import warnings
from datetime import date, timedelta

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


# ---------------------------------------------------------------- our own forecasts

AUTO_ETS = "AutoETS"
SEASONAL_NAIVE = "SeasonalNaive"
RECENT_MEAN = "recent_mean"
MODEL_NAMES = [AUTO_ETS, SEASONAL_NAIVE]
FREQ = "W-MON"  # weeks start on Monday
SINGLE_PROCESS = 1  # a process pool breaks under spawn and inside scheduler threads


def statsforecast_models(season_length: int) -> list:
    """AutoETS and SeasonalNaive with the configured season length."""
    from statsforecast.models import AutoETS, SeasonalNaive  # slow import; only when fitting

    return [AutoETS(season_length=season_length), SeasonalNaive(season_length=season_length)]


def fit_and_forecast(train: pd.DataFrame, horizon: int, season_length: int) -> pd.DataFrame:
    """Forecast every series in train (columns product_id, week_start, y) horizon weeks ahead.

    Returns product_id, week_start, model, forecast_qty. Forecasts are clipped at 0,
    because demand cannot be negative (a falling ETS trend can go below it).
    SeasonalNaive gives no forecast for a series shorter than one season; those rows
    are dropped, so that product has an AutoETS forecast only.
    """
    from statsforecast import StatsForecast  # slow import; only when fitting

    data = train.rename(columns={"product_id": "unique_id", "week_start": "ds"})
    data = data.assign(ds=pd.to_datetime(data["ds"]))[["unique_id", "ds", "y"]]
    engine = StatsForecast(
        models=statsforecast_models(season_length), freq=FREQ, n_jobs=SINGLE_PROCESS
    )
    with warnings.catch_warnings():
        # "Historical data is shorter than season_length": expected for young products.
        warnings.filterwarnings("ignore", message="Historical data", category=UserWarning)
        # ETS on an all-zero series (a discontinued product) divides by zero internally.
        warnings.filterwarnings("ignore", message="divide by zero", category=RuntimeWarning)
        result = engine.forecast(df=data, h=horizon)
    long = result.melt(
        id_vars=["unique_id", "ds"],
        value_vars=MODEL_NAMES,
        var_name="model",
        value_name="forecast_qty",
    ).dropna(subset=["forecast_qty"])
    long["forecast_qty"] = long["forecast_qty"].clip(lower=0)
    long = long.rename(columns={"unique_id": "product_id", "ds": "week_start"})
    long["week_start"] = long["week_start"].dt.date
    return long[["product_id", "week_start", "model", "forecast_qty"]].reset_index(drop=True)


def history_lengths(series: pd.DataFrame) -> pd.Series:
    """Weeks of history per product."""
    return series.groupby("product_id").size()


def recent_mean_forecast(series: pd.DataFrame, product_ids: list, settings: dict) -> pd.DataFrame:
    """Fallback for short histories: the mean of the last N non-stockout weeks, held flat."""
    last_week = series["week_start"].max()
    weeks = [last_week + timedelta(weeks=w + 1) for w in range(settings["horizon_weeks"])]
    rows = []
    for product_id in product_ids:
        usable = series[(series["product_id"] == product_id) & ~series["is_stockout"]]
        level = (
            float(usable["units"].tail(settings["fallback_weeks"]).mean()) if len(usable) else 0.0
        )
        rows += [
            {"product_id": product_id, "week_start": w, "model": RECENT_MEAN, "forecast_qty": level}
            for w in weeks
        ]
    return pd.DataFrame(rows)


def backtest_windows(last_week: date, settings: dict) -> list[tuple[date, date]]:
    """(first test week, last test week) of each rolling origin, newest window last.

    Windows of backtest_horizon_weeks, every backtest_step_weeks, aligned so the newest
    window ends on the last complete week and all lie inside the last backtest_weeks.
    """
    horizon, step = settings["backtest_horizon_weeks"], settings["backtest_step_weeks"]
    count = (settings["backtest_weeks"] - horizon) // step + 1
    windows = []
    for k in range(count):
        end = last_week - timedelta(weeks=k * step)
        windows.append((end - timedelta(weeks=horizon - 1), end))
    return windows[::-1]


def backtest(series: pd.DataFrame, product_ids: list, settings: dict) -> pd.DataFrame:
    """Rolling-origin backtest of both models; one row per product and model.

    Each window fits on the weeks before it (stockout weeks imputed, as in the final
    fit) and is scored on actual units, leaving out stockout weeks, which have no
    real demand to compare with. A product is skipped in a window if it has fewer
    than backtest_min_train_weeks weeks of training data.
    """
    data = series[series["product_id"].isin(product_ids)]
    week = pd.to_datetime(data["week_start"]).dt.date
    data = data.assign(week_start=week)
    last_week = data["week_start"].max()
    scored = []
    for first, last in backtest_windows(last_week, settings):
        train = data[data["week_start"] < first]
        lengths = train.groupby("product_id").size()
        keep = lengths[lengths >= settings["backtest_min_train_weeks"]].index
        train = train[train["product_id"].isin(keep)]
        if train.empty:
            continue
        horizon = settings["backtest_horizon_weeks"]
        forecast = fit_and_forecast(
            train.rename(columns={"units_model": "y"}), horizon, settings["season_length"]
        )
        test = data[(data["week_start"] >= first) & (data["week_start"] <= last)]
        test = test[~test["is_stockout"]][["product_id", "week_start", "units"]]
        pairs = forecast.merge(test, on=["product_id", "week_start"])
        pairs["window_start"] = first
        scored.append(pairs)
    if not scored:
        return pd.DataFrame()
    pairs = pd.concat(scored, ignore_index=True)
    pairs["abs_error"] = (pairs["units"] - pairs["forecast_qty"]).abs()
    grouped = pairs.groupby(["product_id", "model"])
    result = pd.DataFrame(
        {
            "windows": grouped["window_start"].nunique(),
            "weeks_scored": grouped.size(),
            "actual_units": grouped["units"].sum().astype(float),
            "abs_error": grouped["abs_error"].sum(),
        }
    ).reset_index()
    result["wape"] = result["abs_error"] / result["actual_units"].where(result["actual_units"] > 0)
    return result


def choose_models(results: pd.DataFrame) -> pd.Series:
    """The better model per product by backtest WAPE (AutoETS on ties or missing WAPE)."""
    choice = {}
    for product_id, group in results.groupby("product_id"):
        wape = group.set_index("model")["wape"]
        ets, naive = wape.get(AUTO_ETS), wape.get(SEASONAL_NAIVE)
        better_naive = naive is not None and pd.notna(naive) and (pd.isna(ets) or naive < ets)
        choice[product_id] = SEASONAL_NAIVE if better_naive else AUTO_ETS
    return pd.Series(choice, dtype=object)


# Fitted results by (series fingerprint, settings): as_of dates with the same complete
# weeks (2026-09-27 and 2026-09-30) fit once per process. Small: a few snapshots.
_FIT_CACHE: dict[tuple[int, str], tuple[pd.DataFrame, pd.DataFrame]] = {}


def series_fingerprint(series: pd.DataFrame) -> int:
    """A hash of the demand series content (same weeks and units = same fit)."""
    columns = ["product_id", "week_start", "units", "is_stockout", "units_model"]
    return int(pd.util.hash_pandas_object(series[columns], index=False).sum())


def own_forecasts(
    inputs: demand.DemandInputs, series: pd.DataFrame, settings: dict
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(model_forecasts, backtest_results) for every product with a demand series.

    Products with at least min_history_weeks weeks get AutoETS and SeasonalNaive
    forecasts and a backtest that picks one; shorter histories get the recent-mean
    fallback (is_fallback). Forecasts start the week after the last complete week.
    """
    key = (series_fingerprint(series), repr(sorted(settings.items())))
    if key in _FIT_CACHE:
        forecasts, results = _FIT_CACHE[key]
        return forecasts.copy(), results.copy()
    lengths = history_lengths(series)
    long_ids = lengths[lengths >= settings["min_history_weeks"]].index.tolist()
    short_ids = lengths[lengths < settings["min_history_weeks"]].index.tolist()

    results = backtest(series, long_ids, settings)
    chosen = choose_models(results) if not results.empty else pd.Series(dtype=object)
    if not results.empty:
        results["is_chosen"] = [
            chosen.get(p) == m for p, m in zip(results["product_id"], results["model"], strict=True)
        ]

    full = series[series["product_id"].isin(long_ids)].rename(columns={"units_model": "y"})
    forecasts = (
        fit_and_forecast(
            full[["product_id", "week_start", "y"]],
            settings["horizon_weeks"],
            settings["season_length"],
        )
        if long_ids
        else pd.DataFrame()
    )
    if not forecasts.empty:
        picked = forecasts["product_id"].map(chosen).fillna(AUTO_ETS)
        forecasts["is_chosen"] = forecasts["model"] == picked
        forecasts["is_fallback"] = False
    fallback = recent_mean_forecast(series, short_ids, settings)
    if not fallback.empty:
        fallback["is_chosen"], fallback["is_fallback"] = True, True
    forecasts = pd.concat([f for f in (forecasts, fallback) if not f.empty], ignore_index=True)

    skus = inputs.products.set_index("product_id")["sku"]
    forecasts["sku"] = forecasts["product_id"].map(skus)
    if not results.empty:
        results["sku"] = results["product_id"].map(skus)
    _FIT_CACHE[key] = (forecasts.copy(), results.copy())
    return forecasts, results


def backtest_summary(results: pd.DataFrame) -> dict:
    """Overall backtest WAPE per model (on products both models scored) and of the chosen mix."""
    both = results.groupby("product_id")["model"].nunique()
    common = results[results["product_id"].isin(both[both == len(MODEL_NAMES)].index)]
    summary = {}
    for model, group in common.groupby("model"):
        summary[model] = float(group["abs_error"].sum() / group["actual_units"].sum())
    picked = results[results["is_chosen"]]
    summary["chosen_mix"] = float(picked["abs_error"].sum() / picked["actual_units"].sum())
    summary["chosen_counts"] = picked["model"].value_counts().to_dict()
    summary["products_both_models"] = len(both[both == len(MODEL_NAMES)])
    return summary
