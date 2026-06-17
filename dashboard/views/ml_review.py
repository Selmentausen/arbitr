"""ML review page — human review of ML-classified cases."""

import streamlit as st
import plotly.graph_objects as go

from dashboard.app import get_db
from dashboard.components import ML_CATEGORY_LABELS
from dashboard.views._case_detail import _render_ml_case_brief, _render_ml_classification

repo = get_db()

st.title("🤖 ML — Проверка дел")

stats = repo.get_ml_stats()
if stats["total_ml_classified"] == 0:
    st.info("Пока нет дел с ML-классификацией. Запустите: `poetry run classify --limit 50`")
else:
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("С ML-классификацией", stats["total_ml_classified"])
    m2.metric("Проверено вручную", stats["human_reviewed"])
    m3.metric("✅ Верно", stats["by_verdict"]["correct"])
    m4.metric("❌ Неверно", stats["by_verdict"]["wrong"])
    m5.metric("⚠️ Расхождение ML/ключ.", stats["disagreements"])

    if "ml_page" not in st.session_state:
        st.session_state.ml_page = 1

    st.markdown("---")
    st.subheader("Фильтры")

    f1, f2, f3, f4, f5, f6 = st.columns(6)
    with f1:
        human_review = st.selectbox(
            "Проверка ML", ["all", "reviewed", "unreviewed"],
            format_func=lambda x: {"all": "Все", "reviewed": "С проверкой", "unreviewed": "Без проверки"}[x],
            key="ml_filter_human",
        )
    with f2:
        verdict = st.selectbox(
            "Вердикт", [None, "correct", "wrong"],
            format_func=lambda x: {None: "Любой", "correct": "✅ Верно", "wrong": "❌ Неверно"}[x],
            key="ml_filter_verdict",
        )
    with f3:
        ml_cat = st.selectbox(
            "Категория ML", [None, "construction", "intellectual_property", "other"],
            format_func=lambda x: "Все" if x is None else ML_CATEGORY_LABELS.get(x, x),
            key="ml_filter_category",
        )
    with f4:
        uncertainty = st.selectbox(
            "Неопределённость", [None, "low", "medium", "high"],
            format_func=lambda x: "Все" if x is None else x,
            key="ml_filter_uncertainty",
        )
    with f5:
        disagreement_only = st.checkbox("Только расхождения", key="ml_filter_disagree")
    with f6:
        sort_by = st.selectbox(
            "Сортировка", ["ml_analyzed_at", "ml_confidence", "case_number"],
            format_func=lambda x: {"ml_analyzed_at": "По дате ML", "ml_confidence": "По уверенности", "case_number": "По номеру"}[x],
            key="ml_filter_sort",
        )

    page_size = 50
    filter_key = f"{human_review}|{verdict}|{ml_cat}|{uncertainty}|{disagreement_only}|{sort_by}"
    if st.session_state.get("ml_filter_key") != filter_key:
        st.session_state.ml_page = 1
        st.session_state.ml_filter_key = filter_key

    current_page = st.session_state.ml_page
    human_param = None if human_review == "all" else human_review
    if verdict is not None:
        human_param = None

    cases, total = repo.get_ml_cases(
        page=current_page, page_size=page_size,
        human_review=human_param, ml_review_verdict=verdict,
        ml_category=ml_cat, disagreement_only=disagreement_only,
        uncertainty=uncertainty, sort_by=sort_by, sort_desc=True, lite=True,
    )

    if not cases:
        st.warning("Нет дел по выбранным фильтрам.")
    else:
        st.caption(f"Показано {len(cases)} из {total} (страница {current_page})")

        for case in cases:
            ml = case.extracted_data.get("ml_classification") or {}
            review = case.extracted_data.get("ml_review") or {}
            primary = ml.get("primary_category", "—")
            conf = (ml.get("confidence") or 0) * 100
            verdict_label = {"correct": "✅", "wrong": "❌"}.get(review.get("verdict"), "⏳")
            disagree = case.category and primary != "—" and primary != case.category
            header = (
                f"**{case.case_number}** {verdict_label} · "
                f"{case.plaintiff or '?'} vs {case.defendant or '?'} · "
                f"ML: **{ML_CATEGORY_LABELS.get(primary, primary)}** ({conf:.0f}%) · "
                f"Ключ. слова: **{case.category or '—'}**"
            )
            if disagree:
                header += " · ⚠️ расхождение"

            with st.expander(header, expanded=False):
                _render_ml_case_brief(case, case.id)
                _render_ml_classification(repo, case, embedded=True)

        total_pages = max(1, (total + page_size - 1) // page_size)
        st.markdown("---")
        col_prev, col_info, col_next = st.columns([1, 2, 1])
        with col_prev:
            if st.button("◀ Назад", key="ml_page_prev", disabled=(current_page <= 1)):
                st.session_state.ml_page -= 1
                st.rerun()
        with col_info:
            st.markdown(
                f"<div style='text-align:center'><b>Страница {current_page} / {total_pages}</b></div>",
                unsafe_allow_html=True,
            )
        with col_next:
            if st.button("Вперёд ▶", key="ml_page_next", disabled=(current_page >= total_pages)):
                st.session_state.ml_page += 1
                st.rerun()
