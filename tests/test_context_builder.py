"""Tests for case dossier builder."""

from datetime import datetime

from src.analysis.context_builder import build_case_dossier
from src.config.classification import ClassificationConfig
from src.models.case import Case, CaseInstance, InstanceUpdate


def test_dossier_includes_parties_and_chronology():
    case = Case(
        id="test-1",
        case_number="A40-1/2026",
        court="АС города Москвы",
        plaintiff="ООО Строй",
        defendant="ООО Подряд",
        case_category_text="экономические споры",
        claim_amount=1_000_000.0,
        instances=[
            CaseInstance(
                court_name="АС города Москвы",
                instance_level="Первая инстанция",
                updates=[
                    InstanceUpdate(
                        date="01.01.2026",
                        update_type="Определение",
                        content="О назначении экспертизы по договору подряда",
                    )
                ],
            )
        ],
    )
    config = ClassificationConfig()
    dossier = build_case_dossier(case, config)

    assert "A40-1/2026" in dossier
    assert "ООО Строй" in dossier
    assert "договору подряда" in dossier
    assert len(dossier) <= int(config.get("limits.max_total_context_chars", 16000)) + 50
