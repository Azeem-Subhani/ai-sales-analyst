"""Sales analyst dashboard and chat (Week 6).

    streamlit run src/ui/app.py

Pages: Sales, Demand (with pre-build approvals), Chat and Reports. The sidebar date
picker sets the as_of every page uses. The dashboard never calculates a number itself:
it shows what the functions in src/reports, src/demand and src/agent return.
"""

import sys
from pathlib import Path

# streamlit run puts src/ui on the import path, not the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from dotenv import load_dotenv

from src.ui import common

PAGES_DIR = Path(__file__).resolve().with_name("app_pages")


def sidebar_as_of() -> None:
    """The shared as_of date picker (default DATA_END_DATE)."""
    default = common.default_as_of(str(common.path(common.SALES_DB_KEY)))
    st.session_state.setdefault(common.AS_OF_KEY, default)
    st.sidebar.date_input("As of", key=common.AS_OF_KEY, max_value=default)
    st.sidebar.caption(f"Data ends {default:%d %b %Y}.")


def main() -> None:
    """Configure the app, then run the selected page."""
    load_dotenv()  # DATA_END_DATE, and the LLM settings for the chat page
    st.set_page_config(page_title="Sales analyst", layout="wide")
    pages = [
        st.Page(PAGES_DIR / "sales.py", title="Sales", default=True),
        st.Page(PAGES_DIR / "demand.py", title="Demand"),
        st.Page(PAGES_DIR / "chat.py", title="Chat"),
        st.Page(PAGES_DIR / "reports.py", title="Reports"),
    ]
    page = st.navigation(pages)
    sidebar_as_of()
    page.run()


main()
