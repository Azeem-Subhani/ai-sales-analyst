"""Shared pieces of the dashboard pages: paths, the as_of date, cached reads, tables.

Every read goes through a function in src/reports (views, saved) that opens DuckDB
read-only and closes it again, so the CLIs can still write between page loads.
Reads are cached per as_of and file; an approval clears the cache.
"""

from datetime import date
from pathlib import Path

import streamlit as st

from src.analytics import kpis
from src.demand import store
from src.reports import backfill, builder, views

# Session keys. Tests set the *_PATH keys to temporary copies before the first run.
SALES_DB_KEY = "sales_db_path"
PLANNING_DB_KEY = "planning_db_path"
REPORTS_ROOT_KEY = "reports_root"
BACKFILL_ROOT_KEY = "backfill_root"
AS_OF_KEY = "as_of"
DEFAULTS = {
    SALES_DB_KEY: kpis.DB_PATH,
    PLANNING_DB_KEY: store.PLANNING_DB_PATH,
    REPORTS_ROOT_KEY: builder.OUTPUT_ROOT,
    BACKFILL_ROOT_KEY: backfill.BACKFILL_ROOT,
}


def path(key: str) -> Path:
    """A file or folder path from the session, or its default."""
    return Path(st.session_state.get(key) or DEFAULTS[key])


def as_of() -> date:
    """The as_of date picked in the sidebar (shared by every page)."""
    return st.session_state[AS_OF_KEY]


@st.cache_data(show_spinner=False)
def default_as_of(sales_db: str) -> date:
    """DATA_END_DATE, or the last order date."""
    return views.default_as_of(Path(sales_db))


@st.cache_data(show_spinner="Loading sales numbers...")
def sales_view(kind: str, day: date, sales_db: str) -> dict:
    """Cached views.sales_view."""
    return views.sales_view(kind, day, Path(sales_db))


@st.cache_data(show_spinner="Loading the demand snapshot...")
def demand_view(day: date, planning_db: str) -> dict | None:
    """Cached views.demand_view (None when there is no snapshot for that date)."""
    return views.demand_view(day, Path(planning_db))


def show_table(table: views.Table, height: int | None = None) -> None:
    """A sortable table with the number formats of each column kind."""
    config = {
        label: st.column_config.NumberColumn(label, format=fmt)
        for label, fmt in table.formats().items()
    }
    options = {"height": height} if height else {}
    st.dataframe(table.frame, column_config=config, hide_index=True, **options)
