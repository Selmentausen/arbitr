"""Tests for storage layer (SQLite + SQLAlchemy)."""

import pytest
from datetime import datetime

from src.models.case import Case, CaseParticipant, CaseInstance, StatusEnum
from src.storage.database import init_db, get_session, Base, CaseRecord
from src.storage.repository import CaseRepository


@pytest.fixture
def db_session():
    """Create an in-memory database and return a session."""
    init_db(":memory:")
    session = get_session()
    yield session
    session.close()


@pytest.fixture
def repo(db_session):
    """Create a repository with the test session."""
    return CaseRepository(session=db_session)


@pytest.fixture
def sample_case():
    """Create a sample Case for testing."""
    return Case(
        id="test-uuid-123",
        case_number="А40-123456/2024",
        court="Арбитражный суд города Москвы",
        judges=["Иванов И.И.", "Петров П.П."],
        plaintiff="ООО 'Строитель'",
        defendant="ООО 'Заказчик'",
        category="construction",
        relevance_score=75.5,
        status=StatusEnum.HIGH_RELEVANT,
        participants={
            "plaintiffs": [
                CaseParticipant(name="ООО 'Строитель'", address="г. Москва, ул. Ленина 1"),
            ],
            "defendants": [
                CaseParticipant(name="ООО 'Заказчик'", inn="1234567890"),
            ],
        },
        instances=[
            CaseInstance(court_name="АС города Москвы", case_number="А40-123456/2024", date="15.01.2024"),
        ],
        extracted_data={"duration": 120, "outcome": "settled"},
    )


@pytest.fixture
def sample_case_2():
    """Create a second sample Case."""
    return Case(
        id="test-uuid-456",
        case_number="А40-789012/2024",
        court="Арбитражный суд Санкт-Петербурга",
        judges=["Сидоров С.С."],
        plaintiff="ООО 'Подрядчик'",
        defendant="ООО 'Девелопер'",
        category="construction",
        relevance_score=45.0,
        status=StatusEnum.UNCERTAIN,
    )


@pytest.fixture
def sample_case_3():
    """Create a third sample Case (different category)."""
    return Case(
        id="test-uuid-789",
        case_number="А40-111222/2024",
        court="Арбитражный суд города Москвы",
        judges=[],
        plaintiff="ООО 'Кредитор'",
        defendant="ООО 'Должник'",
        category="bankruptcy",
        relevance_score=90.0,
        status=StatusEnum.HIGH_RELEVANT,
    )


class TestSaveAndGet:
    """Test save and retrieve operations."""

    def test_save_case(self, repo, sample_case):
        """Test saving a Case."""
        record = repo.save_case(sample_case)
        assert record.id == "test-uuid-123"
        assert record.case_number == "А40-123456/2024"
        assert record.status == "high_relevant"

    def test_get_case(self, repo, sample_case):
        """Test retrieving a saved Case."""
        repo.save_case(sample_case)
        case = repo.get_case("test-uuid-123")

        assert case is not None
        assert case.id == "test-uuid-123"
        assert case.case_number == "А40-123456/2024"
        assert case.court == "Арбитражный суд города Москвы"
        assert case.plaintiff == "ООО 'Строитель'"
        assert case.defendant == "ООО 'Заказчик'"
        assert case.category == "construction"
        assert case.relevance_score == 75.5
        assert case.status == StatusEnum.HIGH_RELEVANT

    def test_get_case_not_found(self, repo):
        """Test getting a non-existent case returns None."""
        assert repo.get_case("nonexistent") is None

    def test_roundtrip_judges(self, repo, sample_case):
        """Test judges are preserved through save/load."""
        repo.save_case(sample_case)
        case = repo.get_case("test-uuid-123")
        assert len(case.judges) == 2
        assert "Иванов И.И." in case.judges
        assert "Петров П.П." in case.judges

    def test_roundtrip_participants(self, repo, sample_case):
        """Test participants are preserved through save/load."""
        repo.save_case(sample_case)
        case = repo.get_case("test-uuid-123")
        assert "plaintiffs" in case.participants
        assert len(case.participants["plaintiffs"]) == 1
        assert case.participants["plaintiffs"][0].name == "ООО 'Строитель'"
        assert "defendants" in case.participants
        assert case.participants["defendants"][0].inn == "1234567890"

    def test_roundtrip_instances(self, repo, sample_case):
        """Test court instances are preserved through save/load."""
        repo.save_case(sample_case)
        case = repo.get_case("test-uuid-123")
        assert len(case.instances) == 1
        assert case.instances[0].court_name == "АС города Москвы"

    def test_roundtrip_extracted_data(self, repo, sample_case):
        """Test extracted data JSON is preserved."""
        repo.save_case(sample_case)
        case = repo.get_case("test-uuid-123")
        assert case.extracted_data["duration"] == 120
        assert case.extracted_data["outcome"] == "settled"

    def test_save_multiple_cases(self, repo, sample_case, sample_case_2):
        """Test saving multiple cases."""
        count = repo.save_cases([sample_case, sample_case_2])
        assert count == 2

    def test_update_existing_case(self, repo, sample_case):
        """Test updating an existing case."""
        repo.save_case(sample_case)

        # Modify and re-save
        sample_case.relevance_score = 95.0
        sample_case.status = StatusEnum.HIGH_RELEVANT
        sample_case.category = "updated_category"
        repo.save_case(sample_case)

        case = repo.get_case("test-uuid-123")
        assert case.relevance_score == 95.0
        assert case.category == "updated_category"

    def test_save_case(self, repo):
        """Test saving a Case with basic fields."""
        case = Case(
            id="base-uuid-001",
            case_number="А40-999/2024",
            court="Тест",
            plaintiff="П",
            defendant="О",
        )
        record = repo.save_case(case)
        assert record.id == "base-uuid-001"

        case = repo.get_case("base-uuid-001")
        assert case is not None
        assert case.status == StatusEnum.INSUFFICIENT_INFO


class TestGetAllCases:
    """Test listing and filtering cases."""

    def test_get_all_cases(self, repo, sample_case, sample_case_2, sample_case_3):
        """Test basic listing."""
        repo.save_cases([sample_case, sample_case_2, sample_case_3])
        cases, total = repo.get_all_cases()
        assert total == 3
        assert len(cases) == 3

    def test_pagination(self, repo, sample_case, sample_case_2, sample_case_3):
        """Test pagination."""
        repo.save_cases([sample_case, sample_case_2, sample_case_3])

        cases, total = repo.get_all_cases(page=1, page_size=2)
        assert total == 3
        assert len(cases) == 2

        cases, total = repo.get_all_cases(page=2, page_size=2)
        assert len(cases) == 1

    def test_filter_by_status(self, repo, sample_case, sample_case_2, sample_case_3):
        """Test filtering by status."""
        repo.save_cases([sample_case, sample_case_2, sample_case_3])

        cases, total = repo.get_all_cases(status="high_relevant")
        assert total == 2  # sample_case and sample_case_3

        cases, total = repo.get_all_cases(status="uncertain")
        assert total == 1  # sample_case_2

    def test_filter_by_category(self, repo, sample_case, sample_case_2, sample_case_3):
        """Test filtering by category."""
        repo.save_cases([sample_case, sample_case_2, sample_case_3])

        cases, total = repo.get_all_cases(category="construction")
        assert total == 2  # sample_case and sample_case_2

        cases, total = repo.get_all_cases(category="bankruptcy")
        assert total == 1  # sample_case_3


class TestSearch:
    """Test search functionality."""

    def test_search_by_plaintiff(self, repo, sample_case, sample_case_2):
        """Test searching by plaintiff name."""
        repo.save_cases([sample_case, sample_case_2])
        results = repo.search_cases("Строитель")
        assert len(results) == 1
        assert results[0].plaintiff == "ООО 'Строитель'"

    def test_search_by_case_number(self, repo, sample_case, sample_case_2):
        """Test searching by case number."""
        repo.save_cases([sample_case, sample_case_2])
        results = repo.search_cases("123456")
        assert len(results) == 1

    def test_search_no_results(self, repo, sample_case):
        """Test search with no matches."""
        repo.save_case(sample_case)
        results = repo.search_cases("nonexistent_query")
        assert len(results) == 0


class TestReview:
    """Test review marking."""

    def test_mark_reviewed(self, repo, sample_case):
        """Test marking a case as reviewed."""
        repo.save_case(sample_case)
        result = repo.mark_reviewed("test-uuid-123", reviewed=True, notes="Looks good")
        assert result is True

        # Verify via direct DB query
        record = repo.session.get(CaseRecord, "test-uuid-123")
        assert record.reviewed is True
        assert record.review_notes == "Looks good"
        assert record.reviewed_at is not None

    def test_mark_reviewed_nonexistent(self, repo):
        """Test marking a non-existent case."""
        result = repo.mark_reviewed("nonexistent", reviewed=True)
        assert result is False

    def test_unmark_reviewed(self, repo, sample_case):
        """Test unmarking a reviewed case."""
        repo.save_case(sample_case)
        repo.mark_reviewed("test-uuid-123", reviewed=True)
        repo.mark_reviewed("test-uuid-123", reviewed=False)

        record = repo.session.get(CaseRecord, "test-uuid-123")
        assert record.reviewed is False
        assert record.reviewed_at is None


class TestStats:
    """Test stats aggregation."""

    def test_get_stats(self, repo, sample_case, sample_case_2, sample_case_3):
        """Test getting statistics."""
        repo.save_cases([sample_case, sample_case_2, sample_case_3])
        repo.mark_reviewed("test-uuid-123", reviewed=True)

        stats = repo.get_stats()
        assert stats["total_cases"] == 3
        assert stats["reviewed"] == 1
        assert stats["not_reviewed"] == 2
        assert stats["by_status"]["high_relevant"] == 2
        assert stats["by_status"]["uncertain"] == 1
        assert stats["by_category"]["construction"] == 2
        assert stats["by_category"]["bankruptcy"] == 1
        assert stats["avg_relevance_score"] > 0


class TestExport:
    """Test export functionality."""

    def test_export_json(self, repo, sample_case, sample_case_2):
        """Test JSON export."""
        import json
        repo.save_cases([sample_case, sample_case_2])
        result = repo.export_cases(format="json")
        data = json.loads(result)
        assert len(data) == 2

    def test_export_csv(self, repo, sample_case):
        """Test CSV export."""
        repo.save_case(sample_case)
        result = repo.export_cases(format="csv")
        lines = result.strip().split("\n")
        assert len(lines) == 2  # header + 1 row
        assert "А40-123456/2024" in lines[1]


class TestDelete:
    """Test delete operations."""

    def test_delete_case(self, repo, sample_case):
        """Test deleting a case."""
        repo.save_case(sample_case)
        assert repo.delete_case("test-uuid-123") is True
        assert repo.get_case("test-uuid-123") is None

    def test_delete_nonexistent(self, repo):
        """Test deleting a non-existent case."""
        assert repo.delete_case("nonexistent") is False
