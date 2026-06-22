"""Tests for Pydantic data models."""

import pytest
from pydantic import ValidationError

from src.models.case import Case, StatusEnum


class TestStatusEnum:
    """Test cases for StatusEnum."""

    def test_enum_values(self):
        """Test that enum has expected values."""
        assert StatusEnum.HIGH_RELEVANT == "high_relevant"
        assert StatusEnum.REJECT == "reject"
        assert StatusEnum.INSUFFICIENT_INFO == "insufficient_info"
        assert StatusEnum.UNCERTAIN == "uncertain"

    def test_enum_membership(self):
        """Test enum membership checks."""
        assert "high_relevant" in [e.value for e in StatusEnum]
        assert "invalid_status" not in [e.value for e in StatusEnum]


class TestCase:
    """Test cases for Case model (basic fields)."""

    def test_create_valid_case_base(self):
        """Test creating a valid Case instance with basic fields."""
        case = Case(
            id="A40-123456/2024",
            case_number="А40-123456/2024",
            court="Арбитражный суд города Москвы",
            judges=["Иванов И.И.", "Петров П.П."],
            plaintiff="ООО 'Строитель'",
            defendant="ООО 'Заказчик'",
            third_parties=["ООО 'Консультант'"],
        )
        
        assert case.id == "A40-123456/2024"
        assert case.case_number == "А40-123456/2024"
        assert case.court == "Арбитражный суд города Москвы"
        assert len(case.judges) == 2
        assert case.plaintiff == "ООО 'Строитель'"
        assert case.defendant == "ООО 'Заказчик'"
        assert len(case.third_parties) == 1

    def test_case_base_defaults(self):
        """Test Case with default values."""
        case = Case(
            id="A40-123456/2024",
            case_number="А40-123456/2024",
            court="Арбитражный суд города Москвы",
            plaintiff="ООО 'Строитель'",
            defendant="ООО 'Заказчик'",
        )
        
        assert case.judges == []
        assert case.third_parties == []

    def test_case_base_missing_required_fields(self):
        """Test that missing required fields raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Case(
                id="A40-123456/2024",
                case_number="А40-123456/2024",
                court="Арбитражный суд города Москвы",
                # Missing plaintiff and defendant
            )
        
        errors = exc_info.value.errors()
        field_names = [e["loc"][0] for e in errors]
        assert "plaintiff" in field_names
        assert "defendant" in field_names

    def test_case_base_serialization(self):
        """Test Case serialization to dict."""
        case = Case(
            id="A40-123456/2024",
            case_number="А40-123456/2024",
            court="Арбитражный суд города Москвы",
            plaintiff="ООО 'Строитель'",
            defendant="ООО 'Заказчик'",
        )
        
        case_dict = case.model_dump()
        assert case_dict["id"] == "A40-123456/2024"
        assert case_dict["case_number"] == "А40-123456/2024"
        assert case_dict["judges"] == []
        assert case_dict["third_parties"] == []


class TestCase:
    """Test cases for Case model."""

    def test_create_valid_case(self):
        """Test creating a valid Case instance."""
        case = Case(
            id="A40-123456/2024",
            case_number="А40-123456/2024",
            court="Арбитражный суд города Москвы",
            judges=["Иванов И.И."],
            plaintiff="ООО 'Строитель'",
            defendant="ООО 'Заказчик'",
            category="construction",
            relevance_score=75.5,
            status=StatusEnum.HIGH_RELEVANT,
            extracted_data={"duration": 120, "outcome": "settled"},
            related_cases=["A40-123457/2024"],
            aggregated_metrics={"dispute_count": 5, "mediation_rate": 0.4},
        )
        
        assert case.category == "construction"
        assert case.relevance_score == 75.5
        assert case.status == StatusEnum.HIGH_RELEVANT
        assert case.extracted_data["duration"] == 120
        assert len(case.related_cases) == 1

    def test_case_defaults(self):
        """Test Case with default values."""
        case = Case(
            id="A40-123456/2024",
            case_number="А40-123456/2024",
            court="Арбитражный суд города Москвы",
            plaintiff="ООО 'Строитель'",
            defendant="ООО 'Заказчик'",
        )
        
        assert case.category is None
        assert case.relevance_score == 0.0
        assert case.status == StatusEnum.INSUFFICIENT_INFO
        assert case.extracted_data == {}
        assert case.related_cases == []
        assert case.aggregated_metrics == {}
        assert case.raw_html is None
        assert case.pdf_texts == []

    def test_case_inherits_from_case_base(self):
        """Test that Case has all basic fields."""
        case = Case(
            id="A40-123456/2024",
            case_number="А40-123456/2024",
            court="Арбитражный суд города Москвы",
            plaintiff="ООО 'Строитель'",
            defendant="ООО 'Заказчик'",
        )
        
        # Should have all CaseBase fields
        assert hasattr(case, "id")
        assert hasattr(case, "case_number")
        assert hasattr(case, "court")
        assert hasattr(case, "judges")
        assert hasattr(case, "plaintiff")
        assert hasattr(case, "defendant")
        assert hasattr(case, "third_parties")

    def test_relevance_score_validation(self):
        """Test that relevance_score is validated (0-100 range)."""
        # Valid score
        case = Case(
            id="A40-123456/2024",
            case_number="А40-123456/2024",
            court="Арбитражный суд города Москвы",
            plaintiff="ООО 'Строитель'",
            defendant="ООО 'Заказчик'",
            relevance_score=50.0,
        )
        assert case.relevance_score == 50.0
        
        # Invalid score (> 100)
        with pytest.raises(ValidationError) as exc_info:
            Case(
                id="A40-123456/2024",
                case_number="А40-123456/2024",
                court="Арбитражный суд города Москвы",
                plaintiff="ООО 'Строитель'",
                defendant="ООО 'Заказчик'",
                relevance_score=150.0,
            )
        
        assert any("less than or equal to 100" in str(e) for e in exc_info.value.errors())
        
        # Invalid score (< 0)
        with pytest.raises(ValidationError) as exc_info:
            Case(
                id="A40-123456/2024",
                case_number="А40-123456/2024",
                court="Арбитражный суд города Москвы",
                plaintiff="ООО 'Строитель'",
                defendant="ООО 'Заказчик'",
                relevance_score=-10.0,
            )
        
        assert any("greater than or equal to 0" in str(e) for e in exc_info.value.errors())

    def test_status_enum_assignment(self):
        """Test that status accepts StatusEnum values."""
        for status_value in StatusEnum:
            case = Case(
                id="A40-123456/2024",
                case_number="А40-123456/2024",
                court="Арбитражный суд города Москвы",
                plaintiff="ООО 'Строитель'",
                defendant="ООО 'Заказчик'",
                status=status_value,
            )
            assert case.status == status_value

    def test_case_serialization(self):
        """Test Case serialization to dict."""
        case = Case(
            id="A40-123456/2024",
            case_number="А40-123456/2024",
            court="Арбитражный суд города Москвы",
            plaintiff="ООО 'Строитель'",
            defendant="ООО 'Заказчик'",
            category="construction",
            relevance_score=75.5,
            status=StatusEnum.HIGH_RELEVANT,
        )
        
        case_dict = case.model_dump()
        assert case_dict["id"] == "A40-123456/2024"
        assert case_dict["category"] == "construction"
        assert case_dict["relevance_score"] == 75.5
        assert case_dict["status"] == "high_relevant"

    def test_case_json_serialization(self):
        """Test Case JSON serialization."""
        case = Case(
            id="A40-123456/2024",
            case_number="А40-123456/2024",
            court="Арбитражный суд города Москвы",
            plaintiff="ООО 'Строитель'",
            defendant="ООО 'Заказчик'",
            category="construction",
        )
        
        json_str = case.model_dump_json()
        assert "A40-123456/2024" in json_str
        assert "construction" in json_str

    def test_case_from_case_base(self):
        """Test creating Case from basic data."""
        case = Case(
            id="A40-123456/2024",
            case_number="А40-123456/2024",
            court="Арбитражный суд города Москвы",
            judges=["Иванов И.И."],
            plaintiff="ООО 'Строитель'",
            defendant="ООО 'Заказчик'",
        )
        
        # Convert to Case (simulating pipeline progression)
        case = Case(**case_base.model_dump())
        
        assert case.id == case_base.id
        assert case.case_number == case_base.case_number
        assert case.court == case_base.court
        assert case.judges == case_base.judges
        assert case.plaintiff == case_base.plaintiff
        assert case.defendant == case_base.defendant
