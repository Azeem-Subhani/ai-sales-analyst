"""Customer ordering-pattern alerts and RFM segments (Week 5).

Alert types (thresholds in config/demand.yaml, customer_alerts):
- overdue: customers with at least 4 orders in the last 365 days whose days since
  their last order exceed 1.5x their usual interval (serious above 2x). The usual
  interval is the median number of days between their order dates in that window,
  with a floor of 7 days.
- declining: last-90-day revenue more than 25% below the prior 90 days, with prior
  revenue of at least 1,000.
- new_high_value: first-ever order in the last 90 days, and revenue since then in the
  top 10% of all customers' last-90-day revenue.
- segment_change: the RFM segment moved from a top segment (Champions, Loyal) to an
  at-risk one (At risk, Lapsed) compared with 28 days earlier.

Overdue and segment-move alerts cover wholesale customers and the retail customers in
the top 20% of all customers by last-365-day revenue; the other lists cover everyone.

Orders = non-cancelled orders (the demand definition). Revenue = net revenue
(completed orders, as in the KPIs), summed exactly in SQL. Windows are inclusive and
end on as_of: "the last 365 days" is as_of - 364 to as_of.
"""

import math
from datetime import date, timedelta
from itertools import pairwise

import duckdb
import pandas as pd

from src.analytics import demand

ALERT_TYPES = ["overdue", "declining", "new_high_value", "segment_change"]
TOP_SEGMENTS = {"Champions", "Loyal"}
AT_RISK_SEGMENTS = {"At risk", "Lapsed"}
SEGMENT_NOT_ACTIVE = "Lapsed"  # no order in the 365-day window at all
# RFM score thresholds for the segment map (scores are 1-5, 5 = best).
HIGH_SCORE = 4
MID_SCORE = 3
MIN_ORDER_DATES = 2

REVENUE_SQL = """
SELECT o.customer_id,
       SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)) AS revenue
FROM orders o JOIN order_items oi USING (order_id)
WHERE o.status = 'completed' AND o.order_date BETWEEN $start AND $end
GROUP BY o.customer_id
"""
CUSTOMERS_SQL = "SELECT customer_id, name, customer_type, region FROM customers"


def window_start(as_of: date, days: int) -> date:
    """First day of the `days`-day window ending on as_of (inclusive)."""
    return as_of - timedelta(days=days - 1)


def revenue_between(con: duckdb.DuckDBPyConnection, start: date, end: date) -> pd.Series:
    """Net revenue per customer_id between two dates (inclusive), as floats."""
    rows = con.execute(REVENUE_SQL, {"start": start, "end": end}).fetchall()
    return pd.Series({cid: float(revenue) for cid, revenue in rows}, dtype=float)


def customer_orders(inputs: demand.DemandInputs) -> pd.DataFrame:
    """One row per non-cancelled order on or before as_of: order_id, customer_id, order_date."""
    orders = inputs.lines[["order_id", "customer_id", "order_date"]].drop_duplicates("order_id")
    return orders.reset_index(drop=True)


def customer_info(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Name, type and region per customer (never email)."""
    info = con.execute(CUSTOMERS_SQL).df().rename(columns={"name": "customer_name"})
    return info.set_index("customer_id")


# ---------------------------------------------------------------- overdue


def overdue_alerts(
    orders: pd.DataFrame,
    revenue_365d: pd.Series,
    as_of: date,
    settings: dict,
    eligible: set | None = None,
) -> pd.DataFrame:
    """Customers past their usual reorder interval, ranked by last-365-day revenue.

    The usual interval is the median gap, but never less than min_usual_interval_days,
    so a customer who orders every 2 days is not overdue after 5. eligible limits the
    customers considered (None = everyone).
    """
    start = window_start(as_of, settings["window_days"])
    window = orders[orders["order_date"] >= start]
    if eligible is not None:
        window = window[window["customer_id"].isin(eligible)]
    rows = []
    for customer_id, group in window.groupby("customer_id"):
        if len(group) < settings["overdue_min_orders"]:
            continue
        days = sorted(set(group["order_date"]))
        if len(days) < MIN_ORDER_DATES:  # need at least one gap between order dates
            continue
        gaps = pd.Series([(b - a).days for a, b in pairwise(days)])
        usual = max(float(gaps.median()), float(settings["min_usual_interval_days"]))
        since = (as_of - days[-1]).days
        ratio = since / usual
        if ratio > settings["serious_factor"]:
            severity = "serious"
        elif ratio > settings["overdue_factor"]:
            severity = "overdue"
        else:
            continue
        rows.append(
            {
                "customer_id": customer_id,
                "severity": severity,
                "last_order_date": days[-1],
                "days_since_last_order": since,
                "orders_in_window": len(group),
                "usual_interval_days": usual,
                "interval_ratio": ratio,
                "revenue_365d": float(revenue_365d.get(customer_id, 0.0)),
            }
        )
    alerts = pd.DataFrame(rows)
    return ranked(alerts, "overdue", by=["revenue_365d"], ascending=[False])


# ---------------------------------------------------------------- declining


def declining_alerts(recent: pd.Series, prior: pd.Series, settings: dict) -> pd.DataFrame:
    """Customers whose last-90-day revenue fell more than 25% below the 90 days before.

    Ranked by revenue lost (prior minus recent), largest first.
    """
    frame = pd.DataFrame({"revenue_prior": prior}).join(pd.DataFrame({"revenue_recent": recent}))
    frame["revenue_recent"] = frame["revenue_recent"].fillna(0.0)
    big_enough = frame["revenue_prior"] >= settings["declining_min_prior_revenue"]
    dropped = frame["revenue_recent"] < frame["revenue_prior"] * (1 - settings["declining_drop"])
    alerts = frame[big_enough & dropped].copy()
    alerts["revenue_change_pct"] = (alerts["revenue_recent"] / alerts["revenue_prior"] - 1) * 100
    alerts["revenue_lost"] = alerts["revenue_prior"] - alerts["revenue_recent"]
    alerts["severity"] = "warning"
    alerts = alerts.rename_axis("customer_id").reset_index()
    alerts = ranked(alerts, "declining", by=["revenue_lost"], ascending=[False])
    return alerts.drop(columns="revenue_lost", errors="ignore")


# ---------------------------------------------------------------- new high-value


def new_high_value_alerts(
    orders: pd.DataFrame, recent: pd.Series, as_of: date, settings: dict
) -> pd.DataFrame:
    """New customers (first order in the last 90 days) in the top 10% of 90-day revenue.

    The bar is the 90th percentile of last-90-day revenue over all customers with
    revenue in that window.
    """
    start = window_start(as_of, settings["new_high_value_window_days"])
    first = orders.groupby("customer_id")["order_date"].min()
    new = first[first >= start]
    active = recent[recent > 0]
    if active.empty or new.empty:
        return ranked(pd.DataFrame(), "new_high_value", by=[], ascending=[])
    bar = float(active.quantile(1 - settings["new_high_value_top_share"]))
    revenue = recent.reindex(new.index).fillna(0.0)
    picked = revenue[(revenue > 0) & (revenue >= bar)]
    alerts = pd.DataFrame(
        {
            "customer_id": picked.index,
            "first_order_date": new.reindex(picked.index).to_numpy(),
            "revenue_recent": picked.to_numpy(),
        }
    )
    alerts["severity"] = "info"
    return ranked(alerts, "new_high_value", by=["revenue_recent"], ascending=[False])


# ---------------------------------------------------------------- RFM


def quintile_scores(values: pd.Series, quintiles: int) -> pd.Series:
    """Scores 1..quintiles by percentile rank; ties share a score; higher value = higher score."""
    pct = values.rank(method="average", pct=True)
    return pct.map(lambda p: min(quintiles, max(1, math.ceil(p * quintiles)))).astype(int)


def segment_for(r: int, f: int, m: int) -> str:
    """The named segment for one customer's R, F and M scores (map in docs/decisions.md)."""
    if r >= HIGH_SCORE and f >= HIGH_SCORE and m >= HIGH_SCORE:
        return "Champions"
    if r >= MID_SCORE and f >= MID_SCORE:
        return "Loyal"
    if r >= HIGH_SCORE:
        return "Promising"
    if r == MID_SCORE:
        return "Needs attention"
    if f >= MID_SCORE:
        return "At risk"
    return "Lapsed"


def rfm_scores(
    orders: pd.DataFrame, revenue_365d: pd.Series, scored_as_of: date, settings: dict
) -> pd.DataFrame:
    """RFM scores and segments for customers with an order in the 365 days to scored_as_of."""
    start = window_start(scored_as_of, settings["window_days"])
    window = orders[(orders["order_date"] >= start) & (orders["order_date"] <= scored_as_of)]
    if window.empty:
        return pd.DataFrame()
    grouped = window.groupby("customer_id")
    frame = pd.DataFrame(
        {
            "recency_days": grouped["order_date"].max().map(lambda d: (scored_as_of - d).days),
            "frequency": grouped.size(),
        }
    )
    frame["monetary"] = revenue_365d.reindex(frame.index).fillna(0.0)
    quintiles = settings["rfm_quintiles"]
    frame["r_score"] = quintile_scores(-frame["recency_days"], quintiles)
    frame["f_score"] = quintile_scores(frame["frequency"], quintiles)
    frame["m_score"] = quintile_scores(frame["monetary"], quintiles)
    frame["segment"] = [
        segment_for(r, f, m)
        for r, f, m in zip(frame["r_score"], frame["f_score"], frame["m_score"], strict=True)
    ]
    frame["scored_as_of"] = scored_as_of
    return frame.rename_axis("customer_id").reset_index()


def segment_change_alerts(
    now: pd.DataFrame, before: pd.DataFrame, eligible: set | None = None
) -> pd.DataFrame:
    """Customers who moved from a top segment to an at-risk segment.

    A customer in a top segment before and with no order in the window now counts
    as Lapsed. Ranked by their revenue in the earlier window.
    """
    if before.empty:
        return ranked(pd.DataFrame(), "segment_change", by=[], ascending=[])
    was = before.set_index("customer_id")
    current = now.set_index("customer_id")["segment"] if not now.empty else pd.Series(dtype=str)
    top = was[was["segment"].isin(TOP_SEGMENTS)]
    if eligible is not None:
        top = top[top.index.isin(eligible)]
    segment_now = current.reindex(top.index).fillna(SEGMENT_NOT_ACTIVE)
    moved = segment_now[segment_now.isin(AT_RISK_SEGMENTS)]
    alerts = pd.DataFrame(
        {
            "customer_id": moved.index,
            "segment_before": top.loc[moved.index, "segment"].to_numpy(),
            "segment_now": moved.to_numpy(),
            "revenue_365d": top.loc[moved.index, "monetary"].to_numpy(),
        }
    )
    alerts["severity"] = "warning"
    return ranked(alerts, "segment_change", by=["revenue_365d"], ascending=[False])


# ---------------------------------------------------------------- all together


def alert_eligible(info: pd.DataFrame, revenue_365d: pd.Series, top_share: float) -> set:
    """Customers whose overdue and segment-move alerts are shown.

    Wholesale customers always; retail customers only in the top `top_share` of all
    customers by last-365-day revenue (customers with revenue in the window).
    """
    active = revenue_365d[revenue_365d > 0]
    bar = float(active.quantile(1 - top_share)) if not active.empty else 0.0
    wholesale = set(info.index[info["customer_type"] == "wholesale"])
    big_retail = set(active.index[active >= bar]) - wholesale
    return wholesale | big_retail


def ranked(
    alerts: pd.DataFrame, alert_type: str, by: list[str], ascending: list[bool]
) -> pd.DataFrame:
    """Sort (ties by customer_id), number the rows from 1 and label the alert type."""
    if alerts.empty:
        return pd.DataFrame(columns=["customer_id", "alert_type", "severity", "rank"])
    alerts = alerts.sort_values([*by, "customer_id"], ascending=[*ascending, True])
    alerts = alerts.reset_index(drop=True)
    alerts["rank"] = range(1, len(alerts) + 1)
    alerts["alert_type"] = alert_type
    return alerts


def customer_alerts(
    con: duckdb.DuckDBPyConnection, inputs: demand.DemandInputs, settings: dict
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(customer_alerts, customer_rfm) for inputs.as_of."""
    as_of = inputs.as_of
    orders = customer_orders(inputs)
    window = settings["window_days"]
    short = settings["declining_window_days"]
    revenue_365d = revenue_between(con, window_start(as_of, window), as_of)
    recent = revenue_between(con, window_start(as_of, short), as_of)
    prior_end = as_of - timedelta(days=short)
    prior = revenue_between(con, window_start(prior_end, short), prior_end)
    new_window = settings["new_high_value_window_days"]
    recent_new = (
        recent
        if new_window == short
        else revenue_between(con, window_start(as_of, new_window), as_of)
    )

    before_day = as_of - timedelta(days=settings["rfm_compare_days"])
    revenue_before = revenue_between(con, window_start(before_day, window), before_day)
    rfm_now = rfm_scores(orders, revenue_365d, as_of, settings)
    rfm_before = rfm_scores(orders, revenue_before, before_day, settings)

    info = customer_info(con)
    eligible = alert_eligible(info, revenue_365d, settings["retail_top_share"])
    parts = [
        overdue_alerts(orders, revenue_365d, as_of, settings, eligible),
        declining_alerts(recent, prior, settings),
        new_high_value_alerts(orders, recent_new, as_of, settings),
        segment_change_alerts(rfm_now, rfm_before, eligible),
    ]
    alerts = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    if alerts.empty:
        return alerts, pd.concat([rfm_now, rfm_before], ignore_index=True)
    alerts = alerts.join(info, on="customer_id")
    # revenue_365d for every alert type, so any list can be read by account size.
    alerts["revenue_365d"] = alerts["customer_id"].map(revenue_365d).fillna(0.0)
    return alerts, pd.concat([rfm_now, rfm_before], ignore_index=True)
