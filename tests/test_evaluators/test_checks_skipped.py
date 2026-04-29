"""Tests that evaluators emit checks_skipped when data is insufficient."""

from datetime import UTC, datetime, timedelta

from reviewmymcp.evaluators.accuracy.checks import AccuracyEvaluator
from reviewmymcp.evaluators.compliance.checks import ComplianceEvaluator
from reviewmymcp.evaluators.composability.checks import ComposabilityEvaluator
from reviewmymcp.evaluators.conformance.checks import ConformanceEvaluator
from reviewmymcp.evaluators.discoverability.checks import DiscoverabilityEvaluator
from reviewmymcp.evaluators.efficiency.checks import EfficiencyEvaluator
from reviewmymcp.evaluators.performance.checks import PerformanceEvaluator
from reviewmymcp.evaluators.reliability.checks import ReliabilityEvaluator
from reviewmymcp.evaluators.security.checks import SecurityEvaluator

from tests.conftest import (
    EvaluatorConfig,
    make_event,
    make_server_meta,
    make_tool_call_pair,
    make_tool_def,
)


# --- Efficiency ---


def test_efficiency_latency_cliff_skipped_insufficient_data():
    """Tool with < 3 latency observations -> SkippedCheck for latency-cliff."""
    req1, resp1 = make_tool_call_pair("tool", {}, latency_ms=100.0, request_id=1)
    req2, resp2 = make_tool_call_pair("tool", {}, latency_ms=200.0, request_id=2)
    meta = make_server_meta([make_tool_def("tool")])
    result = EfficiencyEvaluator().evaluate([req1, resp1, req2, resp2], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "efficiency.latency-cliff" in skipped_ids


def test_efficiency_token_cost_skipped_no_successes():
    """No successful calls -> SkippedCheck for token-cost-per-task."""
    req, resp = make_tool_call_pair("tool", {}, is_error=True, request_id=1)
    meta = make_server_meta([make_tool_def("tool")])
    result = EfficiencyEvaluator().evaluate([req, resp], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "efficiency.token-cost-per-task" in skipped_ids


def test_efficiency_no_skip_with_enough_data():
    """3+ latency observations and successful calls -> no skipped checks for those."""
    events = []
    for i in range(4):
        req, resp = make_tool_call_pair("tool", {}, latency_ms=100.0, request_id=i + 1)
        events.extend([req, resp])
    meta = make_server_meta([make_tool_def("tool")])
    result = EfficiencyEvaluator().evaluate(events, meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "efficiency.latency-cliff" not in skipped_ids
    assert "efficiency.token-cost-per-task" not in skipped_ids


# --- Conformance ---


def test_conformance_handshake_skipped_small_sessions():
    """Sessions with <= 2 events -> SkippedCheck for initialize-handshake."""
    e1 = make_event(method="ping", is_request=True, session_id="small")
    e2 = make_event(method="ping", is_response=True, session_id="small")
    meta = make_server_meta()
    result = ConformanceEvaluator().evaluate([e1, e2], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "conformance.initialize-handshake" in skipped_ids


# --- Reliability ---


def test_reliability_error_rate_skipped_few_calls():
    """Tools with < 3 calls -> SkippedCheck for error-rate."""
    req, resp = make_tool_call_pair("rare_tool", {}, request_id=1)
    meta = make_server_meta([make_tool_def("rare_tool")])
    result = ReliabilityEvaluator().evaluate([req, resp], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "reliability.error-rate" in skipped_ids


# --- Composability ---


def test_composability_idempotency_skipped_no_repeats():
    """No repeated call groups -> SkippedCheck for idempotency-violation."""
    req, resp = make_tool_call_pair("unique_tool", {"a": 1}, request_id=1)
    meta = make_server_meta([make_tool_def("unique_tool")])
    result = ComposabilityEvaluator().evaluate([req, resp], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "composability.idempotency-violation" in skipped_ids


def test_composability_concurrency_skipped_few_pairs():
    """Tools with < 5 call pairs -> SkippedCheck for concurrency-safety."""
    events = []
    for i in range(3):
        req, resp = make_tool_call_pair("tool", {}, request_id=i + 1)
        events.extend([req, resp])
    meta = make_server_meta([make_tool_def("tool")])
    result = ComposabilityEvaluator().evaluate(events, meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "composability.concurrency-safety" in skipped_ids


# --- Performance ---


def test_performance_concurrent_scaling_skipped_few_sessions():
    """< 3 sessions -> SkippedCheck for concurrent-session-scaling."""
    req, resp = make_tool_call_pair("tool", {}, session_id="s1", request_id=1)
    meta = make_server_meta([make_tool_def("tool")])
    result = PerformanceEvaluator().evaluate([req, resp], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "performance.concurrent-session-scaling" in skipped_ids


def test_performance_throughput_skipped_few_responses():
    """< 20 tool/call responses -> SkippedCheck for throughput-degradation."""
    events = []
    for i in range(5):
        req, resp = make_tool_call_pair("tool", {}, request_id=i + 1)
        events.extend([req, resp])
    meta = make_server_meta([make_tool_def("tool")])
    result = PerformanceEvaluator().evaluate(events, meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "performance.throughput-degradation" in skipped_ids


# --- Compliance ---


def test_compliance_data_residency_skipped_no_regions():
    """No expected_regions configured -> SkippedCheck for data-residency-signals."""
    meta = make_server_meta()
    result = ComplianceEvaluator().evaluate([], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "compliance.data-residency-signals" in skipped_ids


def test_compliance_data_residency_not_skipped_with_regions():
    """expected_regions set -> no SkippedCheck."""
    meta = make_server_meta()
    config = EvaluatorConfig(extra={"expected_regions": ["us-east-1"]})
    result = ComplianceEvaluator().evaluate([], meta, config)
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "compliance.data-residency-signals" not in skipped_ids


# --- Accuracy ---


def test_accuracy_output_drift_skipped_few_responses():
    """Tools with < 3 responses -> SkippedCheck for output-schema-drift."""
    req, resp = make_tool_call_pair("tool", {}, request_id=1)
    meta = make_server_meta([make_tool_def("tool")])
    result = AccuracyEvaluator().evaluate([req, resp], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "accuracy.output-schema-drift" in skipped_ids


def test_accuracy_description_accuracy_skipped_no_judge():
    """No judge configured -> SkippedCheck for description-accuracy."""
    meta = make_server_meta([make_tool_def("tool")])
    result = AccuracyEvaluator().evaluate([], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "accuracy.description-accuracy" in skipped_ids


# --- Discoverability ---


def test_discoverability_clarity_skipped_no_judge():
    """No judge -> SkippedCheck for description-clarity."""
    meta = make_server_meta([make_tool_def("tool")])
    result = DiscoverabilityEvaluator().evaluate([], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "discoverability.description-clarity" in skipped_ids


def test_discoverability_overlap_skipped_no_judge():
    """No judge -> SkippedCheck for semantic-overlap."""
    tools = [make_tool_def("a"), make_tool_def("b")]
    meta = make_server_meta(tools)
    result = DiscoverabilityEvaluator().evaluate([], meta, EvaluatorConfig())
    skipped_ids = [s.check_id for s in result.checks_skipped]
    assert "discoverability.semantic-overlap" in skipped_ids


def test_discoverability_overlap_skipped_single_tool():
    """Single tool + judge -> SkippedCheck 'fewer than 2 tools'."""
    from reviewmymcp.judge.base import JudgeResponse

    meta = make_server_meta([make_tool_def("only_one")])

    class FakeJudge:
        provider_name = "fake"
        def complete(self, request):
            return JudgeResponse(raw_text="{}", parsed={"score": 5, "rationale": "ok", "specific_issues": []})

    config = EvaluatorConfig(judge=FakeJudge())
    result = DiscoverabilityEvaluator().evaluate([], meta, config)
    overlap_skipped = [s for s in result.checks_skipped if s.check_id == "discoverability.semantic-overlap"]
    assert len(overlap_skipped) == 1
    assert "fewer than 2" in overlap_skipped[0].reason


# --- Security ---


def test_security_no_skipped_checks():
    """Security evaluator has no data-insufficiency guards -> no checks_skipped (without judge)."""
    meta = make_server_meta()
    result = SecurityEvaluator().evaluate([], meta, EvaluatorConfig())
    assert result.checks_skipped == []
