"""End-to-end tests for the is_probe / probe_type tagging pipeline.

Covers:
- parser round-trip: wrapper field `is_probe` survives ingestion
- correlator propagation: response inherits is_probe from its matching request
- evaluator filtering: reliability.error-rate excludes probe responses
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.reliability.checks import ReliabilityEvaluator
from reviewmymcp.ingest.correlator import correlate
from reviewmymcp.ingest.file_loader import load_file
from reviewmymcp.ingest.schema import ServerMeta
from tests.conftest import make_event, make_tool_call_pair


def test_parser_reads_probe_fields_from_wrapper(tmp_path: Path) -> None:
    log = tmp_path / "probe.ndjson"
    base = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
    records = [
        {
            "direction": "client_to_server",
            "timestamp": base.isoformat(),
            "session_id": "s1",
            "is_probe": True,
            "probe_type": "missing_required_args",
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {}},
        },
        {
            "direction": "client_to_server",
            "timestamp": (base + timedelta(milliseconds=10)).isoformat(),
            "session_id": "s1",
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "/tmp/x"}},
        },
    ]
    log.write_text("\n".join(json.dumps(r) for r in records))

    events = load_file(log, redact=False)

    assert len(events) == 2
    assert events[0].is_probe is True
    assert events[0].probe_type == "missing_required_args"
    assert events[1].is_probe is False
    assert events[1].probe_type is None


def test_parser_ignores_probe_type_when_is_probe_false(tmp_path: Path) -> None:
    log = tmp_path / "probe.ndjson"
    base = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
    record = {
        "direction": "client_to_server",
        "timestamp": base.isoformat(),
        "is_probe": False,
        "probe_type": "leaked_from_elsewhere",
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "x", "arguments": {}},
    }
    log.write_text(json.dumps(record))

    events = load_file(log, redact=False)

    assert events[0].is_probe is False
    assert events[0].probe_type is None


def test_correlator_propagates_is_probe_to_response() -> None:
    base = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
    req, resp = make_tool_call_pair(
        tool_name="read_file",
        is_error=True,
        latency_ms=5.0,
        request_id=42,
        base_time=base,
        is_probe=False,  # response built without probe flag — correlator should set it
    )
    req.is_probe = True
    req.probe_type = "missing_required_args"
    resp.is_probe = False
    resp.probe_type = None

    correlate([req, resp])

    assert resp.is_probe is True
    assert resp.probe_type == "missing_required_args"


def test_correlator_does_not_override_explicit_probe_response() -> None:
    base = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
    req, resp = make_tool_call_pair(
        tool_name="read_file", request_id=1, base_time=base,
        is_probe=True, probe_type="wrong_type_args",
    )

    correlate([req, resp])

    assert resp.is_probe is True
    assert resp.probe_type == "wrong_type_args"


def test_reliability_error_rate_excludes_probe_responses() -> None:
    base = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
    events = []
    # 5 probe calls, all errors — would normally trigger a HIGH finding.
    for i in range(5):
        req, resp = make_tool_call_pair(
            tool_name="read_file",
            is_error=True,
            request_id=100 + i,
            base_time=base + timedelta(milliseconds=i * 10),
            is_probe=True,
            probe_type="missing_required_args",
        )
        events.extend([req, resp])
    # 4 real calls, all successful.
    for i in range(4):
        req, resp = make_tool_call_pair(
            tool_name="read_file",
            is_error=False,
            request_id=200 + i,
            base_time=base + timedelta(seconds=1, milliseconds=i * 10),
            is_probe=False,
        )
        events.extend([req, resp])

    result = ReliabilityEvaluator().evaluate(events, ServerMeta(), EvaluatorConfig())

    error_rate_findings = [f for f in result.findings if f.check_id == "reliability.error-rate"]
    assert error_rate_findings == [], "probe-induced errors leaked into error-rate finding"


def test_reliability_error_rate_still_fires_on_real_errors() -> None:
    base = datetime(2026, 5, 16, 0, 0, 0, tzinfo=UTC)
    events = []
    # 4 real errors out of 5 real calls — should still fire.
    for i in range(5):
        req, resp = make_tool_call_pair(
            tool_name="read_file",
            is_error=(i < 4),
            request_id=200 + i,
            base_time=base + timedelta(milliseconds=i * 10),
            is_probe=False,
        )
        events.extend([req, resp])
    # Plus 3 probes to confirm they don't dilute the rate.
    for i in range(3):
        req, resp = make_tool_call_pair(
            tool_name="read_file",
            is_error=True,
            request_id=300 + i,
            base_time=base + timedelta(seconds=1, milliseconds=i * 10),
            is_probe=True,
            probe_type="missing_required_args",
        )
        events.extend([req, resp])

    result = ReliabilityEvaluator().evaluate(events, ServerMeta(), EvaluatorConfig())

    error_rate_findings = [f for f in result.findings if f.check_id == "reliability.error-rate"]
    assert len(error_rate_findings) == 1
    # 4/5 = 80%, not (4+3)/(5+3) = 87.5%.
    assert error_rate_findings[0].evidence["error_rate"] == 0.8
    assert error_rate_findings[0].evidence["total"] == 5
    assert error_rate_findings[0].evidence["errors"] == 4
