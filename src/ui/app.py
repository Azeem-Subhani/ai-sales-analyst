"""Sales analyst dashboard and chat (Week 6).

    streamlit run src/ui/app.py

Pages: Sales, Demand (with pre-build approvals), Chat and Reports. The sidebar date
picker sets the as_of every page uses, except Chat: the agent always answers from
DATA_END_DATE, so that page shows the data end instead of a picker. The dashboard never
calculates a number itself: it shows what the functions in src/reports, src/demand and
src/agent return. The look comes from .streamlit/config.toml and src/ui/style.py.
"""

import sys
from pathlib import Path

# streamlit run puts src/ui on the import path, not the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from dotenv import load_dotenv

from src.ui import common, style

PAGES_DIR = Path(__file__).resolve().with_name("app_pages")
CHAT_TITLE = "Chat"
PAGES = [  # (file, title, icon)
    ("sales.py", "Sales", ":material/monitoring:"),
    ("demand.py", "Demand", ":material/inventory_2:"),
    ("chat.py", CHAT_TITLE, ":material/forum:"),
    ("reports.py", "Reports", ":material/description:"),
]


def sidebar_as_of(page_title: str) -> None:
    """The shared as_of date picker (default DATA_END_DATE), or the chat's data end.

    The picked date is kept in session state rather than in the widget, so it survives
    a visit to the Chat page, which shows no picker.
    """
    default = common.default_as_of(str(common.path(common.SALES_DB_KEY)))
    st.session_state.setdefault(common.AS_OF_KEY, default)
    if page_title == CHAT_TITLE:
        st.sidebar.markdown(f":material/event: Answers use data up to **{default:%d %b %Y}**.")
        return
    picked = st.sidebar.date_input(
        "As of", value=st.session_state[common.AS_OF_KEY], max_value=default
    )
    st.session_state[common.AS_OF_KEY] = picked
    st.sidebar.caption(f"Data ends {default:%d %b %Y}.")


def main() -> None:
    """Configure the app, then run the selected page."""
    load_dotenv()  # DATA_END_DATE, and the LLM settings for the chat page
    st.set_page_config(page_title="Sales analyst", page_icon=":material/insights:", layout="wide")
    style.inject()
    pages = [
        st.Page(PAGES_DIR / file, title=title, icon=icon, default=index == 0)
        for index, (file, title, icon) in enumerate(PAGES)
    ]
    page = st.navigation(pages)
    sidebar_as_of(page.title)
    page.run()


main()
