"""Week 7 redesign rules for the dashboard look: one set of colour tokens, no emoji,
one gradient only, none of the old colours, safe CSS, localhost only, and the Reports
page never shows the raw "ok_after_retry" status.

These check files and rendered HTML only; nothing here opens a database except the
Reports page test, which uses the test fixtures from conftest.py.
"""

import csv
import re
import tomllib
from datetime import date
from pathlib import Path

import dotenv
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from src.reports import backfill, builder
from src.ui import common, style

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UI_DIR = PROJECT_ROOT / "src" / "ui"
CSS_PATH = UI_DIR / "style.css"
CONFIG_PATH = PROJECT_ROOT / ".streamlit" / "config.toml"
APP = UI_DIR / "app.py"
AS_OF = date(2026, 9, 30)
# Emoji and pictographs, dingbats, the variation selector and other symbol emoji.
EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿⏩-⏺️‍]")
OLD_COLOURS = ("#2563EB", "#3B82F6", "#06B6D4", "#0B1220")
# Config key -> token, for both themes (the sidebar table is checked separately).
CONFIG_TOKENS = {
    "primaryColor": "accent",
    "backgroundColor": "bg",
    "textColor": "text",
    "borderColor": "border",
    "linkColor": "accent_strong",
}
CSS_PAIR = re.compile(
    r"--(?P<name>[a-z-]+): light-dark\((?P<light>#[0-9A-F]{6}|rgba\([^)]*\)), "
    r"(?P<dark>#[0-9A-F]{6}|rgba\([^)]*\))\);"
)


def ui_text_files() -> list[Path]:
    """Every Python and CSS file of the dashboard."""
    return sorted([*UI_DIR.rglob("*.py"), *UI_DIR.rglob("*.css")])


def test_ui_has_no_emoji() -> None:
    for path in ui_text_files():
        found = EMOJI.findall(path.read_text(encoding="utf-8"))
        assert not found, f"{path.name}: {found}"


def test_only_one_gradient_the_composer_fade() -> None:
    """The fade above the chat input (owner's choice, Week 7) is the only gradient."""
    css = CSS_PATH.read_text(encoding="utf-8")
    assert css.count("linear-gradient") == 1
    rule = css[css.index('stBottomBlockContainer"]::before') :].split("}", 1)[0]
    assert "linear-gradient(to bottom, transparent, var(--bg))" in rule
    for path in UI_DIR.rglob("*.py"):
        assert "linear-gradient" not in path.read_text(encoding="utf-8"), path.name


def test_css_has_none_of_the_old_colours() -> None:
    css = CSS_PATH.read_text(encoding="utf-8").upper()
    for colour in OLD_COLOURS:
        assert colour not in css, colour


def test_css_holds_no_markup() -> None:
    """style.css is loaded with st.html; a '<' could open a tag."""
    assert "<" not in CSS_PATH.read_text(encoding="utf-8")


def test_config_colours_match_the_tokens() -> None:
    theme = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))["theme"]
    for mode in ("light", "dark"):
        tokens = style.TOKENS[mode]
        for key, token in CONFIG_TOKENS.items():
            assert theme[mode][key] == tokens[token], (mode, key)
        assert theme[mode]["sidebar"]["backgroundColor"] == tokens["sidebar"], mode
        assert theme[mode]["chartCategoricalColors"][:2] == [tokens["accent"], tokens["muted"]]
    assert theme["light"]["secondaryBackgroundColor"] == style.TOKENS["light"]["accent_soft"]


def test_css_tokens_match_the_tokens() -> None:
    pairs = {m["name"]: m for m in CSS_PAIR.finditer(CSS_PATH.read_text(encoding="utf-8"))}
    assert set(pairs) == {name.replace("_", "-") for name in style.TOKENS["light"]}
    for name in style.TOKENS["light"]:
        pair = pairs[name.replace("_", "-")]
        assert pair["light"] == style.TOKENS["light"][name], name
        assert pair["dark"] == style.TOKENS["dark"][name], name


def test_dashboard_binds_to_localhost() -> None:
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert config["server"]["address"] == "localhost"


def test_menu_keeps_the_theme_switch() -> None:
    """'viewer' shows the theme switch and hides Deploy; 'minimal' would hide the menu."""
    config = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert config["client"]["toolbarMode"] == "viewer"


# ---------------------------------------------------------------- Reports page

MANIFEST_FIELDS = [
    "run_date", "report", "as_of", "period", "status", "summary_status",
    "summary_retry_reason", "summary_cost_usd", "output_folder", "error",
]  # fmt: skip


@pytest.fixture
def backfill_root(tmp_path: Path) -> Path:
    """A backfill with one ok, one ok_after_retry and one guard_failed report."""
    root = tmp_path / "backfill"
    rows = []
    for day, status in (("13", "ok"), ("14", "ok_after_retry"), ("15", "guard_failed")):
        as_of = f"2026-09-{day}"
        folder = f"2026-09-{int(day) + 1}/daily_sales_snapshot_{as_of}"
        (root / folder).mkdir(parents=True)
        (root / folder / builder.HTML_FILENAME).write_text("<p>Daily</p>", encoding="utf-8")
        rows.append({
            "run_date": f"2026-09-{int(day) + 1}", "report": "daily_sales_snapshot",
            "as_of": as_of, "period": f"Day {day}", "status": "ok",
            "summary_status": status, "summary_retry_reason": "", "summary_cost_usd": "0.0",
            "output_folder": folder, "error": "",
        })  # fmt: skip
    with (root / backfill.MANIFEST_NAME).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return root


def test_reports_page_never_shows_ok_after_retry(
    monkeypatch: pytest.MonkeyPatch, built: dict, backfill_root: Path, tmp_path: Path
) -> None:
    """ok_after_retry shows exactly like ok; only the failed summary gets the badge."""
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("DATA_END_DATE", AS_OF.isoformat())
    st.cache_data.clear()
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.session_state[common.SALES_DB_KEY] = str(built["db_path"])
    at.session_state[common.REPORTS_ROOT_KEY] = str(tmp_path / "none")
    at.session_state[common.BACKFILL_ROOT_KEY] = str(backfill_root)
    at.run().switch_page("app_pages/reports.py").run()
    assert not at.exception, [e.value for e in at.exception]
    html = " ".join(h.proto.body for h in at.get("html"))
    assert "Day 14" in html  # the ok_after_retry report is listed
    assert "ok_after_retry" not in html
    assert html.count("Summary unavailable") == 1  # guard_failed only


# ---------------------------------------------------------------- Week 8 chat limit

WEEK8_RULES = (".chat-turns-left", ".st-key-chat-limit", ".chat-limit-text")


def test_chat_counter_and_limit_notice_use_tokens_only() -> None:
    """The counter line and the limit notice add no colours, emoji or gradients."""
    css = CSS_PATH.read_text(encoding="utf-8")
    for selector in WEEK8_RULES:
        rule = css[css.index(selector + " {") :].split("}", 1)[0]
        assert not re.search(r"#[0-9A-Fa-f]{3,8}\b|rgba?\(|hsla?\(|gradient", rule), selector
        assert "color" not in rule or "var(--" in rule, selector


def test_chat_counter_and_limit_notice_escape_text() -> None:
    from src.ui import chat_session

    for html in (
        style.turns_left(chat_session.turns_left_text([])),
        style.limit_notice(chat_session.LIMIT_TEXT),
    ):
        assert not EMOJI.findall(html)
    assert style.turns_left("<b>hi</b>") == '<p class="chat-turns-left">&lt;b&gt;hi&lt;/b&gt;</p>'
    assert "&lt;b&gt;hi&lt;/b&gt;" in style.limit_notice("<b>hi</b>")
