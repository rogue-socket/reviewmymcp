"""Configuration loading and defaults."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["anthropic", "gemini", "openai"] = "anthropic"
    model: str = ""


class RedactionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    extra_patterns: list[str] = Field(default_factory=list)


class ScoringConfig(BaseModel):
    """Configurable severity weights and square-root curve factor for the grading engine."""

    model_config = ConfigDict(extra="forbid")

    severity_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "critical": 25.0,
            "high": 10.0,
            "medium": 4.0,
            "low": 1.0,
            "info": 0.0,
        }
    )
    curve_factor: float = Field(default=5.0, gt=0)

    @field_validator("severity_weights")
    @classmethod
    def validate_severity_weights(cls, weights: dict[str, float]) -> dict[str, float]:
        invalid = set(weights) - {"critical", "high", "medium", "low", "info"}
        if invalid:
            raise ValueError(f"unknown severity weights: {', '.join(sorted(invalid))}")
        if any(weight < 0 for weight in weights.values()):
            raise ValueError("severity weights must be non-negative")
        return weights


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scopes_used: list[str] = Field(default_factory=list)


class PersistenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paths: dict[str, str] = Field(default_factory=dict)
    working_directory: str = ""
    package_directory: str = ""


class AuditConfig(BaseModel):
    """Top-level configuration for an audit run."""

    model_config = ConfigDict(extra="forbid")

    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    persistence: PersistenceConfig = Field(default_factory=PersistenceConfig)
    dimensions: list[str] | None = None
    min_severity: Literal["critical", "high", "medium", "low", "info"] = "info"
    thresholds: dict[str, Any] = Field(default_factory=dict)
    output_format: Literal["terminal", "json", "html", "sarif"] = "terminal"
    output_file: str | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> AuditConfig:
        with open(path) as f:
            data = json.load(f)
        return cls.model_validate(data)

    @classmethod
    def default(cls) -> AuditConfig:
        return cls()
