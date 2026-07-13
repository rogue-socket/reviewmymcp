"""Tests for the active audit agent loop."""

from __future__ import annotations

import pytest

from reviewmymcp.active.agent_loop import AgentLoop
from reviewmymcp.active.models import ActiveTask, BehavioralSignal, TaskCategory
from reviewmymcp.ingest.schema import ToolDefinition
from reviewmymcp.judge.base import AgentToolCall, AgentTurnResponse
from tests.test_active.conftest import MockAgentProvider, MockCallTool


def _make_tools():
    return [
        ToolDefinition(
            name="search",
            description="Search for items",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        ),
        ToolDefinition(
            name="get_item",
            description="Get an item by ID",
            input_schema={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        ),
    ]


def _make_task(category=TaskCategory.SINGLE_TOOL, expected_tools=None):
    return ActiveTask(
        category=category,
        description="Search for test items",
        expected_tools=expected_tools or ["search"],
        success_criteria="Agent calls search with valid query",
    )


@pytest.mark.asyncio
async def test_single_tool_call_then_stop():
    """Agent makes one tool call, gets result, then stops with text."""
    agent = MockAgentProvider(
        [
            AgentTurnResponse(
                tool_calls=[AgentToolCall(tool_name="search", arguments={"q": "test"}, call_id="c1")],
                stop_reason="tool_use",
            ),
            AgentTurnResponse(text="Found results.", stop_reason="end_turn"),
        ]
    )
    mock_tool = MockCallTool()
    loop = AgentLoop(agent_provider=agent, call_tool_fn=mock_tool, tools=_make_tools())

    result = await loop.execute_task(_make_task())

    assert result.total_turns == 2
    assert result.outcome == "success"
    assert len(mock_tool.calls) == 1
    assert mock_tool.calls[0][0] == "search"
    second_turn_messages = agent.calls[1]["messages"]
    tool_result_message = second_turn_messages[-1]
    assert tool_result_message["role"] == "user"
    assert tool_result_message["content"][0]["type"] == "tool_result"
    assert tool_result_message["content"][0]["tool_use_id"] == "c1"
    assert BehavioralSignal.TOOL_FOUND in result.signals
    assert BehavioralSignal.CHOSE_TO_STOP in result.signals


@pytest.mark.asyncio
async def test_turn_limit_hit():
    """Agent keeps calling tools until max_turns is reached."""
    responses = [
        AgentTurnResponse(
            tool_calls=[AgentToolCall(tool_name="search", arguments={"q": f"try{i}"}, call_id=f"c{i}")],
            stop_reason="tool_use",
        )
        for i in range(5)
    ]
    agent = MockAgentProvider(responses)
    mock_tool = MockCallTool()
    loop = AgentLoop(agent_provider=agent, call_tool_fn=mock_tool, tools=_make_tools(), max_turns=3)

    result = await loop.execute_task(_make_task())

    assert result.total_turns == 3
    assert BehavioralSignal.TURN_LIMIT_HIT in result.signals


@pytest.mark.asyncio
async def test_tool_definitions_passed_to_provider():
    """Verify that tool definitions are correctly passed to the LLM provider."""
    agent = MockAgentProvider(
        [
            AgentTurnResponse(text="Done.", stop_reason="end_turn"),
        ]
    )
    tools = _make_tools()
    loop = AgentLoop(agent_provider=agent, call_tool_fn=MockCallTool(), tools=tools)

    await loop.execute_task(_make_task())

    assert len(agent.calls) == 1
    llm_tools = agent.calls[0]["tools"]
    assert len(llm_tools) == 2
    assert llm_tools[0]["name"] == "search"
    assert llm_tools[1]["name"] == "get_item"
    assert "input_schema" in llm_tools[0]


@pytest.mark.asyncio
async def test_unlisted_tool_call_is_rejected_without_dispatch():
    agent = MockAgentProvider(
        [
            AgentTurnResponse(
                tool_calls=[AgentToolCall(tool_name="delete_item", arguments={"id": "1"}, call_id="c1")],
                stop_reason="tool_use",
            ),
            AgentTurnResponse(text="I cannot use that tool.", stop_reason="end_turn"),
        ]
    )
    mock_tool = MockCallTool()
    loop = AgentLoop(agent_provider=agent, call_tool_fn=mock_tool, tools=_make_tools())

    result = await loop.execute_task(_make_task())

    assert mock_tool.calls == []
    attempt = result.turns[0].tool_calls[0]
    assert attempt.is_error is True
    assert attempt.result == {"error": "Tool `delete_item` is not available to this audit."}


@pytest.mark.asyncio
async def test_error_recovery():
    """Agent calls tool, gets error, retries with different args, succeeds."""
    agent = MockAgentProvider(
        [
            # First attempt — will get error
            AgentTurnResponse(
                tool_calls=[AgentToolCall(tool_name="search", arguments={"q": ""}, call_id="c1")],
                stop_reason="tool_use",
            ),
            # Retry — will succeed
            AgentTurnResponse(
                tool_calls=[AgentToolCall(tool_name="search", arguments={"q": "test"}, call_id="c2")],
                stop_reason="tool_use",
            ),
            AgentTurnResponse(text="Found results.", stop_reason="end_turn"),
        ]
    )

    call_count = 0

    async def mock_tool_fn(name, args):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"result": {"content": [{"type": "text", "text": "Error: empty query"}], "isError": True}}
        return {"result": {"content": [{"type": "text", "text": '{"ok": true}'}], "isError": False}}

    loop = AgentLoop(agent_provider=agent, call_tool_fn=mock_tool_fn, tools=_make_tools())
    result = await loop.execute_task(_make_task())

    assert result.total_turns == 3
    assert BehavioralSignal.ARGUMENT_STRUGGLE in result.signals
    assert BehavioralSignal.ERROR_RECOVERED in result.signals


@pytest.mark.asyncio
async def test_gave_up():
    """Agent produces text indicating it cannot complete the task."""
    agent = MockAgentProvider(
        [
            AgentTurnResponse(text="I'm sorry, I cannot complete this task.", stop_reason="end_turn"),
        ]
    )
    loop = AgentLoop(agent_provider=agent, call_tool_fn=MockCallTool(), tools=_make_tools())

    result = await loop.execute_task(_make_task())

    assert result.total_turns == 1
    assert result.outcome == "gave_up"
    assert BehavioralSignal.GAVE_UP in result.signals


@pytest.mark.asyncio
async def test_chaining_two_tools():
    """Agent calls tool A, then uses result to call tool B."""
    agent = MockAgentProvider(
        [
            AgentTurnResponse(
                tool_calls=[AgentToolCall(tool_name="search", arguments={"q": "test"}, call_id="c1")],
                stop_reason="tool_use",
            ),
            AgentTurnResponse(
                tool_calls=[AgentToolCall(tool_name="get_item", arguments={"id": "123"}, call_id="c2")],
                stop_reason="tool_use",
            ),
            AgentTurnResponse(text="Got the item.", stop_reason="end_turn"),
        ]
    )
    mock_tool = MockCallTool()
    task = _make_task(
        category=TaskCategory.MULTI_STEP,
        expected_tools=["search", "get_item"],
    )
    loop = AgentLoop(agent_provider=agent, call_tool_fn=mock_tool, tools=_make_tools())

    result = await loop.execute_task(task)

    assert result.total_turns == 3
    assert result.outcome == "success"
    assert BehavioralSignal.CHAINING_SUCCESS in result.signals


@pytest.mark.asyncio
async def test_tool_not_found():
    """Agent tries to call a tool that doesn't exist on the server."""
    agent = MockAgentProvider(
        [
            AgentTurnResponse(
                tool_calls=[AgentToolCall(tool_name="nonexistent", arguments={}, call_id="c1")],
                stop_reason="tool_use",
            ),
            AgentTurnResponse(text="That tool doesn't exist.", stop_reason="end_turn"),
        ]
    )

    async def tool_fn(name, args):
        return None  # timeout / not found

    loop = AgentLoop(agent_provider=agent, call_tool_fn=tool_fn, tools=_make_tools())
    result = await loop.execute_task(_make_task())

    assert BehavioralSignal.TOOL_NOT_FOUND in result.signals


@pytest.mark.asyncio
async def test_provider_failure_becomes_gave_up_trace():
    class FailingProvider:
        provider_name = "failing"

        async def agent_turn(self, messages, tools, system="", max_tokens=4096):
            raise RuntimeError("provider unavailable")

    loop = AgentLoop(agent_provider=FailingProvider(), call_tool_fn=MockCallTool(), tools=_make_tools())
    result = await loop.execute_task(_make_task())

    assert result.total_turns == 1
    assert result.turns[0].stop_reason == "provider_error"
    assert result.outcome == "gave_up"
    assert BehavioralSignal.GAVE_UP in result.signals


@pytest.mark.asyncio
async def test_tool_call_exception_is_recorded_as_attempt_error():
    agent = MockAgentProvider(
        [
            AgentTurnResponse(
                tool_calls=[AgentToolCall(tool_name="search", arguments={"q": "test"}, call_id="c1")],
                stop_reason="tool_use",
            ),
            AgentTurnResponse(text="I cannot continue after the tool failed.", stop_reason="end_turn"),
        ]
    )

    async def failing_tool(name, args):
        raise RuntimeError("server disconnected")

    loop = AgentLoop(agent_provider=agent, call_tool_fn=failing_tool, tools=_make_tools())
    result = await loop.execute_task(_make_task())

    attempt = result.turns[0].tool_calls[0]
    assert attempt.is_error is True
    assert attempt.result == {"error": "tool call failed: server disconnected"}
