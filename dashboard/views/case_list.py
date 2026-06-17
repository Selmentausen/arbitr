"""Case list page with filters and pagination."""

import streamlit as st

from dashboard.app import get_db
from dashboard.components import STATUS_LABELS, ML_CATEGORY_LABELS
from dashboard.views._case_detail import render_case_detail

repo = get_db()

st.title("📋 Список дел")
if "current_page" not in st.session_state:
    st.session_state.current_page = 1

col1, col2, col3, col4 = st.columns(4)

with col1:
    status_filter = st.selectbox(
        "Статус",
        [None, "high_relevant", "uncertain", "insufficient_info", "reject"],
        format_func=lambda x: "Все" if x is None else STATUS_LABELS.get(x, x),
    )
with col2:
    stats = repo.get_stats()
    db_cats = [k for k in stats["by_category"].keys() if k]
    for c in ["construction", "intellectual_property", "other"]:
        if c not in db_cats:
            db_cats.append(c)
    db_cats.sort()
    category_filter = st.selectbox(
        "Категория",
        [None] + db_cats,
        format_func=lambda x: "Все" if x is None else ML_CATEGORY_LABELS.get(x, str(x)).capitalize(),
    )
with col3:
    review_filter = st.selectbox(
        "Проверка",
        [None, True, False],
        format_func=lambda x: "Все" if x is None else ("Проверено" if x else "Не проверено"),
    )
with col4:
    sort_by = st.selectbox(
        "Сортировка",
        ["created_at", "relevance_score", "case_number"],
        format_func=lambda x: {"created_at": "По дате", "relevance_score": "По баллу", "case_number": "По номеру"}.get(x, x),
    )

page_size = 15
current_page = st.session_state.current_page

cases, total = repo.get_all_cases(
    page=current_page, page_size=page_size,
    status=status_filter, category=category_filter,
    reviewed=review_filter, sort_by=sort_by,
    sort_desc=(sort_by != "case_number"),
)

if not cases:
    st.info("Нет дел, соответствующих фильтрам.")
else:
    for case in cases:
        status_label = STATUS_LABELS.get(case.status.value, case.status.value)
        with st.expander(
            f"**{case.case_number}** — {case.plaintiff} vs {case.defendant}  |  "
            f"Балл: **{case.relevance_score:.0f}**  |  {status_label}",
            expanded=False,
        ):
            render_case_detail(repo, case)

    total_pages = max(1, (total + page_size - 1) // page_size)
    st.markdown("---")
    col_prev, col_info, col_next = st.columns([1, 2, 1])
    with col_prev:
        if st.button("Назад", width="stretch", disabled=(current_page <= 1)):
            st.session_state.current_page -= 1
            st.rerun()
    with col_info:
        st.markdown(
            f"<div style='text-align: center'><b>Страница {current_page} из {total_pages}</b>"
            f"<br><small>Дела: {page_size * (current_page - 1)}-{page_size * current_page if current_page < total_pages else total} (Всего: {total})</small></div>",
            unsafe_allow_html=True
        )
    with col_next:
        if st.button("Вперед", width="stretch", disabled=(current_page >= total_pages)):
            st.session_state.current_page += 1
            st.rerun()
