"""Evaluator protocol, Finding, and EvaluatorResult definitions."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from reviewmymcp.ingest.schema import McpEvent, ServerMeta


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Finding(BaseModel):
    """A single audit finding from an evaluator check."""

    check_id: str
    severity: Severity
    title: str
    description: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    remediation: str = ""
    affected_entity: str = ""


class EvaluatorResult(BaseModel):
    """Output from a single evaluator run."""

    dimension: str
    checks_run: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class EvaluatorConfig(BaseModel):
    """Configuration passed to evaluators. Evaluators can read dimension-specific keys."""

    thresholds: dict[str, Any] = Field(default_factory=dict)
    enabled_checks: list[str] | None = None
    judge_provider: str = "anthropic"
    judge_model: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class Evaluator(Protocol):
    """Protocol that all evaluators must implement."""

    dimension: str

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult: ...
