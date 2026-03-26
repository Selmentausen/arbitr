"""
Arbitr Dashboard — Streamlit app for reviewing scraped court cases.

Run with:
    cd d:\\dev\\2026\\Arbitr
    poetry run streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

# Add project root to path so we can import src modules
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import json
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime

from src.storage.database import init_db
from src.storage.repository import CaseRepository
from src.models.case import StatusEnum


# --- Configuration ---

DB_PATH = str(project_root / "data" / "arbitr.db")

STATUS_COLORS = {
    "high_relevant": "#22c55e",
    "uncertain": "#f59e0b",
    "insufficient_info": "#6b7280",
    "reject": "#ef4444",
}

STATUS_LABELS = {
    "high_relevant": "✅ Высокая релевантность",
    "uncertain": "⚠️ Неопределенный",
    "insufficient_info": "ℹ️ Недостаточно информации",
    "reject": "❌ Отклонён",
}


# --- App Setup ---

st.set_page_config(
    page_title="Arbitr — Обзор дел",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def get_db():
    """Initialize DB and return a repository."""
    init_db(DB_PATH)
    return CaseRepository()


def main():
    """Main app entry point."""
    repo = get_db()

    # Sidebar navigation
    st.sidebar.title("⚖️ Arbitr")
    st.sidebar.markdown("---")
    page = st.sidebar.radio(
        "Навигация",
        ["📊 Обзор", "📋 Список дел", "🔍 Поиск", "📥 Экспорт"],
        label_visibility="collapsed",
    )

    if page == "📊 Обзор":
        page_overview(repo)
    elif page == "📋 Список дел":
        page_case_list(repo)
    elif page == "🔍 Поиск":
        page_search(repo)
    elif page == "📥 Экспорт":
        page_export(repo)


# --- Pages ---

def page_overview(repo: CaseRepository):
    """Overview / stats dashboard."""
    st.title("📊 Обзор")

    stats = repo.get_stats()

    # Top metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Всего дел", stats["total_cases"])
    col2.metric("Проверено", stats["reviewed"])
    col3.metric("Не проверено", stats["not_reviewed"])
    col4.metric("Средний балл", f"{stats['avg_relevance_score']:.1f}")

    if stats["total_cases"] == 0:
        st.info(
            "Пока нет данных. Запустите скрапер для сбора дел:\n\n"
            "```bash\npoetry run python main.py --judge-name \"Солдатов Р. С.\" --max-cases 25\n```"
        )
        return

    st.markdown("---")

    # Charts
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("По статусу")
        if stats["by_status"]:
            labels = [STATUS_LABELS.get(k, k) for k in stats["by_status"].keys()]
            values = list(stats["by_status"].values())
            colors = [STATUS_COLORS.get(k, "#888") for k in stats["by_status"].keys()]
            fig = go.Figure(data=[go.Pie(
                labels=labels, values=values,
                marker_colors=colors,
                textinfo="label+value",
                hole=0.4,
            )])
            fig.update_layout(height=350, margin=dict(t=20, b=20, l=20, r=20))
            st.plotly_chart(fig, width="stretch")

    with col_right:
        st.subheader("По категории")
        if stats["by_category"]:
            cats = {k or "Без категории": v for k, v in stats["by_category"].items()}
            fig = px.bar(
                x=list(cats.keys()),
                y=list(cats.values()),
                labels={"x": "Категория", "y": "Количество"},
                color=list(cats.keys()),
            )
            fig.update_layout(height=350, margin=dict(t=20, b=20, l=20, r=20), showlegend=False)
            st.plotly_chart(fig, width="stretch")


def page_case_list(repo: CaseRepository):
    """Case list with filters and pagination."""
    st.title("📋 Список дел")
    if "current_page" not in st.session_state:
        st.session_state.current_page = 1

    # Filters
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        status_filter = st.selectbox(
            "Статус",
            [None, "high_relevant", "uncertain", "insufficient_info", "reject"],
            format_func=lambda x: "Все" if x is None else STATUS_LABELS.get(x, x),
        )
    with col2:
        category_filter = st.selectbox(
            "Категория",
            [None, "construction", "bankruptcy"],
            format_func=lambda x: "Все" if x is None else x.capitalize(),
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

    # Pagination
    page_size = 15
    current_page = st.session_state.current_page

    cases, total = repo.get_all_cases(
        page=current_page,
        page_size=page_size,
        status=status_filter,
        category=category_filter,
        reviewed=review_filter,
        sort_by=sort_by,
        sort_desc=(sort_by != "case_number"),
    )


    if not cases:
        st.info("Нет дел, соответствующих фильтрам.")
        return

    # Display cases
    for case in cases:
        status_label = STATUS_LABELS.get(case.status.value, case.status.value)
        score_color = STATUS_COLORS.get(case.status.value, "#888")

        with st.expander(
            f"**{case.case_number}** — {case.plaintiff} vs {case.defendant}  |  "
            f"Балл: **{case.relevance_score:.0f}**  |  {status_label}",
            expanded=False,
        ):
            _render_case_detail(repo, case)
    
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


def page_search(repo: CaseRepository):
    """Search page."""
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
                _render_case_detail(repo, case)
    else:
        st.info("Введите запрос для поиска.")


def page_export(repo: CaseRepository):
    """Export page."""
    st.title("📥 Экспорт данных")

    col1, col2 = st.columns(2)

    with col1:
        export_format = st.selectbox("Формат", ["json", "csv"])
    with col2:
        export_status = st.selectbox(
            "Фильтр по статусу",
            [None, "high_relevant", "uncertain", "insufficient_info", "reject"],
            format_func=lambda x: "Все" if x is None else STATUS_LABELS.get(x, x),
        )

    if st.button("📥 Сгенерировать файл", type="primary"):
        data = repo.export_cases(format=export_format, status=export_status)

        mime = "application/json" if export_format == "json" else "text/csv"
        ext = export_format
        filename = f"arbitr_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"

        st.download_button(
            label=f"⬇️ Скачать {filename}",
            data=data.encode("utf-8"),
            file_name=filename,
            mime=mime,
        )

        st.success(f"Файл готов к скачиванию ({len(data)} байт)")


# --- Shared Components ---

def _render_case_detail(repo: CaseRepository, case):
    """Render detailed case info inside an expander."""
    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown(f"**Суд:** {case.court}")
        st.markdown(f"**Истец:** {case.plaintiff}")
        st.markdown(f"**Ответчик:** {case.defendant}")

        if case.filing_date:
            st.markdown(f"**Дата подачи:** {case.filing_date.strftime('%d.%m.%Y')}")

        if case.case_url:
            st.markdown(f"[🔗 Открыть на kad.arbitr.ru]({case.case_url})")

    with col2:
        st.markdown(f"**Категория:** {case.category or '—'}")
        st.markdown(f"**Балл:** {case.relevance_score:.1f}")
        st.markdown(f"**Статус:** {STATUS_LABELS.get(case.status.value, case.status.value)}")

    # Judges
    if case.judges:
        st.markdown(f"**Судьи:** {', '.join(case.judges)}")

    # Participants
    if case.participants:
        st.markdown("---")
        st.markdown("**Участники:**")
        for role, participants in case.participants.items():
            role_label = {"plaintiffs": "Истцы", "defendants": "Ответчики", "third_parties": "Третьи лица", "others": "Другие"}.get(role, role)
            names = ", ".join([p.name for p in participants])
            st.markdown(f"- **{role_label}:** {names}")

    # Court instances
    if case.instances:
        st.markdown("---")
        st.markdown("**Инстанции:**")
        for inst in case.instances:
            inst_line = f"- {inst.court_name}"
            if inst.case_number:
                inst_line += f" ({inst.case_number})"
            if inst.date:
                inst_line += f" — {inst.date}"
            st.markdown(inst_line)

    # Extracted data
    if case.extracted_data:
        with st.popover("📄 Извлеченные данные"):
            st.json(case.extracted_data)

    # Review section
    st.markdown("---")
    review_col1, review_col2 = st.columns([1, 3])

    with review_col1:
        # Use case.id as key to make checkboxes unique
        reviewed = st.checkbox(
            "✅ Проверено",
            value=False,  # We don't track this in Case model yet
            key=f"review_{case.id}",
        )

    with review_col2:
        notes = st.text_input(
            "Заметки",
            value="",
            key=f"notes_{case.id}",
            placeholder="Добавить заметку...",
        )

    if st.button("💾 Сохранить", key=f"save_{case.id}"):
        repo.mark_reviewed(case.id, reviewed=reviewed, notes=notes if notes else None)
        st.success("Сохранено!")
        st.rerun()


if __name__ == "__main__":
    main()
