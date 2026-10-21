"""Demand-planning tests (Week 5): series, store, alerts, accuracy, forecasts, pre-build.

Unit tests use small hand-made inputs. Checks on the seed-42 data use the shared
demand_runs fixture (conftest.py). Planted-problem checks are in
tests/test_demand_acceptance.py, the only test file that reads the planted issues.
"""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from src.analytics import demand
from src.demand import config as demand_config
from src.demand import run, store

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
