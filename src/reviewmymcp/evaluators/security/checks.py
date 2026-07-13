"""Security dimension evaluators."""

from __future__ import annotations

import re
from typing import Any

from reviewmymcp.evaluators.base import (
    EvaluatorConfig,
    EvaluatorResult,
    Finding,
    Severity,
    SkippedCheck,
)
from reviewmymcp.ingest.schema import McpEvent, ServerMeta

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pos]_[A-Za-z0-9]{36}")),
    ("slack_token", re.compile(r"xox[bpsar]-[A-Za-z0-9-]{10,}")),
    ("api_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    (
        "connection_string",
        re.compile(r"(?:postgres|mysql|mongodb|redis)://[^\s\"']+:[^\s\"']+@[^\s\"']+"),
    ),
    ("password_in_url", re.compile(r"://[^\s\"'/:@]+:[^\s\"'/@]+@")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")),
]

INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"system:\s*", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"IMPORTANT:\s*(?:ignore|disregard|forget)", re.IGNORECASE),
    re.compile(r"(?:output|reveal|show|print)\s+(?:the\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
    re.compile(r"<\|im_start\|>system", re.IGNORECASE),
]

SAFETY_LANGUAGE = (
    "allowlist",
    "allow-listed",
    "allow listed",
    "authenticated to a single host",
    "confirmation",
    "confirm",
    "fixed host",
    "restricted",
    "safe",
    "sandbox",
    "sandboxed",
    "single host",
)
NETWORK_RISK_TERMS = ("url", "uri", "http", "request", "fetch", "download", "crawl", "scrape", "webhook")
FILE_RISK_TERMS = ("file", "path", "attachment", "read", "write", "delete", "upload", "download")
DESTRUCTIVE_ACTION_RE = re.compile(r"\b(?:delete|destroy|erase|purge|remove|wipe)\w*\b", re.IGNORECASE)
SSRF_BLOCK_TERMS = (
    "blocked",
    "disallowed",
    "not allowed",
    "forbidden url",
    "private ip blocked",
    "private network",
    "file scheme",
    "unsupported scheme",
)


class SecurityEvaluator:
    dimension: str = "security"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        skipped: list[SkippedCheck] = []
        findings.extend(self._check_prompt_injection(events, config, skipped))
        findings.extend(self._check_secret_leakage(events))
        findings.extend(self._check_auth_flow(events))
        findings.extend(self._check_scope_creep(events, server_meta))
        ssrf_findings = self._check_ssrf_unblocked(events)
        findings.extend(ssrf_findings)
        ssrf_tools = {finding.affected_entity for finding in ssrf_findings if finding.affected_entity}
        findings.extend(self._check_excessive_permissions(server_meta, ssrf_tools))
        findings.extend(self._check_destructive_hint_missing(server_meta))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "security.prompt-injection-surface",
                "security.secret-leakage",
                "security.auth-flow-correctness",
                "security.scope-creep",
                "security.ssrf-unblocked",
                "security.excessive-permissions",
                "security.network-access-warning",
                "security.destructive-hint-missing",
            ],
            findings=findings,
            checks_skipped=skipped,
        )

    def _check_prompt_injection(
        self,
        events: list[McpEvent],
        config: EvaluatorConfig,
        skipped: list[SkippedCheck],
    ) -> list[Finding]:
        findings: list[Finding] = []
        tool_responses = [e for e in events if e.is_response and e.result and not e.is_error]

        regex_flagged_event_ids: set[str] = set()
        for event in tool_responses:
            content = event.result.get("content", []) if event.result else []
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text", "")
                for pattern in INJECTION_PATTERNS:
                    match = pattern.search(text)
                    if match:
                        regex_flagged_event_ids.add(event.event_id)
                        tool_name = event.method or "unknown"
                        if event.request_event_id:
                            for e in events:
                                if e.event_id == event.request_event_id and e.params:
                                    tool_name = e.params.get("name", tool_name)
                                    break
                        findings.append(
                            Finding(
                                check_id="security.prompt-injection-surface",
                                severity=Severity.CRITICAL,
                                title=f"Potential prompt injection in `{tool_name}` output",
                                description=f"Tool output contains text matching injection pattern: '{match.group()[:100]}'",
                                evidence={
                                    "tool": tool_name,
                                    "pattern": pattern.pattern,
                                    "fragment": text[max(0, match.start() - 50) : match.end() + 50],
                                },
                                remediation="Sanitize or sandbox tool outputs before returning to model context.",
                                affected_entity=tool_name,
                            )
                        )
                        break

        # Judge pass: scan non-flagged responses for subtler injection patterns
        if config.judge is not None:
            from reviewmymcp.judge.base import JudgeRequest
            from reviewmymcp.judge.prompts import PROMPT_INJECTION_SYSTEM, PROMPT_INJECTION_USER

            unflagged = [e for e in tool_responses if e.event_id not in regex_flagged_event_ids]
            for event in unflagged[:50]:
                content = event.result.get("content", []) if event.result else []
                text_parts = [
                    item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"
                ]
                full_text = "\n".join(text_parts)
                if not full_text.strip():
                    continue

                tool_name = "unknown"
                if event.request_event_id:
                    for e in events:
                        if e.event_id == event.request_event_id and e.params:
                            tool_name = e.params.get("name", tool_name)
                            break

                response = config.judge.complete(
                    JudgeRequest(
                        system=PROMPT_INJECTION_SYSTEM,
                        user=PROMPT_INJECTION_USER.format(tool_name=tool_name, content=full_text[:2000]),
                    )
                )
                if response.parsed is None:
                    continue
                risk_score = response.parsed.get("risk_score", 1)
                if risk_score >= 4:
                    findings.append(
                        Finding(
                            check_id="security.prompt-injection-surface",
                            severity=Severity.CRITICAL if risk_score >= 5 else Severity.HIGH,
                            title=f"Judge: potential injection in `{tool_name}` output (risk {risk_score}/5)",
                            description=response.parsed.get("rationale", ""),
                            evidence={
                                "risk_score": risk_score,
                                "suspicious_fragments": response.parsed.get("suspicious_fragments", []),
                            },
                            remediation="Sanitize or sandbox tool outputs before returning to model context.",
                            affected_entity=tool_name,
                        )
                    )

        return findings

    def _check_ssrf_unblocked(self, events: list[McpEvent]) -> list[Finding]:
        requests = {
            event.event_id: event
            for event in events
            if event.is_request
            and event.method == "tools/call"
            and event.is_probe
            and event.probe_type == "ssrf_url_filtering"
        }
        findings: list[Finding] = []
        seen: set[tuple[str, str]] = set()

        for response in events:
            if not (
                response.is_response
                and response.request_event_id in requests
                and response.is_probe
                and response.probe_type == "ssrf_url_filtering"
            ):
                continue

            request = requests[response.request_event_id]
            tool_name = request.params.get("name", "unknown") if request.params else "unknown"
            arguments = request.params.get("arguments", {}) if request.params else {}
            target = _first_url_argument(arguments)
            if not target or _is_explicitly_blocked(response):
                continue

            key = (tool_name, target)
            if key in seen:
                continue
            seen.add(key)
            severity = Severity.CRITICAL if "169.254.169.254" in target else Severity.HIGH
            findings.append(
                Finding(
                    check_id="security.ssrf-unblocked",
                    severity=severity,
                    title=f"Tool `{tool_name}` did not block SSRF probe target",
                    description=(
                        f"The SSRF probe target `{target}` did not produce an explicit blocked/disallowed URL response."
                    ),
                    evidence={
                        "tool": tool_name,
                        "target": target,
                        "response": _response_text(response)[:300],
                    },
                    remediation="Block localhost, private ranges, cloud metadata IPs, and non-HTTP schemes before fetching URLs.",
                    affected_entity=tool_name,
                )
            )
        return findings

    def _check_secret_leakage(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        tool_responses = [e for e in events if e.is_response and e.result and not e.is_error]
        seen: set[str] = set()

        for event in tool_responses:
            content = event.result.get("content", []) if event.result else []
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text", "")
                for label, pattern in SECRET_PATTERNS:
                    match = pattern.search(text)
                    if match:
                        dedup_key = f"{label}:{match.group()[:20]}"
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)

                        tool_name = "unknown"
                        if event.request_event_id:
                            for e in events:
                                if e.event_id == event.request_event_id and e.params:
                                    tool_name = e.params.get("name", tool_name)
                                    break
                        findings.append(
                            Finding(
                                check_id="security.secret-leakage",
                                severity=Severity.CRITICAL,
                                title=f"Secret ({label}) leaked in `{tool_name}` output",
                                description=f"Tool output contains what appears to be a {label}.",
                                evidence={
                                    "tool": tool_name,
                                    "secret_type": label,
                                    "prefix": match.group()[:10] + "...",
                                },
                                remediation="Never include secrets, tokens, or credentials in tool outputs.",
                                affected_entity=tool_name,
                            )
                        )
        return findings

    def _check_auth_flow(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        for event in events:
            if not event.http_headers:
                continue
            for key, value in event.http_headers.items():
                if key.lower() == "www-authenticate" and "bearer" in value.lower():
                    if "resource_metadata" not in value:
                        findings.append(
                            Finding(
                                check_id="security.auth-flow-correctness",
                                severity=Severity.HIGH,
                                title="WWW-Authenticate header missing resource_metadata",
                                description="Server's 401 response should include resource_metadata URL per MCP spec.",
                                evidence={"header": value[:200]},
                                remediation="Include resource_metadata parameter in WWW-Authenticate header.",
                            )
                        )

            query_string = event.http_headers.get("query_string", "")
            if "access_token=" in query_string or "token=" in query_string:
                findings.append(
                    Finding(
                        check_id="security.auth-flow-correctness",
                        severity=Severity.HIGH,
                        title="Access token passed in URL query parameters",
                        description="Tokens in URLs are logged in server access logs and browser history.",
                        evidence={},
                        remediation="Send tokens in the Authorization header, not in query parameters.",
                    )
                )
        return findings

    def _check_scope_creep(self, events: list[McpEvent], server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        caps = server_meta.server_capabilities
        client_caps = server_meta.client_capabilities
        methods_used: set[str] = set()
        for event in events:
            if event.method:
                methods_used.add(event.method)

        if not caps.tools and any(m.startswith("tools/") for m in methods_used):
            findings.append(
                Finding(
                    check_id="security.scope-creep",
                    severity=Severity.HIGH,
                    title="Server handles tool requests without declaring tools capability",
                    description="Server responded to tools/* methods but did not declare tools capability during initialization.",
                    evidence={"tool_methods_seen": sorted(m for m in methods_used if m.startswith("tools/"))},
                    remediation="Declare all implemented capabilities in the initialize response.",
                )
            )

        if "sampling/createMessage" in methods_used and not client_caps.sampling:
            findings.append(
                Finding(
                    check_id="security.scope-creep",
                    severity=Severity.HIGH,
                    title="Server uses sampling without client declaring capability",
                    description="Server sent sampling/createMessage but client did not declare sampling capability.",
                    evidence={},
                    remediation="Only use sampling if the client declared sampling capability.",
                )
            )

        if "elicitation/create" in methods_used:
            if not client_caps.elicitation_form and not client_caps.elicitation_url:
                findings.append(
                    Finding(
                        check_id="security.scope-creep",
                        severity=Severity.HIGH,
                        title="Server uses elicitation without client declaring capability",
                        description="Server sent elicitation/create but client did not declare elicitation capability.",
                        evidence={},
                        remediation="Only use elicitation if the client declared it.",
                    )
                )
        return findings

    def _check_excessive_permissions(
        self,
        server_meta: ServerMeta,
        suppress_network_tools: set[str] | None = None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        suppress_network_tools = suppress_network_tools or set()
        direct_patterns = [
            (r"execut|shell|command|bash|cmd|terminal|subprocess", "arbitrary command execution"),
            (r"database|sql|query.*db|drop\s+table", "database access"),
        ]

        for tool in server_meta.tools:
            text = f"{tool.name} {tool.description}".lower()
            for pattern, risk in direct_patterns:
                if re.search(pattern, text):
                    if not _has_safety_language(text):
                        findings.append(
                            Finding(
                                check_id="security.excessive-permissions",
                                severity=Severity.MEDIUM,
                                title=f"Tool `{tool.name}` indicates {risk} without safeguards",
                                description=f"Tool description suggests {risk} capability with no mention of sandboxing or confirmation.",
                                evidence={"tool": tool.name, "risk": risk},
                                remediation="Add sandboxing, confirmation mechanisms, or restrict scope of dangerous operations.",
                                affected_entity=tool.name,
                            )
                        )
                    break
            else:
                network_terms = [term for term in NETWORK_RISK_TERMS if term in text]
                file_terms = [term for term in FILE_RISK_TERMS if term in text]
                execution = tool.execution or {}
                user_controlled_url = bool(
                    execution.get("user_controlled_url") or execution.get("dereferences_user_url")
                )
                user_controlled_path = bool(
                    execution.get("user_controlled_path") or execution.get("dereferences_user_path")
                )

                if tool.name in suppress_network_tools and (user_controlled_url or len(network_terms) >= 2):
                    continue
                if user_controlled_url and not execution.get("url_filtering"):
                    findings.append(
                        _network_access_finding(tool.name, "unfiltered user-controlled URL access", Severity.MEDIUM)
                    )
                    continue
                elif user_controlled_path and not execution.get("path_filtering"):
                    risk = "unfiltered user-controlled path access"
                elif len(network_terms) >= 2 and not _has_safety_language(text):
                    findings.append(_network_access_finding(tool.name, "description-only network access", Severity.LOW))
                    continue
                elif len(file_terms) >= 2 and not _has_safety_language(text):
                    risk = "file system access"
                else:
                    continue

                findings.append(
                    Finding(
                        check_id="security.excessive-permissions",
                        severity=Severity.MEDIUM,
                        title=f"Tool `{tool.name}` indicates {risk} without safeguards",
                        description=f"Tool description suggests {risk} capability with no mention of sandboxing or confirmation.",
                        evidence={"tool": tool.name, "risk": risk},
                        remediation="Add sandboxing, confirmation mechanisms, or restrict scope of dangerous operations.",
                        affected_entity=tool.name,
                    )
                )
        return findings

    def _check_destructive_hint_missing(self, server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        for tool in server_meta.tools:
            if not DESTRUCTIVE_ACTION_RE.search(tool.name) or DESTRUCTIVE_ACTION_RE.search(tool.description):
                continue
            findings.append(
                Finding(
                    check_id="security.destructive-hint-missing",
                    severity=Severity.MEDIUM,
                    title=f"Tool `{tool.name}` does not describe its destructive action",
                    description=(
                        "The tool name suggests a destructive action, but its description does not name that action or "
                        "its consequences."
                    ),
                    evidence={"tool": tool.name, "description": tool.description},
                    remediation="Describe the destructive action and its consequences in the tool description.",
                    affected_entity=tool.name,
                )
            )
        return findings


def _has_safety_language(text: str) -> bool:
    return any(phrase in text for phrase in SAFETY_LANGUAGE)


def _first_url_argument(value: Any) -> str:
    if isinstance(value, str) and value.startswith(("http://", "https://", "file://")):
        return value
    if isinstance(value, dict):
        for nested in value.values():
            found = _first_url_argument(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _first_url_argument(nested)
            if found:
                return found
    return ""


def _is_explicitly_blocked(response: McpEvent) -> bool:
    text = _response_text(response).lower()
    return bool(text) and any(term in text for term in SSRF_BLOCK_TERMS)


def _response_text(response: McpEvent) -> str:
    parts: list[str] = []
    if response.error:
        message = response.error.get("message")
        if isinstance(message, str):
            parts.append(message)
    if response.result:
        content = response.result.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


def _network_access_finding(tool_name: str, risk: str, severity: Severity) -> Finding:
    return Finding(
        check_id="security.network-access-warning",
        severity=severity,
        title=f"Tool `{tool_name}` appears to accept user-controlled network targets",
        description="Tool name or description suggests network access, but no active SSRF probe result is available.",
        evidence={"tool": tool_name, "risk": risk},
        remediation="Use active SSRF probes where possible, and document URL scheme, host, and private-network filtering.",
        affected_entity=tool_name,
    )
