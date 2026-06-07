"""Pydantic models for ML classification."""

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class LLMClassificationResponse(BaseModel):
    """Raw structured response expected from the LLM."""

    probabilities: dict[str, float]
    inferred_other_category: Optional[str] = None
    reasoning: str = ""
    key_signals: list[str] = Field(default_factory=list)
    uncertainty: Literal["low", "medium", "high"] = "medium"

    @field_validator("uncertainty", mode="before")
    @classmethod
    def normalize_uncertainty(cls, v: object) -> str:
        if v is None:
            return "medium"
        s = str(v).lower().strip()
        if s in ("low", "medium", "high"):
            return s
        return "medium"


class ClassificationResult(BaseModel):
    """Validated classification result stored on a case."""

    probabilities: dict[str, float]
    primary_category: str
    inferred_other_category: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    key_signals: list[str] = Field(default_factory=list)
    uncertainty: Literal["low", "medium", "high"] = "medium"
    prompt_version: str = "1.0"
    model: str = ""
    analyzed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_storage_dict(self) -> dict:
        return {
            "probabilities": self.probabilities,
            "primary_category": self.primary_category,
            "inferred_other_category": self.inferred_other_category,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "key_signals": self.key_signals,
            "uncertainty": self.uncertainty,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "analyzed_at": self.analyzed_at.isoformat(),
        }

    @classmethod
    def from_storage_dict(cls, data: dict) -> "ClassificationResult":
        analyzed_at = data.get("analyzed_at")
        if isinstance(analyzed_at, str):
            analyzed_at = datetime.fromisoformat(analyzed_at.replace("Z", "+00:00"))
        elif analyzed_at is None:
            analyzed_at = datetime.now(timezone.utc)
        return cls(
            probabilities=data["probabilities"],
            primary_category=data["primary_category"],
            inferred_other_category=data.get("inferred_other_category"),
            confidence=data["confidence"],
            reasoning=data.get("reasoning", ""),
            key_signals=data.get("key_signals", []),
            uncertainty=data.get("uncertainty", "medium"),
            prompt_version=data.get("prompt_version", "1.0"),
            model=data.get("model", ""),
            analyzed_at=analyzed_at,
        )


class BuiltPrompt(BaseModel):
    """Assembled prompt ready for Ollama."""

    system: str
    user: str
    dossier: str
