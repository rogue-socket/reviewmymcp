"""Security dimension evaluators."""

from __future__ import annotations

import re

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
        findings.extend(self._check_excessive_permissions(server_meta))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "security.prompt-injection-surface",
                "security.secret-leakage",
                "security.auth-flow-correctness",
                "security.scope-creep",
                "security.excessive-permissions",
            ],
            findings=findings,
            checks_skipped=skipped,
        )

    def _check_prompt_injection(
        self, events: list[McpEvent], config: EvaluatorConfig, skipped: list[SkippedCheck],
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
                text_parts = [item.get("text", "") for item in content
                              if isinstance(item, dict) and item.get("type") == "text"]
                full_text = "\n".join(text_parts)
                if not full_text.strip():
                    continue

                tool_name = "unknown"
                if event.request_event_id:
                    for e in events:
                        if e.event_id == event.request_event_id and e.params:
                            tool_name = e.params.get("name", tool_name)
                            break

                response = config.judge.complete(JudgeRequest(
                    system=PROMPT_INJECTION_SYSTEM,
                    user=PROMPT_INJECTION_USER.format(tool_name=tool_name, content=full_text[:2000]),
                ))
                if response.parsed is None:
                    continue
                risk_score = response.parsed.get("risk_score", 1)
                if risk_score >= 4:
                    findings.append(Finding(
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
                    ))

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

    def _check_excessive_permissions(self, server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        dangerous_patterns = [
            (r"execut|shell|command|bash|cmd|terminal|subprocess", "arbitrary command execution"),
            (r"file.*system|read.*file|write.*file|delete.*file|rm\s", "file system access"),
            (r"network|http|request|fetch|curl|wget", "arbitrary network access"),
            (r"database|sql|query.*db|drop\s+table", "database access"),
        ]

        for tool in server_meta.tools:
            text = f"{tool.name} {tool.description}".lower()
            for pattern, risk in dangerous_patterns:
                if re.search(pattern, text):
                    has_confirmation = any(kw in text for kw in ("confirm", "approve", "sandbox", "restricted", "safe"))
                    if not has_confirmation:
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
        return findings
