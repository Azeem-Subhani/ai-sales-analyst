"""Demand page: full demand-planning lists for the as_of snapshot, and pre-build approvals.

Approvals go through src.demand.approve.decide(), exactly as the approval CLI does;
this page never writes to planning.duckdb any other way.
"""

import pandas as pd
import streamlit as st

from src.demand import approve, store
from src.ui import common

DECIDED_BY_KEY = "decided_by"  # kept for the session, so a planner types it once
NOTE_KEY = "decision_note"
CONFIRM_KEY = "pending_decision"  # (rec_id, decision) waiting for the confirm click
MESSAGE_KEY = "decision_message"
VERBS = {"approve": "Approve", "reject": "Reject"}


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
        st.subheader(f"{label} ({len(table.frame)})")
        common.show_table(table)


def category_stock_tab(view: dict) -> None:
    """Category alerts (all categories, flagged marked) and stock alerts."""
    st.subheader("Category alerts")
    common.show_table(view["category_alerts"])
    st.subheader(f"Stock alerts ({len(view['stock_alerts'].frame)})")
    common.show_table(view["stock_alerts"])


def accuracy_tab(view: dict) -> None:
    """Forecast accuracy overall, by category and for every product."""
    st.subheader("Overall and by category")
    common.show_table(view["accuracy_totals"])
    st.subheader(f"Products ({len(view['accuracy_products'].frame)})")
    common.show_table(view["accuracy_products"], height=420)


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


def pending_row(row: dict) -> None:
    """One pending recommendation with Approve / Reject, each needing a confirm click."""
    text, *buttons = st.columns([6, 1, 1])
    text.markdown(
        f"**{row['rank']}. {row['name']}** ({row['sku']}): {row['recommended_units_shown']} "
        f"units, value at risk {row['value_at_risk_shown']}, {row['days_of_cover_shown']} days "
        f"of cover, {row['priority']}"
    )
    waiting = st.session_state.get(CONFIRM_KEY)
    if waiting and waiting[0] == row["rec_id"]:
        decision = waiting[1]
        if buttons[0].button(f"Confirm {decision}", key=f"confirm-{row['rec_id']}", type="primary"):
            st.session_state.pop(CONFIRM_KEY, None)
            decide(row["rec_id"], decision)
            st.rerun()
        if buttons[1].button("Cancel", key=f"cancel-{row['rec_id']}"):
            st.session_state.pop(CONFIRM_KEY, None)
            st.rerun()
        return
    for column, (decision, verb) in zip(buttons, VERBS.items(), strict=True):
        if column.button(verb, key=f"{decision}-{row['rec_id']}"):
            st.session_state[CONFIRM_KEY] = (row["rec_id"], decision)
            st.rerun()


def prebuild_tab(view: dict) -> None:
    """The full pre-build table, then approvals for pending rows and the decided ones."""
    st.subheader(f"Pre-build recommendations ({len(view['prebuild'].frame)})")
    st.caption("Ranked urgent first, then by value at risk.")
    common.show_table(view["prebuild"], height=320)

    st.subheader("Approvals")
    st.text_input("Decided by (required)", key=DECIDED_BY_KEY)
    st.text_input("Note (optional)", key=NOTE_KEY, max_chars=approve.MAX_NOTE_CHARS)
    message = st.session_state.pop(MESSAGE_KEY, None)
    if message:
        (st.error if message[0] == "error" else st.success)(message[1])
    st.markdown(f"**Pending ({len(view['pending'])})**")
    with st.container(height=420):
        for row in view["pending"]:
            pending_row(row)
    st.markdown(f"**Decided ({len(view['decided'])})**")
    if view["decided"]:
        decided = pd.DataFrame(view["decided"])[
            ["rank", "name", "sku", "status", "decided_by", "decided_at_utc", "note"]
        ]
        st.dataframe(decided, hide_index=True)
    else:
        st.caption("No decisions yet for this snapshot.")

    st.subheader(f"Needs planner review ({len(view['planner_review'].frame)})")
    common.show_table(view["planner_review"])


def main() -> None:
    """Render the page."""
    st.title("Demand")
    day = common.as_of()
    view = common.demand_view(day, str(common.path(common.PLANNING_DB_KEY)))
    if view is None:
        no_snapshot(day)
        return
    st.caption(f"Demand-planning snapshot as of {day}. Full lists, sortable by any column.")
    alerts, category_stock, accuracy, prebuild = st.tabs(
        ["Customer alerts", "Category and stock alerts", "Forecast accuracy", "Pre-build"]
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
