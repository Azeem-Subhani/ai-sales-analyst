"""The report_runs audit table in data/audit.duckdb (plan section 8 step 9, decisions Week 1).

One row per attempt: a run that fails and is retried writes two rows with the same
run_id. The audit database is separate from data/sales.duckdb, which stays read-only.
Timestamps are UTC.
"""

import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DB_PATH = PROJECT_ROOT / "data" / "audit.duckdb"

CREATE_REPORT_RUNS = """
CREATE TABLE IF NOT EXISTS report_runs (
    run_id            VARCHAR NOT NULL,
    report            VARCHAR NOT NULL,
    attempt           INTEGER NOT NULL,
    triggered_by      VARCHAR NOT NULL,   -- schedule | run_now | test
    mode              VARCHAR NOT NULL,   -- dry_run | send
    as_of             DATE,
    period_start      DATE,
    period_end        DATE,
    recipients        VARCHAR,            -- comma-separated
    status            VARCHAR NOT NULL,   -- success | failed
    error             VARCHAR,
    duration_seconds  DOUBLE NOT NULL,
    started_at_utc    TIMESTAMP NOT NULL,
    finished_at_utc   TIMESTAMP NOT NULL
)
"""
INSERT_REPORT_RUN = """
INSERT INTO report_runs VALUES (
    $run_id, $report, $attempt, $triggered_by, $mode, $as_of, $period_start, $period_end,
    $recipients, $status, $error, $duration_seconds, $started_at_utc, $finished_at_utc
)
"""
SELECT_REPORT_RUNS = "SELECT * FROM report_runs ORDER BY started_at_utc, attempt"

# Scheduler jobs run in threads; one writer at a time keeps DuckDB simple.
_write_lock = threading.Lock()


@dataclass
class RunRecord:
    """One attempt of one report run."""

    run_id: str
    report: str
    attempt: int
    triggered_by: str
    mode: str
    as_of: date | None
    period_start: date | None
    period_end: date | None
    recipients: str
    status: str
    error: str | None
    duration_seconds: float
    started_at_utc: datetime
    finished_at_utc: datetime


def record_run(record: RunRecord, db_path: Path = AUDIT_DB_PATH) -> None:
    """Append one attempt to report_runs, creating the table on first use."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock, duckdb.connect(str(db_path)) as con:
        con.execute(CREATE_REPORT_RUNS)
        con.execute(INSERT_REPORT_RUN, asdict(record))


def read_runs(db_path: Path = AUDIT_DB_PATH) -> pd.DataFrame:
    """All rows of report_runs, oldest first (read-only)."""
    with duckdb.connect(str(db_path), read_only=True) as con:
        return con.execute(SELECT_REPORT_RUNS).df()
