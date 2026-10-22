"""Look and feel of the dashboard: one CSS block and small HTML pieces (Week 6 redesign).

The theme colours, font and corner radius are in .streamlit/config.toml. This module
adds what the theme cannot: chat bubbles, the pill-shaped input, cards and tags. Colours
are semi-transparent where possible, so the same CSS works in light and dark mode.

The HTML helpers only lay out text that src/reports already formatted; they never do
arithmetic. Every value is escaped.
"""

from html import escape

import streamlit as st

ACCENT = "#4F46E5"
GOOD = "#16A34A"
BAD = "#E5484D"
TAG_COLOURS = {
    "red": ("rgba(229,72,77,.14)", BAD),
    "amber": ("rgba(245,158,11,.16)", "#B45309"),
    "green": ("rgba(22,163,74,.14)", GOOD),
    "indigo": ("rgba(79,70,229,.14)", ACCENT),
    "grey": ("rgba(127,127,160,.16)", "#6B7085"),
}
ARROWS = {"+": "↑", "-": "↓"}  # up and down arrows; anything else gets none

CSS = """
<style>
/* Page: comfortable width, never a horizontal scrollbar. */
[data-testid="stMainBlockContainer"] { max-width: 1180px; padding-top: 2.2rem; }
[data-testid="stAppViewContainer"] { overflow-x: hidden; }
h1 { letter-spacing: -0.02em; }

/* Cards (KPIs, reports) in a grid that wraps on small screens. */
.card-grid { display: grid; gap: .8rem; margin: .4rem 0 1.2rem;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }
.card { border: 1px solid rgba(127,127,160,.22); border-radius: 14px;
  padding: .85rem 1rem; background: rgba(127,127,160,.05); min-width: 0; }
.card-label { font-size: .8rem; opacity: .72; text-transform: uppercase;
  letter-spacing: .04em; margin-bottom: .2rem; }
.card-value { font-size: 1.55rem; font-weight: 700; line-height: 1.25;
  overflow-wrap: anywhere; }
.card-delta { font-size: .86rem; font-weight: 600; margin-top: .3rem; }
.card-sub { font-size: .78rem; opacity: .7; margin-top: .15rem; }
.tone-good { color: #16A34A; } .tone-bad { color: #E5484D; } .tone-flat { opacity: .7; }

/* Tags */
.tag { display: inline-block; padding: .1rem .55rem; border-radius: 999px;
  font-size: .75rem; font-weight: 600; white-space: nowrap; }

/* Chat: assistant on a subtle card, user right-aligned in an accent bubble. */
[data-testid="stChatMessage"] { background: transparent; padding: .35rem 0; gap: .65rem; }
[data-testid="stChatMessageContent"] { background: rgba(127,127,160,.07);
  border: 1px solid rgba(127,127,160,.18); border-radius: 18px 18px 18px 6px;
  padding: .7rem 1rem; min-width: 0; overflow-wrap: anywhere; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  flex-direction: row-reverse; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] {
  background: #4F46E5; border-color: #4F46E5; color: #FFFFFF;
  border-radius: 18px 18px 6px 18px; flex: 0 1 auto; max-width: min(78%, 620px);
  margin-left: auto; }
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] * { color: #FFFFFF; }
[data-testid="stChatMessageContent"] table { border-collapse: collapse; font-size: .9rem;
  display: block; max-width: 100%; overflow-x: auto; margin: .4rem 0; }
[data-testid="stChatMessageContent"] th, [data-testid="stChatMessageContent"] td {
  padding: .35rem .7rem; border-bottom: 1px solid rgba(127,127,160,.25); }
[data-testid="stChatMessageContent"] th { background: rgba(79,70,229,.08); }
.chat-meta { font-size: .75rem; opacity: .6; margin-top: .35rem; }
.chat-welcome { font-size: 1.05rem; opacity: .8; margin: .6rem 0 .8rem; }

/* Live progress steps */
.step { display: flex; align-items: center; gap: .5rem; font-size: .9rem; padding: .12rem 0; }
.step-time { opacity: .6; font-size: .8rem; margin-left: auto; }
.step-ok { color: #16A34A; } .step-fail { color: #E5484D; }
.spinner { width: .85rem; height: .85rem; border-radius: 50%; flex: none;
  border: 2px solid rgba(127,127,160,.3); border-top-color: #4F46E5;
  animation: spin .8s linear infinite; }
@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }

/* Pill-shaped chat input, fixed at the bottom by Streamlit. */
[data-testid="stChatInput"] { border-radius: 999px; padding-left: .4rem;
  box-shadow: 0 2px 10px rgba(20,20,60,.08); }
[data-testid="stChatInputSubmitButton"] { border-radius: 999px; }

@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; }
  .card-value { font-size: 1.3rem; }
  [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
    [data-testid="stChatMessageContent"] { max-width: 88%; }
}
</style>
"""


def inject() -> None:
    """Add the dashboard CSS to the page (once per run, from app.py)."""
    st.html(CSS)


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


def step_line(icon: str, label: str, seconds_shown: str = "", state: str = "running") -> str:
    """One progress step: a spinner while running, then a tick or a cross."""
    mark = {
        "running": '<span class="spinner"></span>',
        "ok": '<span class="step-ok">✔</span>',
        "fail": '<span class="step-fail">✖</span>',
        "note": '<span class="step-fail">!</span>',
    }[state]
    time_text = f'<span class="step-time">{escape(seconds_shown)}</span>' if seconds_shown else ""
    return f'<div class="step">{mark}<span>{escape(icon)}</span><span>{escape(label)}</span>{time_text}</div>'
