"""run_demand(as_of): compute every demand-planning result and store one snapshot (Week 5).

    python -m src.demand.run --as-of 2026-09-30 [--force]

Reads data/sales.duckdb read-only and writes data/planning.duckdb (next to it).
Rerunning an as_of replaces its snapshot; if a pre-build recommendation of that as_of
was already approved or rejected, the rerun is refused unless --force is given.

Reports call get_or_run(as_of), which reuses a stored snapshot and computes one only
when none exists. The agent tools never compute; they only read stored snapshots.
"""

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from src.analytics import customers, demand, kpis
from src.demand import config as demand_config
from src.demand import store

logger = logging.getLogger(__name__)


@dataclass
class DemandResult:
    """One snapshot: its tables (as stored) and where it lives."""

    as_of: date
    tables: dict[str, pd.DataFrame]
    planning_db: Path
    computed: bool  # False when an existing snapshot was reused

    def table(self, name: str) -> pd.DataFrame:
        """One table of the snapshot."""
        return self.tables[name]


def compute(
    con: duckdb.DuckDBPyConnection, inputs: demand.DemandInputs, config: dict
) -> dict[str, pd.DataFrame]:
    """Every snapshot table for inputs.as_of, in memory (con is the read-only sales DB)."""
    series = demand.weekly_series(inputs, config["forecasts"]["impute_weeks"])
    alerts, rfm = customers.customer_alerts(con, inputs, config["customer_alerts"])
    return {
        "demand_weekly": series,
        "customer_alerts": alerts,
        "customer_rfm": rfm,
        "category_alerts": demand.category_alerts(con, inputs.as_of, config["category_alerts"]),
        "stock_alerts": demand.stock_alerts(inputs, series, config["stock_alerts"]),
    }


def run_demand(
    as_of: date,
    sales_db: Path = kpis.DB_PATH,
    planning_db: Path | None = None,
    config_path: Path = demand_config.DEMAND_CONFIG_PATH,
    force: bool = False,
) -> DemandResult:
    """Compute everything for as_of and replace its snapshot in planning.duckdb."""
    planning_db = planning_db or store.planning_path_for(sales_db)
    config = demand_config.load_config(config_path)
    started = time.perf_counter()
    con = kpis.open_db(sales_db)
    try:
        inputs = demand.load_inputs(con, as_of)
        frames = compute(con, inputs, config)
    finally:
        con.close()
    stock = demand.latest_stock(inputs)
    snapshot = {
        "created_at_utc": datetime.now(UTC).replace(tzinfo=None),
        "config_hash": demand_config.config_hash(config),
        "last_complete_week": demand.last_complete_week(as_of),
        "stock_snapshot_date": stock["snapshot_date"].max() if not stock.empty else None,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }
    store.write_snapshot(planning_db, as_of, frames, snapshot, force=force)
    logger.info("Stored demand snapshot %s in %s", as_of, planning_db)
    return DemandResult(as_of, store.read_snapshot(planning_db, as_of), planning_db, True)


def get_or_run(
    as_of: date, sales_db: Path = kpis.DB_PATH, planning_db: Path | None = None
) -> DemandResult:
    """The stored snapshot for as_of, computing it first if there is none (reports use this)."""
    planning_db = planning_db or store.planning_path_for(sales_db)
    if store.snapshot_exists(planning_db, as_of):
        return DemandResult(as_of, store.read_snapshot(planning_db, as_of), planning_db, False)
    return run_demand(as_of, sales_db=sales_db, planning_db=planning_db)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Compute and store a demand-planning snapshot.")
    parser.add_argument("--as-of", required=True, type=date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--db", type=Path, default=kpis.DB_PATH, help="sales DuckDB file to read")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace the snapshot even if its recommendations were approved or rejected",
    )
    return parser.parse_args(argv)


def counts(result: DemandResult) -> dict[str, int]:
    """Rows per table, for the CLI summary."""
    return {name: len(frame) for name, frame in result.tables.items() if name != "demand_snapshots"}


def main(argv: list[str] | None = None) -> None:
    """Run demand planning for one as_of and print what was stored."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        result = run_demand(args.as_of, sales_db=args.db, force=args.force)
    except (ValueError, store.PlanningStoreError) as error:
        raise SystemExit(f"error: {error}") from error
    meta = result.table("demand_snapshots").iloc[0]
    print(f"Snapshot {result.as_of} stored in {result.planning_db}")
    print(f"  took {meta['duration_seconds']:.1f} s, config {meta['config_hash']}")
    for name, rows in counts(result).items():
        print(f"  {name}: {rows} rows")


if __name__ == "__main__":
    main()
