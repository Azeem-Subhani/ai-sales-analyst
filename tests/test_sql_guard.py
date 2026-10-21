"""SQL guard tests (plan section 13): what run_sql and make_chart accept and refuse."""

import time
from pathlib import Path

import pytest

from src.agent import sql_guard

ACCEPTED = [
    "SELECT count(*) FROM orders",
    "select count(*) from orders;",
    (
        "WITH weekly AS (SELECT date_trunc('week', order_date) AS w, count(*) AS n FROM orders "
        "GROUP BY 1) SELECT * FROM weekly ORDER BY w"
    ),
    (
        "SELECT p.category, sum(oi.quantity) FROM order_items oi JOIN products p USING (product_id) "
        "GROUP BY 1"
    ),
    (
        "SELECT * FROM (SELECT * FROM customers) c WHERE EXISTS "
        "(SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id) LIMIT 3"
    ),
    "SELECT * FROM orders WHERE status = 'drop table orders; pragma version'",  # text, not SQL
    "SELECT * FROM inventory -- COPY in a comment is fine\nLIMIT 1",
    "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM a) SELECT * FROM b",
    "SELECT range AS d FROM range(3)",
    "SELECT * FROM forecasts f JOIN products p ON p.product_id = f.product_id LIMIT 1",
]

REJECTED = {
    "multiple statements": "SELECT 1; SELECT 2",
    "insert": "INSERT INTO orders SELECT * FROM orders",
    "update": "UPDATE orders SET status = 'completed'",
    "delete": "DELETE FROM orders",
    "drop": "DROP TABLE orders",
    "create": "CREATE TABLE t AS SELECT 1",
    "alter": "ALTER TABLE orders ADD COLUMN x INT",
    "pragma": "PRAGMA version",
    "pragma function": "SELECT * FROM pragma_table_info('orders')",
    "attach": "ATTACH 'data/audit.duckdb' AS audit",
    "copy": "COPY orders TO 'orders.csv'",
    "install": "INSTALL httpfs",
    "load": "LOAD httpfs",
    "set": "SET threads = 1",
    "describe": "DESCRIBE orders",
    "show": "SHOW TABLES",
    "read_csv": "SELECT * FROM read_csv('x.csv')",
    "read_parquet in cte": "WITH x AS (SELECT * FROM read_parquet('a.parquet')) SELECT * FROM x",
    "read_json in join": "SELECT * FROM orders, (SELECT * FROM read_json('a.json'))",
    "file replacement scan": "SELECT * FROM 'data/sales.csv'",
    "glob": "SELECT * FROM glob('*')",
    "getenv": "SELECT getenv('HOME')",
    "unknown table": "SELECT * FROM report_runs",
    "agent log table": "SELECT * FROM agent_log",
    "system table function": "SELECT * FROM duckdb_tables()",
    "information schema": "SELECT * FROM information_schema.tables",
    "other catalog": "SELECT * FROM audit.main.report_runs",
    "empty": "   ",
}


@pytest.mark.parametrize("query", ACCEPTED)
def test_guard_accepts_selects_and_ctes(query: str) -> None:
    assert sql_guard.check_sql(query)


@pytest.mark.parametrize("query", list(REJECTED.values()), ids=list(REJECTED))
def test_guard_rejects(query: str) -> None:
    with pytest.raises(sql_guard.SQLGuardError):
        sql_guard.check_sql(query)


def test_rejection_names_the_standard_tables() -> None:
    with pytest.raises(sql_guard.SQLGuardError, match="customers, forecasts, inventory"):
        sql_guard.check_sql("SELECT * FROM report_runs")


def test_row_cap_is_500_and_reports_truncation(built: dict) -> None:
    result = sql_guard.run_query("SELECT order_id FROM orders", built["db_path"])
    assert result.row_count == 500
    assert result.truncated is True
    small = sql_guard.run_query("SELECT order_id FROM orders LIMIT 500", built["db_path"])
    assert (small.row_count, small.truncated) == (500, False)


def test_timeout_interrupts_long_query(built: dict) -> None:
    started = time.monotonic()
    with pytest.raises(sql_guard.SQLTimeoutError, match="longer than 0.3 seconds"):
        sql_guard.run_query(
            "SELECT count(*) FROM order_items a, order_items b",
            built["db_path"],
            timeout_seconds=0.3,
        )
    assert time.monotonic() - started < 5


def test_connection_is_read_only_without_file_access(built: dict, tmp_path: Path) -> None:
    with sql_guard.open_readonly(built["db_path"]) as con:
        with pytest.raises(Exception, match="read-only|Permission|read only"):
            con.execute("CREATE TABLE t (x INT)")
        with pytest.raises(Exception, match="Permission|disabled"):
            con.execute(f"SELECT * FROM read_csv('{tmp_path / 'x.csv'}')")
