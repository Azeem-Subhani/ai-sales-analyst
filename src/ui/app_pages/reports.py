"""Reports page: saved dry-run or backfilled reports as a compact list, grouped by report
type, newest first; view one and download its Excel.

The summary status shows nothing when the summary is fine (ok, and ok_after_retry,
which counts as a success everywhere: docs/decisions.md); otherwise a small "Summary
unavailable" badge. The embedded email HTML is shown as saved.
"""

from datetime import date
from html import escape

import pandas as pd
import streamlit as st

from src.reports import saved
from src.ui import common, icons, style

REPORT_HEIGHT = 1400  # pixels; the email is long, the box scrolls
LIST_HEIGHT = 420  # pixels: the list scrolls, so the preview stays near
SELECTED_KEY = "report_selected"  # folder of the report being viewed
SOURCES = ["Backfill", "Dry runs"]
SUMMARY_OK = {"ok", "ok_after_retry"}  # shown as nothing at all
SUMMARY_UNAVAILABLE = "Summary unavailable"
REPORT_ICONS = {  # report -> Lucide icon
    "daily_sales_snapshot": "calendar",
    "weekly_sales_review": "calendar-range",
    "monthly_business_review": "chart-column",
}
BACKFILL_PREFIX = "backfill "  # SavedReport.source of a backfilled report: "backfill <run date>"


def generated_on(report: saved.SavedReport) -> str:
    """The day the report was produced: the backfill run date, or the dry run's file date."""
    if report.source.startswith(BACKFILL_PREFIX):
        return report.source.removeprefix(BACKFILL_PREFIX)
    return date.fromtimestamp(report.html_path.stat().st_mtime).isoformat()


def summary_badge(status: str | None) -> str:
    """Nothing for a usable summary (or none recorded); a warn badge otherwise."""
    if status is None or status in SUMMARY_OK:
        return ""
    return style.tag(SUMMARY_UNAVAILABLE, "amber")


def row_html(report: saved.SavedReport) -> str:
    """One report in the list: the period, then as-of and generated dates, and the badge."""
    period = report.period or f"As of {report.as_of}"
    return (
        f'<div class="report-row"><div class="report-period">{escape(period)} '
        f"{summary_badge(report.summary_status)}</div>"
        f'<div class="report-meta">As of {escape(report.as_of)} · generated '
        f"{escape(generated_on(report))}</div></div>"
    )


def group_html(report: str, title: str, count: int) -> str:
    """A report-type heading: icon tile, name and a muted count."""
    icon = icons.icon(REPORT_ICONS.get(report, "file-text"))
    return (
        f'<div class="report-group"><span class="report-group-icon">{icon}</span>'
        f"{style.section_title(title, count)}</div>"
    )


def grouped(reports: list[saved.SavedReport]) -> dict[str, list[saved.SavedReport]]:
    """The reports by type, in order of each type's newest report; newest first within."""
    groups: dict[str, list[saved.SavedReport]] = {}
    for report in reports:  # already newest first
        groups.setdefault(report.report, []).append(report)
    return groups


def report_list(reports: list[saved.SavedReport]) -> None:
    """One card per report type with a row per report; View selects one."""
    for report_type, rows in grouped(reports).items():
        with common.card(f"reports-{report_type}"):
            st.html(group_html(report_type, rows[0].title, len(rows)))
            for report in rows:
                text, button = st.columns([5, 1], vertical_alignment="center")
                text.html(row_html(report))
                selected = st.session_state.get(SELECTED_KEY) == str(report.folder)
                if button.button(
                    "Viewing" if selected else "View",
                    key=f"view-{report.folder}",
                    type="primary" if selected else "secondary",
                    width="stretch",
                ):
                    st.session_state[SELECTED_KEY] = str(report.folder)
                    st.rerun()


def show_report(report: saved.SavedReport) -> None:
    """The saved report, with the Excel download."""
    with common.card("report-preview"):
        head, download = st.columns([4, 1], vertical_alignment="center")
        head.html(style.section_title(report.title, note=report.period or report.as_of))
        if report.excel_path:
            with download, st.container(key="excel-download"):
                st.download_button(
                    "Excel",
                    data=report.excel_path.read_bytes(),
                    file_name=report.excel_path.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                )
        # st.html sanitises the page (the report includes LLM-written summary text), unlike
        # st.iframe, which would run it with access to the app.
        with st.container(height=REPORT_HEIGHT, border=False):
            st.html(saved.report_html(report))


def main() -> None:
    """Render the page."""
    controls = common.header("Reports", "Saved dry-run and backfilled reports.")
    with controls:
        source = st.segmented_control(
            "Source",
            SOURCES,
            default="Backfill",
            key="report_source",
            label_visibility="collapsed",
        )
    dry_runs = saved.dry_run_reports(common.path(common.REPORTS_ROOT_KEY))
    backfill_root = common.path(common.BACKFILL_ROOT_KEY)
    manifest = saved.backfill_manifest(backfill_root)
    backfilled = saved.backfill_reports(backfill_root)

    choices = backfilled if source != "Dry runs" else dry_runs
    if source != "Dry runs" and manifest:
        with st.expander(f"Backfill manifest · {len(manifest)} runs"):
            st.dataframe(pd.DataFrame(manifest), hide_index=True)
    if not choices:
        where = (
            "outputs/backfill (run python -m src.reports.backfill)"
            if source != "Dry runs"
            else "outputs/reports"
        )
        st.info(f"No saved reports in {where}.")
        return
    folders = {str(r.folder): r for r in choices}
    if st.session_state.get(SELECTED_KEY) not in folders:
        st.session_state[SELECTED_KEY] = str(choices[0].folder)  # newest first
    with st.container(height=LIST_HEIGHT, border=False):  # the list scrolls
        report_list(choices)
    show_report(folders[st.session_state[SELECTED_KEY]])


main()
