"""Data models for Prod2 active agent-driven audit."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from reviewmymcp.ingest.schema import ServerMeta


class TaskCategory(StrEnum):
    """Categories of tasks the agent is given."""

    DISCOVERY = "discovery"
    SINGLE_TOOL = "single_tool"
    MULTI_STEP = "multi_step"
    ERROR_RECOVERY = "error_recovery"
    AMBIGUOUS = "ambiguous"
    EDGE_CASE = "edge_case"


class BehavioralSignal(StrEnum):
    """Observable behavioral signals from agent execution."""

    TOOL_FOUND = "tool_found"
    TOOL_NOT_FOUND = "tool_not_found"
    ARGUMENT_STRUGGLE = "argument_struggle"
    GAVE_UP = "gave_up"
    CHAINING_FAILURE = "chaining_failure"
    CHAINING_SUCCESS = "chaining_success"
    ERROR_RECOVERED = "error_recovered"
    ERROR_NOT_RECOVERED = "error_not_recovered"
    CHOSE_TO_STOP = "chose_to_stop"
    TURN_LIMIT_HIT = "turn_limit_hit"
    CORRECT_TOOL_SELECTED = "correct_tool_selected"
    WRONG_TOOL_SELECTED = "wrong_tool_selected"
    INJECTION_IN_DESCRIPTION = "injection_in_description"
    INJECTION_IN_OUTPUT = "injection_in_output"
    UNTRUSTED_CONTENT_NO_PROVENANCE = "untrusted_content_no_provenance"


class ToolCallAttempt(BaseModel):
    """A single tool call attempt by the agent."""

    tool_name: str
    arguments: dict[str, Any] = {}
    call_id: str = ""
    result: dict[str, Any] | None = None
    is_error: bool = False
    latency_ms: float = 0.0


class AgentTurn(BaseModel):
    """One turn in the agent conversation."""

    turn_number: int
    tool_calls: list[ToolCallAttempt] = []
    text_response: str = ""
    signals: list[BehavioralSignal] = []
    stop_reason: str = ""


class ActiveTask(BaseModel):
    """A task given to the agent."""

    category: TaskCategory
    description: str
    expected_tools: list[str] = []
    success_criteria: str = ""


class TaskExecution(BaseModel):
    """Result of executing a single task."""

    task: ActiveTask
    turns: list[AgentTurn] = []
    signals: list[BehavioralSignal] = []
    outcome: str = "pending"  # success, partial, failure, gave_up
    total_turns: int = 0


class ActiveDimensionScore(BaseModel):
    """Score for one Prod2 dimension."""

    dimension: str
    score: float = 100.0
    grade: str = "A"
    signal_counts: dict[str, int] = {}
    task_outcomes: dict[str, int] = {}


class ActiveAuditReport(BaseModel):
    """Complete Prod2 active audit report."""

    server_meta: ServerMeta
    task_executions: list[TaskExecution] = []
    dimension_scores: list[ActiveDimensionScore] = []
    total_tasks: int = 0
    total_turns: int = 0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
