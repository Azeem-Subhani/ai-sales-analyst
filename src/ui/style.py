"""Look and feel of the dashboard: colour tokens, the CSS file and small HTML pieces.

Week 7 redesign: a quiet emerald palette. TOKENS is the one list of colours; the same
values are in .streamlit/config.toml (Streamlit's own widgets and the charts) and in
style.css (as light-dark() pairs, which follow the theme picked in the app menu without
a rerun). tests/test_ui.py checks that the three agree.

The HTML helpers only lay out text that src/reports already formatted; they never do
arithmetic. Every value is escaped.
"""

from datetime import date
from html import escape
from pathlib import Path

import streamlit as st

from src.ui import icons

CSS_PATH = Path(__file__).with_name("style.css")
LOGO_PATH = Path(__file__).with_name("logo.svg")  # the brand mark as a file (favicon)
BRAND_NAME = "Sales Analyst"
# Colour tokens, light and dark. accent: fills, chart main series, active nav marker,
# focus ring. accent_text: links and the active nav label (#047857 reads better than
# #059669 on white). good is a darker green than the accent, so a positive delta does
# not look like brand colour; it always carries an arrow too.
TOKENS = {
    "light": {
        "bg": "#F8FAF9",
        "sidebar": "#F0FDF4",
        "surface": "#FFFFFF",
        "subtle": "#ECFDF5",
        "border": "#D1FAE5",
        "text": "#14251B",
        "muted": "#64756A",
        "accent": "#059669",
        "accent_text": "#047857",
        "button": "#166534",
        "button_hover": "#14532D",
        "button_text": "#FFFFFF",
        "good": "#166534",
        "bad": "#B91C1C",
        "warn": "#B45309",
    },
    "dark": {
        "bg": "#07130F",
        "sidebar": "#0A1C15",
        "surface": "#10231A",
        "subtle": "#173326",
        "border": "#28513C",
        "text": "#ECFDF5",
        "muted": "#9AB7A5",
        "accent": "#34D399",
        "accent_text": "#34D399",
        "button": "#34D399",
        "button_hover": "#6EE7B7",
        "button_text": "#07130F",
        "good": "#86EFAC",
        "bad": "#F87171",
        "warn": "#FBBF24",
    },
}
NAV_ICONS = {  # page title -> Lucide icon
    "Sales": "chart-column",
    "Demand": "package",
    "Chat": "message-square",
    "Reports": "file-text",
}
# Widgets that carry a Lucide icon, drawn by CSS (selector -> icon name).
WIDGET_ICONS = {
    **{f".st-key-nav-{title.lower()} a": name for title, name in NAV_ICONS.items()},
    **{f".st-key-nav-{title.lower()}-active a": name for title, name in NAV_ICONS.items()},
    '[class*="st-key-view-"] button': "eye",
    ".st-key-excel-download button": "download",
    '[data-testid="stChatInputSubmitButton"]': "arrow-up",
    ".steps-done > summary": "chevron-right",
    ".step-data > summary": "chevron-right",
}
SIGN_ARROWS = {"+": "arrow-up", "-": "arrow-down"}
# Shorter comparison names for the KPI cards (builder.COMPARISON_LABELS values).
SHORT_LABELS = {
    "previous week": "prev. week",
    "previous month": "prev. month",
    "same week last year": "last year",
    "same month last year": "last year",
}


def inject() -> None:
    """Add the dashboard CSS and the icon classes to the page (once per run, from app.py)."""
    st.html(CSS_PATH)  # a Path to a .css file is wrapped in <style> by Streamlit
    st.html(f"<style>{icons.icon_css(WIDGET_ICONS)}</style>")


def nav_key(title: str, active: bool) -> str:
    """The container key of a sidebar link; it becomes the CSS class st-key-<key>."""
    return f"nav-{title.lower()}" + ("-active" if active else "")


def brand_block() -> str:
    """The sidebar header: an accent monogram with white bars, and the product name."""
    mark = f'<div class="brand-mark">{icons.icon("chart-no-axes-column")}</div>'
    return f'<div class="brand">{mark}<div class="brand-name">{escape(BRAND_NAME)}</div></div>'


def sidebar_footer(data_end: date) -> str:
    """The sidebar footer: the last day of data, as plain muted text."""
    return f'<div class="sidebar-footer">Data through {data_end:%d %b %Y}</div>'


def page_header(title: str, description: str = "") -> str:
    """The page title (no icon, no anchor link) and a one-line muted description."""
    text = f'<p class="page-description">{escape(description)}</p>' if description else ""
    return f'<div class="page-header"><h1 class="page-title">{escape(title)}</h1>{text}</div>'


def section_title(text: str, count: object = None, note: str = "") -> str:
    """A section title with an optional muted count and a muted note after it."""
    extra = f' <span class="section-count">{escape(str(count))}</span>' if count is not None else ""
    tail = f' <span class="section-note">{escape(note)}</span>' if note else ""
    return f'<div class="section-title">{escape(text)}{extra}{tail}</div>'


def short_label(label: str) -> str:
    """'previous week' -> 'prev. week'; labels without a short form stay as they are."""
    return SHORT_LABELS.get(label, label)


def delta_arrow(change_shown: str) -> str:
    """An up or down arrow for a signed change such as '+2.5%', a dash for no change."""
    return icons.icon(SIGN_ARROWS.get(change_shown[:1], "minus"), "delta-icon")


def kpi_card(card: dict, compact: bool = False) -> str:
    """One KPI card from a views.kpi_cards entry (all text already formatted).

    The first comparison is the coloured delta with an arrow; the others are muted
    lines such as '+18.4% vs last year · 96,497.53'.
    """
    first, *others = card["comparisons"]
    delta = (
        f'<div class="card-delta tone-{escape(first["tone"])}">'
        f"{delta_arrow(first['change_shown'])}{escape(first['change_shown'])}"
        f' <span class="vs">vs {escape(short_label(first["label"]))}</span></div>'
    )
    subs = "".join(
        f'<div class="card-sub">{escape(o["change_shown"])} vs {escape(short_label(o["label"]))}'
        f" · {escape(o['value_shown'])}</div>"
        for o in others
    )
    size = " card-compact" if compact else ""
    return (
        f'<div class="card{size}"><div class="card-label">{escape(card["label"])}</div>'
        f'<div class="card-value">{escape(card["value_shown"])}</div>{delta}{subs}</div>'
    )


def card_grid(cards: list[str], compact: bool = False) -> str:
    """Cards in a responsive grid; compact for the secondary KPIs."""
    kind = "card-grid card-grid-compact" if compact else "card-grid"
    return f'<div class="{kind}">{"".join(cards)}</div>'


def user_message(question: str) -> str:
    """The user's question as a right-aligned bubble (escaped, so markup shows as text)."""
    return f'<div class="user-row"><div class="user-bubble">{escape(question)}</div></div>'


def suggestion_card(icon_name: str, title: str, question: str) -> str:
    """An example-question card: an icon in a tinted square, a title and the question."""
    return (
        f'<div class="suggestion"><span class="suggestion-icon">{icons.icon(icon_name)}</span>'
        f'<div class="suggestion-body"><div class="suggestion-title">{escape(title)}</div>'
        f'<div class="suggestion-text">{escape(question)}</div></div></div>'
    )


# ---- Helpers the pages still use until their own redesign step (Week 7 steps 3-5).

TAG_TONES = {"red": "bad", "amber": "warn", "green": "good", "blue": "accent", "grey": "muted"}


def tag(text: str, colour: str = "grey") -> str:
    """A small label in a tone colour."""
    return f'<span class="tag tag-{TAG_TONES.get(colour, "muted")}">{escape(text)}</span>'
