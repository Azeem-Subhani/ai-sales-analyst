"""Sales page: KPI cards, trend, category breakdown and top 10 lists, as in the reports."""

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.ui import chart_theme, common, style

PERIOD_LABELS = {"Day": "day", "Week": "week", "Month": "month"}
TREND_TITLES = {
    "day": "Weekly net revenue",
    "week": "Weekly net revenue",
    "month": "Monthly net revenue",
}


def trend_chart(trend: object) -> go.Figure:
    """Net revenue per week or month, as a line in the accent colour (the theme's first)."""
    figure = px.line(trend, x="Period start", y="Net revenue", markers=True)
    figure.update_traces(
        line={"width": 2},
        marker={"size": 5},
        hovertemplate="%{x|%d %b %Y}<br>Net revenue %{y:,.2f}<extra></extra>",
    )
    return chart_theme.apply(figure)


def category_chart(categories: object) -> go.Figure:
    """Net revenue by category, largest at the top, with the value at each bar end."""
    figure = px.bar(categories, x="Net revenue", y="Category", orientation="h")
    figure.update_traces(
        marker_cornerradius=4,
        texttemplate="%{x:,.0f}",
        textposition="outside",
        cliponaxis=False,  # the value label may sit past the axis end
        hovertemplate="%{y}<br>Net revenue %{x:,.2f}<extra></extra>",
    )
    figure.update_yaxes(categoryorder="total ascending", showgrid=False)
    figure = chart_theme.apply(figure, number_axis="x")
    figure.update_xaxes(showticklabels=False)  # the values are on the bars
    return figure


def main() -> None:
    """Render the page."""
    head, controls = st.columns([3, 2], vertical_alignment="bottom")
    head.html(style.page_header("Sales", "Revenue, orders and margin, as in the reports."))
    with controls, st.container(horizontal=True, horizontal_alignment="right"):
        choice = st.segmented_control(
            "Period",
            list(PERIOD_LABELS),
            default="Week",
            key="sales_period",
            label_visibility="collapsed",
        )
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
    st.html(style.card_grid([style.kpi_card(card) for card in view["kpi_cards"]]))

    trend_box, category_box = st.columns([3, 2], gap="medium")
    with trend_box, st.container(border=True):
        st.markdown(f"**{TREND_TITLES[kind]}**")
        st.plotly_chart(trend_chart(view["trend"].frame), theme="streamlit")
    with category_box, st.container(border=True):
        st.markdown("**Net revenue by category**")
        st.plotly_chart(category_chart(view["categories"].frame), theme="streamlit")

    with st.container(border=True):
        st.markdown("**Net revenue and gross margin by category**")
        common.show_table(view["categories"])

    left, right = st.columns(2, gap="medium")
    with left, st.container(border=True):
        st.markdown("**:material/inventory: Top 10 products**")
        common.show_table(view["top_products"])
    with right, st.container(border=True):
        st.markdown("**:material/groups: Top 10 customers**")
        common.show_table(view["top_customers"])


main()
