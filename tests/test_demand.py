"""Demand-planning tests (Week 5): series, store, alerts, accuracy, forecasts, pre-build.

Unit tests use small hand-made inputs. Checks on the seed-42 data use the shared
demand_runs fixture (conftest.py). Planted-problem checks are in
tests/test_demand_acceptance.py, the only test file that reads the planted issues.
"""

import math
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import pandas as pd
import pytest

from src.analytics import customers, demand, forecasting, prebuild
from src.demand import approve, run, store
from src.demand import config as demand_config

AS_OF_SUNDAY = date(2026, 9, 27)
AS_OF_MONTH_END = date(2026, 9, 30)  # a Wednesday


# ---------------------------------------------------------------- weeks and series


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (date(2026, 9, 27), date(2026, 9, 21)),  # Sunday: its own week is complete
        (date(2026, 9, 30), date(2026, 9, 21)),  # Wednesday: the week before
        (date(2026, 9, 28), date(2026, 9, 21)),  # Monday
    ],
)
def test_last_complete_week(as_of: date, expected: date) -> None:
    assert demand.last_complete_week(as_of) == expected


def test_first_full_week_skips_a_partial_first_week() -> None:
    assert demand.first_full_week(date(2024, 10, 1)) == date(2024, 10, 7)  # a Tuesday
    assert demand.first_full_week(date(2024, 10, 7)) == date(2024, 10, 7)  # a Monday


def test_stockout_weeks_are_imputed_from_the_previous_non_stockout_weeks() -> None:
    units = pd.Series([10, 20, 30, 40, 0, 0, 50])
    stockout = pd.Series([False, False, False, False, True, True, False])
    values = demand.impute_stockouts(units, stockout, weeks=4)
    assert values == [10, 20, 30, 40, 25.0, 25.0, 50]  # both use weeks 1-4


def test_stockout_with_no_history_is_zero() -> None:
    assert demand.impute_stockouts(pd.Series([0, 5]), pd.Series([True, False]), 4) == [0.0, 5]


def small_inputs(as_of: date = date(2026, 2, 1)) -> demand.DemandInputs:
    """Two products over four weeks; product 2 is out of stock in week 3."""
    weeks = [date(2026, 1, 5) + timedelta(weeks=w) for w in range(4)]
    lines = pd.DataFrame(
        {
            "order_id": [1, 2, 3, 4, 5],
            "customer_id": [1, 1, 2, 2, 3],
            "order_date": [weeks[0], weeks[1], weeks[1], weeks[3], weeks[0] + timedelta(6)],
            "product_id": [1, 1, 2, 2, 2],
            "quantity": [3, 4, 5, 6, 7],
        }
    )
    products = pd.DataFrame(
        {
            "product_id": [1, 2],
            "sku": ["A-1", "B-2"],
            "name": ["Alpha", "Beta"],
            "category": ["X", "Y"],
            "launch_date": [date(2025, 1, 1), date(2026, 1, 7)],
            "lead_time_days": [28, None],
            "unit_price": [10.0, 2.5],
        }
    )
    inventory = pd.DataFrame(
        {
            "product_id": [1, 2, 1, 2],
            "snapshot_date": [weeks[2], weeks[2], weeks[3], weeks[3]],
            "on_hand": [9, 0, 8, 4],
            "on_order": [0, 5, 0, 0],
        }
    )
    return demand.DemandInputs(as_of, lines, products, inventory, date(2026, 1, 5))


def test_weekly_series_units_stockouts_and_launch_week() -> None:
    series = demand.weekly_series(small_inputs(), impute_weeks=4)
    a = series[series["sku"] == "A-1"]
    b = series[series["sku"] == "B-2"]
    assert list(a["units"]) == [3, 4, 0, 0]
    assert list(b["week_start"]) == [date(2026, 1, 5) + timedelta(weeks=w) for w in range(4)]
    assert list(b["units"]) == [7, 5, 0, 6]  # Sunday order counts in its Monday's week
    assert list(b["is_stockout"]) == [False, False, True, False]
    assert list(b["units_model"]) == [7, 5, 6.0, 6]
    assert not a["is_stockout"].any()


def test_series_stops_at_the_last_complete_week() -> None:
    series = demand.weekly_series(small_inputs(as_of=date(2026, 1, 28)), impute_weeks=4)
    assert series["week_start"].max() == date(2026, 1, 19)


def test_lead_time_uses_the_column_and_the_default_when_missing() -> None:
    weeks = demand.lead_time_weeks(small_inputs().products, default_weeks=4)
    assert weeks.to_dict() == {1: 4.0, 2: 4.0}


def test_latest_stock_uses_the_newest_snapshot_only() -> None:
    stock = demand.latest_stock(small_inputs())
    assert stock["snapshot_date"].unique().tolist() == [date(2026, 1, 26)]
    assert stock.set_index("product_id")["on_hand"].to_dict() == {1: 8, 2: 4}


def test_days_of_cover_skips_stockout_weeks() -> None:
    series = demand.weekly_series(small_inputs(), impute_weeks=4)
    daily = demand.average_daily_demand(series, weeks=4)
    assert daily[2] == pytest.approx((7 + 5 + 6) / 3 / 7)  # week 3 excluded
    cover = demand.days_of_cover(pd.Series({2: 4}), daily)
    assert cover[2] == pytest.approx(4 / daily[2])


# ---------------------------------------------------------------- store and run


def test_config_hash_is_stable_and_changes_with_settings() -> None:
    config = demand_config.load_config()
    assert demand_config.config_hash(config) == demand_config.config_hash(dict(config))
    changed = {**config, "category_alerts": {**config["category_alerts"], "drop": 0.2}}
    assert demand_config.config_hash(changed) != demand_config.config_hash(config)


def test_snapshot_meta_and_series_on_seed_data(demand_runs: dict) -> None:
    result = demand_runs[AS_OF_MONTH_END]
    meta = result.table("demand_snapshots").iloc[0]
    assert meta["last_complete_week"].date() == date(2026, 9, 21)
    assert meta["stock_snapshot_date"].date() == date(2026, 9, 28)
    series = result.table("demand_weekly")
    assert series["sku"].nunique() == 250
    assert pd.to_datetime(series["week_start"]).min().date() == date(2024, 10, 7)
    assert series["is_stockout"].sum() == 15
    assert (series.loc[series["is_stockout"], "units"] == 0).all()


def test_demand_matches_get_forecast_definition(built: dict, demand_runs: dict) -> None:
    # Same numbers as FORECAST_SQL's actual units (non-cancelled orders, Mon-Sun weeks).
    from src.agent import tools

    payload = tools.get_forecast(
        tools.GetForecastArgs(sku_or_name="AP-1150"), tools.ToolContext(db_path=built["db_path"])
    )
    series = demand_runs[AS_OF_SUNDAY].table("demand_weekly")
    jacket = series[series["sku"] == "AP-1150"].set_index(
        pd.to_datetime(series.loc[series["sku"] == "AP-1150", "week_start"]).dt.date
    )["units"]
    for week in payload["weeks"]:
        assert jacket[date.fromisoformat(week["week_start"])] == week["actual_units"]


def test_rerun_replaces_the_snapshot(built: dict, tmp_path: Path) -> None:
    planning = tmp_path / "planning.duckdb"
    first = run.run_demand(AS_OF_SUNDAY, sales_db=built["db_path"], planning_db=planning)
    again = run.run_demand(AS_OF_SUNDAY, sales_db=built["db_path"], planning_db=planning)
    assert len(again.table("demand_weekly")) == len(first.table("demand_weekly"))
    with store.connect_read(planning) as con:
        assert con.execute("SELECT count(*) FROM demand_snapshots").fetchone()[0] == 1


def test_get_or_run_reuses_a_stored_snapshot(built: dict, tmp_path: Path) -> None:
    planning = tmp_path / "planning.duckdb"
    assert run.get_or_run(AS_OF_SUNDAY, built["db_path"], planning).computed
    assert not run.get_or_run(AS_OF_SUNDAY, built["db_path"], planning).computed


def test_as_of_after_the_data_is_rejected(built: dict, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="after the last order date"):
        run.run_demand(date(2026, 10, 4), built["db_path"], tmp_path / "p.duckdb")


def test_sales_database_is_opened_read_only() -> None:
    source = Path(run.__file__).read_text(encoding="utf-8")
    assert "kpis.open_db(" in source and "duckdb.connect(" not in source


# ---------------------------------------------------------------- customer alerts


ALERT_SETTINGS = demand_config.load_config()["customer_alerts"]
DAY0 = date(2026, 9, 30)


def orders_frame(dates_by_customer: dict[int, list[date]]) -> pd.DataFrame:
    """Orders for hand-made customers, one row per order."""
    rows = [
        {"order_id": i, "customer_id": cid, "order_date": d}
        for i, (cid, d) in enumerate(
            (cid, d) for cid, days in dates_by_customer.items() for d in days
        )
    ]
    return pd.DataFrame(rows)


def test_overdue_uses_the_median_gap_and_both_severities() -> None:
    every_14 = [DAY0 - timedelta(days=d) for d in (100, 86, 72, 58)]  # last order 58 days ago
    every_30 = [DAY0 - timedelta(days=d) for d in (140, 110, 80, 50)]  # 50 / 30 = 1.67x
    on_time = [DAY0 - timedelta(days=d) for d in (40, 30, 20, 10)]
    too_few = [DAY0 - timedelta(days=d) for d in (200, 150, 100)]
    orders = orders_frame({1: every_14, 2: every_30, 3: on_time, 4: too_few})
    revenue = pd.Series({1: 100.0, 2: 500.0, 3: 900.0, 4: 50.0})
    alerts = customers.overdue_alerts(orders, revenue, DAY0, ALERT_SETTINGS).set_index(
        "customer_id"
    )
    assert set(alerts.index) == {1, 2}
    assert alerts.loc[1, "severity"] == "serious" and alerts.loc[1, "usual_interval_days"] == 14
    assert alerts.loc[2, "severity"] == "overdue"
    assert alerts.loc[2, "rank"] == 1  # ranked by 365-day revenue


def test_overdue_ignores_orders_older_than_the_window() -> None:
    old = [DAY0 - timedelta(days=d) for d in (500, 400, 380, 20)]  # only 1 in the window
    alerts = customers.overdue_alerts(
        orders_frame({1: old}), pd.Series(dtype=float), DAY0, ALERT_SETTINGS
    )
    assert alerts.empty


def test_declining_needs_a_drop_over_25_percent_and_prior_of_1000() -> None:
    prior = pd.Series({1: 2000.0, 2: 2000.0, 3: 900.0, 4: 1000.0})
    recent = pd.Series({1: 1400.0, 2: 1600.0, 3: 0.0})  # 4 has no recent revenue
    alerts = customers.declining_alerts(recent, prior, ALERT_SETTINGS).set_index("customer_id")
    assert set(alerts.index) == {1, 4}  # 2 fell 20%, 3 is too small
    assert alerts.loc[1, "revenue_change_pct"] == pytest.approx(-30)
    assert alerts.loc[4, "rank"] == 1  # lost 1,000 vs 600


def test_new_high_value_needs_a_first_order_in_90_days_and_top_10_percent() -> None:
    recent = pd.Series({cid: float(cid) for cid in range(1, 21)})  # bar = 18.1
    first = {cid: [DAY0 - timedelta(days=200)] for cid in range(1, 21)}
    first[19] = [DAY0 - timedelta(days=30)]  # new and 19 >= bar
    first[5] = [DAY0 - timedelta(days=30)]  # new but small
    alerts = customers.new_high_value_alerts(orders_frame(first), recent, DAY0, ALERT_SETTINGS)
    assert alerts["customer_id"].tolist() == [19]


@pytest.mark.parametrize(
    ("scores", "segment"),
    [
        ((5, 5, 5), "Champions"),
        ((4, 4, 3), "Loyal"),
        ((3, 3, 1), "Loyal"),
        ((5, 1, 1), "Promising"),
        ((3, 2, 5), "Needs attention"),
        ((2, 4, 4), "At risk"),
        ((1, 2, 5), "Lapsed"),
    ],
)
def test_segment_map(scores: tuple[int, int, int], segment: str) -> None:
    assert customers.segment_for(*scores) == segment


def test_quintile_scores_give_ties_the_same_score() -> None:
    scores = customers.quintile_scores(pd.Series([1, 2, 3, 4, 5, 5, 5, 8, 9, 10]), 5)
    assert scores.tolist() == [1, 1, 2, 2, 3, 3, 3, 4, 5, 5]


def test_segment_change_flags_top_to_at_risk_only() -> None:
    before = pd.DataFrame(
        {
            "customer_id": [1, 2, 3, 4],
            "segment": ["Champions", "Loyal", "Promising", "Loyal"],
            "monetary": [10.0, 20.0, 30.0, 40.0],
        }
    )
    now = pd.DataFrame({"customer_id": [1, 2, 3], "segment": ["At risk", "Loyal", "Lapsed"]})
    alerts = customers.segment_change_alerts(now, before)
    assert alerts["customer_id"].tolist() == [4, 1]  # 4 has no order now: Lapsed
    assert alerts.set_index("customer_id").loc[4, "segment_now"] == "Lapsed"


def test_customer_alerts_never_carry_email(demand_runs: dict) -> None:
    alerts = demand_runs[AS_OF_MONTH_END].table("customer_alerts")
    assert not [c for c in alerts.columns if "email" in c]
    assert set(alerts["alert_type"]) <= set(customers.ALERT_TYPES)


# ---------------------------------------------------------------- category and stock alerts


def test_category_alerts_cover_all_categories_and_8_week_windows(demand_runs: dict) -> None:
    table = demand_runs[AS_OF_SUNDAY].table("category_alerts")
    assert len(table) == 8
    row = table.iloc[0]
    assert pd.Timestamp(row["current_start"]).date() == date(2026, 8, 3)
    assert pd.Timestamp(row["current_end"]).date() == date(2026, 9, 27)
    assert pd.Timestamp(row["prior_start"]).date() == date(2026, 6, 8)
    assert table["flagged"].sum() <= 2


def test_stock_alerts_count_stockout_weeks_in_the_last_4_weeks() -> None:
    inputs = small_inputs()
    series = demand.weekly_series(inputs, impute_weeks=4)
    alerts = demand.stock_alerts(inputs, series, {"weeks": 4, "cover_weeks": 4})
    assert alerts["sku"].tolist() == ["B-2"]
    row = alerts.iloc[0]
    assert row["stockout_weeks"] == 1 and row["on_hand"] == 4
    assert row["days_of_cover"] == pytest.approx(4 / ((7 + 5 + 6) / 3 / 7))


# ---------------------------------------------------------------- forecast accuracy


def test_wape_and_bias_formulas() -> None:
    wape, bias = forecasting.wape_and_bias(actual=100, forecast=125, abs_error=30)
    assert wape == pytest.approx(0.30) and bias == pytest.approx(0.25)
    assert forecasting.wape_and_bias(0, 5, 5) == (None, None)


def test_accuracy_rows_sum_before_dividing() -> None:
    pairs = pd.DataFrame(
        {
            "week_start": [date(2026, 8, 3), date(2026, 8, 10)],
            "actual": [10.0, 30.0],
            "forecast_qty": [20.0, 25.0],
        }
    )
    row = forecasting.accuracy_rows("product", "A-1", pairs, "seeded")
    assert row["wape"] == pytest.approx(15 / 40)  # (10 + 5) / 40
    assert row["bias"] == pytest.approx(5 / 40)  # (45 - 40) / 40
    assert row["weeks"] == 2


def test_accuracy_levels_and_small_products(demand_runs: dict) -> None:
    table = demand_runs[AS_OF_SUNDAY].table("forecast_accuracy")
    assert set(table["level"]) == {"product", "category", "overall"}
    assert (table.loc[table["level"] == "category", "weeks"] == 8).all()
    small = table[table["below_min_units"]]
    assert not small.empty and not small["flagged"].any()  # stored, never flagged
    assert small["wape"].notna().any()
    totals = table[table["level"] != "product"]
    assert not totals["flagged"].any()
    overall = table[table["level"] == "overall"].iloc[0]
    products = table[table["level"] == "product"]
    assert overall["actual_units"] == products["actual_units"].sum()


# ---------------------------------------------------------------- our own forecasts

FORECAST_SETTINGS = demand_config.load_config()["forecasts"]


def test_backtest_windows_are_every_4_weeks_inside_the_last_26() -> None:
    last = date(2026, 9, 21)
    windows = forecasting.backtest_windows(last, FORECAST_SETTINGS)
    assert len(windows) == 6
    assert windows[-1][1] == last and (last - windows[-1][0]).days == 21
    assert all((b[0] - a[0]).days == 28 for a, b in pairwise(windows))
    assert (last - windows[0][0]).days < 26 * 7


def test_recent_mean_fallback_skips_stockout_weeks() -> None:
    weeks = [date(2026, 8, 3) + timedelta(weeks=w) for w in range(6)]
    series = pd.DataFrame(
        {
            "product_id": 1,
            "week_start": weeks,
            "units": [1, 10, 20, 0, 30, 40],
            "is_stockout": [False, False, False, True, False, False],
        }
    )
    forecast = forecasting.recent_mean_forecast(series, [1], FORECAST_SETTINGS)
    assert len(forecast) == 12
    assert forecast["forecast_qty"].iloc[0] == pytest.approx((10 + 20 + 30 + 40) / 4)
    assert forecast["week_start"].iloc[0] == date(2026, 9, 14)


def test_model_choice_prefers_lower_wape_and_autoets_on_ties() -> None:
    results = pd.DataFrame(
        {
            "product_id": [1, 1, 2, 2, 3],
            "model": ["AutoETS", "SeasonalNaive"] * 2 + ["AutoETS"],
            "wape": [0.5, 0.3, 0.4, 0.4, 0.2],
        }
    )
    assert forecasting.choose_models(results).to_dict() == {
        1: "SeasonalNaive",
        2: "AutoETS",
        3: "AutoETS",
    }


def test_forecasts_are_never_negative() -> None:
    weeks = [date(2025, 1, 6) + timedelta(weeks=w) for w in range(30)]
    falling = pd.DataFrame(
        {"product_id": 1, "week_start": weeks, "y": [60 - 2 * w for w in range(30)]}
    )
    forecast = forecasting.fit_and_forecast(falling, horizon=12, season_length=52)
    assert set(forecast["model"]) == {"AutoETS"}  # under 52 weeks: no SeasonalNaive
    assert (forecast["forecast_qty"] >= 0).all()


def test_own_forecasts_on_seed_data(demand_runs: dict) -> None:
    result = demand_runs[AS_OF_MONTH_END]
    forecasts = result.table("model_forecasts")
    chosen = forecasts[forecasts["is_chosen"]]
    assert chosen.groupby("product_id").size().eq(12).all()  # one chosen model, 12 weeks
    assert pd.Timestamp(chosen["week_start"].min()).date() == date(2026, 9, 28)
    smart = chosen[chosen["sku"] == "SB-1042"]
    assert smart["model"].unique().tolist() == ["recent_mean"] and smart["is_fallback"].all()
    backtest = result.table("backtest_results")
    assert backtest["windows"].max() == 6
    assert set(backtest["model"]) == {"AutoETS", "SeasonalNaive"}
    assert backtest.groupby("product_id")["is_chosen"].sum().eq(1).all()
    summary = forecasting.backtest_summary(backtest)
    assert 0 < summary["chosen_mix"] <= min(summary["AutoETS"], summary["SeasonalNaive"]) + 1e-9


# ---------------------------------------------------------------- pre-build and approval


def test_forecast_over_counts_a_part_week_in_proportion() -> None:
    assert prebuild.forecast_over([10, 20, 30], 2.0) == 30
    assert prebuild.forecast_over([10, 20, 30], 30 / 7) == pytest.approx(60)  # capped at 3 weeks
    assert prebuild.forecast_over([10, 20, 30, 40], 2.5) == pytest.approx(45)


def test_demand_sd_leaves_out_stockout_weeks() -> None:
    series = pd.DataFrame(
        {
            "product_id": 1,
            "units": [10, 20, 0, 30],
            "is_stockout": [False, False, True, False],
        }
    )
    assert prebuild.demand_sd(series, weeks=26, min_weeks=2)[1] == pytest.approx(10.0)
    assert prebuild.demand_sd(series.head(1), weeks=26, min_weeks=2)[1] == 0.0


def test_prebuild_formula_review_list_and_order() -> None:
    inputs = small_inputs()
    series = demand.weekly_series(inputs, impute_weeks=4)
    weeks = [date(2026, 2, 2) + timedelta(weeks=w) for w in range(12)]
    forecasts = pd.DataFrame(
        [
            {
                "product_id": p,
                "week_start": w,
                "model": "AutoETS",
                "forecast_qty": q,
                "is_chosen": True,
                "is_fallback": False,
            }
            for p, q in ((1, 5.0), (2, 1.0))
            for w in weeks
        ]
    )
    config = demand_config.load_config()
    accuracy = pd.DataFrame(columns=["level", "product_id", "flag_bias", "bias", "wape"])
    recs, review = prebuild.prebuild(inputs, series, forecasts, accuracy, config)
    assert recs["sku"].tolist() == ["B-2", "A-1"]  # B-2 urgent (4.7 < 28 days), A-1 not
    assert recs["priority"].tolist() == ["urgent", "normal"]
    rec = recs.set_index("sku").loc["A-1"]
    sd = pd.Series([3, 4, 0, 0]).std()
    assert rec["safety_stock"] == pytest.approx(1.65 * sd * 2)
    assert rec["prebuild_raw"] == pytest.approx(20 + 1.65 * sd * 2 - 8 - 0)
    assert rec["recommended_units"] == math.ceil(rec["prebuild_raw"])
    assert rec["status"] == "pending" and rec["rec_id"] == "20260201-A-1"
    assert rec["value_at_risk"] == rec["recommended_units"] * 10.0
    assert rec["lead_time_weeks"] == 4 and rec["forecast_lead_time"] == 20
    assert review.empty

    biased = pd.DataFrame(
        [{"level": "product", "product_id": 1, "flag_bias": True, "bias": 0.3, "wape": 0.4}]
    )
    recs, review = prebuild.prebuild(inputs, series, forecasts, biased, config)
    assert recs["sku"].tolist() == ["B-2"] and review["sku"].tolist() == ["A-1"]
    assert "+30.0%" in review.iloc[0]["reason"]


def test_prebuild_on_seed_data(demand_runs: dict) -> None:
    result = demand_runs[AS_OF_MONTH_END]
    recs = result.table("prebuild_recommendations")
    assert (recs["recommended_units"] > 0).all() and (recs["status"] == "pending").all()
    assert recs["rank"].tolist() == list(range(1, len(recs) + 1))
    urgent = recs["priority"] == "urgent"
    assert urgent.iloc[: urgent.sum()].all()  # every urgent row before every normal one
    assert (urgent == (recs["days_of_cover"] < recs["lead_time_weeks"] * 7)).all()
    for _, group in recs.groupby("priority"):
        values = group["value_at_risk"].tolist()
        assert values == sorted(values, reverse=True)
    assert (recs["value_at_risk"] == recs["recommended_units"] * recs["unit_price"]).all()
    review = result.table("planner_review")
    assert not set(review["sku"]) & set(recs["sku"])
    flagged = result.table("forecast_accuracy").query("level == 'product' and flag_bias")
    assert set(review["sku"]) == set(flagged["key"])


@pytest.fixture
def planning_copy(built: dict, demand_runs: dict, tmp_path: Path) -> Path:
    """A copy of the 2026-09-30 planning database for tests that write decisions."""
    import shutil

    source = demand_runs[AS_OF_MONTH_END].planning_db
    target = tmp_path / "planning.duckdb"
    shutil.copy(source, target)
    return target


def first_rec_id(planning: Path) -> str:
    """The rank-1 recommendation of 2026-09-30."""
    with store.connect_read(planning) as con:
        query = "SELECT rec_id FROM prebuild_recommendations WHERE as_of = $d AND rank = 1"
        return con.execute(query, {"d": AS_OF_MONTH_END}).fetchone()[0]


def test_approve_records_who_when_and_note(planning_copy: Path) -> None:
    rec = first_rec_id(planning_copy)
    approve.main(
        [
            "--id",
            rec,
            "--approve",
            "--by",
            "A. Planner",
            "--note",
            "ok",
            "--planning-db",
            str(planning_copy),
        ]
    )
    with store.connect_read(planning_copy) as con:
        status, by, at, note = con.execute(
            "SELECT status, decided_by, decided_at_utc, note FROM prebuild_recommendations "
            "WHERE rec_id = $r",
            {"r": rec},
        ).fetchone()
        history = con.execute("SELECT decision, decided_by FROM prebuild_decisions").fetchall()
    assert (status, by, note) == ("approved", "A. Planner", "ok") and at is not None
    assert history == [("approved", "A. Planner")]


def test_a_decision_is_final(planning_copy: Path) -> None:
    rec = first_rec_id(planning_copy)
    approve.decide(rec, "reject", "A. Planner", planning_db=planning_copy)
    with pytest.raises(approve.ApprovalError, match="already rejected"):
        approve.decide(rec, "approve", "B. Planner", planning_db=planning_copy)


@pytest.mark.parametrize(
    ("rec", "by", "message"),
    [("nope", "A. Planner", "No pre-build"), (None, "  ", "--by")],
)
def test_bad_approvals_are_refused(
    planning_copy: Path, rec: str | None, by: str, message: str
) -> None:
    with pytest.raises(approve.ApprovalError, match=message):
        approve.decide(rec or first_rec_id(planning_copy), "approve", by, planning_db=planning_copy)


def test_rerun_refuses_a_decided_snapshot_without_force(built: dict, planning_copy: Path) -> None:
    rec = first_rec_id(planning_copy)
    approve.decide(rec, "approve", "A. Planner", planning_db=planning_copy)
    with pytest.raises(store.PlanningStoreError, match="--force"):
        run.run_demand(AS_OF_MONTH_END, built["db_path"], planning_copy)
    result = run.run_demand(AS_OF_MONTH_END, built["db_path"], planning_copy, force=True)
    assert (result.table("prebuild_recommendations")["status"] == "pending").all()
    with store.connect_read(planning_copy) as con:  # the history survives --force
        assert con.execute("SELECT count(*) FROM prebuild_decisions").fetchone()[0] == 1


def test_only_the_approval_cli_can_change_a_status() -> None:
    root = Path(__file__).resolve().parents[1] / "src"
    for folder in ("agent", "reports", "app"):
        for path in (root / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "demand.approve" not in text and "import approve" not in text, path
            assert "UPDATE prebuild_recommendations" not in text, path


def test_usual_interval_has_a_7_day_floor() -> None:
    every_2 = [DAY0 - timedelta(days=d) for d in (11, 9, 7, 5)]  # 5 days since, gap 2
    late = [DAY0 - timedelta(days=d) for d in (21, 19, 17, 15)]  # 15 days since: > 2 x 7
    orders = orders_frame({1: every_2, 2: late})
    alerts = customers.overdue_alerts(orders, pd.Series(dtype=float), DAY0, ALERT_SETTINGS)
    assert alerts["customer_id"].tolist() == [2]
    assert alerts.iloc[0]["usual_interval_days"] == 7 and alerts.iloc[0]["severity"] == "serious"


def test_retail_needs_top_20_percent_revenue_wholesale_always_counts() -> None:
    info = pd.DataFrame(
        {"customer_type": ["retail"] * 9 + ["wholesale"]}, index=pd.Index(range(1, 11))
    )
    revenue = pd.Series({cid: float(cid * 100) for cid in range(1, 11)})
    eligible = customers.alert_eligible(info, revenue, top_share=0.2)
    assert eligible == {9, 10}  # bar = 820: retail 9 (900) in, wholesale 10 always
    orders = orders_frame({8: [DAY0 - timedelta(days=d) for d in (100, 86, 72, 58)]})
    assert customers.overdue_alerts(orders, revenue, DAY0, ALERT_SETTINGS, eligible).empty


def test_accuracy_leaves_out_stockout_weeks(demand_runs: dict) -> None:
    table = demand_runs[AS_OF_SUNDAY].table("forecast_accuracy").set_index("key")
    assert table.loc["TO-1019", "weeks"] == 5  # 3 of its 8 seeded weeks were stockouts
    assert table.loc["PS-1003", "weeks"] == 7
    assert table.loc["AP-1150", "weeks"] == 8
