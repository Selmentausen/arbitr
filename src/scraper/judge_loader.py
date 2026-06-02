"""Load judge names from config and normalize to site search format (Surname I. M.)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class JudgeEntry:
    """One judge line from judges.txt."""

    display_name: str
    search_name: str
    full_fallback: str


def _strip_alias_in_parens(line: str) -> str:
    """Remove a single parenthetical alias, e.g. '(Григорьева)' from the surname token."""
    return re.sub(r"\s*\([^)]+\)\s*", " ", line).strip()


def _to_search_name(full_line: str) -> str:
    """
    Convert 'Фамилия Имя Отчество' to 'Фамилия И. О.'.
    Expects three tokens after cleanup (surname, first name, patronymic).
    """
    cleaned = _strip_alias_in_parens(full_line)
    parts = cleaned.split()
    if len(parts) < 3:
        raise ValueError(f"Expected surname + first + patronymic, got: {full_line!r}")
    surname, first, patronymic = parts[0], parts[1], parts[2]
    i1 = first[0].upper() + "."
    i2 = patronymic[0].upper() + "."
    return f"{surname} {i1} {i2}"


def load_judges_from_file(path: str | Path) -> List[JudgeEntry]:
    """
    Read judges.txt: one full name per line, dedupe preserving order.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Judges file not found: {p}")

    seen: set[str] = set()
    out: List[JudgeEntry] = []

    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        norm = " ".join(line.split())
        if norm in seen:
            continue
        seen.add(norm)

        display = norm
        search_name = _to_search_name(norm)
        alias_stripped = _strip_alias_in_parens(norm)
        full_fallback = " ".join(alias_stripped.split())

        out.append(
            JudgeEntry(
                display_name=display,
                search_name=search_name,
                full_fallback=full_fallback,
            )
        )

    return out
