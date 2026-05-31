"""Tests for performance evaluators."""

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.performance.checks import PerformanceEvaluator
from tests.conftest import make_server_meta, make_tool_call_pair


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


def test_throughput_under_load_detects_latency_degradation():
    events = []
    for i in range(3):
        req, resp = make_tool_call_pair("search", {"q": i}, latency_ms=100, request_id=i + 1)
        events.extend([req, resp])
    for i in range(5):
        req, resp = make_tool_call_pair(
            "search",
            {"q": i},
            latency_ms=500,
            request_id=100 + i,
            is_stress=True,
        )
        events.extend([req, resp])

    evaluator = PerformanceEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    load = [f for f in result.findings if f.check_id == "performance.throughput-under-load"]

    assert len(load) == 1
    assert load[0].severity.value == "medium"


def test_checks_run():
    evaluator = PerformanceEvaluator()
    result = evaluator.evaluate([], make_server_meta(), EvaluatorConfig())
    assert "performance.concurrent-session-scaling" in result.checks_run
    assert "performance.throughput-under-load" in result.checks_run
    assert "performance.connection-pool-exhaustion" in result.checks_run
    assert result.dimension == "performance"
