"""Tests for protocol conformance evaluators."""

from datetime import UTC, datetime, timedelta

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.conformance.checks import ConformanceEvaluator
from reviewmymcp.ingest.schema import ServerCapabilities
from tests.conftest import make_event, make_init_pair, make_server_meta, make_tool_call_pair, make_tool_def


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


def test_is_error_flag_set_detects_error_as_content():
    req, resp = make_tool_call_pair(
        "fetch_content",
        {"url": "http://example.invalid"},
        result_content=[{"type": "text", "text": "Error: failed to fetch URL with HTTP 500"}],
    )
    resp.result["isError"] = False

    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([req, resp], make_server_meta(), EvaluatorConfig())
    flag_findings = [f for f in result.findings if f.check_id == "conformance.is-error-flag-set"]

    assert len(flag_findings) == 1
    assert flag_findings[0].affected_entity == "fetch_content"


def test_is_error_flag_set_ignores_non_error_content():
    req, resp = make_tool_call_pair(
        "scan_results",
        {},
        result_content=[{"type": "text", "text": "No errors found in the latest scan."}],
    )

    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([req, resp], make_server_meta(), EvaluatorConfig())
    flag_findings = [f for f in result.findings if f.check_id == "conformance.is-error-flag-set"]

    assert flag_findings == []


def test_destructive_tool_missing_destructive_hint():
    tool = make_tool_def("delete_entities", description="Delete entities permanently")
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([], make_server_meta([tool]), EvaluatorConfig())
    missing_hint = [f for f in result.findings if f.check_id == "conformance.destructive-hint-missing"]

    assert len(missing_hint) == 1
    assert missing_hint[0].severity.value == "medium"


def test_destructive_tool_with_destructive_hint_ok():
    tool = make_tool_def(
        "delete_entities",
        description="Delete entities permanently",
        annotations={"destructiveHint": True},
    )
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([], make_server_meta([tool]), EvaluatorConfig())
    missing_hint = [f for f in result.findings if f.check_id == "conformance.destructive-hint-missing"]

    assert missing_hint == []


def test_mutating_tool_missing_behavior_annotations():
    tool = make_tool_def("create_pull_request", description="Create a pull request")
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([], make_server_meta([tool]), EvaluatorConfig())
    missing_annotations = [
        f for f in result.findings if f.check_id == "conformance.mutating-annotations-missing"
    ]

    assert len(missing_annotations) == 1
    assert missing_annotations[0].affected_entity == "create_pull_request"


def test_mutating_tool_with_behavior_annotation_ok():
    tool = make_tool_def(
        "create_pull_request",
        description="Create a pull request",
        annotations={"readOnlyHint": False, "idempotentHint": False},
    )
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([], make_server_meta([tool]), EvaluatorConfig())
    missing_annotations = [
        f for f in result.findings if f.check_id == "conformance.mutating-annotations-missing"
    ]

    assert missing_annotations == []


def test_destructive_tool_not_duplicated_as_mutating_annotation_gap():
    tool = make_tool_def("delete_entities", description="Delete entities permanently")
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([], make_server_meta([tool]), EvaluatorConfig())
    mutating = [f for f in result.findings if f.check_id == "conformance.mutating-annotations-missing"]
    destructive = [f for f in result.findings if f.check_id == "conformance.destructive-hint-missing"]

    assert mutating == []
    assert len(destructive) == 1


def test_output_schema_wire_format_flags_zod_fingerprint():
    tool = make_tool_def(
        "get_memory",
        output_schema={"_def": {"typeName": "ZodObject"}, "shape": {}},
    )
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([], make_server_meta([tool]), EvaluatorConfig())
    schema_findings = [f for f in result.findings if f.check_id == "conformance.output-schema-wire-format"]

    assert len(schema_findings) == 1
    assert schema_findings[0].severity.value == "medium"
    assert schema_findings[0].evidence["tool"] == "get_memory"


def test_output_schema_wire_format_accepts_json_schema():
    tool = make_tool_def(
        "get_memory",
        output_schema={"type": "object", "properties": {"content": {"type": "string"}}},
    )
    evaluator = ConformanceEvaluator()
    result = evaluator.evaluate([], make_server_meta([tool]), EvaluatorConfig())
    schema_findings = [f for f in result.findings if f.check_id == "conformance.output-schema-wire-format"]

    assert schema_findings == []
