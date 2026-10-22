"""Reports page: browse saved dry-run and backfilled reports, view them, download the Excel."""

import pandas as pd
import streamlit as st

from src.reports import saved
from src.ui import common

REPORT_HEIGHT = 1400  # pixels; the email is long, the box scrolls


def label(report: saved.SavedReport) -> str:
    """How a saved report appears in the picker."""
    return f"{report.report} as of {report.as_of} ({report.source})"


def main() -> None:
    """Render the page."""
    st.title("Reports")
    dry_runs = saved.dry_run_reports(common.path(common.REPORTS_ROOT_KEY))
    backfill_root = common.path(common.BACKFILL_ROOT_KEY)
    manifest = saved.backfill_manifest(backfill_root)
    backfilled = saved.backfill_reports(backfill_root)

    source = st.radio("Source", ["Backfill", "Dry runs"], horizontal=True, key="report_source")
    choices = backfilled if source == "Backfill" else dry_runs
    if source == "Backfill" and manifest:
        with st.expander(f"Backfill manifest ({len(manifest)} runs)"):
            st.dataframe(pd.DataFrame(manifest), hide_index=True)
    if not choices:
        where = (
            "outputs/backfill (run python -m src.reports.backfill)"
            if source == "Backfill"
            else "outputs/reports"
        )
        st.info(f"No saved reports in {where}.")
        return
    chosen = st.selectbox("Report", choices, format_func=label, key="report_choice")
    if chosen.excel_path:
        st.download_button(
            "Download Excel",
            data=chosen.excel_path.read_bytes(),
            file_name=chosen.excel_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    # st.html sanitises the page (the report includes LLM-written summary text), unlike
    # st.iframe, which would run it with access to the app.
    with st.container(height=REPORT_HEIGHT):
        st.html(saved.report_html(chosen))


main()
