"""Protocol conformance dimension evaluators."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from reviewmymcp.evaluators.base import (
    EvaluatorConfig,
    EvaluatorResult,
    Finding,
    Severity,
    SkippedCheck,
)
from reviewmymcp.ingest.schema import McpEvent, ServerMeta

STANDARD_ERROR_CODES = {-32700, -32600, -32601, -32602, -32603, -32042}
DESTRUCTIVE_TOOL_RE = re.compile(r"^(delete|remove|drop|clear|purge|truncate|reset)[_-]", re.IGNORECASE)
DESTRUCTIVE_DESC_RE = re.compile(r"\b(delete|remove|drop|purge|clear|truncate|reset)\b", re.IGNORECASE)
MUTATING_TOOL_RE = re.compile(
    r"^(add|append|assign|create|edit|import|insert|merge|move|patch|post|publish|push|set|submit|update|upload|write)[_-]",
    re.IGNORECASE,
)
MUTATING_DESC_RE = re.compile(
    r"\b(add|append|assign|create|edit|import|insert|merge|move|patch|publish|push|set|submit|update|upload|write)\b",
    re.IGNORECASE,
)
MUTATION_ANNOTATIONS = {"readOnlyHint", "destructiveHint", "idempotentHint"}
JSON_SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}
JSON_SCHEMA_TOP_LEVEL_KEYS = {"oneOf", "anyOf", "allOf", "enum", "$ref"}
NON_JSON_SCHEMA_FINGERPRINTS = {"_def", "_zod", "_validators", "__fields__", "model_fields"}
ERROR_CONTENT_RE = re.compile(
    r"\b(api error|error:|exception|failed|forbidden|http\s+[45]\d\d|invalid|not found|rate limit|traceback|unauthorized)\b",
    re.IGNORECASE,
)
ERROR_FALSE_POSITIVE_RE = re.compile(r"\b(?:no|zero|without)\s+errors?\b|\berrors?\s+found:\s*0\b", re.IGNORECASE)


class ConformanceEvaluator:
    dimension: str = "conformance"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        skipped: list[SkippedCheck] = []
        findings.extend(self._check_initialize_handshake(events, skipped))
        findings.extend(self._check_capability_mismatch(events, server_meta))
        findings.extend(self._check_jsonrpc_conformance(events))
        findings.extend(self._check_session_management(events))
        findings.extend(self._check_error_codes(events))
        findings.extend(self._check_notification_correctness(events))
        findings.extend(self._check_is_error_flag_set(events))
        findings.extend(self._check_destructive_hint_missing(server_meta))
        findings.extend(self._check_mutating_annotations_missing(server_meta))
        findings.extend(self._check_output_schema_wire_format(server_meta))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "conformance.initialize-handshake",
                "conformance.capability-mismatch",
                "conformance.jsonrpc-conformance",
                "conformance.session-management",
                "conformance.error-code-correctness",
                "conformance.notification-correctness",
                "conformance.is-error-flag-set",
                "conformance.destructive-hint-missing",
                "conformance.mutating-annotations-missing",
                "conformance.output-schema-wire-format",
            ],
            findings=findings,
            checks_skipped=skipped,
        )

    def _check_initialize_handshake(self, events: list[McpEvent], skipped: list[SkippedCheck]) -> list[Finding]:
        findings: list[Finding] = []
        sessions: dict[str | None, list[McpEvent]] = defaultdict(list)
        for event in events:
            sessions[event.session_id].append(event)

        small_sessions = 0
        for sid, session_events in sessions.items():
            sorted_events = sorted(session_events, key=lambda e: e.timestamp)
            if not sorted_events:
                continue
            if len(sorted_events) <= 2:
                small_sessions += 1
                continue

            init_request_idx = None
            init_response_idx = None
            initialized_idx = None

            for i, event in enumerate(sorted_events):
                if event.method == "initialize" and event.is_request and init_request_idx is None:
                    init_request_idx = i
                if event.method == "initialize" and event.is_response and init_response_idx is None:
                    init_response_idx = i
                if event.method == "notifications/initialized" and initialized_idx is None:
                    initialized_idx = i

            if init_request_idx is None and len(sorted_events) > 2:
                findings.append(
                    Finding(
                        check_id="conformance.initialize-handshake",
                        severity=Severity.CRITICAL,
                        title=f"Session {sid or '(unnamed)'} missing initialize request",
                        description="MCP sessions must begin with an initialize request.",
                        evidence={"session_id": sid, "first_method": sorted_events[0].method},
                        remediation="Ensure the client sends an initialize request as the first message.",
                    )
                )
                continue

            if init_request_idx is not None:
                pre_init = [
                    e
                    for i, e in enumerate(sorted_events)
                    if i < init_request_idx and e.method != "ping" and not e.is_response
                ]
                if pre_init:
                    findings.append(
                        Finding(
                            check_id="conformance.initialize-handshake",
                            severity=Severity.CRITICAL,
                            title=f"Messages sent before initialize in session {sid or '(unnamed)'}",
                            description=f"{len(pre_init)} non-ping messages appeared before the initialize request.",
                            evidence={
                                "session_id": sid,
                                "pre_init_methods": [e.method for e in pre_init[:5]],
                            },
                            remediation="No requests (except ping) should be sent before the initialize handshake.",
                        )
                    )

            if init_response_idx is not None and initialized_idx is not None:
                if initialized_idx < init_response_idx:
                    findings.append(
                        Finding(
                            check_id="conformance.initialize-handshake",
                            severity=Severity.HIGH,
                            title="notifications/initialized sent before initialize response",
                            description="Client must wait for the server's initialize response before sending initialized notification.",
                            evidence={"session_id": sid},
                            remediation="Wait for the initialize response before sending notifications/initialized.",
                        )
                    )
        if small_sessions:
            skipped.append(SkippedCheck(
                check_id="conformance.initialize-handshake",
                reason=f"skipped {small_sessions} session(s) with <= 2 events",
            ))
        return findings

    def _check_capability_mismatch(self, events: list[McpEvent], server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        caps = server_meta.server_capabilities
        methods_used: set[str] = set()
        for event in events:
            if event.method:
                methods_used.add(event.method)

        capability_method_map = {
            "tools": ["tools/list", "tools/call"],
            "prompts": ["prompts/list", "prompts/get"],
            "resources": ["resources/list", "resources/read", "resources/subscribe"],
        }

        for cap_name, methods in capability_method_map.items():
            declared = getattr(caps, cap_name, False)
            used = [m for m in methods if m in methods_used]

            if not declared and used:
                findings.append(
                    Finding(
                        check_id="conformance.capability-mismatch",
                        severity=Severity.HIGH,
                        title=f"Server handles {cap_name} methods without declaring capability",
                        description=f"Methods {used} used but `{cap_name}` not in server capabilities.",
                        evidence={"capability": cap_name, "undeclared_methods": used},
                        remediation=f"Declare `{cap_name}` capability in the initialize response.",
                    )
                )
        return findings

    def _check_jsonrpc_conformance(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        issues: list[dict] = []

        for event in events:
            raw = event.raw_message
            if not raw:
                continue
            if event.is_probe and not event.is_response:
                continue

            if raw.get("jsonrpc") != "2.0":
                issues.append(
                    {
                        "event_id": event.event_id,
                        "issue": "missing or wrong jsonrpc field",
                        "value": raw.get("jsonrpc"),
                    }
                )

            if event.is_response:
                has_result = "result" in raw
                has_error = "error" in raw
                if has_result and has_error:
                    issues.append({"event_id": event.event_id, "issue": "response has both result and error"})
                if not has_result and not has_error:
                    issues.append(
                        {
                            "event_id": event.event_id,
                            "issue": "response has neither result nor error",
                        }
                    )

            if event.is_request and "id" not in raw:
                issues.append({"event_id": event.event_id, "issue": "request missing id field"})

        if issues:
            findings.append(
                Finding(
                    check_id="conformance.jsonrpc-conformance",
                    severity=Severity.HIGH,
                    title=f"{len(issues)} JSON-RPC 2.0 conformance violations",
                    description="Messages do not conform to the JSON-RPC 2.0 specification.",
                    evidence={"violation_count": len(issues), "samples": issues[:5]},
                    remediation='Ensure all messages include `jsonrpc: "2.0"` and follow request/response/notification structure.',
                )
            )
        return findings

    def _check_output_schema_wire_format(self, server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        for tool in server_meta.tools:
            schema = tool.output_schema
            if not schema:
                continue

            issue = self._output_schema_issue(schema)
            if issue is None:
                continue

            findings.append(
                Finding(
                    check_id="conformance.output-schema-wire-format",
                    severity=Severity.MEDIUM,
                    title=f"Tool `{tool.name}` outputSchema is not plain JSON Schema",
                    description=f"tools/list outputSchema for `{tool.name}` appears to use {issue}.",
                    evidence={
                        "tool": tool.name,
                        "issue": issue,
                        "schema_snippet": json.dumps(schema, default=str)[:200],
                    },
                    remediation="Emit plain JSON Schema in tools/list outputSchema, not SDK-native schema objects.",
                    affected_entity=tool.name,
                )
            )
        return findings

    def _output_schema_issue(self, schema: Any) -> str | None:
        if not isinstance(schema, dict):
            return "a non-object schema"

        fingerprint = _find_non_json_schema_fingerprint(schema)
        if fingerprint:
            return f"a non-JSON-Schema field `{fingerprint}`"

        schema_type = schema.get("type")
        if _is_valid_json_schema_type(schema_type):
            return None
        if any(key in schema for key in JSON_SCHEMA_TOP_LEVEL_KEYS):
            return None

        return "an unrecognized top-level schema shape"

    def _check_session_management(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        session_id_assigned: str | None = None
        missing_session_id = 0

        for event in events:
            if event.http_headers and not session_id_assigned:
                sid = event.http_headers.get("mcp-session-id") or event.http_headers.get("Mcp-Session-Id")
                if sid:
                    session_id_assigned = sid

            if session_id_assigned and event.http_headers:
                client_sid = event.http_headers.get("mcp-session-id") or event.http_headers.get("Mcp-Session-Id")
                if event.direction.value == "client_to_server" and not client_sid:
                    missing_session_id += 1

        if session_id_assigned and missing_session_id > 0:
            findings.append(
                Finding(
                    check_id="conformance.session-management",
                    severity=Severity.MEDIUM,
                    title=f"{missing_session_id} client requests missing Mcp-Session-Id header",
                    description="After the server assigns a session ID, all client requests must include it.",
                    evidence={
                        "missing_count": missing_session_id,
                        "session_id": session_id_assigned,
                    },
                    remediation="Include Mcp-Session-Id header in all requests after initialization.",
                )
            )
        return findings

    def _check_error_codes(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        bad_codes: list[dict] = []

        for event in events:
            if event.is_error and event.error:
                code = event.error.get("code")
                if isinstance(code, int) and code not in STANDARD_ERROR_CODES:
                    if not (-32099 <= code <= -32000):
                        bad_codes.append({"code": code, "message": event.error.get("message", "")[:100]})

        if bad_codes:
            findings.append(
                Finding(
                    check_id="conformance.error-code-correctness",
                    severity=Severity.LOW,
                    title=f"{len(bad_codes)} non-standard JSON-RPC error codes used",
                    description="Error codes should use standard JSON-RPC 2.0 codes or the server-defined range (-32000 to -32099).",
                    evidence={"non_standard_codes": bad_codes[:10]},
                    remediation="Use standard error codes: -32700, -32600, -32601, -32602, -32603.",
                )
            )
        return findings

    def _check_notification_correctness(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        bad_notifications: int = 0

        for event in events:
            if not event.raw_message:
                continue
            raw = event.raw_message
            has_id = "id" in raw

            if event.is_notification and has_id:
                bad_notifications += 1

        if bad_notifications:
            findings.append(
                Finding(
                    check_id="conformance.notification-correctness",
                    severity=Severity.MEDIUM,
                    title=f"{bad_notifications} notifications incorrectly include an `id` field",
                    description="Per JSON-RPC 2.0, notifications must not have an `id` field.",
                    evidence={"count": bad_notifications},
                    remediation="Remove the `id` field from notification messages.",
                )
            )
        return findings

    def _check_is_error_flag_set(self, events: list[McpEvent]) -> list[Finding]:
        requests = {
            event.event_id: event
            for event in events
            if event.is_request and event.method == "tools/call" and not event.is_probe
        }
        samples_by_tool: dict[str, list[str]] = defaultdict(list)

        for response in events:
            if not (
                response.is_response
                and response.request_event_id in requests
                and response.result
                and not response.is_error
                and not response.is_probe
            ):
                continue
            if response.result.get("isError") is True:
                continue

            text = _tool_response_text(response.result)
            if not _looks_like_error_content(text):
                continue

            request = requests[response.request_event_id]
            tool_name = request.params.get("name", "unknown") if request.params else "unknown"
            samples_by_tool[tool_name].append(text[:300])

        findings = []
        for tool_name, samples in samples_by_tool.items():
            findings.append(
                Finding(
                    check_id="conformance.is-error-flag-set",
                    severity=Severity.MEDIUM,
                    title=f"Tool `{tool_name}` returned error content without `isError: true`",
                    description="Tool responses that contain errors should set `isError: true` so agents can distinguish failures from successful content.",
                    evidence={"tool": tool_name, "samples": samples[:3]},
                    remediation="Return `isError: true` for tool-level failures, or use a JSON-RPC error for protocol-level failures.",
                    affected_entity=tool_name,
                )
            )
        return findings

    def _check_destructive_hint_missing(self, server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        for tool in server_meta.tools:
            text = f"{tool.name} {tool.description}"
            if not _is_destructive_tool(tool.name, text):
                continue
            if tool.annotations.get("destructiveHint") is True:
                continue
            findings.append(
                Finding(
                    check_id="conformance.destructive-hint-missing",
                    severity=Severity.MEDIUM,
                    title=f"Tool `{tool.name}` appears destructive but lacks destructiveHint",
                    description=(
                        "Tool name or description suggests permanent destructive behavior, "
                        "but tools/list did not advertise annotations.destructiveHint: true."
                    ),
                    evidence={
                        "tool": tool.name,
                        "annotations": tool.annotations,
                    },
                    remediation="Set `annotations.destructiveHint: true` for destructive tools so clients can apply safeguards.",
                    affected_entity=tool.name,
                )
            )
        return findings

    def _check_mutating_annotations_missing(self, server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        for tool in server_meta.tools:
            text = f"{tool.name} {tool.description}"
            if _is_destructive_tool(tool.name, text) or not _is_mutating_tool(tool.name, text):
                continue
            if any(key in tool.annotations for key in MUTATION_ANNOTATIONS):
                continue
            findings.append(
                Finding(
                    check_id="conformance.mutating-annotations-missing",
                    severity=Severity.MEDIUM,
                    title=f"Tool `{tool.name}` appears mutating but lacks behavior annotations",
                    description=(
                        "Tool name or description suggests state-changing behavior, but tools/list did not advertise "
                        "readOnlyHint, destructiveHint, or idempotentHint."
                    ),
                    evidence={
                        "tool": tool.name,
                        "annotations": tool.annotations,
                    },
                    remediation=(
                        "Set behavior annotations for mutating tools, such as readOnlyHint: false plus appropriate "
                        "destructiveHint and idempotentHint values."
                    ),
                    affected_entity=tool.name,
                )
            )
        return findings


def _is_valid_json_schema_type(value: Any) -> bool:
    if isinstance(value, str):
        return value in JSON_SCHEMA_TYPES
    if isinstance(value, list):
        return bool(value) and all(isinstance(item, str) and item in JSON_SCHEMA_TYPES for item in value)
    return False


def _tool_response_text(result: dict[str, Any]) -> str:
    content = result.get("content", [])
    if not isinstance(content, list):
        return ""
    return "\n".join(
        item["text"]
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )


def _looks_like_error_content(text: str) -> bool:
    if not text:
        return False
    if ERROR_FALSE_POSITIVE_RE.search(text):
        return False
    return bool(ERROR_CONTENT_RE.search(text))


def _is_destructive_tool(name: str, text: str) -> bool:
    return bool(DESTRUCTIVE_TOOL_RE.search(name) or DESTRUCTIVE_DESC_RE.search(text))


def _is_mutating_tool(name: str, text: str) -> bool:
    return bool(MUTATING_TOOL_RE.search(name) or MUTATING_DESC_RE.search(text))


def _find_non_json_schema_fingerprint(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in NON_JSON_SCHEMA_FINGERPRINTS:
                return key
            found = _find_non_json_schema_fingerprint(nested)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_non_json_schema_fingerprint(item)
            if found:
                return found
    return None
