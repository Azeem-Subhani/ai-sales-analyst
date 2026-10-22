"""Sales page: KPI cards, trend, category breakdown and top 10 lists, as in the reports."""

import plotly.express as px
import streamlit as st

from src.ui import common

PERIOD_LABELS = {"Day": "day", "Week": "week", "Month": "month"}
CARDS_PER_ROW = 4
TREND_TITLES = {
    "day": "Weekly net revenue",
    "week": "Weekly net revenue",
    "month": "Monthly net revenue",
}


# st.metric colours a delta by its sign; the report's tone says whether that is good news.
TONE_COLORS = {
    "good": {"+": "normal", "-": "inverse"},
    "bad": {"+": "inverse", "-": "normal"},
}


def delta_color(tone: str, change_shown: str) -> str:
    """Green for good news and red for bad, whichever way the number moved."""
    return TONE_COLORS.get(tone, {}).get(change_shown[:1], "off")


def kpi_cards(cards: list[dict]) -> None:
    """One metric per KPI: change vs the first comparison, the others underneath."""
    for start in range(0, len(cards), CARDS_PER_ROW):
        row = cards[start : start + CARDS_PER_ROW]
        for column, card in zip(st.columns(CARDS_PER_ROW)[: len(row)], row, strict=True):
            first, *others = card["comparisons"]
            with column:
                st.metric(
                    card["label"],
                    card["value_shown"],
                    delta=f"{first['change_shown']} vs {first['label']}",
                    delta_color=delta_color(first["tone"], first["change_shown"]),
                )
                for other in others:
                    st.caption(
                        f"{other['change_shown']} vs {other['label']} ({other['value_shown']})"
                    )


def main() -> None:
    """Render the page."""
    st.title("Sales")
    choice = st.segmented_control("Period", list(PERIOD_LABELS), default="Week", key="sales_period")
    kind = PERIOD_LABELS[choice or "Week"]
    try:
        view = common.sales_view(kind, common.as_of(), str(common.path(common.SALES_DB_KEY)))
    except ValueError as error:
        st.error(str(error))
        return
    st.subheader(view["period"].label)
    st.caption(
        "Changes are % for money and counts and percentage points (pp) for rates. "
        f"Tables compare with the {view['primary_comparison']}."
    )
    kpi_cards(view["kpi_cards"])

    trend = view["trend"].frame
    st.subheader(TREND_TITLES[kind])
    st.plotly_chart(px.line(trend, x="Period start", y="Net revenue", markers=True))

    st.subheader("Net revenue and gross margin by category")
    categories = view["categories"]
    st.plotly_chart(px.bar(categories.frame, x="Category", y="Net revenue"))
    common.show_table(categories)

    left, right = st.columns(2)
    with left:
        st.subheader("Top 10 products")
        common.show_table(view["top_products"])
    with right:
        st.subheader("Top 10 customers")
        common.show_table(view["top_customers"])


main()
