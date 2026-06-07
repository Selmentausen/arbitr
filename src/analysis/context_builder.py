"""Build compact case dossier text for LLM classification."""

from src.config.classification import ClassificationConfig
from src.models.case import Case
from src.utils.logger import get_logger

logger = get_logger(__name__)

_ROLE_LABELS = {
    "plaintiff": "Истец",
    "defendant": "Ответчик",
    "third_party": "Третье лицо",
    "other_party": "Иное лицо",
}


def _format_participants(case: Case) -> list[str]:
    lines: list[str] = []
    if case.plaintiff:
        lines.append(f"Истец: {case.plaintiff}")
    if case.defendant:
        lines.append(f"Ответчик: {case.defendant}")

    for role, participants in (case.participants or {}).items():
        label = _ROLE_LABELS.get(role, role)
        for p in participants:
            parts = [f"{label}: {p.name}"]
            if p.inn:
                parts.append(f"ИНН {p.inn}")
            if p.address:
                parts.append(f"адрес: {p.address}")
            lines.append(" | ".join(parts))
    return lines


def _collect_chronology(case: Case, max_entries: int) -> list[str]:
    entries: list[tuple[str, str]] = []
    for inst in case.instances or []:
        prefix = inst.instance_level or inst.court_name or ""
        for upd in inst.updates or []:
            parts = []
            if upd.date:
                parts.append(upd.date)
            if upd.update_type:
                parts.append(upd.update_type)
            if upd.content:
                parts.append(upd.content)
            if upd.additional_info:
                parts.append(f"({upd.additional_info})")
            line = " — ".join(parts)
            if prefix:
                line = f"[{prefix}] {line}"
            entries.append((upd.date or "", line))
        if inst.result_text:
            line = f"[{prefix}] Итог: {inst.result_text}"
            entries.append((inst.date or "", line))

    entries.sort(key=lambda x: x[0], reverse=True)
    return [e[1] for e in entries[:max_entries]]


def _collect_pdf_excerpts(case: Case, max_chars: int) -> list[str]:
    excerpts: list[str] = []
    used = 0
    for block in case.pdf_texts or []:
        if used >= max_chars:
            break
        remaining = max_chars - used
        chunk = block if len(block) <= remaining else block[:remaining] + "\n[...]"
        excerpts.append(chunk)
        used += len(chunk)
    return excerpts


def build_case_dossier(case: Case, config: ClassificationConfig) -> str:
    """Assemble a Russian-language dossier string for the classifier."""
    limits = config.get("limits", {}) or {}
    max_chronology = int(limits.get("max_chronology_entries", 20))
    max_total = int(limits.get("max_total_context_chars", 16000))

    sections: list[str] = [
        f"Номер дела: {case.case_number}",
        f"Суд: {case.court}",
    ]
    if case.case_type:
        sections.append(f"Тип дела (с сайта): {case.case_type}")
    if case.case_category_text:
        sections.append(f"Категория спора (с сайта): {case.case_category_text}")
    if case.case_status_text:
        sections.append(f"Состояние: {case.case_status_text}")
    if case.claim_amount:
        sections.append(f"Сумма иска: {case.claim_amount:,.2f} ₽")
    if case.judges:
        sections.append(f"Судьи: {', '.join(case.judges)}")
    if case.filing_date:
        sections.append(f"Дата подачи: {case.filing_date.strftime('%d.%m.%Y')}")

    participants = _format_participants(case)
    if participants:
        sections.append("\nУчастники:")
        sections.extend(participants)

    chronology = _collect_chronology(case, max_chronology)
    if chronology:
        sections.append("\nХронология (последние события):")
        sections.extend(f"- {line}" for line in chronology)

    pdf_budget = max(2000, max_total // 3)
    pdf_excerpts = _collect_pdf_excerpts(case, pdf_budget)
    if pdf_excerpts:
        sections.append("\nФрагменты PDF:")
        sections.extend(pdf_excerpts)

    dossier = "\n".join(sections)
    if len(dossier) > max_total:
        dossier = dossier[:max_total] + "\n[... досье обрезано ...]"
        logger.debug("Dossier truncated to %d chars for case %s", max_total, case.case_number)

    return dossier
