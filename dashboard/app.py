"""
Arbitr Dashboard — Streamlit app for reviewing scraped court cases.

Run with:
    poetry run dashboard
"""

import streamlit as st

from src.cli.constants import DB_PATH
from src.storage.database import init_db
from src.storage.repository import CaseRepository


@st.cache_resource
def get_db():
    """Initialize DB and return a repository."""
    init_db(DB_PATH)
    return CaseRepository()


# --- Programmatic Navigation ---

# Configure modern multi-page routing with Streamlit's official API
scrape_page = st.Page("views/scrape_live.py", title="Скрапинг — Live", icon="⚡")
overview_page = st.Page("views/overview.py", title="Обзор", icon="📊")
ml_page = st.Page("views/ml_review.py", title="ML — Проверка", icon="🤖")
list_page = st.Page("views/case_list.py", title="Список дел", icon="📋")
search_page = st.Page("views/search.py", title="Поиск", icon="🔍")
pdf_page = st.Page("views/pdf_categorization.py", title="Категоризация PDF", icon="🏷️")
export_page = st.Page("views/export.py", title="Экспорт", icon="📥")

pg = st.navigation(
    [
        scrape_page,
        overview_page,
        ml_page,
        list_page,
        search_page,
        pdf_page,
        export_page,
    ]
)

# Set page config for the general app wrapper
st.set_page_config(
    page_title="Arbitr — Обзор дел",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Run page execution
pg.run()
