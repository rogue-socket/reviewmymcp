from datetime import UTC, datetime

from collect_logs import scenario_coverage_summary, scenario_tool_gaps
from mcp_driver.schema import McpEvent


def _tool_call(name: str, *, is_probe: bool = False) -> McpEvent:
    return McpEvent(
        event_id=f"call-{name}",
        timestamp=datetime.now(UTC),
        direction="client_to_server",
        method="tools/call",
        is_request=True,
        params={"name": name, "arguments": {}},
        is_probe=is_probe,
    )


def test_scenario_coverage_summary_counts_expected_artifact_calls():
    scenarios = [
        {"name": "lookup", "steps": [{"tool": "search"}, {"tool": "fetch"}]},
        {"name": "retry", "steps": [{"tool": "search"}]},
    ]
    events = [
        _tool_call("search"),
        _tool_call("fetch"),
        _tool_call("search"),
        _tool_call("search", is_probe=True),
    ]

    summary = scenario_coverage_summary(scenarios, events)

    assert summary["expected_tool_call_steps"] == 3
    assert summary["observed_scenario_tool_calls"] == 3
    assert summary["expected_tools"] == ["fetch", "search"]
    assert summary["observed_tools"] == ["fetch", "search"]
    assert summary["missing_tool_call_counts"] == {}


def test_scenario_coverage_summary_reports_missing_calls():
    scenarios = [{"name": "lookup", "steps": [{"tool": "search"}, {"tool": "fetch"}]}]
    events = [_tool_call("search")]

    summary = scenario_coverage_summary(scenarios, events)

    assert summary["missing_tool_call_counts"] == {"fetch": 1}


def test_scenario_tool_gaps_flags_stale_scenario_tools():
    scenarios = [{"name": "lookup", "steps": [{"tool": "old-search"}, {"tool": "fetch"}]}]

    assert scenario_tool_gaps(scenarios, {"fetch"}) == [{"scenario": "lookup", "tool": "old-search"}]
