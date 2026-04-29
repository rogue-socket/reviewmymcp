"""Compliance & governance dimension evaluators."""

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

PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")),
    ("phone", re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b")),
]


class ComplianceEvaluator:
    dimension: str = "compliance"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        skipped: list[SkippedCheck] = []
        findings.extend(self._check_pii(events))
        findings.extend(self._check_audit_trail(events))
        findings.extend(self._check_consent_flows(events, server_meta))
        findings.extend(self._check_data_residency(events, config, skipped))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "compliance.pii-in-responses",
                "compliance.audit-trail-completeness",
                "compliance.consent-flow-gaps",
                "compliance.data-residency-signals",
            ],
            findings=findings,
            checks_skipped=skipped,
        )

    def _check_pii(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []
        pii_counts: dict[str, int] = {}

        for event in events:
            if not (event.is_response and event.result):
                continue
            content = event.result.get("content", [])
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text", "")
                for label, pattern in PII_PATTERNS:
                    matches = pattern.findall(text)
                    if matches:
                        pii_counts[label] = pii_counts.get(label, 0) + len(matches)

        for label, count in pii_counts.items():
            findings.append(
                Finding(
                    check_id="compliance.pii-in-responses",
                    severity=Severity.HIGH,
                    title=f"PII detected in tool responses: {label} ({count} instances)",
                    description=f"Found {count} {label} pattern matches across tool responses. This data flows into model context.",
                    evidence={"pii_type": label, "count": count},
                    remediation="Mask or redact PII before returning data to the model. Consider if this data is necessary.",
                )
            )
        return findings

    def _check_audit_trail(self, events: list[McpEvent]) -> list[Finding]:
        findings: list[Finding] = []

        requests: dict[tuple, McpEvent] = {}
        responses: set[tuple] = set()
        for event in events:
            key = (event.session_id, event.jsonrpc_id)
            if event.is_request and event.jsonrpc_id is not None:
                requests[key] = event
            if event.is_response and event.jsonrpc_id is not None:
                responses.add(key)

        orphan_requests = [req for key, req in requests.items() if key not in responses]
        if orphan_requests:
            findings.append(
                Finding(
                    check_id="compliance.audit-trail-completeness",
                    severity=Severity.MEDIUM,
                    title=f"{len(orphan_requests)} requests have no matching response",
                    description="Requests without responses create gaps in the audit trail.",
                    evidence={
                        "orphan_count": len(orphan_requests),
                        "methods": list(set(r.method or "?" for r in orphan_requests[:10])),
                    },
                    remediation="Ensure all requests receive responses, even if the response is an error.",
                )
            )

        sessions: dict[str | None, list[McpEvent]] = {}
        for event in events:
            sessions.setdefault(event.session_id, []).append(event)

        for sid, session_events in sessions.items():
            has_init = any(e.method == "initialize" and e.is_request for e in session_events)
            if not has_init and len(session_events) > 2:
                findings.append(
                    Finding(
                        check_id="compliance.audit-trail-completeness",
                        severity=Severity.MEDIUM,
                        title=f"Session {sid or '(unnamed)'} has no initialize request",
                        description=f"Session with {len(session_events)} events lacks an initialize handshake.",
                        evidence={"session_id": sid, "event_count": len(session_events)},
                        remediation="Ensure logging captures the full session lifecycle including initialization.",
                    )
                )
        return findings

    def _check_consent_flows(self, events: list[McpEvent], server_meta: ServerMeta) -> list[Finding]:
        findings: list[Finding] = []
        sampling_requests = [e for e in events if e.method == "sampling/createMessage" and e.is_request]
        elicitation_requests = [e for e in events if e.method == "elicitation/create" and e.is_request]

        if sampling_requests and not server_meta.client_capabilities.sampling:
            findings.append(
                Finding(
                    check_id="compliance.consent-flow-gaps",
                    severity=Severity.HIGH,
                    title=f"{len(sampling_requests)} sampling requests without client declaring capability",
                    description="Sampling requests should only occur when the client has declared sampling support.",
                    evidence={"count": len(sampling_requests)},
                    remediation="Ensure client declares sampling capability and prompts user for consent.",
                )
            )

        for elicit in elicitation_requests:
            if elicit.params and elicit.params.get("mode") == "form":
                schema = elicit.params.get("requestedSchema", {})
                props = schema.get("properties", {})
                for prop_name, prop_def in props.items():
                    desc = str(prop_def.get("description", "")).lower()
                    name_lower = prop_name.lower()
                    if any(
                        kw in name_lower or kw in desc
                        for kw in ("password", "token", "api_key", "secret", "credit_card")
                    ):
                        findings.append(
                            Finding(
                                check_id="compliance.consent-flow-gaps",
                                severity=Severity.HIGH,
                                title=f"Form elicitation requests sensitive data: `{prop_name}`",
                                description="MCP spec requires URL mode (not form mode) for sensitive data like passwords and tokens.",
                                evidence={"field": prop_name, "mode": "form"},
                                remediation="Use URL mode elicitation for passwords, API keys, and other sensitive data.",
                            )
                        )
        return findings

    def _check_data_residency(self, events: list[McpEvent], config: EvaluatorConfig, skipped: list[SkippedCheck]) -> list[Finding]:
        findings: list[Finding] = []
        expected_regions = config.extra.get("expected_regions", [])
        if not expected_regions:
            skipped.append(SkippedCheck(
                check_id="compliance.data-residency-signals",
                reason="no expected_regions configured",
            ))
            return findings

        region_patterns = [
            re.compile(r"(?:us|eu|ap|sa|ca|af|me)-(?:east|west|south|north|central|northeast|southeast)-\d"),
            re.compile(r"(?:us-east|us-west|europe-west|asia-east|asia-south)\d*"),
        ]

        unexpected_regions: set[str] = set()
        for event in events:
            if not (event.is_response and event.result):
                continue
            content = event.result.get("content", [])
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text = item.get("text", "")
                for rp in region_patterns:
                    for match in rp.finditer(text):
                        region = match.group()
                        if region not in expected_regions:
                            unexpected_regions.add(region)

        if unexpected_regions:
            findings.append(
                Finding(
                    check_id="compliance.data-residency-signals",
                    severity=Severity.MEDIUM,
                    title=f"Tool responses reference unexpected regions: {', '.join(sorted(unexpected_regions))}",
                    description=f"Expected regions: {expected_regions}. Found: {sorted(unexpected_regions)}.",
                    evidence={
                        "unexpected": sorted(unexpected_regions),
                        "expected": expected_regions,
                    },
                    remediation="Verify data is not being transferred to unexpected geographic regions.",
                )
            )
        return findings
