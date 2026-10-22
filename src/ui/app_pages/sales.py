"""Sales page: KPI cards, trend, category breakdown and top 10 lists, as in the reports.

Four primary KPIs in one row, the other nine in a compact grid; the same cards and
comparisons as the reports (views.sales_view).
"""

from datetime import date
from html import escape

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.analytics import kpis
from src.ui import chart_theme, common, style

PERIOD_LABELS = {"Day": "day", "Week": "week", "Month": "month"}
TREND_TITLES = {
    "day": "Weekly net revenue",
    "week": "Weekly net revenue",
    "month": "Monthly net revenue",
}
BAR_LABEL_ROOM = 64  # pixels right of the bars for their value labels
PRIMARY_KPIS = ("net_revenue", "orders", "aov", "gross_margin_pct")  # the top row, in order


def day_month(day: date) -> str:
    """'21 Sep' without a leading zero, on every platform."""
    return f"{day.day} {day:%b}"


def period_heading(period: kpis.Period) -> str:
    """'Week 39 · 21–27 Sep 2026', 'Wed 30 Sep 2026' or 'September 2026' (dates only)."""
    start, end = period.start, period.end
    if period.kind == "day":
        return f"{end:%a} {day_month(end)} {end:%Y}"
    if period.kind == "month":
        return f"{start:%B %Y}"
    first = str(start.day) if start.month == end.month else day_month(start)
    return f"Week {start.isocalendar().week} \u00b7 {first}\u2013{day_month(end)} {end:%Y}"


def kpi_grids(cards: list[dict]) -> str:
    """The primary cards in one row, then the rest in a compact grid."""
    by_kpi = {card["kpi"]: card for card in cards}
    primary = [by_kpi[kpi] for kpi in PRIMARY_KPIS if kpi in by_kpi]
    others = [card for card in cards if card["kpi"] not in PRIMARY_KPIS]
    return style.card_grid([style.kpi_card(c) for c in primary]) + style.card_grid(
        [style.kpi_card(c, compact=True) for c in others], compact=True
    )


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
    # Room on the right for the value label of the longest bar.
    figure.update_layout(margin={**chart_theme.MARGIN, "r": BAR_LABEL_ROOM})
    return figure


def main() -> None:
    """Render the page."""
    controls = common.header("Sales", "Revenue, orders and margin, as in the reports.")
    with controls:
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
    st.html(
        style.section_title(period_heading(view["period"]))
        + '<p class="muted">Changes are % for money and counts and percentage points (pp) '
        f"for rates. Tables compare with the {escape(view['primary_comparison'])}.</p>"
        + kpi_grids(view["kpi_cards"])
    )

    trend_box, category_box = st.columns([3, 2], gap="medium")
    with trend_box, common.card("trend"):
        st.html(style.section_title(TREND_TITLES[kind]))
        st.plotly_chart(trend_chart(view["trend"].frame), theme="streamlit")
    with category_box, common.card("category-chart"):
        st.html(style.section_title("Net revenue by category"))
        st.plotly_chart(category_chart(view["categories"].frame), theme="streamlit")

    with common.card("category-table"):
        st.html(style.section_title("Net revenue and gross margin by category"))
        common.show_table(view["categories"])

    left, right = st.columns(2, gap="medium")
    with left, common.card("top-products"):
        st.html(style.section_title("Top 10 products"))
        common.show_table(view["top_products"])
    with right, common.card("top-customers"):
        st.html(style.section_title("Top 10 customers"))
        common.show_table(view["top_customers"])


main()
