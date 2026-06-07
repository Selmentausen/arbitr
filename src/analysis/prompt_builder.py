"""Assemble system and user prompts from classification config."""

from src.analysis.models import BuiltPrompt
from src.config.classification import ClassificationConfig


def _format_category_definitions(categories: list[dict]) -> str:
    lines = []
    for cat in categories:
        lines.append(f"- {cat['id']} ({cat.get('label', cat['id'])}): {cat.get('description', '').strip()}")
    return "\n".join(lines)


def _format_disambiguation_rules(rules: list[str]) -> str:
    return "\n".join(f"- {rule}" for rule in rules)


def _format_few_shot_examples(examples: list[dict]) -> str:
    lines = []
    for i, ex in enumerate(examples, 1):
        expected = ex.get("expected", ex.get("category", ""))
        summary = (ex.get("summary") or "").strip()
        lines.append(f"Пример {i} → {expected}:\n{summary}")
    return "\n\n".join(lines)


def build_prompt(dossier: str, config: ClassificationConfig) -> BuiltPrompt:
    """Build system and user messages from config templates."""
    prompts = config.get("prompts", {}) or {}
    system_template = prompts.get("system", "")
    user_template = prompts.get("user", "{case_dossier}")

    category_definitions = _format_category_definitions(config.categories)
    disambiguation = _format_disambiguation_rules(config.get("disambiguation_rules", []) or [])
    few_shot = _format_few_shot_examples(config.get("few_shot_examples", []) or [])

    system = system_template.format(
        category_definitions=category_definitions,
        disambiguation_rules=disambiguation,
        few_shot_examples=few_shot,
        case_dossier=dossier,
    )
    user = user_template.format(case_dossier=dossier)

    return BuiltPrompt(system=system, user=user, dossier=dossier)
