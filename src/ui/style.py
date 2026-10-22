"""Look and feel of the dashboard: the CSS file and small HTML pieces (Week 6 redesign).

The theme colours, font and corner radius are in .streamlit/config.toml. Everything the
theme cannot do (chat bubbles, the pill-shaped input, cards and tags) is in style.css,
loaded once per run from app.py.

The HTML helpers only lay out text that src/reports already formatted; they never do
arithmetic. Every value is escaped.
"""

from html import escape
from pathlib import Path

import streamlit as st

CSS_PATH = Path(__file__).with_name("style.css")
LOGO_PATH = Path(__file__).with_name("logo.svg")  # gradient square with rising bars
BRAND_NAME = "Sales Analyst"
BRAND_TAGLINE = "Proof of concept"
ACCENT = "#2563EB"  # blue, the start of the accent gradient
ACCENT_FILL = "rgba(37,99,235,.12)"  # the accent, faint, for chart fills
GOOD = "#16A34A"
BAD = "#E5484D"
TAG_COLOURS = {
    "red": ("rgba(229,72,77,.14)", BAD),
    "amber": ("rgba(245,158,11,.16)", "#B45309"),
    "green": ("rgba(22,163,74,.14)", GOOD),
    "blue": ("rgba(37,99,235,.14)", ACCENT),
    "grey": ("rgba(127,127,160,.16)", "#6B7085"),
}
ARROWS = {"+": "↑", "-": "↓"}  # up and down arrows; anything else gets none


def inject() -> None:
    """Add the dashboard CSS to the page (once per run, from app.py)."""
    st.html(CSS_PATH)  # a Path to a .css file is wrapped in <style> by Streamlit


def tag(text: str, colour: str = "grey") -> str:
    """A small rounded label."""
    background, ink = TAG_COLOURS.get(colour, TAG_COLOURS["grey"])
    return f'<span class="tag" style="background:{background};color:{ink}">{escape(text)}</span>'


def arrow(change_shown: str) -> str:
    """An up or down arrow for a signed change text such as '+2.5%'."""
    return ARROWS.get(change_shown[:1], "")


def kpi_card(card: dict) -> str:
    """One KPI card from a views.kpi_cards entry (all text already formatted)."""
    first, *others = card["comparisons"]
    delta = (
        f'<div class="card-delta tone-{escape(first["tone"])}">'
        f"{arrow(first['change_shown'])} {escape(first['change_shown'])} vs "
        f"{escape(first['label'])}</div>"
    )
    subs = "".join(
        f'<div class="card-sub">{escape(o["change_shown"])} vs {escape(o["label"])} '
        f"({escape(o['value_shown'])})</div>"
        for o in others
    )
    return (
        f'<div class="card"><div class="card-label">{escape(card["label"])}</div>'
        f'<div class="card-value">{escape(card["value_shown"])}</div>{delta}{subs}</div>'
    )


def card_grid(cards: list[str]) -> str:
    """Cards in a responsive grid."""
    return f'<div class="card-grid">{"".join(cards)}</div>'


def logo_mark() -> str:
    """The logo mark in HTML and CSS: a gradient square with three rising bars.

    st.html strips inline <svg> and SVG data URIs, so the sidebar draws the mark with
    CSS. logo.svg is the same mark as a file, for places that take an image.
    """
    bars = "".join(f'<span class="logo-bar logo-bar-{n}"></span>' for n in (1, 2, 3))
    return f'<div class="logo-mark" aria-hidden="true">{bars}</div>'


def brand_block() -> str:
    """The sidebar header: logo mark, product name and tagline."""
    return (
        f'<div class="brand">{logo_mark()}<div><div class="brand-name">{escape(BRAND_NAME)}</div>'
        f'<div class="brand-tagline">{escape(BRAND_TAGLINE)}</div></div></div>'
    )


def status_chips(data_end_shown: str, model: str | None) -> str:
    """The sidebar footer: a green-dot data-date chip and, if known, the main model chip."""
    chips = [f'<span class="chip"><span class="chip-dot"></span>{escape(data_end_shown)}</span>']
    if model:
        chips.append(f'<span class="chip chip-model">{escape(model)}</span>')
    return f'<div class="sidebar-status">{"".join(chips)}</div>'


def user_message(question: str) -> str:
    """The user's question as a right-aligned gradient bubble next to a slate avatar.

    The question is escaped, so markup in it (a <script> tag, say) shows as text.
    """
    return (
        f'<div class="user-row"><div class="user-bubble">{escape(question)}</div>'
        '<div class="user-avatar" aria-hidden="true"><span class="user-avatar-head"></span>'
        '<span class="user-avatar-body"></span></div></div>'
    )
