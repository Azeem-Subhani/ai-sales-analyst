"""Reports page: a card per saved dry-run or backfilled report; view one, download its Excel."""

from html import escape

import pandas as pd
import streamlit as st

from src.reports import saved
from src.ui import common, style

REPORT_HEIGHT = 1400  # pixels; the email is long, the box scrolls
CARDS_PER_ROW = 3
CARDS_HEIGHT = 360  # pixels: about two rows of cards, the rest scroll
SELECTED_KEY = "report_selected"  # folder of the report being viewed
SOURCES = {"Backfill": ":material/history:", "Dry runs": ":material/draft:"}
SUMMARY_TAGS = {"ok": "green", "guard_failed": "amber", "truncated": "amber",
                "llm_unavailable": "red", "skipped": "grey"}  # fmt: skip
REPORT_ICONS = {
    "daily_sales_snapshot": "\U0001f4c5",
    "weekly_sales_review": "\U0001f5d3️",
    "monthly_business_review": "\U0001f4ca",
}


def card_html(report: saved.SavedReport) -> str:
    """Name, period, as_of and summary status of one report."""
    summary = report.summary_status or "n/a"
    period = report.period or f"as of {report.as_of}"
    return (
        f'<div style="font-weight:650">{REPORT_ICONS.get(report.report, "")} '
        f"{escape(report.title)}</div>"
        f'<div style="font-size:.86rem;opacity:.8;margin:.15rem 0 .35rem">{escape(period)}'
        f" · as of {escape(report.as_of)}</div>"
        f"{style.tag('summary ' + summary, SUMMARY_TAGS.get(summary, 'grey'))}"
        f' <span style="font-size:.78rem;opacity:.6">{escape(report.source)}</span>'
    )


def report_cards(reports: list[saved.SavedReport]) -> None:
    """A grid of cards; View selects one."""
    for start in range(0, len(reports), CARDS_PER_ROW):
        row = reports[start : start + CARDS_PER_ROW]
        for column, report in zip(st.columns(CARDS_PER_ROW)[: len(row)], row, strict=True):
            with column, st.container(border=True):
                st.html(card_html(report))
                selected = st.session_state.get(SELECTED_KEY) == str(report.folder)
                if st.button(
                    "Viewing" if selected else "View",
                    key=f"view-{report.folder}",
                    icon=":material/visibility:",
                    type="primary" if selected else "secondary",
                    width="stretch",
                ):
                    st.session_state[SELECTED_KEY] = str(report.folder)
                    st.rerun()


def show_report(report: saved.SavedReport) -> None:
    """The saved report, with the Excel download."""
    with st.container(border=True):
        head, download = st.columns([4, 1], vertical_alignment="center")
        head.markdown(f"**{report.title}** · {report.period or report.as_of}")
        if report.excel_path:
            download.download_button(
                "Excel",
                data=report.excel_path.read_bytes(),
                file_name=report.excel_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
                width="stretch",
            )
        # st.html sanitises the page (the report includes LLM-written summary text), unlike
        # st.iframe, which would run it with access to the app.
        with st.container(height=REPORT_HEIGHT, border=False):
            st.html(saved.report_html(report))


def main() -> None:
    """Render the page."""
    st.title(":material/description: Reports")
    dry_runs = saved.dry_run_reports(common.path(common.REPORTS_ROOT_KEY))
    backfill_root = common.path(common.BACKFILL_ROOT_KEY)
    manifest = saved.backfill_manifest(backfill_root)
    backfilled = saved.backfill_reports(backfill_root)

    source = st.segmented_control(
        "Source",
        list(SOURCES),
        default="Backfill",
        format_func=lambda name: f"{SOURCES[name]} {name}",
        key="report_source",
    )
    choices = backfilled if source != "Dry runs" else dry_runs
    if source != "Dry runs" and manifest:
        with st.expander(f"Backfill manifest · {len(manifest)} runs", icon=":material/list_alt:"):
            st.dataframe(pd.DataFrame(manifest), hide_index=True)
    if not choices:
        where = (
            "outputs/backfill (run python -m src.reports.backfill)"
            if source != "Dry runs"
            else "outputs/reports"
        )
        st.info(f"No saved reports in {where}.", icon=":material/folder_off:")
        return
    folders = {str(r.folder): r for r in choices}
    if st.session_state.get(SELECTED_KEY) not in folders:
        st.session_state[SELECTED_KEY] = str(choices[0].folder)  # newest first
    with st.container(height=CARDS_HEIGHT, border=False):  # the cards scroll, the report stays near
        report_cards(choices)
    show_report(folders[st.session_state[SELECTED_KEY]])


main()
