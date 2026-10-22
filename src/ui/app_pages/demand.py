"""Demand page: full demand-planning lists for the as_of snapshot, and pre-build approvals.

Approvals go through src.demand.approve.decide(), exactly as the approval CLI does;
this page never writes to planning.duckdb any other way.
"""

from html import escape

import pandas as pd
import streamlit as st

from src.demand import approve, store
from src.reports import views
from src.ui import common, style

DECIDED_BY_KEY = "decided_by"  # kept for the session, so a planner types it once
NOTE_KEY = "decision_note"
CONFIRM_KEY = "pending_decision"  # (rec_id, decision) waiting for the confirm click
MESSAGE_KEY = "decision_message"
VERBS = {"approve": "Approve", "reject": "Reject"}
BUTTONS = {  # decision -> (button type, icon)
    "approve": ("primary", ":material/check:"),
    "reject": ("secondary", ":material/close:"),
}
# Coloured text tags inside the sortable tables (text only, sorting still works).
TABLE_TAGS = {
    "Severity": {
        "serious": "🔴 serious",
        "overdue": "🟠 overdue",
        "warning": "🟡 warning",
        "info": "🔵 info",
    },
    "Reason": {"urgent": "🔴 urgent", "normal": "⚪ normal"},
    "Status": {"pending": "⏳ pending", "approved": "✅ approved", "rejected": "❌ rejected"},
    "Flagged": {True: "🚩 flagged", False: ""},
}
PRIORITY_TAGS = {"urgent": "red", "normal": "grey"}


def tagged(table: views.Table) -> views.Table:
    """The table with severity, priority, status and flag columns shown as coloured tags."""
    frame = table.frame.copy()
    for column, labels in TABLE_TAGS.items():
        if column in frame:
            frame[column] = frame[column].map(lambda v, labels=labels: labels.get(v, v))
    return views.Table(frame, table.kinds)


def show(table: views.Table, height: int | None = None) -> None:
    """A sortable table with its tags."""
    common.show_table(tagged(table), height=height)


def no_snapshot(day: object) -> None:
    """Explain how to create the missing snapshot (the page never runs it)."""
    st.info(f"There is no demand-planning snapshot for {day}. A planner creates it with:")
    st.code(f"python -m src.demand.run --as-of {day}", language="bash")


def customer_alerts_tab(view: dict) -> None:
    """One sortable list per alert type."""
    st.caption(
        "Overdue: ranked by 365-day revenue. Declining: ranked by revenue lost "
        "(prior 90 days minus last 90 days), largest first."
    )
    for label, table in view["customer_alerts"].items():
        with st.container(border=True):
            st.markdown(f"**{label}** · {view['counts'][label]}")
            show(table)


def category_stock_tab(view: dict) -> None:
    """Category alerts (all categories, flagged marked) and stock alerts."""
    counts = view["counts"]
    with st.container(border=True):
        st.markdown(
            f"**:material/category: Category alerts** · {counts['category_flagged']} flagged"
        )
        show(view["category_alerts"])
    with st.container(border=True):
        st.markdown(
            f"**:material/production_quantity_limits: Stock alerts** · {counts['stock_alerts']}"
        )
        show(view["stock_alerts"])


def accuracy_tab(view: dict) -> None:
    """Forecast accuracy overall, by category and for every product."""
    counts = view["counts"]
    with st.container(border=True):
        st.markdown("**Overall and by category**")
        show(view["accuracy_totals"])
    with st.container(border=True):
        st.markdown(
            f"**Products** · {counts['accuracy_products']} · {counts['accuracy_flagged']} flagged"
        )
        show(view["accuracy_products"], height=420)


def decide(rec_id: str, decision: str) -> None:
    """Record a decision with approve.decide(); show any error instead of raising."""
    decided_by = (st.session_state.get(DECIDED_BY_KEY) or "").strip()
    if not decided_by:
        st.session_state[MESSAGE_KEY] = ("error", "Enter your name in Decided by first.")
        return
    note = (st.session_state.get(NOTE_KEY) or "").strip() or None
    try:
        result = approve.decide(
            rec_id,
            decision,
            decided_by,
            note,
            planning_db=common.path(common.PLANNING_DB_KEY),
        )
    except (approve.ApprovalError, store.PlanningStoreError) as error:
        st.session_state[MESSAGE_KEY] = ("error", f"Not saved: {error}")
        return
    except Exception as error:  # noqa: BLE001 - e.g. the file is locked by another program
        st.session_state[MESSAGE_KEY] = (
            "error",
            f"Not saved, the planning database failed: {error}",
        )
        return
    st.session_state[MESSAGE_KEY] = (
        "success",
        f"{result.rec_id}: {result.status} by {result.decided_by} at {result.decided_at_utc} UTC",
    )
    st.cache_data.clear()  # the lists must show the new status


def row_html(row: dict) -> str:
    """The text of one pending recommendation (all values already formatted)."""
    priority = row["priority"] or "normal"
    return (
        f'<div style="line-height:1.45"><b>{escape(str(row["rank"]))}. {escape(row["name"])}</b> '
        f'<span style="opacity:.65">{escape(row["sku"])}</span> '
        f"{style.tag(priority, PRIORITY_TAGS.get(priority, 'grey'))}<br>"
        f'<span style="font-size:.86rem;opacity:.8">{escape(row["recommended_units_shown"])} '
        f"units · value at risk {escape(row['value_at_risk_shown'])} · "
        f"{escape(row['days_of_cover_shown'])} days of cover</span></div>"
    )


def pending_row(row: dict) -> None:
    """One pending recommendation with Approve / Reject, each needing a confirm click."""
    with st.container(border=True):
        text, *buttons = st.columns([5, 1.4, 1.4], vertical_alignment="center")
        text.html(row_html(row))
        waiting = st.session_state.get(CONFIRM_KEY)
        if waiting and waiting[0] == row["rec_id"]:
            decision = waiting[1]
            if buttons[0].button(
                "Confirm",
                help=f"Confirm: {decision} {row['rec_id']}. A decision is final.",
                key=f"confirm-{row['rec_id']}",
                type="primary",
                icon=":material/done_all:",
                width="stretch",
            ):
                st.session_state.pop(CONFIRM_KEY, None)
                decide(row["rec_id"], decision)
                st.rerun()
            if buttons[1].button("Cancel", key=f"cancel-{row['rec_id']}", width="stretch"):
                st.session_state.pop(CONFIRM_KEY, None)
                st.rerun()
            return
        for column, (decision, verb) in zip(buttons, VERBS.items(), strict=True):
            kind, icon = BUTTONS[decision]
            if column.button(
                verb, key=f"{decision}-{row['rec_id']}", type=kind, icon=icon, width="stretch"
            ):
                st.session_state[CONFIRM_KEY] = (row["rec_id"], decision)
                st.rerun()


def prebuild_tab(view: dict) -> None:
    """The full pre-build table, then approvals for pending rows and the decided ones."""
    counts = view["counts"]
    message = st.session_state.pop(MESSAGE_KEY, None)
    if message and message[0] == "success":
        st.toast(message[1], icon=":material/task_alt:")
    with st.container(border=True):
        st.markdown(f"**:material/gavel: Approvals** · {counts['pending']} pending")
        st.caption("Each decision needs a confirm click and is final.")
        who, note = st.columns(2)
        who.text_input("Decided by (required)", key=DECIDED_BY_KEY, icon=":material/person:")
        note.text_input("Note (optional)", key=NOTE_KEY, max_chars=approve.MAX_NOTE_CHARS)
        if message and message[0] == "error":
            st.error(message[1], icon=":material/error:")
        with st.container(height=440, border=False):
            for row in view["pending"]:
                pending_row(row)
    with st.container(border=True):
        st.markdown(f"**:material/history: Decided** · {counts['decided']}")
        if view["decided"]:
            decided = pd.DataFrame(view["decided"])[
                ["rank", "name", "sku", "status", "decided_by", "decided_at_utc", "note"]
            ]
            decided["status"] = decided["status"].map(TABLE_TAGS["Status"])
            st.dataframe(decided, hide_index=True)
        else:
            st.caption("No decisions yet for this snapshot.")
    with st.container(border=True):
        st.markdown(
            f"**Pre-build recommendations** · {counts['prebuild']} "
            "· ranked urgent first, then by value at risk"
        )
        show(view["prebuild"], height=320)
    with st.container(border=True):
        st.markdown(f"**Needs planner review** · {counts['planner_review']}")
        show(view["planner_review"])


def main() -> None:
    """Render the page."""
    st.title(":material/inventory_2: Demand")
    day = common.as_of()
    view = common.demand_view(day, str(common.path(common.PLANNING_DB_KEY)))
    if view is None:
        no_snapshot(day)
        return
    st.caption(f"Demand-planning snapshot as of {day}. Full lists, sortable by any column.")
    counts = view["counts"]
    alerts, category_stock, accuracy, prebuild = st.tabs(
        [
            f":material/groups: Customer alerts · {counts['customer_alerts']}",
            f":material/warning: Stock & category · {counts['category_and_stock']}",
            f":material/target: Forecasts · {counts['accuracy_flagged']} flagged",
            f":material/precision_manufacturing: Pre-build · {counts['pending']}",
        ]
    )
    with alerts:
        customer_alerts_tab(view)
    with category_stock:
        category_stock_tab(view)
    with accuracy:
        accuracy_tab(view)
    with prebuild:
        prebuild_tab(view)


main()
