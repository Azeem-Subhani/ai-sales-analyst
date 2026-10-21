"""Safety stock and pre-build recommendations (Week 5).

Per product with a current stock record and one of our own forecasts:
- forecast over lead time = the chosen forecast summed over the lead time (a part
  week counts in proportion, e.g. 30 days = 4 weeks + 2/7 of the fifth)
- safety stock = z (1.65) x sd(weekly demand, last 26 weeks, stockout weeks excluded)
  x sqrt(lead time in weeks)
- pre-build = max(0, forecast over lead time + safety stock - on hand - on order)

Products flagged for forecast bias (seeded forecasts, forecast_accuracy) are not
recommended; they are listed for planner review instead. Recommendations above 0 are
stored with status "pending" and sorted by days of cover, lowest first. Only the
approval CLI (src/demand/approve.py) changes a status; the agent and reports never do.
"""

import math
from datetime import date

import pandas as pd

from src.analytics import demand

STATUS_PENDING = "pending"
REC_ID_FORMAT = "{as_of:%Y%m%d}-{sku}"


def rec_id(as_of: date, sku: str) -> str:
    """Stable recommendation id, e.g. 20260930-SB-1042 (the same on every rerun)."""
    return REC_ID_FORMAT.format(as_of=as_of, sku=sku)


def forecast_over(weekly: list[float], weeks: float) -> float:
    """Sum of the first `weeks` weeks of a forecast; a part week counts in proportion."""
    whole = math.floor(weeks)
    total = sum(weekly[:whole])
    if weeks > whole and whole < len(weekly):
        total += weekly[whole] * (weeks - whole)
    return float(total)


def demand_sd(series: pd.DataFrame, weeks: int, min_weeks: int) -> pd.Series:
    """Sample sd of weekly units over the last `weeks` weeks, stockout weeks left out.

    0 when fewer than min_weeks usable weeks remain (no spread can be measured).
    """
    recent = series.groupby("product_id", group_keys=False).tail(weeks)
    usable = recent[~recent["is_stockout"]]
    grouped = usable.groupby("product_id")["units"]
    sd = grouped.std(ddof=1).where(grouped.size() >= min_weeks)
    return sd.fillna(0.0)


def bias_flagged(accuracy: pd.DataFrame) -> pd.DataFrame:
    """Product accuracy rows flagged for bias, indexed by product_id."""
    if accuracy.empty:
        return pd.DataFrame(columns=["bias", "wape"])
    rows = accuracy[(accuracy["level"] == "product") & accuracy["flag_bias"]]
    return rows.set_index("product_id")[["bias", "wape"]]


def prebuild(
    inputs: demand.DemandInputs,
    series: pd.DataFrame,
    forecasts: pd.DataFrame,
    accuracy: pd.DataFrame,
    config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(prebuild_recommendations, planner_review) for inputs.as_of."""
    settings = config["prebuild"]
    lead = demand.lead_time_weeks(inputs.products, config["demand"]["default_lead_time_weeks"])
    sd = demand_sd(series, settings["sd_weeks"], settings["min_sd_weeks"])
    cover = demand.cover_table(inputs, series, config["stock_alerts"]["cover_weeks"])
    flagged = bias_flagged(accuracy)
    products = inputs.products.set_index("product_id")
    chosen = forecasts[forecasts["is_chosen"]].sort_values(["product_id", "week_start"])

    recommendations, review = [], []
    for product_id, group in chosen.groupby("product_id"):
        if product_id not in cover.index:
            continue  # no current stock record (discontinued)
        stock, product = cover.loc[product_id], products.loc[product_id]
        weeks = float(lead[product_id])
        lead_forecast = forecast_over(group["forecast_qty"].tolist(), weeks)
        safety = settings["service_z"] * float(sd.get(product_id, 0.0)) * math.sqrt(weeks)
        raw = lead_forecast + safety - float(stock["on_hand"]) - float(stock["on_order"])
        row = {
            "product_id": product_id,
            "sku": product["sku"],
            "name": product["name"],
            "category": product["category"],
            "days_of_cover": stock["days_of_cover"],
        }
        if product_id in flagged.index:
            bias = float(flagged.loc[product_id, "bias"])
            review.append(
                {
                    **row,
                    "reason": f"forecast bias {bias * 100:+.1f}% (seeded forecasts)",
                    "bias": bias,
                    "wape": flagged.loc[product_id, "wape"],
                    "prebuild_raw": raw,
                }
            )
            continue
        if raw <= 0:
            continue
        recommendations.append(
            {
                **row,
                "rec_id": rec_id(inputs.as_of, product["sku"]),
                "forecast_model": group["model"].iloc[0],
                "is_fallback": bool(group["is_fallback"].iloc[0]),
                "lead_time_weeks": weeks,
                "forecast_lead_time": lead_forecast,
                "demand_sd": float(sd.get(product_id, 0.0)),
                "safety_stock": safety,
                "snapshot_date": stock["snapshot_date"],
                "on_hand": int(stock["on_hand"]),
                "on_order": int(stock["on_order"]),
                "avg_daily_demand": stock["avg_daily_demand"],
                "prebuild_raw": raw,
                "recommended_units": math.ceil(raw),
                "status": STATUS_PENDING,
            }
        )
    return sorted_by_cover(pd.DataFrame(recommendations)), sorted_by_cover(pd.DataFrame(review))


def sorted_by_cover(frame: pd.DataFrame) -> pd.DataFrame:
    """Lowest days of cover first (unknown cover last), ties by SKU, ranked from 1."""
    if frame.empty:
        return frame
    frame = frame.sort_values(["days_of_cover", "sku"], na_position="last").reset_index(drop=True)
    if "rec_id" in frame.columns:
        frame["rank"] = range(1, len(frame) + 1)
    return frame
