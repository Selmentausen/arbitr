"""Live scraping throughput dashboard page."""

import os

import pandas as pd
import requests
import streamlit as st
import plotly.express as px

from dashboard.app import get_db

try:
    from streamlit_autorefresh import st_autorefresh
except ImportError:
    st_autorefresh = None

repo = get_db()

# --- Orchestrator API helpers (localhost) ---
_ORCH_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8000")
_API_KEY = os.environ.get("API_KEY", "dev-key-change-me")
_HEADERS = {"Authorization": f"Bearer {_API_KEY}"}


def _get_fleet_status() -> bool:
    """Returns True if scraping is paused."""
    try:
        r = requests.get(f"{_ORCH_URL}/api/fleet/status", headers=_HEADERS, timeout=3)
        if r.ok:
            return r.json().get("data", {}).get("scraping_paused", True)
    except Exception:
        pass
    return False


def _set_fleet_pause(pause: bool) -> str:
    """Pause or resume the fleet. Returns response message."""
    endpoint = "pause" if pause else "resume"
    try:
        r = requests.post(f"{_ORCH_URL}/api/fleet/{endpoint}", headers=_HEADERS, timeout=5)
        if r.ok:
            return r.json().get("message", "OK")
        return f"Error: {r.status_code}"
    except Exception as e:
        return f"Connection error: {e}"


st.title("⚡ Скрапинг — Live")

if st_autorefresh is not None:
    st_autorefresh(interval=5000, key="scrape_live_refresh")

# --- Pause / Resume control ---
is_paused = _get_fleet_status()

if is_paused:
    st.error("🔴 **СКРАПИНГ ПРИОСТАНОВЛЕН** — воркеры не берут новые задания.")
    if st.button("▶️ Возобновить скрапинг", type="primary", key="resume_btn"):
        msg = _set_fleet_pause(False)
        st.success(msg)
        st.rerun()
else:
    if st.button("⏸️ Приостановить скрапинг", type="secondary", key="pause_btn"):
        msg = _set_fleet_pause(True)
        st.warning(msg)
        st.rerun()

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

# Reset buttons
rcol1, rcol2 = st.columns(2)
with rcol1:
    if st.button("🔄 Сбросить общий счётчик дел/ч", type="secondary"):
        repo.reset_throughput()
        st.success("Счётчик сброшен!")
        st.rerun()
with rcol2:
    if st.button("🗑️ Сбросить прогресс судей", type="secondary"):
        cleared = repo.reset_judge_progress()
        st.success(f"Прогресс сброшен ({cleared} записей). Следующий запуск начнёт с нуля.")
        st.rerun()

# Judge progress table
all_progress = repo.get_all_judge_progress()
if all_progress:
    st.subheader("Прогресс по судьям")
    status_icons = {
        "completed": "✅",
        "collecting": "📥",
        "enriching": "🔄",
        "failed": "❌",
        "pending": "⏳",
    }
    prog_rows = []
    for p in all_progress:
        prog_rows.append({
            "Судья": p.judge_name,
            "Статус": f"{status_icons.get(p.status, '?')} {p.status}",
            "Собрано": p.cases_collected,
            "Макс.": p.max_cases,
            "Всего на сайте": p.total_count_at_start,
            "Ошибка": (p.error_message or "")[:60],
            "Обновлено": p.updated_at,
        })
    st.dataframe(prog_rows, width="stretch")

    # Per-judge controls
    judge_names = [p.judge_name for p in all_progress]
    c1, c2, c3, c4 = st.columns([3, 1.5, 1, 1])
    with c1:
        selected_judge = st.selectbox(
            "Судья",
            judge_names,
            label_visibility="collapsed",
            key="judge_ctrl_select",
        )
    with c2:
        new_status = st.selectbox(
            "Новый статус",
            ["collecting", "enriching", "failed", "pending"],
            format_func=lambda x: f"{status_icons.get(x, '?')} {x}",
            label_visibility="collapsed",
            key="judge_ctrl_status",
        )
    with c3:
        if st.button("✏️ Изменить статус", key="change_judge_status"):
            repo.upsert_judge_progress(selected_judge, status=new_status, error_message="")
            st.success(f"Статус «{selected_judge}» → {new_status}")
            st.rerun()
    with c4:
        if st.button("🗑️ Сбросить", key="reset_single_judge"):
            repo.reset_judge_progress(judge_name=selected_judge)
            st.success(f"Судья «{selected_judge}» сброшен.")
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
    st.caption("Запустите: poetry run scrape-parallel")
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
