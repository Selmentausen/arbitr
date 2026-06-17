"""Search page."""

import streamlit as st

from dashboard.app import get_db
from dashboard.components import STATUS_LABELS
from dashboard.views._case_detail import render_case_detail

repo = get_db()

st.title("🔍 Поиск")

query = st.text_input("Поиск по истцу, ответчику, номеру дела или суду", "")

if query:
    results = repo.search_cases(query)
    st.caption(f"Найдено: {len(results)} дел")

    for case in results:
        status_label = STATUS_LABELS.get(case.status.value, case.status.value)
        with st.expander(
            f"**{case.case_number}** — {case.plaintiff} vs {case.defendant}  |  {status_label}",
            expanded=False,
        ):
            render_case_detail(repo, case)
else:
    st.info("Введите запрос для поиска.")
