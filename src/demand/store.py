"""The planning database, data/planning.duckdb: demand-planning results (Week 5).

data/sales.duckdb stays read-only; everything demand planning computes is stored here,
one snapshot per as_of. The schema is created by code on first use, like audit.duckdb.

- demand_snapshots: one row per run (as_of, created_at, config hash).
- Every other table has an as_of column naming the snapshot its rows belong to.
  Rerunning an as_of replaces all of its rows in one transaction.
- prebuild_decisions is the exception: an append-only history of approvals that is
  never deleted by a rerun.

Readers open the file read-only. Writers take a process-wide lock (the scheduler runs
reports in threads); DuckDB allows one writing process at a time.
"""

import threading
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from src.analytics import kpis

PLANNING_DB_NAME = "planning.duckdb"
PLANNING_DB_PATH = kpis.DB_PATH.with_name(PLANNING_DB_NAME)

# Table name -> column definitions. Order matters only for readability.
TABLES: dict[str, str] = {
    "demand_snapshots": """
        as_of               DATE PRIMARY KEY,
        created_at_utc      TIMESTAMP NOT NULL,
        config_hash         VARCHAR NOT NULL,
        last_complete_week  DATE NOT NULL,   -- Monday of the last week in the series
        stock_snapshot_date DATE,            -- latest inventory snapshot on or before as_of
        duration_seconds    DOUBLE NOT NULL
    """,
    "demand_weekly": """
        as_of        DATE NOT NULL,
        product_id   INTEGER NOT NULL,
        sku          VARCHAR NOT NULL,
        week_start   DATE NOT NULL,
        units        INTEGER NOT NULL,   -- demand: units on non-cancelled orders
        is_stockout  BOOLEAN NOT NULL,   -- the week's Monday snapshot shows on_hand = 0
        units_model  DOUBLE NOT NULL,    -- units, or the imputed value in stockout weeks
        is_imputed   BOOLEAN NOT NULL
    """,
    "customer_rfm": """
        as_of           DATE NOT NULL,
        scored_as_of    DATE NOT NULL,   -- as_of, or as_of minus 28 days
        customer_id     INTEGER NOT NULL,
        recency_days    INTEGER NOT NULL,
        frequency       INTEGER NOT NULL,
        monetary        DOUBLE NOT NULL,
        r_score         INTEGER NOT NULL,
        f_score         INTEGER NOT NULL,
        m_score         INTEGER NOT NULL,
        segment         VARCHAR NOT NULL
    """,
    "customer_alerts": """
        as_of                 DATE NOT NULL,
        alert_type            VARCHAR NOT NULL,  -- overdue | declining | new_high_value | segment_change
        severity              VARCHAR NOT NULL,  -- serious | overdue | warning | info
        rank                  INTEGER NOT NULL,  -- within alert_type
        customer_id           INTEGER NOT NULL,
        customer_name         VARCHAR NOT NULL,
        customer_type         VARCHAR NOT NULL,
        region                VARCHAR NOT NULL,
        last_order_date       DATE,
        days_since_last_order INTEGER,
        orders_in_window      INTEGER,
        usual_interval_days   DOUBLE,
        interval_ratio        DOUBLE,   -- days since last order / usual interval
        revenue_365d          DOUBLE,
        revenue_recent        DOUBLE,   -- declining / new high-value: last 90 days
        revenue_prior         DOUBLE,   -- declining: the 90 days before
        revenue_change_pct    DOUBLE,
        first_order_date      DATE,
        segment_before        VARCHAR,
        segment_now           VARCHAR
    """,
    "category_alerts": """
        as_of               DATE NOT NULL,
        category            VARCHAR NOT NULL,
        current_start       DATE NOT NULL,
        current_end         DATE NOT NULL,
        prior_start         DATE NOT NULL,
        prior_end           DATE NOT NULL,
        current_revenue     DOUBLE NOT NULL,
        prior_revenue       DOUBLE NOT NULL,
        revenue_change_pct  DOUBLE,
        flagged             BOOLEAN NOT NULL
    """,
    "stock_alerts": """
        as_of               DATE NOT NULL,
        rank                INTEGER NOT NULL,
        product_id          INTEGER NOT NULL,
        sku                 VARCHAR NOT NULL,
        name                VARCHAR NOT NULL,
        category            VARCHAR NOT NULL,
        stockout_weeks      INTEGER NOT NULL,   -- in the last 4 complete weeks
        last_stockout_week  DATE NOT NULL,
        snapshot_date       DATE,
        on_hand             INTEGER,
        on_order            INTEGER,
        avg_daily_demand    DOUBLE,             -- last 4 weeks, stockout weeks excluded
        days_of_cover       DOUBLE
    """,
    "forecast_accuracy": """
        as_of           DATE NOT NULL,
        level           VARCHAR NOT NULL,   -- product | category | overall
        key             VARCHAR NOT NULL,   -- sku, category name, or 'all'
        product_id      INTEGER,
        name            VARCHAR,
        category        VARCHAR,
        model           VARCHAR NOT NULL,
        first_week      DATE NOT NULL,
        last_week       DATE NOT NULL,
        weeks           INTEGER NOT NULL,
        actual_units    DOUBLE NOT NULL,
        forecast_units  DOUBLE NOT NULL,
        abs_error       DOUBLE NOT NULL,
        wape            DOUBLE,             -- NULL when actual units are 0
        bias            DOUBLE,
        below_min_units BOOLEAN NOT NULL,
        flag_wape       BOOLEAN NOT NULL,
        flag_bias       BOOLEAN NOT NULL,
        flagged         BOOLEAN NOT NULL
    """,
    "model_forecasts": """
        as_of         DATE NOT NULL,
        product_id    INTEGER NOT NULL,
        sku           VARCHAR NOT NULL,
        model         VARCHAR NOT NULL,     -- AutoETS | SeasonalNaive | recent_mean
        week_start    DATE NOT NULL,
        forecast_qty  DOUBLE NOT NULL,
        is_chosen     BOOLEAN NOT NULL,
        is_fallback   BOOLEAN NOT NULL
    """,
    "backtest_results": """
        as_of         DATE NOT NULL,
        product_id    INTEGER NOT NULL,
        sku           VARCHAR NOT NULL,
        model         VARCHAR NOT NULL,
        windows       INTEGER NOT NULL,
        weeks_scored  INTEGER NOT NULL,     -- stockout weeks are not scored
        actual_units  DOUBLE NOT NULL,
        abs_error     DOUBLE NOT NULL,
        wape          DOUBLE,
        is_chosen     BOOLEAN NOT NULL
    """,
    "prebuild_recommendations": """
        rec_id              VARCHAR PRIMARY KEY,   -- YYYYMMDD-SKU
        as_of               DATE NOT NULL,
        rank                INTEGER NOT NULL,      -- by days of cover, lowest first
        product_id          INTEGER NOT NULL,
        sku                 VARCHAR NOT NULL,
        name                VARCHAR NOT NULL,
        category            VARCHAR NOT NULL,
        forecast_model      VARCHAR NOT NULL,
        is_fallback         BOOLEAN NOT NULL,
        lead_time_weeks     DOUBLE NOT NULL,
        forecast_lead_time  DOUBLE NOT NULL,
        demand_sd           DOUBLE NOT NULL,
        safety_stock        DOUBLE NOT NULL,
        snapshot_date       DATE NOT NULL,
        on_hand             INTEGER NOT NULL,
        on_order            INTEGER NOT NULL,
        avg_daily_demand    DOUBLE,
        days_of_cover       DOUBLE,
        prebuild_raw        DOUBLE NOT NULL,       -- unrounded formula result
        recommended_units   INTEGER NOT NULL,      -- prebuild_raw rounded up
        status              VARCHAR NOT NULL,      -- pending | approved | rejected
        decided_by          VARCHAR,
        decided_at_utc      TIMESTAMP,
        note                VARCHAR
    """,
    "planner_review": """
        as_of               DATE NOT NULL,
        product_id          INTEGER NOT NULL,
        sku                 VARCHAR NOT NULL,
        name                VARCHAR NOT NULL,
        category            VARCHAR NOT NULL,
        reason              VARCHAR NOT NULL,
        bias                DOUBLE,
        wape                DOUBLE,
        prebuild_raw        DOUBLE,     -- what the formula would give; not recommended
        days_of_cover       DOUBLE
    """,
    "prebuild_decisions": """
        rec_id          VARCHAR NOT NULL,
        as_of           DATE NOT NULL,
        sku             VARCHAR NOT NULL,
        recommended_units INTEGER NOT NULL,
        decision        VARCHAR NOT NULL,   -- approved | rejected
        decided_by      VARCHAR NOT NULL,
        decided_at_utc  TIMESTAMP NOT NULL,
        note            VARCHAR
    """,
}
SNAPSHOT_TABLES = [
    name for name in TABLES if name not in ("demand_snapshots", "prebuild_decisions")
]

_write_lock = threading.Lock()


class PlanningStoreError(Exception):
    """A planning-database problem the caller should show as is (missing file, lock)."""


def planning_path_for(sales_db_path: Path) -> Path:
    """planning.duckdb sits next to the sales database it was computed from."""
    return Path(sales_db_path).with_name(PLANNING_DB_NAME)


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create every planning table that does not exist yet."""
    for name, columns in TABLES.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns})")


def connect_write(path: Path) -> duckdb.DuckDBPyConnection:
    """A read-write connection with the schema in place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = duckdb.connect(str(path))
    except duckdb.IOException as error:  # another process holds the write lock
        raise PlanningStoreError(f"{path} is in use by another process: {error}") from error
    create_schema(con)
    return con


def connect_read(path: Path) -> duckdb.DuckDBPyConnection:
    """A read-only connection; raises PlanningStoreError if the file does not exist."""
    if not Path(path).exists():
        raise PlanningStoreError(f"{path} does not exist; run python -m src.demand.run first.")
    return duckdb.connect(str(path), read_only=True)


def decided_rec_ids(con: duckdb.DuckDBPyConnection, as_of: date) -> list[str]:
    """Recommendations for as_of that were already approved or rejected."""
    rows = con.execute(
        "SELECT rec_id FROM prebuild_recommendations WHERE as_of = $as_of "
        "AND status <> 'pending' ORDER BY rec_id",
        {"as_of": as_of},
    ).fetchall()
    return [row[0] for row in rows]


def write_snapshot(
    path: Path, as_of: date, frames: dict[str, pd.DataFrame], snapshot: dict, force: bool
) -> None:
    """Replace every row of as_of's snapshot with frames, in one transaction.

    Refuses (PlanningStoreError) when a recommendation of that as_of was already
    approved or rejected, unless force is True. prebuild_decisions is never touched.
    """
    unknown = set(frames) - set(SNAPSHOT_TABLES)
    if unknown:
        raise ValueError(f"Unknown planning tables: {sorted(unknown)}.")
    with _write_lock:
        con = connect_write(path)
        in_transaction = False
        try:
            decided = decided_rec_ids(con, as_of)
            if decided and not force:
                raise PlanningStoreError(
                    f"Snapshot {as_of} has decided pre-build recommendations {decided}; "
                    "rerun with --force to replace it (the decisions stay in prebuild_decisions)."
                )
            con.execute("BEGIN TRANSACTION")
            in_transaction = True
            for table in ["demand_snapshots", *SNAPSHOT_TABLES]:
                con.execute(f"DELETE FROM {table} WHERE as_of = $as_of", {"as_of": as_of})
            insert_frame(con, "demand_snapshots", pd.DataFrame([{"as_of": as_of, **snapshot}]))
            for table, frame in frames.items():
                insert_frame(con, table, frame.assign(as_of=as_of))
            con.execute("COMMIT")
        except Exception:
            if in_transaction:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()


def insert_frame(con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame) -> None:
    """Insert a DataFrame by column name (columns missing from it are NULL)."""
    if frame.empty:
        return
    con.register("frame_to_insert", frame)
    try:
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM frame_to_insert")
    finally:
        con.unregister("frame_to_insert")


def snapshot_exists(path: Path, as_of: date) -> bool:
    """True if planning.duckdb has a snapshot for exactly as_of."""
    if not Path(path).exists():
        return False
    with connect_read(path) as con:
        if not table_exists(con, "demand_snapshots"):
            return False
        query = "SELECT count(*) FROM demand_snapshots WHERE as_of = $as_of"
        return con.execute(query, {"as_of": as_of}).fetchone()[0] > 0


def latest_snapshot(path: Path, on_or_before: date | None) -> date | None:
    """The newest snapshot as_of, optionally on or before a date; None if there is none."""
    if not Path(path).exists():
        return None
    with connect_read(path) as con:
        if not table_exists(con, "demand_snapshots"):
            return None
        if on_or_before is None:
            return con.execute("SELECT max(as_of) FROM demand_snapshots").fetchone()[0]
        query = "SELECT max(as_of) FROM demand_snapshots WHERE as_of <= $day"
        return con.execute(query, {"day": on_or_before}).fetchone()[0]


def read_snapshot(path: Path, as_of: date) -> dict[str, pd.DataFrame]:
    """Every snapshot table's rows for one as_of, plus its demand_snapshots row."""
    with connect_read(path) as con:
        return {
            table: con.execute(f"SELECT * FROM {table} WHERE as_of = $as_of", {"as_of": as_of}).df()
            for table in ["demand_snapshots", *SNAPSHOT_TABLES]
        }


def read_table(path: Path, table: str, as_of: date) -> pd.DataFrame:
    """One snapshot table's rows for one as_of (read-only)."""
    if table not in SNAPSHOT_TABLES:
        raise ValueError(f"Unknown planning table {table!r}.")
    with connect_read(path) as con:
        return con.execute(f"SELECT * FROM {table} WHERE as_of = $as_of", {"as_of": as_of}).df()


def table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    """True if the table exists in the connected database."""
    query = "SELECT count(*) FROM information_schema.tables WHERE table_name = $name"
    return con.execute(query, {"name": table}).fetchone()[0] > 0
