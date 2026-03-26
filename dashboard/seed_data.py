"""
Seed the database with sample cases for dashboard testing.

Run with:
    poetry run python dashboard/seed_data.py
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from datetime import datetime, timedelta
import random

from src.storage.database import init_db
from src.storage.repository import CaseRepository
from src.models.case import Case, CaseParticipant, CaseInstance, StatusEnum


DB_PATH = str(project_root / "data" / "arbitr.db")

COURTS = [
    "Арбитражный суд города Москвы",
    "Арбитражный суд Московской области",
    "Арбитражный суд Санкт-Петербурга и Ленинградской области",
    "Девятый арбитражный апелляционный суд",
    "Арбитражный суд Свердловской области",
]

JUDGES = [
    "Солдатов Р. С.",
    "Иванов И. И.",
    "Петров П. П.",
    "Сидорова А. В.",
    "Козлова М. Н.",
]

PLAINTIFFS = [
    "ООО 'СтройГарант'",
    "ООО 'МосПодряд'",
    "АО 'ГринБилд'",
    "ООО 'Подряд-Сервис'",
    "ИП Петров А.С.",
    "ООО 'ТехМонтаж'",
    "ООО 'Кредитор Плюс'",
    "АО 'Строительный Альянс'",
    "ООО 'Медиа Групп'",
    "ПАО 'РусИнвест'",
]

DEFENDANTS = [
    "ООО 'Заказчик-Строй'",
    "АО 'Девелопмент XXI'",
    "ООО 'ГорСтрой'",
    "ООО 'Промышленное Строительство'",
    "АО 'Ремонт-Сервис'",
    "ООО 'БанкротМенеджмент'",
    "ООО 'Торг-Инвест'",
    "ИП Сидоров В.Г.",
    "ООО 'Должник'",
    "ООО 'КонтрактСтрой'",
]

CATEGORIES = ["construction", "construction", "construction", "bankruptcy", None]
STATUSES = [
    StatusEnum.HIGH_RELEVANT,
    StatusEnum.HIGH_RELEVANT,
    StatusEnum.UNCERTAIN,
    StatusEnum.UNCERTAIN,
    StatusEnum.INSUFFICIENT_INFO,
    StatusEnum.REJECT,
]


def generate_sample_cases(count: int = 30) -> list[Case]:
    """Generate sample cases for testing."""
    cases = []
    base_date = datetime(2024, 1, 15)

    for i in range(count):
        case_num = f"А40-{100000 + i}/2024"
        case_id = f"sample-uuid-{i:04d}"
        status = random.choice(STATUSES)
        category = random.choice(CATEGORIES)

        score = 0.0
        if status == StatusEnum.HIGH_RELEVANT:
            score = random.uniform(80, 100)
        elif status == StatusEnum.UNCERTAIN:
            score = random.uniform(30, 79)
        elif status == StatusEnum.INSUFFICIENT_INFO:
            score = random.uniform(0, 29)
        elif status == StatusEnum.REJECT:
            score = random.uniform(0, 20)

        filing_date = base_date + timedelta(days=random.randint(0, 365))
        plaintiff = random.choice(PLAINTIFFS)
        defendant = random.choice(DEFENDANTS)
        court = random.choice(COURTS)
        judge = random.choice(JUDGES)

        participants = {
            "plaintiffs": [CaseParticipant(name=plaintiff, address=f"г. Москва, ул. Тестовая {i}")],
            "defendants": [CaseParticipant(name=defendant, inn=f"{1000000000 + i}")],
        }

        instances = [
            CaseInstance(
                court_name=court,
                case_number=case_num,
                date=filing_date.strftime("%d.%m.%Y"),
            )
        ]

        case = Case(
            id=case_id,
            case_number=case_num,
            court=court,
            judges=[judge],
            plaintiff=plaintiff,
            defendant=defendant,
            filing_date=filing_date,
            case_url=f"https://kad.arbitr.ru/Card/{case_id}",
            category=category,
            relevance_score=round(score, 1),
            status=status,
            participants=participants,
            instances=instances,
            extracted_data={
                "stage1_score": round(score, 1),
                "sample": True,
            },
        )
        cases.append(case)

    return cases


def main():
    print(f"Initializing database at {DB_PATH}...")
    init_db(DB_PATH)

    repo = CaseRepository()
    cases = generate_sample_cases(30)

    print(f"Seeding {len(cases)} sample cases...")
    count = repo.save_cases(cases)
    print(f"✓ Saved {count} cases")

    # Mark a few as reviewed
    for case in cases[:5]:
        repo.mark_reviewed(case.id, reviewed=True, notes="Автоматически проверено (тестовые данные)")

    stats = repo.get_stats()
    print(f"\nDatabase stats:")
    print(f"  Total: {stats['total_cases']}")
    print(f"  By status: {stats['by_status']}")
    print(f"  By category: {stats['by_category']}")
    print(f"  Reviewed: {stats['reviewed']}")
    print(f"  Avg score: {stats['avg_relevance_score']}")

    print(f"\n✓ Done! Run dashboard with:")
    print(f"  poetry run streamlit run dashboard/app.py")

    repo.close()


if __name__ == "__main__":
    main()
