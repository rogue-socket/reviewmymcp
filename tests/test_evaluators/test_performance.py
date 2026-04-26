"""Tests for performance evaluators."""

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.performance.checks import PerformanceEvaluator

from tests.conftest import make_event, make_server_meta, make_tool_call_pair


def test_connection_pool_exhaustion():
    events = []
    for i in range(5):
        req, resp = make_tool_call_pair("db_query", {"i": i}, request_id=i + 1)
        resp.is_error = False
        resp.result = {"content": [{"type": "text", "text": "connection pool exhausted"}], "isError": True}
        events.extend([req, resp])

    evaluator = PerformanceEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    pool = [f for f in result.findings if f.check_id == "performance.connection-pool-exhaustion"]
    assert len(pool) == 1


def test_no_connection_pool_on_normal_errors():
    events = []
    for i in range(5):
        req, resp = make_tool_call_pair("query", {"i": i}, request_id=i + 1)
        resp.is_error = False
        resp.result = {"content": [{"type": "text", "text": "Record not found"}], "isError": True}
        events.extend([req, resp])

    evaluator = PerformanceEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    pool = [f for f in result.findings if f.check_id == "performance.connection-pool-exhaustion"]
    assert len(pool) == 0


def test_checks_run():
    evaluator = PerformanceEvaluator()
    result = evaluator.evaluate([], make_server_meta(), EvaluatorConfig())
    assert "performance.concurrent-session-scaling" in result.checks_run
    assert "performance.connection-pool-exhaustion" in result.checks_run
    assert result.dimension == "performance"
