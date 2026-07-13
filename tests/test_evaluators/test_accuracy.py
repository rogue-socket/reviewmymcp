"""Tests for accuracy evaluators."""

from reviewmymcp.evaluators.accuracy.checks import AccuracyEvaluator
from reviewmymcp.evaluators.base import EvaluatorConfig
from tests.conftest import make_server_meta, make_tool_call_pair, make_tool_def


def test_schema_misuse_invalid_accepted():
    tool = make_tool_def("create", properties={"name": {"type": "string"}}, required=["name"])
    meta = make_server_meta([tool])

    # Call with integer instead of string — schema violation — but server accepts
    req, resp = make_tool_call_pair("create", {"name": 12345}, request_id=1)
    evaluator = AccuracyEvaluator()
    result = evaluator.evaluate([req, resp], meta, EvaluatorConfig())
    misuse = [f for f in result.findings if f.check_id == "accuracy.schema-misuse"]
    assert any("invalid" in f.title.lower() for f in misuse)


def test_schema_misuse_valid_rejected():
    tool = make_tool_def("search", properties={"q": {"type": "string"}}, required=["q"])
    meta = make_server_meta([tool])

    req, resp = make_tool_call_pair("search", {"q": "test"}, is_error=True, request_id=1)
    evaluator = AccuracyEvaluator()
    result = evaluator.evaluate([req, resp], meta, EvaluatorConfig())
    misuse = [f for f in result.findings if f.check_id == "accuracy.schema-misuse"]
    assert any("rejected" in f.title.lower() for f in misuse)


def test_argument_validation_gap():
    tool = make_tool_def("delete", properties={"id": {"type": "integer"}}, required=["id"])
    meta = make_server_meta([tool])

    # Missing required "id" but server returns success
    req, resp = make_tool_call_pair("delete", {}, request_id=1)
    evaluator = AccuracyEvaluator()
    result = evaluator.evaluate([req, resp], meta, EvaluatorConfig())
    gaps = [f for f in result.findings if f.check_id == "accuracy.argument-validation-gap"]
    assert len(gaps) == 1


def test_error_channel_correctness_detects_error_as_success_content():
    tool = make_tool_def("fetch_content")
    meta = make_server_meta([tool])
    req, resp = make_tool_call_pair(
        "fetch_content",
        {"url": "https://example.invalid/missing"},
        result_content=[{"type": "text", "text": "错误: HTTP 404 - 无法访问网页"}],
        request_id=1,
    )
    resp.result["isError"] = False

    evaluator = AccuracyEvaluator()
    result = evaluator.evaluate([req, resp], meta, EvaluatorConfig())
    channel = [f for f in result.findings if f.check_id == "accuracy.error-channel-correctness"]

    assert len(channel) == 1
    assert channel[0].affected_entity == "fetch_content"


def test_error_channel_correctness_ignores_is_error_true():
    tool = make_tool_def("fetch_content")
    meta = make_server_meta([tool])
    req, resp = make_tool_call_pair(
        "fetch_content",
        {"url": "https://example.invalid/missing"},
        result_content=[{"type": "text", "text": "Error: HTTP 404 - unable to access webpage"}],
        request_id=1,
    )
    resp.result["isError"] = True

    evaluator = AccuracyEvaluator()
    result = evaluator.evaluate([req, resp], meta, EvaluatorConfig())
    channel = [f for f in result.findings if f.check_id == "accuracy.error-channel-correctness"]

    assert channel == []


def test_error_message_quality_generic():
    tool = make_tool_def("query")
    meta = make_server_meta([tool])

    events = []
    for i in range(5):
        req, resp = make_tool_call_pair(
            "query",
            {"sql": "SELECT 1"},
            result_content=[{"type": "text", "text": "error"}],
            is_error=True,
            request_id=i + 1,
        )
        # Override to use isError in result
        resp.is_error = False
        resp.result = {"content": [{"type": "text", "text": "something went wrong"}], "isError": True}
        events.extend([req, resp])

    evaluator = AccuracyEvaluator()
    result = evaluator.evaluate(events, meta, EvaluatorConfig())
    quality = [f for f in result.findings if f.check_id == "accuracy.error-message-quality"]
    assert len(quality) == 1


def test_no_findings_on_clean_calls():
    tool = make_tool_def("search", properties={"q": {"type": "string"}}, required=["q"])
    meta = make_server_meta([tool])

    req, resp = make_tool_call_pair("search", {"q": "test"}, request_id=1)
    evaluator = AccuracyEvaluator()
    result = evaluator.evaluate([req, resp], meta, EvaluatorConfig())
    assert all(f.check_id != "accuracy.argument-validation-gap" for f in result.findings)
