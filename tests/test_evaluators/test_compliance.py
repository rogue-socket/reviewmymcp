"""Tests for compliance evaluators."""

from reviewmymcp.evaluators.base import EvaluatorConfig
from reviewmymcp.evaluators.compliance.checks import ComplianceEvaluator

from tests.conftest import make_event, make_init_pair, make_server_meta, make_tool_call_pair


def test_pii_email_detected():
    req, resp = make_tool_call_pair(
        "search_users",
        {"q": "smith"},
        result_content=[{"type": "text", "text": "Found: john@example.com, jane@corp.com"}],
        request_id=1,
    )
    evaluator = ComplianceEvaluator()
    result = evaluator.evaluate([req, resp], make_server_meta(), EvaluatorConfig())
    pii = [f for f in result.findings if f.check_id == "compliance.pii-in-responses"]
    assert any("email" in f.title.lower() for f in pii)


def test_pii_ssn_detected():
    req, resp = make_tool_call_pair(
        "get_user",
        {"id": 1},
        result_content=[{"type": "text", "text": "SSN: 123-45-6789"}],
        request_id=1,
    )
    evaluator = ComplianceEvaluator()
    result = evaluator.evaluate([req, resp], make_server_meta(), EvaluatorConfig())
    pii = [f for f in result.findings if f.check_id == "compliance.pii-in-responses"]
    assert any("ssn" in f.title.lower() for f in pii)


def test_audit_trail_completeness_orphan_request():
    req = make_event(
        method="tools/call",
        is_request=True,
        jsonrpc_id=42,
        params={"name": "test", "arguments": {}},
    )
    # No matching response
    init_req, init_resp = make_init_pair()
    evaluator = ComplianceEvaluator()
    result = evaluator.evaluate([init_req, init_resp, req], make_server_meta(), EvaluatorConfig())
    trail = [f for f in result.findings if f.check_id == "compliance.audit-trail-completeness"]
    assert any("no matching response" in f.title.lower() for f in trail)


def test_consent_flow_form_elicitation_sensitive():
    event = make_event(
        method="elicitation/create",
        is_request=True,
        params={
            "mode": "form",
            "message": "Enter your API key",
            "requestedSchema": {
                "type": "object",
                "properties": {"api_key": {"type": "string", "description": "Your API key"}},
            },
        },
    )
    meta = make_server_meta()
    evaluator = ComplianceEvaluator()
    result = evaluator.evaluate([event], meta, EvaluatorConfig())
    consent = [f for f in result.findings if f.check_id == "compliance.consent-flow-gaps"]
    assert any("api_key" in f.title for f in consent)


def test_no_pii_on_clean_data():
    req, resp = make_tool_call_pair(
        "count",
        {},
        result_content=[{"type": "text", "text": "Count: 42"}],
        request_id=1,
    )
    evaluator = ComplianceEvaluator()
    result = evaluator.evaluate([req, resp], make_server_meta(), EvaluatorConfig())
    pii = [f for f in result.findings if f.check_id == "compliance.pii-in-responses"]
    assert len(pii) == 0
