"""PDF document priority categorization page."""

from datetime import datetime
from pathlib import Path

import yaml
import streamlit as st

from dashboard.app import get_db
from dashboard.components import PROJECT_ROOT

repo = get_db()

PRIORITIES_PATH = PROJECT_ROOT / "configs" / "dictionaries" / "document_priorities.yaml"
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

text_to_level: dict[str, str] = {}
for level in ("high", "medium", "low"):
    for item in priorities_data.get(level, []):
        text_to_level[item] = level

# --- Collect unique doc texts from DB ---
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

for text in text_to_level:
    if text not in doc_texts:
        doc_texts[text] = 0

# --- Session state: track edits ---
if "prio_edits" not in st.session_state:
    st.session_state.prio_edits = {}


def _current_level(text: str) -> str:
    if text in st.session_state.prio_edits:
        return st.session_state.prio_edits[text]
    return text_to_level.get(text, "uncategorized")


grouped: dict[str, list[tuple[str, int]]] = {k: [] for k in TAB_KEYS}
for text, count in doc_texts.items():
    grouped[_current_level(text)].append((text, count))
for k in grouped:
    grouped[k].sort(key=lambda x: (-x[1], x[0]))

# --- Header ---
st.title("🏷️ Категоризация документов PDF")
st.caption(
    "Выберите категорию, измените приоритеты, затем нажмите «Сохранить». "
    "Изменения влияют на следующие скрапинги."
)

hcol1, hcol2, hcol3, hcol4, hcol5, hcol6 = st.columns([1.5, 1.5, 1, 1, 1, 1])
pending = st.session_state.get("prio_edits", {})
with hcol1:
    save_clicked = st.button(
        f"💾 Сохранить ({len(pending)})" if pending else "💾 Сохранить",
        type="primary", disabled=len(pending) == 0, width="stretch",
    )
with hcol2:
    section_labels = {
        "high": "ВЫСОКИЙ ПРИОРИТЕТ (скачивать PDF)",
        "medium": "СРЕДНИЙ ПРИОРИТЕТ (только URL)",
        "low": "НИЗКИЙ ПРИОРИТЕТ (только URL)",
        "uncategorized": "БЕЗ КАТЕГОРИИ",
    }
    lines = []
    for key in TAB_KEYS:
        items = grouped[key]
        lines.append(f"{'=' * 50}")
        lines.append(f"  {section_labels[key]}  ({len(items)} шт.)")
        lines.append(f"{'=' * 50}")
        if items:
            for text, count in items:
                lines.append(f"  {text}  —  {count}×")
        else:
            lines.append("  (пусто)")
        lines.append("")
    export_text = "\n".join(lines)
    st.download_button(
        "📥 Экспорт (.txt)",
        data=export_text.encode("utf-8"),
        file_name=f"pdf_categories_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        mime="text/plain", width="stretch",
    )
with hcol3:
    st.metric("⚪ Без категории", len(grouped["uncategorized"]))
with hcol4:
    st.metric("🔴 Высокий", len(grouped["high"]))
with hcol5:
    st.metric("🟡 Средний", len(grouped["medium"]))
with hcol6:
    st.metric("🟢 Низкий", len(grouped["low"]))

# --- Save logic ---
if save_clicked and pending:
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
                    "Приоритет", PRIORITY_OPTIONS,
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
