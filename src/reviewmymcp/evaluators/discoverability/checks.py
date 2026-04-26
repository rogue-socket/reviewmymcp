"""Discoverability dimension evaluators."""

from __future__ import annotations

import re
from collections import defaultdict

from reviewmymcp.evaluators.base import (
    EvaluatorConfig,
    EvaluatorResult,
    Finding,
    Severity,
)
from reviewmymcp.ingest.schema import McpEvent, ServerMeta

VERB_PREFIXES = {
    "get",
    "set",
    "create",
    "delete",
    "update",
    "list",
    "search",
    "find",
    "read",
    "write",
    "send",
    "run",
    "execute",
    "check",
    "validate",
    "fetch",
    "query",
    "add",
    "remove",
    "put",
    "post",
    "patch",
    "start",
    "stop",
    "enable",
    "disable",
    "open",
    "close",
    "load",
    "save",
    "export",
    "import",
    "generate",
    "build",
    "parse",
    "format",
    "convert",
    "move",
    "copy",
}

CRUD_VERBS = {"create", "get", "read", "list", "update", "patch", "delete", "remove"}

HTTP_METHOD_PATTERN = re.compile(r"\b(GET|POST|PUT|DELETE|PATCH)\b")
ENDPOINT_PATTERN = re.compile(r"(?:/api/|endpoint|/v\d+/)")


class DiscoverabilityEvaluator:
    dimension: str = "discoverability"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        findings.extend(self._check_name_quality(server_meta))
        findings.extend(self._check_missing_examples(server_meta))
        findings.extend(self._check_enum_undocumented(events, server_meta))
        findings.extend(self._check_rest_wrapper_smell(server_meta))
        # description-clarity: LLM judge — placeholder
        # semantic-overlap: LLM judge — placeholder

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "discoverability.name-quality",
                "discoverability.missing-examples",
                "discoverability.enum-undocumented",
                "discoverability.rest-wrapper-smell",
                "discoverability.description-clarity",
                "discoverability.semantic-overlap",
            ],
            findings=findings,
        )

    def _check_name_quality(self, server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        for tool in server_meta.tools:
            issues: list[str] = []
            if len(tool.name) < 4:
                issues.append("name is too short (<4 chars)")
            parts = re.split(r"[_\-.]", tool.name.lower())
            has_verb = any(p in VERB_PREFIXES for p in parts)
            if not has_verb:
                issues.append("name contains no recognizable verb")
            if tool.name.isupper() and len(tool.name) > 1:
                issues.append("name is all uppercase")

            if issues:
                findings.append(
                    Finding(
                        check_id="discoverability.name-quality",
                        severity=Severity.LOW,
                        title=f"Tool `{tool.name}` has naming issues: {', '.join(issues)}",
                        description="Poor tool names reduce model comprehension and tool selection accuracy.",
                        evidence={"tool": tool.name, "issues": issues},
                        remediation="Use descriptive verb_noun format (e.g., get_document, search_users).",
                        affected_entity=tool.name,
                    )
                )
        return findings

    def _check_missing_examples(self, server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        example_patterns = re.compile(r"(?:example|e\.g\.|for instance|```|\{.*:.*\})", re.IGNORECASE)

        for tool in server_meta.tools:
            schema = tool.input_schema
            if not schema:
                continue
            required = schema.get("required", [])
            properties = schema.get("properties", {})
            has_nested = any(isinstance(p, dict) and p.get("type") == "object" for p in properties.values())
            is_complex = len(required) > 3 or has_nested

            if is_complex and not example_patterns.search(tool.description):
                findings.append(
                    Finding(
                        check_id="discoverability.missing-examples",
                        severity=Severity.MEDIUM,
                        title=f"Tool `{tool.name}` has complex schema but no examples in description",
                        description=f"Tool has {len(required)} required params{' and nested objects' if has_nested else ''}. Models will guess at structure.",
                        evidence={
                            "tool": tool.name,
                            "required_count": len(required),
                            "has_nested": has_nested,
                        },
                        remediation="Add a usage example in the description showing the expected argument structure.",
                        affected_entity=tool.name,
                    )
                )
        return findings

    def _check_enum_undocumented(self, events: list[McpEvent], server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        tool_lookup = {t.name: t for t in server_meta.tools}

        for tool in server_meta.tools:
            props = tool.input_schema.get("properties", {})
            for prop_name, prop_def in props.items():
                if not isinstance(prop_def, dict):
                    continue
                enum_values = prop_def.get("enum")
                if enum_values:
                    mentioned = any(str(v).lower() in tool.description.lower() for v in enum_values)
                    if not mentioned:
                        findings.append(
                            Finding(
                                check_id="discoverability.enum-undocumented",
                                severity=Severity.MEDIUM,
                                title=f"Tool `{tool.name}` param `{prop_name}` has undocumented enum values",
                                description=f"Enum values {enum_values} not mentioned in tool description.",
                                evidence={
                                    "tool": tool.name,
                                    "param": prop_name,
                                    "enum": enum_values,
                                },
                                remediation="Document allowed values in the tool description.",
                                affected_entity=tool.name,
                            )
                        )

        observed_values: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for event in events:
            if event.is_request and event.method == "tools/call" and event.params:
                name = event.params.get("name", "")
                args = event.params.get("arguments", {})
                if isinstance(args, dict):
                    for k, v in args.items():
                        if isinstance(v, str):
                            observed_values[name][k].add(v)

        for tool_name, params in observed_values.items():
            tool = tool_lookup.get(tool_name)
            if not tool:
                continue
            props = tool.input_schema.get("properties", {})
            for param_name, values in params.items():
                prop_def = props.get(param_name, {})
                if isinstance(prop_def, dict) and prop_def.get("enum"):
                    continue
                call_count = sum(
                    1
                    for e in events
                    if e.is_request and e.method == "tools/call" and e.params and e.params.get("name") == tool_name
                )
                if call_count >= 10 and len(values) <= 5:
                    findings.append(
                        Finding(
                            check_id="discoverability.enum-undocumented",
                            severity=Severity.MEDIUM,
                            title=f"Tool `{tool_name}` param `{param_name}` has implicit enum (observed: {sorted(values)})",
                            description=f"Across {call_count} calls, only {len(values)} distinct values observed. Consider adding an enum constraint.",
                            evidence={
                                "tool": tool_name,
                                "param": param_name,
                                "observed": sorted(values),
                                "call_count": call_count,
                            },
                            remediation="Add an enum constraint to the schema to guide model behavior.",
                            affected_entity=tool_name,
                        )
                    )
        return findings

    def _check_rest_wrapper_smell(self, server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        resources: dict[str, set[str]] = defaultdict(set)

        for tool in server_meta.tools:
            parts = re.split(r"[_\-.]", tool.name.lower())
            if len(parts) >= 2 and parts[0] in CRUD_VERBS:
                resource = "_".join(parts[1:])
                resources[resource].add(parts[0])

        crud_tools = 0
        total_tools = len(server_meta.tools)
        for resource, verbs in resources.items():
            if len(verbs) >= 3:
                crud_tools += len(verbs)

        if total_tools > 0 and crud_tools / total_tools > 0.5:
            findings.append(
                Finding(
                    check_id="discoverability.rest-wrapper-smell",
                    severity=Severity.MEDIUM,
                    title=f"Server looks like a REST API wrapper ({crud_tools}/{total_tools} tools follow CRUD patterns)",
                    description="Tools mirror REST resource CRUD operations. Agent-oriented tools should be task-focused.",
                    evidence={
                        "crud_tools": crud_tools,
                        "total": total_tools,
                        "resources": {r: sorted(v) for r, v in resources.items() if len(v) >= 3},
                    },
                    remediation="Design higher-level task-oriented tools instead of exposing raw CRUD operations.",
                )
            )

        for tool in server_meta.tools:
            desc = tool.description
            if HTTP_METHOD_PATTERN.search(desc) or ENDPOINT_PATTERN.search(desc):
                findings.append(
                    Finding(
                        check_id="discoverability.rest-wrapper-smell",
                        severity=Severity.MEDIUM,
                        title=f"Tool `{tool.name}` description references HTTP methods or endpoints",
                        description="Tool descriptions should describe agent-level actions, not HTTP implementation details.",
                        evidence={"tool": tool.name, "description_excerpt": desc[:200]},
                        remediation="Rewrite description to focus on what the tool does for the agent, not the HTTP details.",
                        affected_entity=tool.name,
                    )
                )
        return findings
