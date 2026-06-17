"""Shared case detail renderer — used by case_list and search pages."""

from pathlib import Path

import streamlit as st
import plotly.graph_objects as go

from src.storage.repository import CaseRepository
from src.analysis.classifier import (
    apply_classification_to_case,
    build_prompt_audit,
    classify_case,
    prepare_case_for_classification,
)
from src.config.classification import ClassificationConfig
from src.config.manager import ConfigManager
from dashboard.components import (
    STATUS_LABELS, ML_CATEGORY_LABELS,
    CLASSIFICATION_CONFIG_PATH, PDF_DIR,
    collect_case_pdfs, render_pdf_link,
)


def _render_ml_case_brief(case, case_id: str):
    """Compact case facts for ML review."""
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Истец:** {case.plaintiff or '—'}")
        st.markdown(f"**Ответчик:** {case.defendant or '—'}")
        if case.claim_amount:
            st.markdown(f"**Сумма иска:** {case.claim_amount:,.2f} ₽")
        if case.filing_date:
            st.markdown(f"**Дата подачи:** {case.filing_date.strftime('%d.%m.%Y')}")
    with c2:
        st.markdown(f"**Суд:** {case.court}")
        if case.case_category_text:
            st.markdown(f"**Категория спора:** {case.case_category_text}")
        if case.case_status_text:
            st.markdown(f"**Состояние:** {case.case_status_text}")
        if case.case_url:
            st.markdown(f"[🔗 kad.arbitr.ru]({case.case_url})")

    pdfs = collect_case_pdfs(case)
    if pdfs:
        downloaded = sum(1 for p in pdfs if p["downloaded"])
        st.markdown(f"**PDF:** {len(pdfs)} документов ({downloaded} скачано локально)")
        for p in pdfs[:10]:
            render_pdf_link(p["url"], p["label"][:100], case_id, p["priority"])
        if len(pdfs) > 10:
            st.caption(f"... и ещё {len(pdfs) - 10}")
    else:
        meta = case.extracted_data or {}
        dl = meta.get("pdf_download_count")
        rec = meta.get("pdf_recorded_urls")
        if dl or rec:
            st.markdown(f"**PDF:** скачано {dl or 0}, URL записано {len(rec) if rec else 0}")
        else:
            st.caption("PDF: нет данных")


def _render_ollama_prompt_inspector(case, audit: dict | None = None):
    """Show the prompt sent to Ollama, with PDF inclusion stats."""
    st.markdown("**📨 Запрос к Ollama**")
    if audit:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Символов в досье", audit.get("dossier_chars", "—"))
        c2.metric("PDF-блоков", audit.get("pdf_text_blocks", 0))
        c3.metric("Символов PDF", audit.get("pdf_text_chars", 0))
        pdf_ok = audit.get("includes_pdf_section", False)
        c4.metric("PDF в промпте", "✅ да" if pdf_ok else "❌ нет")
        if audit.get("pdf_text_blocks", 0) > 0 and not pdf_ok:
            st.warning("PDF извлечены, но не попали в досье — проверьте лимиты в classification.yaml")
    tab_user, tab_system = st.tabs(["User prompt (досье)", "System prompt"])
    with tab_user:
        st.code(audit.get("user_prompt", "—") if audit else "—", language=None)
    with tab_system:
        st.code(audit.get("system_prompt", "—") if audit else "—", language=None)


def _render_ml_classification(repo: CaseRepository, case, embedded: bool = False):
    """ML classification section with probabilities, reasoning, and review."""
    if not embedded:
        st.markdown("---")
    st.markdown("**🤖 ML-классификация**")

    ml = (case.extracted_data or {}).get("ml_classification")
    ml_review = (case.extracted_data or {}).get("ml_review", {})

    col_kw, col_ml = st.columns(2)
    with col_kw:
        st.caption("Ключевые слова (Stage 1/2)")
        st.markdown(f"Категория: **{case.category or '—'}**")
        st.markdown(f"Балл: **{case.relevance_score:.1f}**")
    with col_ml:
        st.caption("ML (Ollama)")
        if ml:
            primary = ml.get("primary_category", "—")
            conf = ml.get("confidence", 0) * 100
            st.markdown(f"Категория: **{ML_CATEGORY_LABELS.get(primary, primary)}**")
            st.markdown(f"Уверенность: **{conf:.0f}%**")
            if case.category and primary != case.category:
                st.warning("⚠️ ML ≠ ключевые слова")
        else:
            st.markdown("_Ещё не классифицировано_")

    if ml:
        probs = ml.get("probabilities", {})
        if probs:
            fig = go.Figure(
                go.Bar(
                    x=[ML_CATEGORY_LABELS.get(k, k) for k in probs],
                    y=[v * 100 for v in probs.values()],
                    marker_color=["#3b82f6", "#8b5cf6", "#6b7280"][: len(probs)],
                )
            )
            fig.update_layout(
                height=200, margin=dict(t=10, b=30, l=20, r=20),
                yaxis_title="%", showlegend=False,
            )
            st.plotly_chart(fig, width="stretch", key=f"ml_probs_{case.id}")

        if ml.get("reasoning"):
            st.markdown(f"**Обоснование:** {ml['reasoning']}")
        signals = ml.get("key_signals") or []
        if signals:
            st.markdown("**Сигналы:** " + " · ".join(f"`{s}`" for s in signals))
        uncertainty = ml.get("uncertainty", "")
        if uncertainty:
            badge = {"low": "🟢", "medium": "🟡", "high": "🔴"}.get(uncertainty, "")
            st.caption(
                f"{badge} Неопределённость: {uncertainty} · "
                f"Модель: {ml.get('model', '—')} · "
                f"Prompt v{ml.get('prompt_version', '—')} · "
                f"{ml.get('analyzed_at', '')[:19]}"
            )
        pa = ml.get("prompt_audit")
        if pa:
            pdf_flag = "✅" if pa.get("includes_pdf_section") else "❌"
            st.caption(
                f"Запрос Ollama: {pa.get('dossier_chars', 0)} симв. · "
                f"PDF {pdf_flag} ({pa.get('pdf_text_blocks', 0)} блоков, "
                f"{pa.get('pdf_text_chars', 0)} симв.)"
            )

    # Ollama prompt inspector
    audit = (ml or {}).get("prompt_audit")
    insp_col1, insp_col2 = st.columns([1, 3])
    with insp_col1:
        show_saved = st.button("📨 Показать запрос", key=f"ml_show_prompt_{case.id}")
    with insp_col2:
        rebuild = st.button(
            "🔁 Пересобрать превью (с PDF)", key=f"ml_rebuild_prompt_{case.id}",
            help="Извлекает PDF заново и показывает актуальный промпт без вызова Ollama",
        )

    if show_saved and audit:
        _render_ollama_prompt_inspector(case, audit)
    elif rebuild:
        try:
            clf_config = ClassificationConfig(str(CLASSIFICATION_CONFIG_PATH))
            main_cfg = ConfigManager()
            pdf_dir = Path(main_cfg.get("scraping.pdf_storage_dir", "data/pdfs"))
            with st.spinner("Сборка досье и PDF..."):
                updated, prompt = prepare_case_for_classification(case, clf_config, pdf_dir)
                live_audit = build_prompt_audit(updated, prompt)
            _render_ollama_prompt_inspector(updated, live_audit)
        except Exception as e:
            st.error(f"Ошибка сборки промпта: {e}")
    elif show_saved and not audit:
        st.info(
            "Нет сохранённого запроса (классификация до этой функции). "
            "Нажмите «Пересобрать превью» или переклассифицируйте дело."
        )

    btn_col1, btn_col2 = st.columns([1, 3])
    with btn_col1:
        if st.button("🔄 Классифицировать", key=f"ml_classify_{case.id}"):
            try:
                clf_config = ClassificationConfig(str(CLASSIFICATION_CONFIG_PATH))
                main_cfg = ConfigManager()
                pdf_dir = Path(main_cfg.get("scraping.pdf_storage_dir", "data/pdfs"))
                with st.spinner("Ollama классифицирует дело..."):
                    updated, result, prompt = classify_case(case, clf_config, pdf_dir)
                    if result:
                        updated = apply_classification_to_case(updated, result, clf_config, prompt)
                        repo.save_case(updated)
                        st.success("Классификация сохранена!")
                        st.rerun()
            except Exception as e:
                st.error(f"Ошибка: {e}")

    # Human review of ML result
    if ml:
        st.markdown("**Проверка ML:**")
        rc1, rc2, rc3 = st.columns([1, 1, 2])
        with rc1:
            if st.button("✅ Верно", key=f"ml_ok_{case.id}"):
                repo.save_ml_review(case.id, verdict="correct")
                st.rerun()
        with rc2:
            if st.button("❌ Неверно", key=f"ml_wrong_{case.id}"):
                repo.save_ml_review(case.id, verdict="wrong")
                st.rerun()
        with rc3:
            correct_cat = st.selectbox(
                "Правильная категория",
                [None, "construction", "bankruptcy", "other"],
                format_func=lambda x: "—" if x is None else ML_CATEGORY_LABELS.get(x, x),
                key=f"ml_correct_{case.id}", index=0,
            )
            review_notes = st.text_input(
                "Заметки по ML", value=ml_review.get("notes") or "",
                key=f"ml_notes_{case.id}",
            )
            if st.button("💾 Сохранить проверку ML", key=f"ml_save_review_{case.id}"):
                verdict = ml_review.get("verdict") or "reviewed"
                repo.save_ml_review(
                    case.id, verdict=verdict,
                    correct_category=correct_cat, notes=review_notes or None,
                )
                st.success("Проверка ML сохранена")
                st.rerun()
        if ml_review.get("verdict"):
            st.caption(
                f"Проверено: {ml_review.get('verdict')} "
                f"({ml_review.get('reviewed_at', '')[:19]})"
            )


def render_case_detail(repo: CaseRepository, case):
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

    if case.judges:
        st.markdown(f"**Судьи:** {', '.join(case.judges)}")

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
                            render_pdf_link(upd.pdf_url, content_str or "Документ", case.id, prio)

                if inst.documents:
                    st.markdown("**📄 Документы:**")
                    for doc in inst.documents:
                        if doc.url:
                            render_pdf_link(doc.url, doc.filename or "Документ", case.id, doc.priority)

    if case.extracted_data:
        with st.popover("📄 Извлеченные данные"):
            st.json(case.extracted_data)

    _render_ml_classification(repo, case)

    st.markdown("---")
    review_col1, review_col2 = st.columns([1, 3])
    with review_col1:
        reviewed = st.checkbox("✅ Проверено", value=False, key=f"review_{case.id}")
    with review_col2:
        notes = st.text_input(
            "Заметки", value="", key=f"notes_{case.id}", placeholder="Добавить заметку...",
        )

    if st.button("💾 Сохранить", key=f"save_{case.id}"):
        repo.mark_reviewed(case.id, reviewed=reviewed, notes=notes if notes else None)
        st.success("Сохранено!")
        st.rerun()
