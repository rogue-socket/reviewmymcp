"""Tests for efficiency evaluators."""

from reviewmymcp.evaluators.efficiency.checks import EfficiencyEvaluator

from tests.conftest import make_event, make_server_meta, make_tool_call_pair, make_tool_def, EvaluatorConfig


def test_description_bloat_single_tool():
    long_desc = "x " * 2000  # ~1000 tokens
    meta = make_server_meta([make_tool_def("bloaty", description=long_desc)])
    evaluator = EfficiencyEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    bloat_findings = [f for f in result.findings if f.check_id == "efficiency.description-bloat"]
    assert any("bloaty" in f.title for f in bloat_findings)


def test_description_bloat_total():
    tools = [make_tool_def(f"tool_{i}", description="word " * 400) for i in range(15)]
    meta = make_server_meta(tools)
    evaluator = EfficiencyEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    bloat_findings = [f for f in result.findings if f.check_id == "efficiency.description-bloat"]
    assert any("Total" in f.title for f in bloat_findings)


def test_no_bloat_on_short_descriptions():
    tools = [make_tool_def("small", description="Search for items")]
    meta = make_server_meta(tools)
    evaluator = EfficiencyEvaluator()
    result = evaluator.evaluate([], meta, EvaluatorConfig())
    bloat_findings = [f for f in result.findings if f.check_id == "efficiency.description-bloat"]
    assert len(bloat_findings) == 0


def test_redundant_calls():
    req1, resp1 = make_tool_call_pair("search", {"q": "test"}, request_id=1)
    req2, resp2 = make_tool_call_pair("search", {"q": "test"}, request_id=2)
    meta = make_server_meta()
    evaluator = EfficiencyEvaluator()
    result = evaluator.evaluate([req1, resp1, req2, resp2], meta, EvaluatorConfig())
    redundant = [f for f in result.findings if f.check_id == "efficiency.redundant-calls"]
    assert len(redundant) == 1
    assert "search" in redundant[0].title


def test_no_redundant_for_different_args():
    req1, resp1 = make_tool_call_pair("search", {"q": "alice"}, request_id=1)
    req2, resp2 = make_tool_call_pair("search", {"q": "bob"}, request_id=2)
    evaluator = EfficiencyEvaluator()
    result = evaluator.evaluate([req1, resp1, req2, resp2], make_server_meta(), EvaluatorConfig())
    redundant = [f for f in result.findings if f.check_id == "efficiency.redundant-calls"]
    assert len(redundant) == 0


def test_latency_cliff():
    events = []
    meta = make_server_meta()
    for i in range(20):
        latency = 100.0 if i < 18 else 50000.0  # 2 calls with extreme latency
        req, resp = make_tool_call_pair("slow_tool", {"i": i}, latency_ms=latency, request_id=i + 10)
        events.extend([req, resp])

    evaluator = EfficiencyEvaluator()
    result = evaluator.evaluate(events, meta, EvaluatorConfig())
    cliff = [f for f in result.findings if f.check_id == "efficiency.latency-cliff"]
    assert len(cliff) == 1


def test_checks_run_list():
    evaluator = EfficiencyEvaluator()
    result = evaluator.evaluate([], make_server_meta(), EvaluatorConfig())
    assert "efficiency.description-bloat" in result.checks_run
    assert "efficiency.redundant-calls" in result.checks_run
    assert result.dimension == "efficiency"
