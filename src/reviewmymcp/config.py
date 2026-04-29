"""Configuration loading and defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class JudgeConfig(BaseModel):
    provider: str = "anthropic"
    model: str = ""
    temperature: float = 0.0
    max_tokens: int = 1024
    dual_call: bool = True


class RedactionConfig(BaseModel):
    enabled: bool = True
    extra_patterns: list[str] = Field(default_factory=list)


class ScoringConfig(BaseModel):
    """Configurable severity weights and normalization multiplier for the grading engine."""

    severity_weights: dict[str, float] = Field(default_factory=lambda: {
        "critical": 25.0,
        "high": 10.0,
        "medium": 4.0,
        "low": 1.0,
        "info": 0.0,
    })
    normalization_multiplier: float = 4.0


class AuditConfig(BaseModel):
    """Top-level configuration for an audit run."""

    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    dimensions: list[str] | None = None
    min_severity: str = "info"
    thresholds: dict[str, Any] = Field(default_factory=dict)
    output_format: str = "terminal"
    output_file: str | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> AuditConfig:
        with open(path) as f:
            data = json.load(f)
        return cls.model_validate(data)

    @classmethod
    def default(cls) -> AuditConfig:
        return cls()
