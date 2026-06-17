"""Export page."""

from datetime import datetime

import streamlit as st

from dashboard.app import get_db
from dashboard.components import STATUS_LABELS

repo = get_db()

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
