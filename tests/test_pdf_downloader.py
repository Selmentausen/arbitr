"""Tests for PDF priority classification and URL collection."""

from src.models.case import Case, CaseDocument, CaseInstance, InstanceUpdate
from src.scraper.pdf_downloader import (
    _is_pdf,
    _safe_filename,
    classify_priority,
    collect_pdf_entries,
)


def test_classify_high_priority():
    assert classify_priority("Иск удовлетворить полностью") == "high"
    assert classify_priority("О назначении экспертизы") == "high"
    assert classify_priority("Об утверждении мирового соглашения") == "high"


def test_classify_medium_priority():
    assert classify_priority("О принятии обеспечительных (предварительных обеспечительных) мер") == "medium"
    assert classify_priority("О приостановлении производства по делу (жалобе)") == "medium"


def test_classify_low_priority():
    assert classify_priority("О принятии искового заявления (заявления) к производству") == "low"
    assert classify_priority("Об отложении судебного разбирательства") == "low"


def test_classify_uncategorized():
    assert classify_priority("Письмо (входящее)") == "uncategorized"
    assert classify_priority(None) == "uncategorized"
    assert classify_priority("") == "uncategorized"


def test_classify_substring_match():
    """Priority patterns should match as substrings (case result text can be long)."""
    long_text = (
        "Оставить без изменения Решение; Оставить без изменения решение, "
        "а апелляционную жалобу - без удовлетворения (п.1 ст.269 АПК)"
    )
    # "Оставить без изменения Решение" is not in high, but the actual
    # result "Иск удовлетворить полностью" would be
    assert classify_priority("Иск удовлетворить частично, встречный иск удовлетворить полностью") == "high"


def test_collect_pdf_entries_with_priorities():
    case = Case(
        id="1", case_number="A40-1/2024", court="АС",
        judges=[], plaintiff="x", defendant="y",
        instances=[
            CaseInstance(
                court_name="АС",
                result_pdf_url="/Kad/Pdf/1",
                result_text="Иск удовлетворить полностью",
                updates=[
                    InstanceUpdate(
                        pdf_url="/Kad/Pdf/1",
                        content="Иск удовлетворить полностью",
                    ),
                    InstanceUpdate(
                        pdf_url="/Kad/Pdf/2",
                        content="О принятии искового заявления (заявления) к производству",
                    ),
                ],
                documents=[],
            ),
        ],
    )
    entries = collect_pdf_entries(case)
    assert len(entries) == 2
    assert entries[0].priority == "high"
    assert entries[1].priority == "low"


def test_collect_pdf_entries_dedupes():
    case = Case(
        id="1", case_number="A40-1/2024", court="АС",
        judges=[], plaintiff="x", defendant="y",
        instances=[
            CaseInstance(
                court_name="АС",
                result_pdf_url="/Kad/Pdf/1",
                updates=[
                    InstanceUpdate(pdf_url="/Kad/Pdf/1"),
                    InstanceUpdate(pdf_url="/Kad/Pdf/2"),
                ],
                documents=[CaseDocument(url="/Kad/Pdf/2")],
            ),
        ],
    )
    entries = collect_pdf_entries(case)
    assert len(entries) == 2


def test_is_pdf():
    assert _is_pdf(b"%PDF-1.4 ...")
    assert not _is_pdf(b"<html>...")
    assert not _is_pdf(b"")


def test_safe_filename():
    url = "https://kad.arbitr.ru/Kad/PdfDocument/uuid/file/A40-1_20260101_Opredelenie.pdf"
    assert _safe_filename(url) == "A40-1_20260101_Opredelenie"
