"""One shared Plotly look for the dashboard charts (Week 7).

Colours are not set here: the charts are drawn with theme="streamlit", which takes the
series colours (accent first, muted second), gridlines and hover label from the light or
dark theme in .streamlit/config.toml and follows the theme the viewer picks without a
rerun. This module sets everything else: font, light y gridlines only, no borders, axis
labels that never clip, and thousands separators.
"""

import plotly.graph_objects as go

FONT_FAMILY = "Geist, system-ui, -apple-system, 'Segoe UI', sans-serif"
FONT_SIZE = 12
CHART_HEIGHT = 320
MARGIN = {"l": 4, "r": 4, "t": 8, "b": 4}  # axes use automargin, so labels never clip


def apply(figure: go.Figure, number_axis: str = "y", height: int = CHART_HEIGHT) -> go.Figure:
    """The shared look; number_axis ('x' or 'y') gets thousands separators."""
    figure.update_layout(
        height=height,
        margin=MARGIN,
        font={"family": FONT_FAMILY, "size": FONT_SIZE},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        separators=".,",  # decimal point, thousands comma
        hoverlabel={"font": {"family": FONT_FAMILY, "size": FONT_SIZE}},
        showlegend=False,
    )
    figure.update_xaxes(
        showgrid=False, showline=False, zeroline=False, ticks="", title=None, automargin=True
    )
    figure.update_yaxes(
        showgrid=True, showline=False, zeroline=False, ticks="", title=None, automargin=True
    )
    getattr(figure, f"update_{number_axis}axes")(tickformat=",.0f")
    return figure
