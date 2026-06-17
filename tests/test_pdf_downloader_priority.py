import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from src.models.case import Case, CaseInstance, InstanceUpdate
from src.scraper.pdf_downloader import download_pdfs_for_case

@pytest.mark.asyncio
@patch("src.scraper.pdf_downloader._download_single_pdf")
async def test_download_all_high_priority(mock_download):
    # Setup mock to return dummy path and size
    mock_download.return_value = (Path("dummy.pdf"), 1024)

    # A case with 2 high priority, 1 medium, 1 low
    case = Case(
        id="1", case_number="A40-1/2024", court="АС",
        judges=[], plaintiff="x", defendant="y",
        instances=[
            CaseInstance(
                court_name="АС",
                updates=[
                    InstanceUpdate(pdf_url="/Pdf/high1", content="Иск удовлетворить полностью"),
                    InstanceUpdate(pdf_url="/Pdf/high2", content="О назначении экспертизы"),
                    InstanceUpdate(pdf_url="/Pdf/medium1", content="О возобновлении производства по делу"),
                    InstanceUpdate(pdf_url="/Pdf/low1", content="Об отложении судебного разбирательства"),
                ]
            )
        ]
    )

    mock_page = MagicMock()
    summary = await download_pdfs_for_case(mock_page, case, storage_dir=Path("data/test_out"))

    # Should download all high priority PDFs (2 of them) and skip the rest
    assert summary.urls_found == 4
    assert summary.downloaded == 2
    assert summary.skipped_low_priority == 2
    
    # Check that download was called only for high priority urls
    called_urls = [args[1] for args, kwargs in mock_download.call_args_list]
    assert len(called_urls) == 2
    assert "https://kad.arbitr.ru/Pdf/high1" in called_urls
    assert "https://kad.arbitr.ru/Pdf/high2" in called_urls

@pytest.mark.asyncio
@patch("src.scraper.pdf_downloader._download_single_pdf")
async def test_download_one_medium_if_no_high(mock_download):
    mock_download.return_value = (Path("dummy.pdf"), 1024)

    # A case with 0 high priority, 2 medium, 1 low
    case = Case(
        id="1", case_number="A40-1/2024", court="АС",
        judges=[], plaintiff="x", defendant="y",
        instances=[
            CaseInstance(
                court_name="АС",
                updates=[
                    InstanceUpdate(pdf_url="/Pdf/medium1", content="О возобновлении производства по делу"),
                    InstanceUpdate(pdf_url="/Pdf/medium2", content="О замене ненадлежащего ответчика"),
                    InstanceUpdate(pdf_url="/Pdf/low1", content="Об отложении судебного разбирательства"),
                ]
            )
        ]
    )

    mock_page = MagicMock()
    summary = await download_pdfs_for_case(mock_page, case, storage_dir=Path("data/test_out"))

    # Should download exactly 1 medium priority PDF and skip the rest (2 skipped)
    assert summary.urls_found == 3
    assert summary.downloaded == 1
    assert summary.skipped_low_priority == 2
    
    called_urls = [args[1] for args, kwargs in mock_download.call_args_list]
    assert len(called_urls) == 1
    assert called_urls[0] == "https://kad.arbitr.ru/Pdf/medium1"

@pytest.mark.asyncio
@patch("src.scraper.pdf_downloader._download_single_pdf")
async def test_download_one_low_if_no_high_or_medium(mock_download):
    mock_download.return_value = (Path("dummy.pdf"), 1024)

    # A case with 0 high, 0 medium, 2 low
    case = Case(
        id="1", case_number="A40-1/2024", court="АС",
        judges=[], plaintiff="x", defendant="y",
        instances=[
            CaseInstance(
                court_name="АС",
                updates=[
                    InstanceUpdate(pdf_url="/Pdf/low1", content="Об отложении судебного разбирательства"),
                    InstanceUpdate(pdf_url="/Pdf/low2", content="О принятии искового заявления (заявления) к производству"),
                ]
            )
        ]
    )

    mock_page = MagicMock()
    summary = await download_pdfs_for_case(mock_page, case, storage_dir=Path("data/test_out"))

    # Should download exactly 1 low priority PDF and skip the rest (1 skipped)
    assert summary.urls_found == 2
    assert summary.downloaded == 1
    assert summary.skipped_low_priority == 1
    
    called_urls = [args[1] for args, kwargs in mock_download.call_args_list]
    assert len(called_urls) == 1
    assert called_urls[0] == "https://kad.arbitr.ru/Pdf/low1"
