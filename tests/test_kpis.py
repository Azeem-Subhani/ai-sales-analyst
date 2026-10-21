"""KPI tests on a 20-order mini dataset with hand-calculated answers (plan section 13).

Expected values are typed in as literals from the hand calculation below. They are
never computed with the code under test, so a wrong formula in kpis.sql fails here.

Mini dataset
============
Customers: 1 Ann (retail, North), 2 Bob Ltd (wholesale, South),
           3 Cara (retail, North), 4 Dan (retail, South)
Products:  1 A-1 Alpha  (Apparel)        price 10.00, cost 4.00
           2 E-1 Echo   (Electronics)    price 50.00, cost 30.00
           3 H-1 Hearth (Home & Kitchen) price 20.00, cost 12.00
Line value = qty * price * (1 - discount); line cost = qty * unit_cost.

Same week last year (ISO week 39 of 2025 = Mon 2025-09-22 to Sun 2025-09-28)
  o1  Ann 2025-09-23 web completed  A 2 x 10 @0%   = 20.00   cost  8
  o2  Bob 2025-09-24 web completed  E 4 x 50 @10%  = 180.00  cost 120

Previous week W1 (Mon 2026-09-14 to Sun 2026-09-20)
  o3  Ann 09-14 web         completed  A 1 x 10 @0%                    = 10.00   cost   4
  o4  Bob 09-15 web         completed  E 2 x 50 @0% = 100, H 5 x 20 @20% = 80
                                                                       = 180.00  cost 120
  o5  Bob 09-16 mobile      cancelled  E 1 x 50                        (excluded)
  o6  Ann 09-17 mobile      returned   H 1 x 20 @0%                    = 20.00 returns
  o7  Ann 09-18 marketplace completed  E 1 x 50 @0%                    = 50.00   cost  30
  o8  Bob 09-19 web         completed  A 10 x 10 @5%                   = 95.00   cost  40
  o9  Dan 09-19 web         cancelled  E 1 x 50   (Dan's first-ever order, cancelled)
  o10 Ann 09-20 web         completed  H 2 x 20 @0%                    = 40.00   cost  24

Current week W2 (Mon 2026-09-21 to Sun 2026-09-27)
  o11 Ann  09-21 web         completed A 3 x 10 @0%                    = 30.00   cost  12
  o12 Cara 09-22 mobile      completed H 2 x 20 @10%                   = 36.00   cost  24
  o13 Bob  09-22 web         completed E 3 x 50 @10% = 135, A 20 x 10 @10% = 180
                                                                       = 315.00  cost 170
  o14 Dan  09-24 marketplace returned  A 1 x 10 @0%                    = 10.00 returns
  o15 Cara 09-25 web         completed E 1 x 50 @0%                    = 50.00   cost  30
  o16 Ann  09-26 mobile      cancelled H 1 x 20                        (excluded)
  o17 Bob  09-26 web         returned  H 4 x 20 @5%                    = 76.00 returns
  o18 Ann  09-27 web         completed E 1 x 50 @20%                   = 40.00   cost  30
  o19 Cara 09-27 marketplace completed A 1 x 10 @0%                    = 10.00   cost   4
After W2 (boundary check, must not count in W2)
  o20 Bob  09-28 web         completed A 1 x 10 @0%                    = 10.00   cost   4

Hand calculation
----------------
W2: completed o11 o12 o13 o15 o18 o19 = 6 orders; all orders = 9 (o11 to o19)
    net revenue = 30 + 36 + 315 + 50 + 40 + 10 = 481.00
    cost of goods = 12 + 24 + 170 + 30 + 30 + 4 = 270.00
    units = 3 + 2 + 23 + 1 + 1 + 1 = 31
    AOV = 481 / 6 = 80.166667
    gross margin = (481 - 270) / 481 = 211 / 481 = 0.438669
    returns = 10 (o14) + 76 (o17) = 86.00; returned orders = 2; cancelled = 1 (o16)
    cancellation rate = 1 / 9 = 0.111111
    return rate = 2 / (6 + 2) = 0.25
    active (non-cancelled) customers: Ann, Bob, Cara, Dan
      Ann first non-cancelled order 2025-09-23 -> returning
      Bob 2025-09-24 -> returning
      Cara 2026-09-22 -> new
      Dan 2026-09-24 (o9 was cancelled, so it does not count) -> new
    new = 2, returning = 2

W1: completed o3 o4 o7 o8 o10 = 5; all orders = 8 (o3 to o10)
    net = 10 + 180 + 50 + 95 + 40 = 375.00; cost = 4 + 120 + 30 + 40 + 24 = 218.00
    units = 1 + 7 + 1 + 10 + 2 = 21; AOV = 75.00; margin = 157 / 375 = 0.418667
    returns = 20.00; returned = 1; cancelled = 2 (o5, o9)
    cancellation rate = 2 / 8 = 0.25; return rate = 1 / (5 + 1) = 0.166667
    active: Ann, Bob (Dan only cancelled) -> new 0, returning 2

Last year (2025 week 39): completed o1 o2 = 2; all orders = 2
    net = 200.00; cost = 128.00; units = 6; AOV = 100.00; margin = 72 / 200 = 0.36
    returns 0; cancelled 0; cancellation rate 0; return rate 0 / 2 = 0
    new = 2 (both first orders), returning = 0

Changes W2 vs W1 (percent; pp for rates)
    net (481-375)/375 = +28.266667%     orders (6-5)/5 = +20%
    AOV (80.166667-75)/75 = +6.888889%  units (31-21)/21 = +47.619048%
    cost (270-218)/218 = +23.853211%    margin (0.438669-0.418667)*100 = +2.000277 pp
    returns (86-20)/20 = +330%          cancelled (1-2)/2 = -50%
    cancellation rate (0.111111-0.25)*100 = -13.888889 pp
    returned (2-1)/1 = +100%            return rate (0.25-0.166667)*100 = +8.333333 pp
    new: base 0 -> None                 returning (2-2)/2 = 0%
Changes W2 vs last year
    net +140.5%; orders +200%; AOV (80.166667-100)/100 = -19.833333%;
    units (31-6)/6 = +416.666667%; cost (270-128)/128 = +110.9375%;
    margin (0.438669-0.36)*100 = +7.866944 pp; returns base 0 -> None;
    cancelled base 0 -> None; cancellation rate +11.111111 pp; returned base 0 -> None;
    return rate +25 pp; new (2-2)/2 = 0%; returning base 0 -> None

W2 by category: Electronics 135+50+40 = 225 (cost 150, units 5, orders 3)
                Apparel 30+180+10 = 220 (cost 96, units 24, orders 3)
                Home & Kitchen 36 (cost 24, units 2, orders 1)
    margins: E 75/225 = 0.333333; A 124/220 = 0.563636; H 12/36 = 0.333333
    shares:  E 225/481 = 0.467775; A 220/481 = 0.457380; H 36/481 = 0.074844
W1 by category: E 100+50 = 150 (cost 90); A 10+95 = 105 (cost 44); H 80+40 = 120 (cost 84)
    margins: E 0.4; A 61/105 = 0.580952; H 36/120 = 0.3
    revenue change: E +50%; A (220-105)/105 = +109.523810%; H (36-120)/120 = -70%
    margin change:  E -6.666667 pp; A -1.731602 pp; H +3.333333 pp
W2 by channel: web 30+315+50+40 = 435; mobile 36; marketplace 10
W2 by region:  North (Ann 70 + Cara 96) = 166; South (Bob) 315
W2 by type:    wholesale 315; retail 166

Top customers W2: Bob 315, Cara 36+50+10 = 96, Ann 30+40 = 70
    W1: Bob 180+95 = 275, Ann 10+50+40 = 100
    Bob +14.545455%; Cara no W1 revenue -> None; Ann -30%
    With n=2: W2 top 2 = Bob, Cara; W1 top 2 = Bob, Ann -> Ann dropped out
    (previous 100, current 70, current rank 3, change -30%)
Top products W2: E-1 225, A-1 220, H-1 36 (same changes as their categories)

Daily Tue 2026-09-22: o12 36 + o13 315 = 351.00, 2 orders
    vs Tue 2026-09-15: o4 180.00, 1 order -> +95%, +100%
Month September 2026: W1 375 + W2 481 + o20 10 = 866.00, 12 completed orders
Weekly trend to 2026-09-27, 3 weeks: 09-07 -> 0, 09-14 -> 375, 09-21 -> 481
"""

from collections.abc import Iterator
from datetime import date

import duckdb
import pytest

from src.analytics import kpis
from src.data.generate import SCHEMA_SQL

MONEY_TOL = 1e-6
RATE_TOL = 1e-6

CUSTOMERS = [
    (1, "Ann", "ann@example.com", "retail", "UK", "North", date(2024, 1, 1)),
    (2, "Bob Ltd", "bob@example.com", "wholesale", "UK", "South", date(2024, 1, 1)),
    (3, "Cara", "cara@example.com", "retail", "UK", "North", date(2026, 9, 1)),
    (4, "Dan", "dan@example.com", "retail", "UK", "South", date(2026, 9, 1)),
]
PRODUCTS = [
    (1, "A-1", "Alpha", "Apparel", 10.00, 4.00, date(2024, 1, 1), 14),
    (2, "E-1", "Echo", "Electronics", 50.00, 30.00, date(2024, 1, 1), 14),
    (3, "H-1", "Hearth", "Home & Kitchen", 20.00, 12.00, date(2024, 1, 1), 14),
]
A, E, H = 1, 2, 3
# order_id, customer_id, order_date, channel, status, [(product, qty, price, discount)]
ORDERS = [
    (1, 1, date(2025, 9, 23), "web", "completed", [(A, 2, 10.00, 0)]),
    (2, 2, date(2025, 9, 24), "web", "completed", [(E, 4, 50.00, 0.10)]),
    (3, 1, date(2026, 9, 14), "web", "completed", [(A, 1, 10.00, 0)]),
    (4, 2, date(2026, 9, 15), "web", "completed", [(E, 2, 50.00, 0), (H, 5, 20.00, 0.20)]),
    (5, 2, date(2026, 9, 16), "mobile", "cancelled", [(E, 1, 50.00, 0)]),
    (6, 1, date(2026, 9, 17), "mobile", "returned", [(H, 1, 20.00, 0)]),
    (7, 1, date(2026, 9, 18), "marketplace", "completed", [(E, 1, 50.00, 0)]),
    (8, 2, date(2026, 9, 19), "web", "completed", [(A, 10, 10.00, 0.05)]),
    (9, 4, date(2026, 9, 19), "web", "cancelled", [(E, 1, 50.00, 0)]),
    (10, 1, date(2026, 9, 20), "web", "completed", [(H, 2, 20.00, 0)]),
    (11, 1, date(2026, 9, 21), "web", "completed", [(A, 3, 10.00, 0)]),
    (12, 3, date(2026, 9, 22), "mobile", "completed", [(H, 2, 20.00, 0.10)]),
    (13, 2, date(2026, 9, 22), "web", "completed", [(E, 3, 50.00, 0.10), (A, 20, 10.00, 0.10)]),
    (14, 4, date(2026, 9, 24), "marketplace", "returned", [(A, 1, 10.00, 0)]),
    (15, 3, date(2026, 9, 25), "web", "completed", [(E, 1, 50.00, 0)]),
    (16, 1, date(2026, 9, 26), "mobile", "cancelled", [(H, 1, 20.00, 0)]),
    (17, 2, date(2026, 9, 26), "web", "returned", [(H, 4, 20.00, 0.05)]),
    (18, 1, date(2026, 9, 27), "web", "completed", [(E, 1, 50.00, 0.20)]),
    (19, 3, date(2026, 9, 27), "marketplace", "completed", [(A, 1, 10.00, 0)]),
    (20, 2, date(2026, 9, 28), "web", "completed", [(A, 1, 10.00, 0)]),
]

WEEK_AS_OF = date(2026, 9, 27)


@pytest.fixture(scope="module")
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    """In-memory database with the real schema and the 20 orders above."""
    connection = duckdb.connect(":memory:")
    connection.execute(SCHEMA_SQL)
    connection.executemany("INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)", CUSTOMERS)
    connection.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", PRODUCTS)
    item_id = 0
    for order_id, customer_id, order_date, channel, status, lines in ORDERS:
        connection.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, NULL)",
            [order_id, customer_id, order_date, channel, status],
        )
        for product_id, qty, price, discount in lines:
            item_id += 1
            connection.execute(
                "INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)",
                [item_id, order_id, product_id, qty, price, discount],
            )
    connection.execute(kpis.load_queries()["order_lines"])
    yield connection
    connection.close()


@pytest.fixture(scope="module")
def week() -> kpis.Period:
    """The current week W2."""
    return kpis.period_for("week", WEEK_AS_OF)


@pytest.fixture(scope="module")
def table(con: duckdb.DuckDBPyConnection, week: kpis.Period):
    """KPI table for W2 vs W1 and vs the same week last year, indexed by KPI key."""
    comparisons = {
        name: kpis.comparison_period(week, name)
        for name in ("previous_week", "same_week_last_year")
    }
    return kpis.kpi_table(con, week, comparisons).set_index("kpi")


def close(actual: float | None, expected: float | None, tol: float = MONEY_TOL) -> bool:
    """True when both are None or the numbers agree within tol."""
    if expected is None:
        return actual is None
    return actual is not None and abs(actual - expected) <= tol


# ---------------------------------------------------------------- periods


def test_week_period_runs_monday_to_sunday(week: kpis.Period) -> None:
    assert (week.start, week.end) == (date(2026, 9, 21), date(2026, 9, 27))


@pytest.mark.parametrize(
    ("kind", "as_of"),
    [("week", date(2026, 9, 26)), ("month", date(2026, 8, 30)), ("year", date(2026, 9, 30))],
)
def test_partial_or_unknown_periods_are_rejected(kind: str, as_of: date) -> None:
    with pytest.raises(ValueError):
        kpis.period_for(kind, as_of)


@pytest.mark.parametrize(
    ("kind", "as_of", "name", "start", "end"),
    [
        ("day", date(2026, 9, 29), "same_weekday_last_week", date(2026, 9, 22), date(2026, 9, 22)),
        ("week", WEEK_AS_OF, "previous_week", date(2026, 9, 14), date(2026, 9, 20)),
        ("week", WEEK_AS_OF, "same_week_last_year", date(2025, 9, 22), date(2025, 9, 28)),
        # 2020 has ISO week 53, 2019 does not: fall back to 2019 week 52.
        ("week", date(2021, 1, 3), "same_week_last_year", date(2019, 12, 23), date(2019, 12, 29)),
        ("month", date(2026, 3, 31), "previous_month", date(2026, 2, 1), date(2026, 2, 28)),
        ("month", date(2026, 8, 31), "same_month_last_year", date(2025, 8, 1), date(2025, 8, 31)),
    ],
)
def test_comparison_periods(kind: str, as_of: date, name: str, start: date, end: date) -> None:
    period = kpis.comparison_period(kpis.period_for(kind, as_of), name)
    assert (period.start, period.end) == (start, end)


def test_comparison_must_match_period_kind() -> None:
    with pytest.raises(ValueError):
        kpis.comparison_period(kpis.period_for("day", WEEK_AS_OF), "previous_week")


# ---------------------------------------------------------------- headline KPIs

# kpi: (W2 value, W1 value, change vs W1, last-year value, change vs last year)
EXPECTED_KPIS = {
    "net_revenue": (481.00, 375.00, 28.266667, 200.00, 140.5),
    "orders": (6, 5, 20.0, 2, 200.0),
    "aov": (80.166667, 75.00, 6.888889, 100.00, -19.833333),
    "units": (31, 21, 47.619048, 6, 416.666667),
    "cost_of_goods": (270.00, 218.00, 23.853211, 128.00, 110.9375),
    "gross_margin_pct": (0.438669, 0.418667, 2.000277, 0.36, 7.866944),
    "returns_value": (86.00, 20.00, 330.0, 0.0, None),
    "cancelled_orders": (1, 2, -50.0, 0, None),
    "cancellation_rate": (0.111111, 0.25, -13.888889, 0.0, 11.111111),
    "returned_orders": (2, 1, 100.0, 0, None),
    "return_rate": (0.25, 0.166667, 8.333333, 0.0, 25.0),
    "new_customers": (2, 0, None, 2, 0.0),
    "returning_customers": (2, 2, 0.0, 0, None),
}
# Hand values above are rounded to 6 decimals, so compare at that precision.
HAND_TOL = 1e-6 + 5e-7


@pytest.mark.parametrize("kpi", list(EXPECTED_KPIS))
def test_kpi_values_and_changes(table, kpi: str) -> None:
    current, prev, prev_change, last_year, ly_change = EXPECTED_KPIS[kpi]
    row = table.loc[kpi]
    assert close(row["current"], current, HAND_TOL)
    assert close(row["previous_week"], prev, HAND_TOL)
    assert close(kpis.none_if_nan(row["previous_week_change"]), prev_change, HAND_TOL)
    assert close(row["same_week_last_year"], last_year, HAND_TOL)
    assert close(kpis.none_if_nan(row["same_week_last_year_change"]), ly_change, HAND_TOL)


def test_daily_vs_same_weekday_last_week(con: duckdb.DuckDBPyConnection) -> None:
    day = kpis.period_for("day", date(2026, 9, 22))
    compare = {"same_weekday_last_week": kpis.comparison_period(day, "same_weekday_last_week")}
    t = kpis.kpi_table(con, day, compare).set_index("kpi")
    assert close(t.loc["net_revenue", "current"], 351.00)
    assert close(t.loc["net_revenue", "same_weekday_last_week"], 180.00)
    assert close(t.loc["net_revenue", "same_weekday_last_week_change"], 95.0)
    assert close(t.loc["orders", "same_weekday_last_week_change"], 100.0)


def test_month_includes_every_day(con: duckdb.DuckDBPyConnection) -> None:
    values = kpis.kpi_values(con, kpis.period_for("month", date(2026, 9, 30)))
    assert close(values["net_revenue"], 866.00)
    assert values["orders"] == 12


# ---------------------------------------------------------------- breakdowns


def test_category_breakdown(con: duckdb.DuckDBPyConnection, week: kpis.Period) -> None:
    previous = kpis.comparison_period(week, "previous_week")
    df = kpis.breakdown(con, "category", week, previous).set_index("value")
    assert list(df.index) == ["Electronics", "Apparel", "Home & Kitchen"]  # by net revenue
    # value: (net, cost, margin, units, orders, share, revenue change %, margin change pp)
    expected = {
        "Electronics": (225.0, 150.0, 0.333333, 5, 3, 0.467775, 50.0, -6.666667),
        "Apparel": (220.0, 96.0, 0.563636, 24, 3, 0.457380, 109.523810, -1.731602),
        "Home & Kitchen": (36.0, 24.0, 0.333333, 2, 1, 0.074844, -70.0, 3.333333),
    }
    for value, (net, cost, margin, units, orders, share, rev_chg, gm_chg) in expected.items():
        row = df.loc[value]
        assert close(row["net_revenue"], net)
        assert close(row["cost_of_goods"], cost)
        assert close(row["gross_margin_pct"], margin, HAND_TOL)
        assert row["units"] == units
        assert row["orders"] == orders
        assert close(row["share"], share, HAND_TOL)
        assert close(row["net_revenue_change"], rev_chg, HAND_TOL)
        assert close(row["gross_margin_change"], gm_chg, HAND_TOL)


@pytest.mark.parametrize(
    ("dimension", "expected"),
    [
        ("channel", {"web": 435.0, "mobile": 36.0, "marketplace": 10.0}),
        ("region", {"South": 315.0, "North": 166.0}),
        ("customer_type", {"wholesale": 315.0, "retail": 166.0}),
    ],
)
def test_other_breakdowns(
    con: duckdb.DuckDBPyConnection, week: kpis.Period, dimension: str, expected: dict
) -> None:
    df = kpis.breakdown(con, dimension, week)
    assert dict(zip(df["value"], df["net_revenue"], strict=True)) == pytest.approx(expected)
    assert list(df["value"]) == list(expected)  # sorted by net revenue, highest first


def test_unknown_dimension_is_rejected(con: duckdb.DuckDBPyConnection, week: kpis.Period) -> None:
    with pytest.raises(ValueError):
        kpis.breakdown(con, "colour; DROP TABLE orders", week)


# ---------------------------------------------------------------- top lists


def test_top_customers_with_change_and_dropouts(
    con: duckdb.DuckDBPyConnection, week: kpis.Period
) -> None:
    previous = kpis.comparison_period(week, "previous_week")
    top, dropped = kpis.top_customers(con, week, previous, n=2)
    assert list(top["customer_name"]) == ["Bob Ltd", "Cara"]
    assert list(top["net_revenue"]) == pytest.approx([315.0, 96.0])
    assert close(top.loc[0, "net_revenue_change"], 14.545455, HAND_TOL)
    assert kpis.none_if_nan(top.loc[1, "net_revenue_change"]) is None  # Cara is new
    assert list(dropped["customer_name"]) == ["Ann"]
    row = dropped.iloc[0]
    assert close(row["previous_net_revenue"], 100.0)
    assert close(row["current_net_revenue"], 70.0)
    assert row["current_rank"] == 3
    assert close(row["net_revenue_change"], -30.0)


def test_top_products(con: duckdb.DuckDBPyConnection, week: kpis.Period) -> None:
    previous = kpis.comparison_period(week, "previous_week")
    top, dropped = kpis.top_products(con, week, previous)
    assert list(top["sku"]) == ["E-1", "A-1", "H-1"]
    assert list(top["net_revenue"]) == pytest.approx([225.0, 220.0, 36.0])
    assert list(top["units"]) == [5, 24, 2]
    assert [round(v, 6) for v in top["net_revenue_change"]] == [50.0, 109.52381, -70.0]
    assert dropped.empty


# ---------------------------------------------------------------- trend


def test_weekly_trend_fills_empty_weeks(con: duckdb.DuckDBPyConnection) -> None:
    trend = kpis.revenue_trend(con, "week", WEEK_AS_OF, 3)
    assert list(trend["bucket_start"]) == [date(2026, 9, 7), date(2026, 9, 14), date(2026, 9, 21)]
    assert list(trend["net_revenue"]) == pytest.approx([0.0, 375.0, 481.0])
