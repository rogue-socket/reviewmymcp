"""Tests for active audit task generator."""

from __future__ import annotations

import pytest

from reviewmymcp.active.models import TaskCategory
from reviewmymcp.active.task_generator import TaskGenerator
from reviewmymcp.ingest.schema import ToolDefinition


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


@pytest.mark.asyncio
async def test_fallback_generates_all_categories():
    """Without LLM, fallback generates tasks for all 6 categories."""
    gen = TaskGenerator(judge_provider=None)
    tasks = await gen.generate_tasks(_make_tools())

    categories = {t.category for t in tasks}
    assert TaskCategory.DISCOVERY in categories
    assert TaskCategory.SINGLE_TOOL in categories
    assert TaskCategory.MULTI_STEP in categories
    assert TaskCategory.ERROR_RECOVERY in categories
    assert TaskCategory.AMBIGUOUS in categories
    assert TaskCategory.EDGE_CASE in categories


@pytest.mark.asyncio
async def test_fallback_with_specific_categories():
    """Fallback respects the categories filter."""
    gen = TaskGenerator(judge_provider=None)
    tasks = await gen.generate_tasks(_make_tools(), categories=[TaskCategory.DISCOVERY, TaskCategory.SINGLE_TOOL])

    categories = {t.category for t in tasks}
    assert categories == {TaskCategory.DISCOVERY, TaskCategory.SINGLE_TOOL}


@pytest.mark.asyncio
async def test_fallback_references_real_tools():
    """Fallback tasks reference tools that actually exist."""
    tools = _make_tools()
    tool_names = {t.name for t in tools}
    gen = TaskGenerator(judge_provider=None)
    tasks = await gen.generate_tasks(tools)

    for task in tasks:
        for expected in task.expected_tools:
            assert expected in tool_names, f"Task references unknown tool '{expected}'"


@pytest.mark.asyncio
async def test_fallback_empty_tools():
    """Fallback with no tools generates only discovery task (others need tools)."""
    gen = TaskGenerator(judge_provider=None)
    tasks = await gen.generate_tasks([], categories=[TaskCategory.DISCOVERY])

    assert len(tasks) == 1
    assert tasks[0].category == TaskCategory.DISCOVERY


@pytest.mark.asyncio
async def test_single_tool_only():
    """With only one tool, multi_step is skipped (needs >= 2 tools)."""
    tools = [_make_tools()[0]]
    gen = TaskGenerator(judge_provider=None)
    tasks = await gen.generate_tasks(tools, categories=[TaskCategory.MULTI_STEP])

    # multi_step requires >= 2 tools, so no task generated
    assert len(tasks) == 0
