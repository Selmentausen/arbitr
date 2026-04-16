import sys
import asyncio
from src.storage.repository import CaseRepository
from src.models.case import CaseBase
from src.storage.database import init_db, CaseRecord, ParticipantRecord, CaseParticipantLink
from src.filters.stage1_screen import stage1_initial_screen
from src.config.manager import ConfigManager

init_db("data/arbitr.db")
repo = CaseRepository()

base = CaseBase(
    id="test-id", 
    case_number="A40", 
    court="Court", 
    plaintiff="Test Plaintiff", 
    defendant="Test Defendant"
)

config = ConfigManager("configs/main.yaml")
full_case = stage1_initial_screen(base, config)
print(f"Participants after stage1: {full_case.participants}")

repo.save_case(full_case)

saved = repo.get_case("test-id")
print(f"Saved case participants: {saved.participants}")
print(f"Saved plaintiff string: {saved.plaintiff}")
print(f"Saved defendant string: {saved.defendant}")

