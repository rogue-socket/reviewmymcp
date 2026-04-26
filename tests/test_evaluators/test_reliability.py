"""Tests for reliability evaluators."""

from datetime import UTC, datetime, timedelta

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.reliability.checks import ReliabilityEvaluator

from tests.conftest import make_event, make_server_meta, make_tool_call_pair


def test_error_rate_high():
    events = []
    for i in range(10):
        is_err = i < 5  # 50% error rate
        req, resp = make_tool_call_pair("flaky", {"i": i}, is_error=is_err, request_id=i + 1)
        events.extend([req, resp])

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    err_findings = [f for f in result.findings if f.check_id == "reliability.error-rate"]
    assert len(err_findings) == 1
    assert "50%" in err_findings[0].title


def test_error_rate_below_threshold():
    events = []
    for i in range(20):
        is_err = i == 0  # 5% error rate
        req, resp = make_tool_call_pair("stable", {"i": i}, is_error=is_err, request_id=i + 1)
        events.extend([req, resp])

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    err_findings = [f for f in result.findings if f.check_id == "reliability.error-rate"]
    assert len(err_findings) == 0


def test_timeout_behavior_unanswered():
    req = make_event(
        event_id="req-orphan",
        method="tools/call",
        is_request=True,
        params={"name": "hanging_tool", "arguments": {}},
        jsonrpc_id=99,
    )
    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate([req], make_server_meta(), EvaluatorConfig())
    timeout = [f for f in result.findings if f.check_id == "reliability.timeout-behavior"]
    assert len(timeout) == 1
    assert "never received" in timeout[0].title.lower()


def test_task_lifecycle_invalid_transition():
    base = datetime(2025, 1, 1, tzinfo=UTC)
    events = [
        make_event(
            method="notifications/tasks/status",
            is_notification=True,
            params={"taskId": "t1", "status": "completed"},
            timestamp=base,
        ),
        make_event(
            method="notifications/tasks/status",
            is_notification=True,
            params={"taskId": "t1", "status": "working"},
            timestamp=base + timedelta(seconds=1),
        ),
    ]

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    lifecycle = [f for f in result.findings if f.check_id == "reliability.task-lifecycle"]
    assert len(lifecycle) == 1
    assert "terminal" in lifecycle[0].title.lower()


def test_task_lifecycle_valid_transition():
    base = datetime(2025, 1, 1, tzinfo=UTC)
    events = [
        make_event(
            method="notifications/tasks/status",
            is_notification=True,
            params={"taskId": "t1", "status": "working"},
            timestamp=base,
        ),
        make_event(
            method="notifications/tasks/status",
            is_notification=True,
            params={"taskId": "t1", "status": "completed"},
            timestamp=base + timedelta(seconds=1),
        ),
    ]

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    lifecycle = [f for f in result.findings if f.check_id == "reliability.task-lifecycle"]
    assert len(lifecycle) == 0


def test_progress_reporting_long_call():
    req, resp = make_tool_call_pair("slow", {"x": 1}, latency_ms=12000, request_id=1)
    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate([req, resp], make_server_meta(), EvaluatorConfig())
    progress = [f for f in result.findings if f.check_id == "reliability.progress-reporting"]
    assert len(progress) == 1
