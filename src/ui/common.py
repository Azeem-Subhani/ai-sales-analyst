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
from src.ui import style

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


def card(name: str) -> object:
    """A bordered container drawn as a surface card (style.css: st-key-card-*).

    Streamlit gives bordered containers no class of their own, so the key carries one.
    name must be unique on the page.
    """
    return st.container(border=True, key=f"card-{name}")


def header(
    title: str,
    description: str,
    split: tuple[int, int] = (3, 2),
    page_class: str = "",
    wrap: bool = True,
) -> object:
    """The page header: title and description on the left; on the right the 'Data
    through' status pill, then a row the page fills with its own controls (returned).
    split is the width ratio of the two sides; page_class marks the page for CSS; wrap
    False keeps the pill and the controls on one line.
    """
    left, right = st.columns(list(split), vertical_alignment="center")
    left.html(style.page_header(title, description, page_class))
    controls = right.container(
        horizontal=True, horizontal_alignment="right", vertical_alignment="center", wrap=wrap
    )
    data_end = default_as_of(str(path(SALES_DB_KEY)))
    controls.html(style.status_pill(f"Data through {data_end:%d %b %Y}"), width="content")
    return controls


@st.cache_data(show_spinner="Loading sales numbers...")
def sales_view(kind: str, day: date, sales_db: str) -> dict:
    """Cached views.sales_view."""
    return views.sales_view(kind, day, Path(sales_db))


@st.cache_data(show_spinner="Loading the demand snapshot...")
def demand_view(day: date, planning_db: str) -> dict | None:
    """Cached views.demand_view (None when there is no snapshot for that date)."""
    return views.demand_view(day, Path(planning_db))


# Dashboard number formats: the report formats (views.DISPLAY_FORMATS) with thousands
# separators added; the decimals stay the same as the KPI cards and the reports.
THOUSANDS = {"%.2f": "%,.2f", "%d": "%,d", "%.1f": "%,.1f"}


def show_table(table: views.Table, height: int | None = None, data: object = None) -> None:
    """A sortable table with the number formats of each column kind.

    data replaces table.frame when given, e.g. a pandas Styler that colours a column.
    """
    config = {
        label: st.column_config.NumberColumn(label, format=THOUSANDS.get(fmt, fmt))
        for label, fmt in table.formats().items()
    }
    options = {"height": height} if height else {}
    shown = table.frame if data is None else data
    st.dataframe(shown, column_config=config, hide_index=True, **options)
