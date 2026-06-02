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
import os
import subprocess

import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import yaml
from datetime import datetime

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

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
        [
            "⚡ Скрапинг — Live",
            "📊 Обзор",
            "📋 Список дел",
            "🔍 Поиск",
            "🏷️ Категоризация PDF",
            "📥 Экспорт",
        ],
        label_visibility="collapsed",
    )

    if page == "⚡ Скрапинг — Live":
        page_scrape_live(repo)
    elif page == "📊 Обзор":
        page_overview(repo)
    elif page == "📋 Список дел":
        page_case_list(repo)
    elif page == "🔍 Поиск":
        page_search(repo)
    elif page == "🏷️ Категоризация PDF":
        page_pdf_categorization()
    elif page == "📥 Экспорт":
        page_export(repo)


# --- Pages ---


def page_scrape_live(repo: CaseRepository):
    """Live throughput from scrape_events (parallel runner)."""
    st.title("⚡ Скрапинг — Live")

    if st_autorefresh is not None:
        st_autorefresh(interval=5000, key="scrape_live_refresh")

    tp = repo.get_throughput()

    # Row 1: Main cases/hour metrics
    col1, col2, col3 = st.columns(3)
    col1.metric(
        "📈 Дел/ч (общий)",
        f"{tp['cases_per_hour_overall']:.1f}",
        help="Всего дел / часов с момента последнего сброса",
    )
    col2.metric(
        "🔄 Дел/ч (последняя сессия)",
        f"{tp['cases_per_hour_latest_session']:.1f}",
        help=f"Сессия: {tp['latest_session_id']}",
    )
    col3.metric(
        "⏱️ Дел/ч (окно 60 мин)",
        f"{tp['cases_per_hour_60m_window']:.1f}",
        help="Сумма собранных карточек за последний час",
    )

    # Row 2: Supporting stats
    col4, col5, col6 = st.columns(3)
    col4.metric(
        "Дел/ч (экстр. 10 мин)",
        f"{tp['cases_per_hour_10m_extrapolated']:.1f}",
        help="Дел за 10 минут × 6",
    )
    col5.metric("Активные воркеры", tp["active_workers"])
    col6.metric("Судьи (событий за 24ч)", tp["judges_done_24h"])

    # Captions
    reset_label = tp["throughput_reset_at"].strftime("%d.%m.%Y %H:%M") if tp["throughput_reset_at"] else "никогда"
    st.caption(
        f"Общий: {tp['total_cases_overall']} дел за {tp['hours_elapsed_overall']:.1f}ч (сброс: {reset_label}) · "
        f"Сессия: {tp['latest_session_cases']} дел · "
        f"60м: {tp['cases_last_60m']} · 10м: {tp['cases_last_10m']}"
    )

    # Reset button
    if st.button("🔄 Сбросить общий счётчик дел/ч", type="secondary"):
        repo.reset_throughput()
        st.success("Счётчик сброшен!")
        st.rerun()

    if tp.get("by_status_last_hour"):
        st.subheader("Статусы событий (за час)")
        st.json(tp["by_status_last_hour"])

    buckets = repo.get_scrape_case_buckets(hours=6, bucket_minutes=5)
    if buckets:
        st.subheader("Дела по времени (успешные, 5‑мин корзины)")
        fig = px.bar(
            x=[b["bucket_start"] for b in buckets],
            y=[b["cases"] for b in buckets],
            labels={"x": "Время", "y": "Дел"},
        )
        fig.update_layout(height=320, margin=dict(t=20, b=20, l=20, r=20))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Нет завершённых scrape_events с делами за последние 6 часов.")

    st.subheader("Воркеры (последнее событие)")
    snaps = repo.get_worker_snapshots()
    if snaps:
        df = pd.DataFrame(snaps)
        if "started_ago_seconds" in df.columns:
            df["started_ago_seconds"] = df["started_ago_seconds"].round(1)
        st.dataframe(df, width="stretch")
    else:
        st.caption("Нет записей scrape_events.")

    st.subheader("Последние события")
    recent = repo.get_scrape_events_recent(30)
    if not recent:
        st.caption("Запустите: poetry run python scrape_parallel.py")
    else:
        rows = []
        for r in recent:
            dur = None
            if r.started_at and r.finished_at:
                dur = (r.finished_at - r.started_at).total_seconds()
            rows.append(
                {
                    "judge": r.judge_name,
                    "worker": r.worker_id,
                    "port": r.proxy_port,
                    "status": r.status,
                    "cases": r.cases_collected,
                    "duration_s": round(dur, 1) if dur is not None else None,
                    "error": (r.error_message or "")[:80],
                }
            )
        st.dataframe(rows, width="stretch")


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


def page_pdf_categorization():
    """Manage PDF document priorities — view by category, reassign, save."""
    PRIORITIES_PATH = project_root / "configs" / "dictionaries" / "document_priorities.yaml"
    TABS = ["⚪ Без категории", "🔴 Высокий", "🟡 Средний", "🟢 Низкий"]
    TAB_KEYS = ["uncategorized", "high", "medium", "low"]
    PRIORITY_OPTIONS = ["uncategorized", "high", "medium", "low"]
    PRIORITY_DISPLAY = {
        "uncategorized": "⚪ Без категории",
        "high": "🔴 Высокий — скачивать PDF",
        "medium": "🟡 Средний — только URL",
        "low": "🟢 Низкий — только URL",
    }

    # --- Load YAML ---
    try:
        with open(PRIORITIES_PATH, encoding="utf-8") as f:
            priorities_data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        priorities_data = {}
        st.warning(f"Файл приоритетов не найден: {PRIORITIES_PATH}")

    # Build text→current_level map from the YAML (original state on disk)
    text_to_level: dict[str, str] = {}
    for level in ("high", "medium", "low"):
        for item in priorities_data.get(level, []):
            text_to_level[item] = level

    # --- Collect unique doc texts from DB ---
    repo = get_db()
    cases, _ = repo.get_all_cases(page=1, page_size=10000)
    doc_texts: dict[str, int] = {}
    for case in cases:
        for inst in case.instances or []:
            for doc in inst.documents or []:
                text = (doc.filename or "").strip()
                if text:
                    doc_texts[text] = doc_texts.get(text, 0) + 1
            for upd in inst.updates or []:
                if upd.pdf_url and upd.content:
                    text = upd.content.strip()
                    doc_texts[text] = doc_texts.get(text, 0) + 1

    # Also include items from YAML that may not be in the DB yet
    for text in text_to_level:
        if text not in doc_texts:
            doc_texts[text] = 0

    # --- Session state: track edits across all tabs ---
    if "prio_edits" not in st.session_state:
        st.session_state.prio_edits = {}

    def _current_level(text: str) -> str:
        """Get the effective level: edited value > yaml value > uncategorized."""
        if text in st.session_state.prio_edits:
            return st.session_state.prio_edits[text]
        return text_to_level.get(text, "uncategorized")

    # Group texts by their effective level
    grouped: dict[str, list[tuple[str, int]]] = {k: [] for k in TAB_KEYS}
    for text, count in doc_texts.items():
        grouped[_current_level(text)].append((text, count))
    for k in grouped:
        grouped[k].sort(key=lambda x: (-x[1], x[0]))

    # --- Header: title + save button + stats ---
    st.title("🏷️ Категоризация документов PDF")
    st.caption(
        "Выберите категорию, измените приоритеты, затем нажмите «Сохранить». "
        "Изменения влияют на следующие скрапинги."
    )

    hcol1, hcol2, hcol3, hcol4, hcol5 = st.columns([1.5, 1, 1, 1, 1])
    pending = st.session_state.get("prio_edits", {})
    with hcol1:
        save_clicked = st.button(
            f"💾 Сохранить ({len(pending)})" if pending else "💾 Сохранить",
            type="primary",
            disabled=len(pending) == 0,
            use_container_width=True,
        )
    with hcol2:
        st.metric("⚪ Без категории", len(grouped["uncategorized"]))
    with hcol3:
        st.metric("🔴 Высокий", len(grouped["high"]))
    with hcol4:
        st.metric("🟡 Средний", len(grouped["medium"]))
    with hcol5:
        st.metric("🟢 Низкий", len(grouped["low"]))

    # --- Save logic ---
    if save_clicked and pending:
        # Rebuild the yaml structure from scratch based on all effective levels
        new_data = {"high": [], "medium": [], "low": []}
        for text in doc_texts:
            level = _current_level(text)
            if level in new_data:
                new_data[level].append(text)
        for level in new_data:
            new_data[level].sort()

        with open(PRIORITIES_PATH, "w", encoding="utf-8") as f:
            yaml.dump(new_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        st.session_state.prio_edits = {}
        st.success(f"Сохранено! Обновлён {PRIORITIES_PATH.name}")
        st.rerun()

    st.markdown("---")

    # --- Tabs ---
    tabs = st.tabs(TABS)
    for tab, tab_key in zip(tabs, TAB_KEYS):
        with tab:
            items = grouped[tab_key]
            if not items:
                st.info("Пусто.")
                continue

            for text, count in items:
                c1, c2, c3 = st.columns([5, 2, 0.7])
                with c1:
                    edited = text in st.session_state.prio_edits
                    prefix = "✏️ " if edited else ""
                    st.markdown(f"{prefix}**{text}**")
                with c2:
                    new_val = st.selectbox(
                        "Приоритет",
                        PRIORITY_OPTIONS,
                        index=PRIORITY_OPTIONS.index(_current_level(text)),
                        format_func=lambda x: PRIORITY_DISPLAY[x],
                        key=f"cat_{tab_key}_{hash(text)}",
                        label_visibility="collapsed",
                    )
                    original = text_to_level.get(text, "uncategorized")
                    if new_val != original:
                        st.session_state.prio_edits[text] = new_val
                    elif text in st.session_state.prio_edits:
                        del st.session_state.prio_edits[text]
                with c3:
                    st.caption(f"{count}×")


# --- Shared Components ---

PDF_DIR = project_root / "data" / "pdfs"
PRIORITY_BADGES = {"high": "🔴", "medium": "🟡", "low": "🟢", "uncategorized": "⚪"}
PRIORITY_TIPS = {
    "high": "Высокий — PDF скачан",
    "medium": "Средний — только ссылка",
    "low": "Низкий — только ссылка",
    "uncategorized": "Без категории",
}


def _safe_filename_from_url(url: str) -> str:
    """Reproduce the same naming logic as pdf_downloader._safe_filename."""
    import re
    from urllib.parse import unquote, urlparse
    name = unquote(urlparse(url).path.split("/")[-1] or "")
    if not name:
        return ""
    name = re.sub(r'[<>:"|?*\\]', "_", name)
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    return name[:120]


def _find_local_pdf(url: str) -> Path | None:
    """Find a downloaded PDF on disk by matching the URL to the expected filename."""
    if not url or not PDF_DIR.exists():
        return None
    safe = _safe_filename_from_url(url)
    if not safe:
        return None
    candidate = PDF_DIR / (safe + ".pdf")
    if candidate.exists():
        return candidate
    return None


_pdf_link_counter = 0

def _render_pdf_link(url: str, label: str, case_id: str, priority: str | None = None):
    """Render a single PDF entry: priority badge + local download or remote link."""
    global _pdf_link_counter
    _pdf_link_counter += 1
    uid = _pdf_link_counter

    prio = priority or "uncategorized"
    badge = PRIORITY_BADGES.get(prio, "⚪")
    tip = PRIORITY_TIPS.get(prio, "")
    full_url = url if url.startswith("http") else f"https://kad.arbitr.ru{url}"

    local = _find_local_pdf(url)
    if local:
        c1, c2 = st.columns([5, 1.5])
        with c1:
            st.markdown(f"{badge} **{label}**", help=tip)
        with c2:
            if st.button("📄 Открыть PDF", key=f"pdf_{uid}"):
                abs_path = str(local.resolve())
                if sys.platform == "win32":
                    os.startfile(abs_path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", abs_path])
                else:
                    subprocess.Popen(["xdg-open", abs_path])
    else:
        st.markdown(
            f"  {badge} [{label}]({full_url})",
            help=tip,
        )


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
        if case.case_type:
            st.markdown(f"**Тип дела:** {case.case_type}")
        if case.case_status_text:
            st.markdown(f"**Состояние:** {case.case_status_text}")
        if case.case_category_text:
            st.markdown(f"**Категория спора:** {case.case_category_text}")
        if case.claim_amount:
            st.markdown(f"**Сумма иска:** {case.claim_amount:,.2f} ₽")
        if case.extracted_data and case.extracted_data.get("duration"):
            st.markdown(f"**Длительность:** {case.extracted_data.get('duration')}")

    # Judges
    if case.judges:
        st.markdown(f"**Судьи:** {', '.join(case.judges)}")

    # Participants with INN/address
    if case.participants:
        st.markdown("---")
        st.markdown("**Участники:**")
        for role, participants in case.participants.items():
            role_label = {"plaintiff": "Истцы", "defendant": "Ответчики", "third_party": "Третьи лица", "other_party": "Иные лица"}.get(role, role)
            st.markdown(f"**{role_label}:**")
            for p in participants:
                parts = [f"  - {p.name}"]
                if p.inn:
                    parts.append(f"ИНН: {p.inn}")
                if p.address:
                    parts.append(f"Адрес: {p.address}")
                st.markdown(" | ".join(parts))

    # Court instances with update history
    if case.instances:
        st.markdown("---")
        st.markdown("**Инстанции:**")
        for inst in case.instances:
            title_parts = [inst.court_name]
            if inst.instance_level:
                title_parts.append(f"({inst.instance_level})")
            if inst.date:
                title_parts.append(f"— {inst.date}")
            if inst.result_text:
                title_parts.append(f"| {inst.result_text}")
            title = " ".join(title_parts)

            with st.expander(title):
                if inst.case_number:
                    st.markdown(f"**Номер дела:** {inst.case_number}")
                if inst.incoming_number:
                    st.markdown(f"**Входящий номер:** {inst.incoming_number}")

                # Build a URL→priority lookup from the structured documents list
                _doc_priority = {}
                for doc in inst.documents or []:
                    if doc.url:
                        _doc_priority[doc.url] = doc.priority

                if inst.updates:
                    st.markdown("**📜 Хронология:**")
                    for upd in inst.updates:
                        date_str = upd.date or ""
                        type_str = upd.update_type or ""
                        content_str = upd.content or ""
                        line = f"- **{date_str}** {type_str}"
                        if content_str:
                            line += f" — {content_str}"
                        if upd.subject:
                            line += f" _(от: {upd.subject})_"
                        st.markdown(line)

                        if upd.pdf_url:
                            prio = _doc_priority.get(upd.pdf_url)
                            _render_pdf_link(upd.pdf_url, content_str or "Документ", case.id, prio)

                if inst.documents:
                    st.markdown("**📄 Документы:**")
                    for doc in inst.documents:
                        if doc.url:
                            _render_pdf_link(doc.url, doc.filename or "Документ", case.id, doc.priority)

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
