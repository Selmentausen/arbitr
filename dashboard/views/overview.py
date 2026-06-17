"""Overview / stats dashboard page."""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go

from dashboard.app import get_db
from dashboard.components import STATUS_COLORS, STATUS_LABELS

repo = get_db()

st.title("📊 Обзор")

stats = repo.get_stats()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Всего дел", stats["total_cases"])
col2.metric("Проверено", stats["reviewed"])
col3.metric("Не проверено", stats["not_reviewed"])
col4.metric("Средний балл", f"{stats['avg_relevance_score']:.1f}")

if stats["total_cases"] == 0:
    st.info(
        "Пока нет данных. Запустите скрапер:\n\n"
        "```bash\npoetry run scrape --judge \"Титова Е. В.\" --max-cases 25\n```"
    )
else:
    st.markdown("---")
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("По статусу")
        if stats["by_status"]:
            labels = [STATUS_LABELS.get(k, k) for k in stats["by_status"].keys()]
            values = list(stats["by_status"].values())
            colors = [STATUS_COLORS.get(k, "#888") for k in stats["by_status"].keys()]
            fig = go.Figure(data=[go.Pie(
                labels=labels, values=values,
                marker_colors=colors, textinfo="label+value", hole=0.4,
            )])
            fig.update_layout(height=350, margin=dict(t=20, b=20, l=20, r=20))
            st.plotly_chart(fig, width="stretch")

    with col_right:
        st.subheader("По категории")
        if stats["by_category"]:
            cats = {k or "Без категории": v for k, v in stats["by_category"].items()}
            fig = px.bar(
                x=list(cats.keys()), y=list(cats.values()),
                labels={"x": "Категория", "y": "Количество"}, color=list(cats.keys()),
            )
            fig.update_layout(height=350, margin=dict(t=20, b=20, l=20, r=20), showlegend=False)
            st.plotly_chart(fig, width="stretch")
