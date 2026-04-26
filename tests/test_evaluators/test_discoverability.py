"""Tests for discoverability evaluators."""

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.discoverability.checks import DiscoverabilityEvaluator

from tests.conftest import make_server_meta, make_tool_def


def test_name_quality_short_name():
    tool = make_tool_def("gd", description="Get document")
    meta = make_server_meta([tool])
    evaluator = DiscoverabilityEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    name_findings = [f for f in result.findings if f.check_id == "discoverability.name-quality"]
    assert any("gd" in f.title for f in name_findings)


def test_name_quality_no_verb():
    tool = make_tool_def("documents", description="Access documents")
    meta = make_server_meta([tool])
    evaluator = DiscoverabilityEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    name_findings = [f for f in result.findings if f.check_id == "discoverability.name-quality"]
    assert any("verb" in f.title.lower() for f in name_findings)


def test_name_quality_good_name():
    tool = make_tool_def("search_users", description="Search for users")
    meta = make_server_meta([tool])
    evaluator = DiscoverabilityEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    name_findings = [f for f in result.findings if f.check_id == "discoverability.name-quality"]
    assert len(name_findings) == 0


def test_missing_examples_complex_schema():
    tool = make_tool_def(
        "advanced_search",
        description="Search with filters",
        properties={
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "offset": {"type": "integer"},
            "format": {"type": "string"},
        },
        required=["query", "limit", "offset", "format"],
    )
    meta = make_server_meta([tool])
    evaluator = DiscoverabilityEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    missing = [f for f in result.findings if f.check_id == "discoverability.missing-examples"]
    assert len(missing) == 1


def test_missing_examples_with_example_in_description():
    tool = make_tool_def(
        "advanced_search",
        description='Search with filters. Example: {"query": "test", "limit": 10, "offset": 0, "format": "json"}',
        properties={
            "query": {"type": "string"},
            "limit": {"type": "integer"},
            "offset": {"type": "integer"},
            "format": {"type": "string"},
        },
        required=["query", "limit", "offset", "format"],
    )
    meta = make_server_meta([tool])
    evaluator = DiscoverabilityEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    missing = [f for f in result.findings if f.check_id == "discoverability.missing-examples"]
    assert len(missing) == 0


def test_rest_wrapper_smell():
    tools = [
        make_tool_def("create_user", description="POST /api/v1/users"),
        make_tool_def("get_user", description="GET /api/v1/users/:id"),
        make_tool_def("update_user", description="PUT /api/v1/users/:id"),
        make_tool_def("delete_user", description="DELETE /api/v1/users/:id"),
        make_tool_def("list_users", description="GET /api/v1/users"),
    ]
    meta = make_server_meta(tools)
    evaluator = DiscoverabilityEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    rest = [f for f in result.findings if f.check_id == "discoverability.rest-wrapper-smell"]
    assert len(rest) >= 1  # at least the CRUD pattern finding


def test_no_rest_smell_for_task_oriented_tools():
    tools = [
        make_tool_def("search_documents", description="Search the document store"),
        make_tool_def("onboard_user", description="Complete user onboarding"),
    ]
    meta = make_server_meta(tools)
    evaluator = DiscoverabilityEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    rest = [f for f in result.findings if "rest-wrapper" in f.check_id]
    assert len(rest) == 0
