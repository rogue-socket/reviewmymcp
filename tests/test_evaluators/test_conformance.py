"""Tests for protocol conformance evaluators."""

from datetime import UTC, datetime, timedelta

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.conformance.checks import ConformanceEvaluator
from reviewmymcp.ingest.schema import ServerCapabilities

from tests.conftest import make_event, make_init_pair, make_server_meta


def test_missing_initialize():
    base = datetime(2025, 1, 1, tzinfo=UTC)
    events = [
        make_event(method="tools/list", is_request=True, jsonrpc_id=1, timestamp=base),
        make_event(method="tools/call", is_request=True, jsonrpc_id=2, timestamp=base + timedelta(seconds=1)),
        make_event(is_response=True, jsonrpc_id=1, timestamp=base + timedelta(seconds=2)),
    ]
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    init = [f for f in result.findings if f.check_id == "conformance.initialize-handshake"]
    assert len(init) >= 1
    assert any("missing" in f.title.lower() for f in init)


def test_valid_initialize_no_finding():
    init_req, init_resp = make_init_pair()
    notification = make_event(
        method="notifications/initialized",
        is_notification=True,
        timestamp=init_resp.timestamp + timedelta(milliseconds=10),
    )
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([init_req, init_resp, notification], make_server_meta(), EvaluatorConfig())
    init = [f for f in result.findings if f.check_id == "conformance.initialize-handshake"]
    assert len(init) == 0


def test_jsonrpc_conformance_missing_field():
    event = make_event(
        is_response=True,
        raw_message={"id": 1, "result": {}},  # missing "jsonrpc": "2.0"
    )
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([event], make_server_meta(), EvaluatorConfig())
    jsonrpc = [f for f in result.findings if f.check_id == "conformance.jsonrpc-conformance"]
    assert len(jsonrpc) == 1


def test_jsonrpc_conformance_both_result_and_error():
    event = make_event(
        is_response=True,
        raw_message={"jsonrpc": "2.0", "id": 1, "result": {}, "error": {"code": -1, "message": "oops"}},
    )
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([event], make_server_meta(), EvaluatorConfig())
    jsonrpc = [f for f in result.findings if f.check_id == "conformance.jsonrpc-conformance"]
    assert len(jsonrpc) == 1


def test_capability_mismatch_undeclared():
    meta = make_server_meta()
    meta.server_capabilities = ServerCapabilities(tools=False)

    events = [
        make_event(method="tools/list", is_request=True, jsonrpc_id=1),
        make_event(method="tools/call", is_request=True, jsonrpc_id=2, params={"name": "x", "arguments": {}}),
    ]
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate(events, meta, EvaluatorConfig())
    cap = [f for f in result.findings if f.check_id == "conformance.capability-mismatch"]
    assert len(cap) == 1


def test_error_code_nonstandard():
    event = make_event(
        is_response=True,
        is_error=True,
        error={"code": -1, "message": "custom error"},
        raw_message={"jsonrpc": "2.0", "id": 1, "error": {"code": -1, "message": "custom error"}},
    )
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([event], make_server_meta(), EvaluatorConfig())
    codes = [f for f in result.findings if f.check_id == "conformance.error-code-correctness"]
    assert len(codes) == 1


def test_notification_with_id():
    event = make_event(
        is_notification=True,
        method="notifications/progress",
        raw_message={"jsonrpc": "2.0", "id": 5, "method": "notifications/progress"},
    )
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([event], make_server_meta(), EvaluatorConfig())
    notif = [f for f in result.findings if f.check_id == "conformance.notification-correctness"]
    assert len(notif) == 1
