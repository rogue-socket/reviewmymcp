"""Tests for composability evaluators."""

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.composability.checks import ComposabilityEvaluator
from tests.conftest import make_server_meta, make_tool_call_pair


def test_error_recovery_ambiguous():
    events = []
    for i in range(4):
        req, resp = make_tool_call_pair("failing_tool", {"x": i}, is_error=True, request_id=i + 1)
        resp.is_error = False
        resp.result = {"content": [{"type": "text", "text": "error"}], "isError": True}
        events.extend([req, resp])

    evaluator = ComposabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    recovery = [f for f in result.findings if f.check_id == "composability.error-recovery-surface"]
    assert len(recovery) == 1
    assert "ambiguous" in recovery[0].title.lower()


def test_error_recovery_not_triggered_for_good_errors():
    events = []
    for i in range(4):
        req, resp = make_tool_call_pair("tool", {"x": i}, request_id=i + 1)
        resp.is_error = False
        resp.result = {"content": [{"type": "text", "text": "Invalid argument: missing 'name'. Try again with the required field."}], "isError": True}
        events.extend([req, resp])

    evaluator = ComposabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    recovery = [f for f in result.findings if f.check_id == "composability.error-recovery-surface"]
    assert len(recovery) == 0


def test_error_recovery_accepts_specific_saas_error_envelope():
    text = (
        "**Input Error**\n\n"
        "There was an HTTP 404 error while calling the API. "
        "API error (404): Project does not exist. "
        "You may be able to resolve the issue by addressing the concern and trying again."
    )
    events = []
    for i in range(4):
        req, resp = make_tool_call_pair("sentry_get_issue", {"x": i}, request_id=i + 1)
        resp.result = {"content": [{"type": "text", "text": text}], "isError": True}
        events.extend([req, resp])

    evaluator = ComposabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    recovery = [f for f in result.findings if f.check_id == "composability.error-recovery-surface"]
    assert recovery == []


def test_programmatic_readiness_prose():
    events = []
    for i in range(5):
        req, resp = make_tool_call_pair(
            "report_tool",
            {"id": i},
            result_content=[{"type": "text", "text": "This is a long prose response that describes the results in natural language without any structured data format at all. It goes on and on about what was found."}],
            request_id=i + 1,
        )
        events.extend([req, resp])

    evaluator = ComposabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    readiness = [f for f in result.findings if f.check_id == "composability.programmatic-readiness"]
    assert len(readiness) == 1


def test_no_programmatic_readiness_for_json():
    events = []
    for i in range(5):
        req, resp = make_tool_call_pair(
            "api_tool",
            {"id": i},
            result_content=[{"type": "text", "text": '{"status": "ok", "count": 42}'}],
            request_id=i + 1,
        )
        events.extend([req, resp])

    evaluator = ComposabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    readiness = [f for f in result.findings if f.check_id == "composability.programmatic-readiness"]
    assert len(readiness) == 0


def test_checks_run():
    evaluator = ComposabilityEvaluator()
    result = evaluator.evaluate([], make_server_meta(), EvaluatorConfig())
    assert "composability.error-recovery-surface" in result.checks_run
    assert "composability.programmatic-readiness" in result.checks_run
    assert result.dimension == "composability"
