"""Shared pytest fixtures."""

import pytest

from src.data import generate as gen


@pytest.fixture(scope="session")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Build the data once per test run into a temporary database."""
    tables, issues = gen.build_tables(gen.SEED)
    db_path = tmp_path_factory.mktemp("data") / "sales.duckdb"
    gen.write_database(tables, db_path)
    return {"tables": tables, "issues": issues, "db_path": db_path}
