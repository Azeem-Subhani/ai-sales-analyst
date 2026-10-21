"""Tests for the dummy data generator (plan section 5).

The tests build their own database in a temporary folder, so they never touch
data/sales.duckdb. Each planted problem is checked with independent SQL against
the written database, not by re-reading the generator's own numbers.
"""

import json
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from src.data import generate as gen

# Expected values come straight from plan section 5, not from the generator's constants,
# so a change to the generator cannot quietly weaken these tests.
PLAN_START, PLAN_END = date(2024, 10, 1), date(2026, 9, 30)
PLAN_CUSTOMERS, PLAN_WHOLESALE, PLAN_PRODUCTS = 2000, 150, 250
PLAN_STOCKOUT_PRODUCTS = 10
PLAN_FORECAST_WEEKS = [date(2026, 8, 3) + timedelta(weeks=w) for w in range(8)]
PLAN_CHURN_AFTER = date(2026, 8, 15)

TABLES = {"customers", "products", "orders", "order_items", "inventory", "forecasts"}
ROW_COUNT_RANGES = {
    "orders": (65_000, 75_000),
    "order_items": (170_000, 190_000),
    "inventory": (24_000, 26_500),
}
CATEGORIES = {
    "Electronics",
    "Home & Kitchen",
    "Apparel",
    "Beauty",
    "Sports & Outdoors",
    "Toys",
    "Office",
    "Pet Supplies",
}
FOREIGN_KEYS = [  # (child table, child column, parent table, parent column)
    ("orders", "customer_id", "customers", "customer_id"),
    ("order_items", "order_id", "orders", "order_id"),
    ("order_items", "product_id", "products", "product_id"),
    ("inventory", "product_id", "products", "product_id"),
    ("forecasts", "product_id", "products", "product_id"),
]
CHURN_NAMES = ("Harbor & Pine Co.", "Bluegate Supplies", "Kestrel Home Stores")

# Net revenue of completed orders, per the plan's KPI definition.
NET = "oi.quantity * oi.unit_price * (1 - oi.discount_pct)"
# Weekly demand: units on non-cancelled orders per product per Monday-starting week.
WEEKLY_DEMAND_SQL = """
    SELECT oi.product_id, CAST(date_trunc('week', o.order_date) AS DATE) AS week_start,
           SUM(oi.quantity) AS units
    FROM order_items oi JOIN orders o USING (order_id)
    WHERE o.status <> 'cancelled'
    GROUP BY 1, 2
"""


@pytest.fixture(scope="session")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Build the data once per test run into a temporary database."""
    tables, issues = gen.build_tables(gen.SEED)
    db_path = tmp_path_factory.mktemp("data") / "sales.duckdb"
    gen.write_database(tables, db_path)
    return {"tables": tables, "issues": issues, "db_path": db_path}


@pytest.fixture(scope="session")
def con(built: dict) -> Iterator[duckdb.DuckDBPyConnection]:
    """Read-only connection to the temporary database."""
    connection = duckdb.connect(str(built["db_path"]), read_only=True)
    yield connection
    connection.close()


def scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None):
    """Run a query that returns a single value."""
    return con.execute(sql, params or []).fetchone()[0]


def issue(built: dict, issue_id: int) -> dict:
    """Return one planted-issue description by id."""
    return next(i for i in built["issues"] if i["id"] == issue_id)


# --- Structure and size -----------------------------------------------------
def test_all_tables_exist(con: duckdb.DuckDBPyConnection) -> None:
    names = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    assert TABLES <= names


def test_customer_and_product_counts(con: duckdb.DuckDBPyConnection) -> None:
    assert scalar(con, "SELECT COUNT(*) FROM customers") == PLAN_CUSTOMERS
    assert scalar(con, "SELECT COUNT(*) FROM customers WHERE customer_type = 'wholesale'") == (
        PLAN_WHOLESALE
    )
    assert scalar(con, "SELECT COUNT(*) FROM products") == PLAN_PRODUCTS
    categories = {r[0] for r in con.execute("SELECT DISTINCT category FROM products").fetchall()}
    assert categories == CATEGORIES


@pytest.mark.parametrize("table", sorted(ROW_COUNT_RANGES))
def test_row_counts_in_expected_range(con: duckdb.DuckDBPyConnection, table: str) -> None:
    low, high = ROW_COUNT_RANGES[table]
    count = scalar(con, f"SELECT COUNT(*) FROM {table}")  # table name from our own constant
    assert low <= count <= high, f"{table} has {count} rows"


def test_forecasts_cover_eight_weeks_aug_sep_2026(con: duckdb.DuckDBPyConnection) -> None:
    weeks = [
        r[0] for r in con.execute("SELECT DISTINCT week_start FROM forecasts ORDER BY 1").fetchall()
    ]
    assert weeks == PLAN_FORECAST_WEEKS
    assert all(w.weekday() == 0 for w in weeks)


@pytest.mark.parametrize(("child", "child_col", "parent", "parent_col"), FOREIGN_KEYS)
def test_no_orphan_foreign_keys(
    con: duckdb.DuckDBPyConnection, child: str, child_col: str, parent: str, parent_col: str
) -> None:
    orphans = scalar(
        con,
        f"""SELECT COUNT(*) FROM {child} c LEFT JOIN {parent} p ON c.{child_col} = p.{parent_col}
            WHERE p.{parent_col} IS NULL""",  # names from FOREIGN_KEYS constant only
    )
    assert orphans == 0


# --- Dates ------------------------------------------------------------------
def test_order_dates_within_period(con: duckdb.DuckDBPyConnection) -> None:
    first, last = con.execute("SELECT MIN(order_date), MAX(order_date) FROM orders").fetchone()
    assert (first, last) == (PLAN_START, PLAN_END)


def test_other_dates_within_range(con: duckdb.DuckDBPyConnection) -> None:
    assert scalar(con, "SELECT MAX(signup_date) FROM customers") <= PLAN_END
    assert scalar(con, "SELECT MAX(launch_date) FROM products") <= PLAN_END
    first, last = con.execute(
        "SELECT MIN(snapshot_date), MAX(snapshot_date) FROM inventory"
    ).fetchone()
    assert PLAN_START <= first and last <= PLAN_END


def test_no_order_before_signup_or_launch(con: duckdb.DuckDBPyConnection) -> None:
    before_signup = scalar(
        con,
        """SELECT COUNT(*) FROM orders o JOIN customers c
                                   USING (customer_id) WHERE o.order_date < c.signup_date""",
    )
    before_launch = scalar(
        con,
        """SELECT COUNT(*) FROM order_items oi JOIN orders o
                                   USING (order_id) JOIN products p USING (product_id)
                                   WHERE o.order_date < p.launch_date""",
    )
    assert before_signup == 0
    assert before_launch == 0


# --- Reproducibility --------------------------------------------------------
def test_same_seed_gives_identical_data(built: dict) -> None:
    again, issues_again = gen.build_tables(gen.SEED)
    for name, frame in built["tables"].items():
        pd.testing.assert_frame_equal(frame, again[name], obj=name)
    assert json.dumps(built["issues"]) == json.dumps(issues_again)


# --- Planted problems -------------------------------------------------------
def test_planted_issues_described(built: dict) -> None:
    assert [i["id"] for i in built["issues"]] == [1, 2, 3, 4, 5]


def test_planted_1_wholesale_churn(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        """SELECT c.name, c.customer_type, COUNT(*) AS orders, MAX(o.order_date) AS last_order
           FROM customers c JOIN orders o USING (customer_id)
           WHERE c.name IN (?, ?, ?) GROUP BY 1, 2""",
        list(CHURN_NAMES),
    ).fetchall()
    assert {r[0] for r in rows} == set(CHURN_NAMES)
    for name, customer_type, orders, last_order in rows:
        assert customer_type == "wholesale", name
        assert orders >= 20, f"{name} should be a regular buyer before churning"
        assert last_order <= PLAN_CHURN_AFTER, f"{name} ordered after mid-August"
        assert last_order >= PLAN_CHURN_AFTER.replace(day=1), f"{name} stopped too early"

    apparel_share = scalar(
        con,
        """SELECT SUM(CASE WHEN p.category = 'Apparel' THEN oi.quantity END) * 1.0
                  / SUM(oi.quantity)
           FROM order_items oi JOIN orders o USING (order_id)
           JOIN customers c USING (customer_id) JOIN products p USING (product_id)
           WHERE c.name IN (?, ?, ?)""",
        list(CHURN_NAMES),
    )
    assert apparel_share >= 0.7


def monthly_net_revenue(con: duckdb.DuckDBPyConnection, month: int, apparel: bool) -> float:
    """Completed-order net revenue in a 2026 month, for Apparel or everything else."""
    op = "=" if apparel else "<>"
    return float(
        scalar(
            con,
            f"""SELECT SUM({NET}) FROM order_items oi JOIN orders o USING (order_id)
            JOIN products p USING (product_id)
            WHERE o.status = 'completed' AND p.category {op} 'Apparel'
              AND year(o.order_date) = 2026 AND month(o.order_date) = ?""",
            [month],
        )
    )


def test_planted_2_apparel_decline(con: duckdb.DuckDBPyConnection) -> None:
    apparel_change = monthly_net_revenue(con, 9, True) / monthly_net_revenue(con, 7, True) - 1
    other_change = monthly_net_revenue(con, 9, False) / monthly_net_revenue(con, 7, False) - 1
    assert -0.40 <= apparel_change <= -0.20, f"Apparel changed {apparel_change:.1%}"
    assert other_change > -0.10, "the decline should be specific to Apparel"


def test_planted_3_stockouts(con: duckdb.DuckDBPyConnection, built: dict) -> None:
    zero_weeks = con.execute(
        """SELECT p.sku, COUNT(*) AS weeks, MIN(i.snapshot_date), MAX(i.snapshot_date)
           FROM inventory i JOIN products p USING (product_id)
           WHERE i.on_hand = 0 GROUP BY 1"""
    ).fetchall()
    assert len(zero_weeks) == PLAN_STOCKOUT_PRODUCTS
    for sku, weeks, first, last in zero_weeks:
        assert 1 <= weeks <= 3, sku
        assert (first.year, first.month) == (last.year, last.month) == (2026, 9), sku

    planted_skus = {p["sku"] for p in issue(built, 3)["products"]}
    assert {row[0] for row in zero_weeks} == planted_skus

    # Sales are capped: no units sold in any week that starts with on_hand = 0.
    sold_while_out = scalar(
        con,
        f"""SELECT COALESCE(SUM(d.units), 0) FROM inventory i
            JOIN ({WEEKLY_DEMAND_SQL}) d
              ON d.product_id = i.product_id AND d.week_start = i.snapshot_date
            WHERE i.on_hand = 0""",
    )
    assert sold_while_out == 0


def forecast_bias(con: duckdb.DuckDBPyConnection, sku_condition: str) -> float:
    """Bias = sum(forecast - actual) / sum(actual) over the seeded forecast weeks."""
    return float(
        scalar(
            con,
            f"""SELECT (SUM(f.forecast_qty) - SUM(COALESCE(d.units, 0))) / SUM(COALESCE(d.units, 0))
            FROM forecasts f JOIN products p USING (product_id)
            LEFT JOIN ({WEEKLY_DEMAND_SQL}) d
              ON d.product_id = f.product_id AND d.week_start = f.week_start
            WHERE {sku_condition}""",  # condition is a literal from this file
        )
    )


def test_planted_4_forecast_bias(con: duckdb.DuckDBPyConnection) -> None:
    name = scalar(con, "SELECT name FROM products WHERE sku = 'AP-1150'")
    assert name == "Classic Denim Jacket"
    assert 0.18 <= forecast_bias(con, "p.sku = 'AP-1150'") <= 0.32
    # Other products are roughly unbiased, so the jacket stands out.
    stockout_skus = "SELECT DISTINCT product_id FROM inventory WHERE on_hand = 0"
    others = f"p.sku <> 'AP-1150' AND p.product_id NOT IN ({stockout_skus})"
    assert abs(forecast_bias(con, others)) <= 0.05


def test_planted_5_prebuild_need(con: duckdb.DuckDBPyConnection) -> None:
    product_id, name, launch, lead_time = con.execute(
        "SELECT product_id, name, launch_date, lead_time_days FROM products WHERE sku = 'SB-1042'"
    ).fetchone()
    assert name == "Smart Bottle Pro"
    assert (launch.year, launch.month) == (2026, 7)

    units = """SELECT COALESCE(SUM(oi.quantity), 0) FROM order_items oi JOIN orders o
               USING (order_id) WHERE oi.product_id = ? AND o.status <> 'cancelled'
               AND o.order_date BETWEEN ? AND ?"""
    end = PLAN_END
    recent = scalar(con, units, [product_id, end - timedelta(days=27), end])
    prior = scalar(con, units, [product_id, end - timedelta(days=55), end - timedelta(days=28)])
    assert 1.4 <= recent / prior <= 1.8, f"demand change {recent / prior - 1:.0%}"

    on_hand, on_order = con.execute(
        """SELECT on_hand, on_order FROM inventory WHERE product_id = ?
           ORDER BY snapshot_date DESC LIMIT 1""",
        [product_id],
    ).fetchone()
    cover_days = on_hand / (recent / 28)
    assert 7 <= cover_days <= 11, f"stock covers {cover_days:.1f} days"
    assert on_order == 0
    assert lead_time > cover_days, "lead time must exceed cover, or no pre-build is needed"


def test_main_writes_database_and_issues_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Running the CLI entry point writes both files and prints the summary."""
    monkeypatch.setattr(gen, "DB_PATH", tmp_path / "sales.duckdb")
    monkeypatch.setattr(gen, "ISSUES_PATH", tmp_path / "planted_issues.json")
    gen.main()

    assert (tmp_path / "sales.duckdb").is_file()
    issues = json.loads((tmp_path / "planted_issues.json").read_text(encoding="utf-8"))
    assert [i["problem"] for i in issues] == [
        "Wholesale churn",
        "Category decline",
        "Stockouts",
        "Forecast bias",
        "Pre-build need",
    ]
    printed = capsys.readouterr().out
    assert "order dates  2024-10-01 to 2026-09-30" in printed
    assert "net revenue" in printed
