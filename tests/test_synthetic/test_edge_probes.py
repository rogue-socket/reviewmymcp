"""Tests for edge probe generation."""

from reviewmymcp.ingest.schema import ToolDefinition
from reviewmymcp.synthetic.edge_probes import generate_edge_probes


def _tool(name: str, required: list[str] | None = None, properties: dict | None = None) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=f"Tool {name}",
        input_schema={
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    )


def test_generates_probes_for_tool():
    tool = _tool("search", required=["query"], properties={"query": {"type": "string"}})
    probes = generate_edge_probes([tool])
    assert len(probes) > 0
    probe_types = {p["probe_type"] for p in probes}
    assert "missing_required_args" in probe_types
    assert "wrong_type_args" in probe_types
    assert "empty_args" in probe_types
    assert "extra_args" in probe_types
    assert "nonexistent_tool" in probe_types
    assert "malformed_jsonrpc" in probe_types
    assert "missing_method" in probe_types


def test_missing_required_args_per_field():
    tool = _tool("create", required=["name", "email"], properties={"name": {"type": "string"}, "email": {"type": "string"}})
    probes = generate_edge_probes([tool])
    missing = [p for p in probes if p["probe_type"] == "missing_required_args"]
    # One probe with all missing + one per required field
    assert len(missing) == 3


def test_wrong_type_for_integer():
    tool = _tool("get", required=["id"], properties={"id": {"type": "integer"}})
    probes = generate_edge_probes([tool])
    wrong = [p for p in probes if p["probe_type"] == "wrong_type_args"]
    assert len(wrong) == 1
    assert wrong[0]["request"]["params"]["arguments"]["id"] == "not_a_number"


def test_nonexistent_tool_probe():
    probes = generate_edge_probes([])
    nonexistent = [p for p in probes if p["probe_type"] == "nonexistent_tool"]
    assert len(nonexistent) == 1
    assert "__nonexistent_tool_12345__" in nonexistent[0]["request"]["params"]["name"]


def test_malformed_jsonrpc_probe():
    probes = generate_edge_probes([])
    malformed = [p for p in probes if p["probe_type"] == "malformed_jsonrpc"]
    assert len(malformed) == 1
    assert "jsonrpc" not in malformed[0]["request"]


def test_all_probes_have_request():
    tool = _tool("test", required=["x"], properties={"x": {"type": "string"}})
    probes = generate_edge_probes([tool])
    for probe in probes:
        assert "request" in probe
        assert "probe_type" in probe
        assert "description" in probe
