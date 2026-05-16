"""Tests for active-audit safety guards (mutator classification + trace writer)."""

from __future__ import annotations

import json
from pathlib import Path

from reviewmymcp.active.models import ActiveTask, AgentTurn, TaskCategory, TaskExecution, ToolCallAttempt
from reviewmymcp.active.safety import (
    WRITE_PREFIXES,
    classify_mutators,
    filter_readonly,
    write_trace,
)
from reviewmymcp.ingest.schema import ToolDefinition


def _tool(name: str) -> ToolDefinition:
    return ToolDefinition(name=name, description=f"Tool: {name}", input_schema={"type": "object"})


def test_classify_mutators_catches_each_write_prefix():
    tools = [_tool(prefix + "thing") for prefix in WRITE_PREFIXES]
    assert classify_mutators(tools) == [t.name for t in tools]


def test_classify_mutators_passes_through_read_only_tools():
    tools = [_tool("read_file"), _tool("list_directory"), _tool("get_issue"), _tool("search")]
    assert classify_mutators(tools) == []


def test_classify_mutators_preserves_input_order():
    tools = [_tool("read_file"), _tool("create_repository"), _tool("get_issue"), _tool("delete_branch")]
    assert classify_mutators(tools) == ["create_repository", "delete_branch"]


def test_classify_mutators_prefix_only_not_substring():
    # "increate_status" doesn't start with "create_" — must not match.
    tools = [_tool("increate_status"), _tool("recreate_log")]
    assert classify_mutators(tools) == []


def test_filter_readonly_drops_only_mutators():
    tools = [_tool("read_file"), _tool("create_repository"), _tool("list_directory"), _tool("delete_branch")]
    remaining = filter_readonly(tools)
    assert [t.name for t in remaining] == ["read_file", "list_directory"]


def test_write_trace_emits_one_record_per_turn(tmp_path: Path):
    execution = TaskExecution(
        task=ActiveTask(category=TaskCategory.SINGLE_TOOL, description="Find X"),
        turns=[
            AgentTurn(
                turn_number=1,
                text_response="Calling search.",
                stop_reason="tool_use",
                tool_calls=[
                    ToolCallAttempt(
                        tool_name="search",
                        arguments={"q": "x"},
                        call_id="c1",
                        result={"hits": 3},
                        is_error=False,
                    ),
                ],
            ),
            AgentTurn(turn_number=2, text_response="Done.", stop_reason="end_turn"),
        ],
        outcome="success",
        total_turns=2,
    )

    path = tmp_path / "trace.jsonl"
    write_trace([execution], path)

    lines = path.read_text().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["task_category"] == "single_tool"
    assert first["task_description"] == "Find X"
    assert first["turn_number"] == 1
    assert first["text_response"] == "Calling search."
    assert first["stop_reason"] == "tool_use"
    assert len(first["tool_calls"]) == 1
    assert first["tool_calls"][0] == {
        "tool_name": "search",
        "arguments": {"q": "x"},
        "result": {"hits": 3},
        "is_error": False,
        "call_id": "c1",
    }

    second = json.loads(lines[1])
    assert second["turn_number"] == 2
    assert second["tool_calls"] == []


def test_write_trace_handles_multiple_executions(tmp_path: Path):
    executions = [
        TaskExecution(
            task=ActiveTask(category=TaskCategory.DISCOVERY, description="Task A"),
            turns=[AgentTurn(turn_number=1, text_response="A")],
        ),
        TaskExecution(
            task=ActiveTask(category=TaskCategory.SINGLE_TOOL, description="Task B"),
            turns=[AgentTurn(turn_number=1, text_response="B1"), AgentTurn(turn_number=2, text_response="B2")],
        ),
    ]
    path = tmp_path / "trace.jsonl"
    write_trace(executions, path)

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(rec["task_description"], rec["turn_number"]) for rec in lines] == [
        ("Task A", 1),
        ("Task B", 1),
        ("Task B", 2),
    ]


def test_active_audit_refuses_mutating_server_without_flag():
    """End-to-end: CLI exits 2 with a clear message when mutators are detected."""
    from unittest.mock import AsyncMock, patch

    from click.testing import CliRunner

    from reviewmymcp.cli import cli

    # Build a fake driver that reports two mutating tools and one read-only one.
    fake_driver = AsyncMock()
    fake_driver.start = AsyncMock()
    fake_driver.initialize = AsyncMock()
    fake_driver.send_initialized = AsyncMock()
    fake_driver.stop = AsyncMock()
    fake_driver.events = []
    fake_driver.list_tools = AsyncMock(return_value={
        "result": {
            "tools": [
                {"name": "get_issue", "description": "Read an issue", "inputSchema": {}},
                {"name": "create_repository", "description": "Create a repo", "inputSchema": {}},
                {"name": "delete_branch", "description": "Delete a branch", "inputSchema": {}},
            ]
        }
    })

    with patch("reviewmymcp.synthetic.agent_driver.StdioAgentDriver", return_value=fake_driver), \
         patch("reviewmymcp.cli._build_agent_provider", return_value=object()), \
         patch("reviewmymcp.cli._build_judge", return_value=None):
        runner = CliRunner()
        result = runner.invoke(cli, ["active-audit", "fake-server"])

    assert result.exit_code == 2, result.output
    assert "create_repository" in result.output
    assert "delete_branch" in result.output
    assert "--readonly" in result.output
    assert "--allow-mutations" in result.output


def test_active_audit_rejects_both_flags_together():
    from click.testing import CliRunner

    from reviewmymcp.cli import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["active-audit", "fake-server", "--readonly", "--allow-mutations"])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output
