"""Sales analyst dashboard and chat (Week 6).

    streamlit run src/ui/app.py

Pages: Sales, Demand (with pre-build approvals), Chat and Reports. The sidebar is built
here rather than by Streamlit's own menu, so the brand block can sit above the links:
brand, page links, the as_of date picker (every page except Chat, which always answers
from DATA_END_DATE), then status chips with the data end and the main model. The
dashboard never calculates a number itself: it shows what the functions in src/reports,
src/demand and src/agent return. The look comes from .streamlit/config.toml,
src/ui/style.css and src/ui/style.py.
"""

import sys
from pathlib import Path

# streamlit run puts src/ui on the import path, not the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
from dotenv import load_dotenv

from src.agent import llm
from src.ui import common, style

PAGES_DIR = Path(__file__).resolve().with_name("app_pages")
CHAT_TITLE = "Chat"
PAGES = [  # (file, title, icon)
    ("sales.py", "Sales", ":material/monitoring:"),
    ("demand.py", "Demand", ":material/inventory_2:"),
    ("chat.py", CHAT_TITLE, ":material/forum:"),
    ("reports.py", "Reports", ":material/description:"),
]


def main_model() -> str | None:
    """The main model name from the LLM settings (.env), or None when it is not set."""
    try:
        return llm.llm_settings().model  # reads the environment only; no API call
    except llm.LLMConfigError:
        return None


def sidebar(pages: list, page_title: str) -> None:
    """Brand block, page links, the as_of picker and the status chips.

    The picked date is kept in session state rather than in the widget, so it survives
    a visit to the Chat page, which shows no picker.
    """
    default = common.default_as_of(str(common.path(common.SALES_DB_KEY)))
    st.session_state.setdefault(common.AS_OF_KEY, default)
    with st.sidebar:
        st.html(style.brand_block())
        for page in pages:
            # The key becomes a stable CSS class (st-key-nav-active-...), so style.css can
            # draw the current page as a gradient pill.
            state = "active" if page.title == page_title else "idle"
            with st.container(key=f"nav-{state}-{page.url_path or 'home'}"):
                st.page_link(page)
        if page_title != CHAT_TITLE:
            picked = st.date_input(
                "As of", value=st.session_state[common.AS_OF_KEY], max_value=default
            )
            st.session_state[common.AS_OF_KEY] = picked
        st.html(style.status_chips(f"Data up to {default:%d %b %Y}", main_model()))


def main() -> None:
    """Configure the app, then run the selected page."""
    load_dotenv()  # DATA_END_DATE, and the LLM settings for the chat page
    st.set_page_config(page_title="Sales analyst", page_icon=":material/insights:", layout="wide")
    style.inject()
    pages = [
        st.Page(PAGES_DIR / file, title=title, icon=icon, default=index == 0)
        for index, (file, title, icon) in enumerate(PAGES)
    ]
    page = st.navigation(pages, position="hidden")  # the sidebar draws its own links
    sidebar(pages, page.title)
    page.run()


main()
