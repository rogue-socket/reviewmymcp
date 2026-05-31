"""Tests for reliability evaluators."""

from datetime import UTC, datetime, timedelta

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.reliability.checks import ReliabilityEvaluator
from tests.conftest import make_event, make_server_meta, make_tool_call_pair, make_tool_def


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


def test_error_rate_downgrades_when_auth_scope_missing():
    events = []
    for i in range(3):
        req, resp = make_tool_call_pair("update_issue", {"i": i}, is_error=True, request_id=i + 1)
        events.extend([req, resp])

    meta = make_server_meta([make_tool_def("update_issue", required_scopes=["event:write"])])
    meta.auth.scopes_used = ["event:read"]
    meta.auth.scopes_required = {"update_issue": ["event:write"]}

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate(events, meta, EvaluatorConfig())
    err_findings = [f for f in result.findings if f.check_id == "reliability.error-rate"]

    assert len(err_findings) == 1
    assert err_findings[0].severity.value == "info"
    assert err_findings[0].evidence["missing_scopes"] == ["event:write"]
    assert err_findings[0].evidence["suppressed"] is True


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


def test_concurrent_write_integrity_detects_json_corruption_probe_error():
    req, resp = make_tool_call_pair(
        "read_graph",
        {},
        is_error=True,
        request_id=1,
        is_probe=True,
        probe_type="concurrent_write_integrity_readback",
    )
    resp.error = {"code": -32603, "message": "Unexpected end of JSON input while reading memory.jsonl"}

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate([req, resp], make_server_meta(), EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "reliability.concurrent-write-integrity"]

    assert len(findings) == 1
    assert findings[0].severity.value == "high"
    assert findings[0].evidence["tools"] == ["read_graph"]


def test_unsafe_persistence_default_flags_npx_node_modules_path():
    meta = make_server_meta()
    meta.persistence.paths = {
        "env:MEMORY_FILE_PATH": (
            "/Users/me/.npm/_npx/abc/node_modules/@modelcontextprotocol/server-memory/dist/memory.jsonl"
        )
    }

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "reliability.unsafe-persistence-default"]

    assert len(findings) == 1
    assert findings[0].severity.value == "medium"
    assert findings[0].evidence["source"] == "env:MEMORY_FILE_PATH"
    assert findings[0].evidence["reason"] == "inside the npx cache"


def test_unsafe_persistence_default_allows_project_data_path():
    meta = make_server_meta()
    meta.persistence.paths = {"MEMORY_FILE_PATH": "/Users/me/project/.reviewmymcp/memory.jsonl"}

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "reliability.unsafe-persistence-default"]

    assert findings == []


def test_unsafe_persistence_default_flags_tmp_path():
    meta = make_server_meta()
    meta.persistence.paths = {"MEMORY_FILE_PATH": "/tmp/server-memory/memory.jsonl"}

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "reliability.unsafe-persistence-default"]

    assert len(findings) == 1
    assert findings[0].evidence["reason"] == "inside a temp directory"


def test_silent_failure_suspect_flags_all_empty_successes():
    events = []
    for i in range(4):
        req, resp = make_tool_call_pair(
            "search",
            {"q": i},
            result_content=[{"type": "text", "text": '{"results": []}'}],
            request_id=300 + i,
        )
        events.extend([req, resp])

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "reliability.silent-failure-suspect"]

    assert len(findings) == 1
    assert findings[0].severity.value == "high"


def test_silent_failure_suspect_flags_empty_cluster_after_populated_results():
    events = []
    payloads = ['{"results": [{"title": "a"}]}', '{"results": [{"title": "b"}]}', "[]", "[]", "[]"]
    for i, payload in enumerate(payloads):
        req, resp = make_tool_call_pair(
            "search",
            {"q": i},
            result_content=[{"type": "text", "text": payload}],
            request_id=400 + i,
        )
        events.extend([req, resp])

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "reliability.silent-failure-suspect"]

    assert len(findings) == 1
    assert findings[0].evidence["max_empty_streak"] == 3


def test_silent_failure_suspect_ignores_isolated_empty_result():
    events = []
    payloads = ['{"results": [{"title": "a"}]}', "[]", '{"results": [{"title": "b"}]}']
    for i, payload in enumerate(payloads):
        req, resp = make_tool_call_pair(
            "search",
            {"q": i},
            result_content=[{"type": "text", "text": payload}],
            request_id=500 + i,
        )
        events.extend([req, resp])

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "reliability.silent-failure-suspect"]

    assert findings == []


def test_rate_limit_collapse_detects_stress_error_spike():
    events = []
    for i in range(5):
        req, resp = make_tool_call_pair("search", {"q": i}, request_id=i + 1)
        events.extend([req, resp])
    for i in range(10):
        req, resp = make_tool_call_pair(
            "search",
            {"q": i},
            is_error=i < 5,
            request_id=100 + i,
            is_stress=True,
        )
        events.extend([req, resp])

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "reliability.rate-limit-collapse"]

    assert len(findings) == 1
    assert findings[0].severity.value == "high"


def test_silent_degradation_detects_empty_successes_under_stress():
    events = []
    for i in range(6):
        req, resp = make_tool_call_pair(
            "search",
            {"q": i},
            result_content=[{"type": "text", "text": ""}],
            request_id=200 + i,
            is_stress=True,
        )
        events.extend([req, resp])

    evaluator = ReliabilityEvaluator()
    result = evaluator.evaluate(events, make_server_meta(), EvaluatorConfig())
    findings = [f for f in result.findings if f.check_id == "reliability.silent-degradation"]

    assert len(findings) == 1
    assert findings[0].evidence["empty_success"] == 6
