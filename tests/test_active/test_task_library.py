"""Tests for active check task library."""

from reviewmymcp.active.task_library import TaskCategory, TaskLibrary
from reviewmymcp.ingest.schema import ToolDefinition


def _sample_tools() -> list[ToolDefinition]:
    return [
        ToolDefinition(
            name="search_documents",
            description="Search for documents by keyword",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["query"],
            },
        ),
        ToolDefinition(
            name="get_document",
            description="Get a document by ID",
            input_schema={
                "type": "object",
                "properties": {"document_id": {"type": "string"}},
                "required": ["document_id"],
            },
        ),
        ToolDefinition(
            name="create_document",
            description="Create a new document",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["title", "content"],
            },
        ),
    ]


def test_fallback_tasks_generates_expected_categories():
    tools = _sample_tools()
    library = TaskLibrary(judge=None)
    tasks = library._fallback_tasks(tools, 8)

    categories = {t.category for t in tasks}
    assert TaskCategory.DISCOVERY in categories
    assert TaskCategory.SINGLE_TOOL in categories
    assert TaskCategory.ERROR_RECOVERY in categories


def test_fallback_tasks_respects_count():
    tools = _sample_tools()
    library = TaskLibrary(judge=None)
    tasks = library._fallback_tasks(tools, 3)
    assert len(tasks) <= 3


def test_fallback_tasks_references_real_tools():
    tools = _sample_tools()
    library = TaskLibrary(judge=None)
    tasks = library._fallback_tasks(tools, 8)

    tool_names = {t.name for t in tools}
    for task in tasks:
        for expected in task.expected_tools:
            assert expected in tool_names


def test_fallback_tasks_with_single_tool():
    tools = [_sample_tools()[0]]
    library = TaskLibrary(judge=None)
    tasks = library._fallback_tasks(tools, 5)
    assert len(tasks) >= 2  # at least discovery + single_tool


def test_fallback_tasks_with_no_tools():
    library = TaskLibrary(judge=None)
    tasks = library._fallback_tasks([], 5)
    assert len(tasks) >= 1  # at least discovery
    assert tasks[0].category == TaskCategory.DISCOVERY
