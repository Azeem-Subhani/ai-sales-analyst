"""Sales page: KPI cards, trend, category breakdown and top 10 lists, as in the reports."""

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.ui import common, style

PERIOD_LABELS = {"Day": "day", "Week": "week", "Month": "month"}
TREND_TITLES = {
    "day": "Weekly net revenue",
    "week": "Weekly net revenue",
    "month": "Monthly net revenue",
}
CHART_HEIGHT = 320
GRID = "rgba(127,127,160,.18)"


def styled(figure: go.Figure, money_axis: str = "y") -> go.Figure:
    """Transparent background, light grid, the accent colour and money-style hovers."""
    figure.update_layout(
        height=CHART_HEIGHT,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel={"font_size": 13},
        showlegend=False,
    )
    figure.update_xaxes(showgrid=False, title=None)
    figure.update_yaxes(gridcolor=GRID, title=None, zeroline=False)
    getattr(figure, f"update_{money_axis}axes")(tickformat=",.0f")
    return figure


def trend_chart(trend: object) -> go.Figure:
    """Net revenue per week or month, as a line with soft fill."""
    figure = px.area(trend, x="Period start", y="Net revenue", markers=True)
    figure.update_traces(
        line={"color": style.ACCENT, "width": 2.5},
        fillcolor="rgba(79,70,229,.12)",
        hovertemplate="%{x|%d %b %Y}<br>Net revenue %{y:,.2f}<extra></extra>",
    )
    return styled(figure)


def category_chart(categories: object) -> go.Figure:
    """Net revenue by category, largest at the top."""
    figure = px.bar(categories, x="Net revenue", y="Category", orientation="h")
    figure.update_traces(
        marker_color=style.ACCENT,
        marker_cornerradius=6,
        hovertemplate="%{y}<br>Net revenue %{x:,.2f}<extra></extra>",
    )
    figure.update_yaxes(categoryorder="total ascending", showgrid=False)
    return styled(figure, money_axis="x")


def main() -> None:
    """Render the page."""
    st.title(":material/monitoring: Sales")
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
