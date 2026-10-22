"""Look and feel of the dashboard: colour tokens, the CSS file and small HTML pieces.

Week 7 redesign, built to the owner's HTML chat mock-up: a soft green-grey page, white
surface cards with gentle shadows, a deep emerald accent, pill badges and rounded
inputs. TOKENS is the one list of colours; the same values are in .streamlit/config.toml
(Streamlit's own widgets and the charts) and in style.css (as light-dark() pairs, which
follow the theme picked in the app menu without a rerun). tests/test_ui.py checks that
the three agree.

The HTML helpers only lay out text that src/reports already formatted; they never do
arithmetic. Every value is escaped.
"""

from html import escape
from pathlib import Path

import streamlit as st

from src.ui import icons

CSS_PATH = Path(__file__).with_name("style.css")
LOGO_PATH = Path(__file__).with_name("logo.svg")  # the brand mark as a file (favicon)
BRAND_NAME = "Sales Analyst"
# Colour tokens, light and dark, from the owner's HTML. accent: fills, the chart main
# series, focus rings; accent_strong: hover and text on accent_soft; on_accent: text on
# accent fills. good, bad and warn are separate from the accent, so a positive delta does
# not look like brand colour (it always carries an arrow too).
TOKENS = {
    "light": {
        "bg": "#EFF3F1",
        "sidebar": "#FFFFFF",
        "surface": "#FFFFFF",
        "text": "#0F1A15",
        "muted": "#5B6862",
        "border": "rgba(15, 26, 21, 0.09)",
        "accent": "#0B7A54",
        "accent_strong": "#095F42",
        "accent_soft": "#E3F5EC",
        "on_accent": "#FFFFFF",
        "good": "#166534",
        "bad": "#B91C1C",
        "warn": "#B45309",
    },
    "dark": {
        "bg": "#0E1411",
        "sidebar": "#161E1A",
        "surface": "#161E1A",
        "text": "#EEF4F0",
        "muted": "#9AA9A1",
        "border": "rgba(255, 255, 255, 0.09)",
        "accent": "#3DD69B",
        "accent_strong": "#62E3B2",
        "accent_soft": "rgba(61, 214, 155, 0.14)",
        "on_accent": "#04140D",
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
    ".st-key-clear-chat button": "trash-2",
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


def status_pill(text: str) -> str:
    """A rounded status badge with an accent dot, e.g. 'Data through 30 Sep 2026'."""
    return f'<span class="status-pill">{escape(text)}</span>'


def trust_pill(text: str) -> str:
    """The accent-tinted pill with a shield icon under the chat heading."""
    return f'<div class="trust-pill">{icons.icon("shield-check")}{escape(text)}</div>'


def page_header(title: str, description: str = "", page_class: str = "") -> str:
    """The page title (no icon, no anchor link) and a one-line muted description.

    page_class is an extra class on the header, for page-wide CSS (e.g. 'chat-page').
    """
    text = f'<p class="page-description">{escape(description)}</p>' if description else ""
    classes = f"page-header {escape(page_class)}".strip()
    return f'<div class="{classes}"><h1 class="page-title">{escape(title)}</h1>{text}</div>'


def section_title(text: str, count: object = None, note: str = "") -> str:
    """A section title with an optional muted count and a muted note after it."""
    extra = f' <span class="section-count">{escape(str(count))}</span>' if count is not None else ""
    tail = f' <span class="section-note">\u00b7 {escape(note)}</span>' if note else ""
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
    """An example-question card: an icon tile, a title, the question and a hover arrow."""
    return (
        f'<div class="suggestion"><span class="suggestion-icon">{icons.icon(icon_name)}</span>'
        f'<div class="suggestion-body"><div class="suggestion-title">{escape(title)}</div>'
        f'<div class="suggestion-text">{escape(question)}</div></div>'
        f"{icons.icon('arrow-right', 'suggestion-go')}</div>"
    )


TAG_TONES = {"red": "bad", "amber": "warn", "green": "good", "blue": "accent", "grey": "muted"}


def tag(text: str, colour: str = "grey") -> str:
    """A small pill label tinted in a tone colour."""
    return f'<span class="tag tag-{TAG_TONES.get(colour, "muted")}">{escape(text)}</span>'
