"""Tests for the agent loop."""

import pytest

from reviewmymcp.active.agent_loop import AgentLoop
from reviewmymcp.active.task_library import AgentTask, TaskCategory
from reviewmymcp.ingest.schema import ToolDefinition
from reviewmymcp.judge.base import JudgeRequest, JudgeResponse


class MockJudge:
    provider_name = "mock"

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self._call_count = 0

    async def complete(self, request: JudgeRequest) -> JudgeResponse:
        import json
        idx = min(self._call_count, len(self._responses) - 1)
        resp = self._responses[idx]
        self._call_count += 1
        raw = json.dumps(resp)
        return JudgeResponse(raw_text=raw, parsed=resp, model="mock", provider="mock")


SAMPLE_TOOLS = [
    ToolDefinition(
        name="search",
        description="Search for items",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
    ),
]


async def mock_call_tool(name: str, arguments: dict):
    if name == "search" and "q" in arguments:
        return {"result": {"content": [{"type": "text", "text": "found 3 results"}]}}
    return {"error": {"code": -1, "message": "invalid arguments"}}


@pytest.mark.asyncio
async def test_agent_completes_simple_task():
    judge = MockJudge([
        {
            "reasoning": "I need to search for something",
            "tool_calls": [{"tool": "search", "arguments": {"q": "test"}}],
            "response": "",
            "done": False,
        },
        {
            "reasoning": "Got results, I'm done",
            "tool_calls": [],
            "response": "Found 3 results",
            "done": True,
        },
    ])

    task = AgentTask(
        task_id="test",
        category=TaskCategory.SINGLE_TOOL,
        instruction="Search for test items",
        expected_tools=["search"],
        max_turns=5,
    )

    loop = AgentLoop(judge=judge, call_tool_fn=mock_call_tool, tools=SAMPLE_TOOLS)
    session = await loop.run_task(task)

    assert session.completed
    assert session.total_tool_calls == 1
    assert "search" in session.unique_tools_used
    assert len(session.turns) == 2


@pytest.mark.asyncio
async def test_agent_stops_on_max_turns():
    judge = MockJudge([
        {
            "reasoning": "trying",
            "tool_calls": [{"tool": "search", "arguments": {"q": "x"}}],
            "response": "",
            "done": False,
        },
    ])

    task = AgentTask(
        task_id="test",
        category=TaskCategory.SINGLE_TOOL,
        instruction="Search",
        max_turns=2,
    )

    loop = AgentLoop(judge=judge, call_tool_fn=mock_call_tool, tools=SAMPLE_TOOLS)
    session = await loop.run_task(task)

    assert len(session.turns) == 2
    assert not session.completed or session.turns[-1].chose_to_stop


@pytest.mark.asyncio
async def test_agent_handles_tool_error():
    judge = MockJudge([
        {
            "reasoning": "try with bad args",
            "tool_calls": [{"tool": "search", "arguments": {}}],
            "response": "",
            "done": False,
        },
        {
            "reasoning": "fix args",
            "tool_calls": [{"tool": "search", "arguments": {"q": "fixed"}}],
            "response": "",
            "done": False,
        },
        {
            "reasoning": "done",
            "tool_calls": [],
            "response": "complete",
            "done": True,
        },
    ])

    task = AgentTask(
        task_id="test",
        category=TaskCategory.ERROR_RECOVERY,
        instruction="Try searching",
        max_turns=5,
    )

    loop = AgentLoop(judge=judge, call_tool_fn=mock_call_tool, tools=SAMPLE_TOOLS)
    session = await loop.run_task(task)

    assert session.errors_encountered >= 1
    assert session.completed
