"""Composability dimension evaluators."""

from __future__ import annotations

import json
import re
from collections import defaultdict

from reviewmymcp.evaluators.base import (
    EvaluatorConfig,
    EvaluatorResult,
    Finding,
    Severity,
    SkippedCheck,
)
from reviewmymcp.ingest.schema import McpEvent, ServerMeta

RETRYABILITY_HINTS = ("retry", "try again", "resolve", "verify")
SPECIFIC_FAILURE_HINTS = (
    "api error",
    "does not exist",
    "invalid",
    "missing",
    "not found",
    "permission",
    "rate limit",
    "timeout",
    "unauthorized",
    "forbidden",
)


class ComposabilityEvaluator:
    dimension: str = "composability"

    def evaluate(
        self,
        events: list[McpEvent],
        server_meta: ServerMeta,
        config: EvaluatorConfig,
    ) -> EvaluatorResult:
        findings: list[Finding] = []
        skipped: list[SkippedCheck] = []
        findings.extend(self._check_error_recovery(events))
        findings.extend(self._check_idempotency(events, skipped))
        findings.extend(self._check_chained_calls(events))
        findings.extend(self._check_concurrency(events, skipped))
        findings.extend(self._check_programmatic_readiness(events))

        return EvaluatorResult(
            dimension=self.dimension,
            checks_run=[
                "composability.error-recovery-surface",
                "composability.idempotency-violation",
                "composability.chained-call-failure",
                "composability.concurrency-safety",
                "composability.programmatic-readiness",
            ],
            findings=findings,
            checks_skipped=skipped,
        )

    def _check_error_recovery(self, events: list[McpEvent]) -> list[Finding]:
        """Check if error responses give enough info for recovery."""
        findings: list[Finding] = []
        error_responses = [
            e for e in events
            if e.is_response and not e.is_probe and (e.is_error or (e.result and e.result.get("isError")))
        ]
        if not error_responses:
            return findings

        ambiguous = 0
        ambiguous_examples: list[str] = []
        for event in error_responses:
            text = ""
            if event.error:
                text = event.error.get("message", "")
            elif event.result:
                content = event.result.get("content", [])
                if content and isinstance(content[0], dict):
                    text = content[0].get("text", "")

            text_lower = text.lower().strip()
            is_ambiguous = (
                not _has_retryability_hint(text_lower)
                and not _has_specific_failure_reason(text_lower)
            )
            if is_ambiguous:
                ambiguous += 1
                if text and text not in ambiguous_examples and len(ambiguous_examples) < 3:
                    ambiguous_examples.append(text)

        if error_responses and ambiguous / len(error_responses) > 0.5:
            total = len(error_responses)
            pct = ambiguous * 100 // total
            findings.append(
                Finding(
                    check_id="composability.error-recovery-surface",
                    severity=Severity.HIGH,
                    title=f"{ambiguous}/{total} error responses ({pct}%) are ambiguous",
                    description=f"{ambiguous} of {total} error responses ({pct}%) do not indicate whether the error is retryable or what went wrong.",
                    evidence={
                        "ambiguous_count": ambiguous,
                        "total_errors": total,
                        "examples": ambiguous_examples,
                    },
                    remediation="Include retryability hints and specific failure reasons in error responses.",
                )
            )
        return findings

    def _check_idempotency(self, events: list[McpEvent], skipped: list[SkippedCheck]) -> list[Finding]:
        """Check if repeated identical calls produce different results."""
        findings: list[Finding] = []
        requests = {
            e.event_id: e
            for e in events
            if e.is_request and e.method == "tools/call" and not e.is_probe
        }
        responses = [e for e in events if e.is_response and e.request_event_id in requests]

        call_results: dict[str, list[dict]] = defaultdict(list)
        for resp in responses:
            req = requests.get(resp.request_event_id, None)
            if not req or not req.params:
                continue
            key = json.dumps(
                {"name": req.params.get("name"), "args": req.params.get("arguments")},
                sort_keys=True,
            )
            call_results[key].append({"result": resp.result, "is_error": resp.is_error})

        skipped_groups = 0
        for key, results in call_results.items():
            if len(results) < 2:
                skipped_groups += 1
                continue
            success_results = [r["result"] for r in results if not r["is_error"] and r["result"]]
            if len(success_results) < 2:
                skipped_groups += 1
                continue
            structures = [json.dumps(sorted(r.keys()) if isinstance(r, dict) else r) for r in success_results]
            unique = set(structures)
            if len(unique) > 1:
                call_info = json.loads(key)
                findings.append(
                    Finding(
                        check_id="composability.idempotency-violation",
                        severity=Severity.HIGH,
                        title=f"Tool `{call_info.get('name')}` returns different results for identical calls",
                        description=f"Tool called {len(results)} times with identical arguments produced {len(unique)} distinct response structures.",
                        evidence={
                            "tool": call_info.get("name"),
                            "call_count": len(results),
                            "unique_structures": len(unique),
                        },
                        remediation="Ensure tools are idempotent or clearly document non-idempotent behavior.",
                        affected_entity=call_info.get("name", ""),
                    )
                )
        if skipped_groups:
            skipped.append(SkippedCheck(
                check_id="composability.idempotency-violation",
                reason=f"fewer than 2 repeated call groups with 2+ successes ({skipped_groups} group(s) skipped)",
            ))
        return findings

    def _check_chained_calls(self, events: list[McpEvent]) -> list[Finding]:
        """Check if tool outputs can be used as inputs to subsequent tools."""
        findings: list[Finding] = []
        requests = sorted(
            [e for e in events if e.is_request and e.method == "tools/call" and not e.is_probe],
            key=lambda e: e.timestamp,
        )
        responses = {e.request_event_id: e for e in events if e.is_response}

        sessions: dict[str | None, list[McpEvent]] = defaultdict(list)
        for req in requests:
            sessions[req.session_id].append(req)

        for session_reqs in sessions.values():
            for i in range(len(session_reqs) - 1):
                req_a = session_reqs[i]
                req_b = session_reqs[i + 1]
                resp_a = responses.get(req_a.event_id)
                if not resp_a or resp_a.is_error or not resp_a.result:
                    continue
                if resp_a.result.get("isError"):
                    continue
                if not req_b.params:
                    continue
                b_args = req_b.params.get("arguments", {})
                if not isinstance(b_args, dict):
                    continue
                resp_b = responses.get(req_b.event_id)
                if resp_b and resp_b.is_error:
                    err_text = ""
                    if resp_b.error:
                        err_text = resp_b.error.get("message", "")
                    elif resp_b.result:
                        content = resp_b.result.get("content", [])
                        if content and isinstance(content[0], dict):
                            err_text = content[0].get("text", "")
                    if "type" in err_text.lower() or "invalid" in err_text.lower():
                        name_a = req_a.params.get("name", "") if req_a.params else ""
                        name_b = req_b.params.get("name", "") if req_b.params else ""
                        findings.append(
                            Finding(
                                check_id="composability.chained-call-failure",
                                severity=Severity.MEDIUM,
                                title=f"Chained call from `{name_a}` to `{name_b}` failed with type/validation error",
                                description=f"Tool `{name_b}` called after `{name_a}` failed with: {err_text[:200]}",
                                evidence={
                                    "tool_a": name_a,
                                    "tool_b": name_b,
                                    "error": err_text[:500],
                                },
                                remediation="Ensure output formats of upstream tools match input expectations of downstream tools.",
                                affected_entity=f"{name_a} -> {name_b}",
                            )
                        )
                        break
        return findings

    def _check_concurrency(self, events: list[McpEvent], skipped: list[SkippedCheck]) -> list[Finding]:
        """Check error rate difference between concurrent and sequential calls."""
        findings: list[Finding] = []
        requests = {
            e.event_id: e
            for e in events
            if e.is_request and e.method == "tools/call" and not e.is_probe
        }
        responses = {e.request_event_id: e for e in events if e.is_response and e.request_event_id in requests}

        tool_calls: dict[str, list[tuple[McpEvent, McpEvent]]] = defaultdict(list)
        for req_id, req in requests.items():
            resp = responses.get(req_id)
            if resp and req.params:
                tool_calls[req.params.get("name", "")].append((req, resp))

        skipped_tools = 0
        for tool_name, pairs in tool_calls.items():
            if len(pairs) < 5:
                skipped_tools += 1
                continue
            pairs.sort(key=lambda p: p[0].timestamp)
            concurrent_errors = 0
            concurrent_total = 0
            sequential_errors = 0
            sequential_total = 0

            for i, (req, resp) in enumerate(pairs):
                is_concurrent = False
                for j, (other_req, other_resp) in enumerate(pairs):
                    if i == j:
                        continue
                    if other_req.timestamp <= req.timestamp and other_resp.timestamp >= req.timestamp:
                        is_concurrent = True
                        break

                is_err = resp.is_error or (resp.result and resp.result.get("isError"))
                if is_concurrent:
                    concurrent_total += 1
                    if is_err:
                        concurrent_errors += 1
                else:
                    sequential_total += 1
                    if is_err:
                        sequential_errors += 1

            if concurrent_total >= 3 and sequential_total >= 3:
                conc_rate = concurrent_errors / concurrent_total
                seq_rate = sequential_errors / sequential_total if sequential_total else 0
                if conc_rate > seq_rate * 2 and conc_rate > 0.2:
                    findings.append(
                        Finding(
                            check_id="composability.concurrency-safety",
                            severity=Severity.MEDIUM,
                            title=f"Tool `{tool_name}` has higher error rate under concurrency",
                            description=f"Concurrent error rate: {conc_rate:.0%} ({concurrent_errors}/{concurrent_total}) vs sequential: {seq_rate:.0%} ({sequential_errors}/{sequential_total}).",
                            evidence={
                                "tool": tool_name,
                                "concurrent_error_rate": conc_rate,
                                "sequential_error_rate": seq_rate,
                            },
                            remediation="Investigate race conditions or shared resource contention.",
                            affected_entity=tool_name,
                        )
                    )
            else:
                skipped_tools += 1
        if skipped_tools:
            skipped.append(SkippedCheck(
                check_id="composability.concurrency-safety",
                reason=f"skipped {skipped_tools} tool(s): need >= 5 pairs and >= 3 concurrent+sequential samples each",
            ))
        return findings

    def _check_programmatic_readiness(self, events: list[McpEvent]) -> list[Finding]:
        """Check if tool outputs are structured for programmatic use."""
        findings: list[Finding] = []
        requests = {
            e.event_id: e
            for e in events
            if e.is_request and e.method == "tools/call" and not e.is_probe
        }
        responses = [e for e in events if e.is_response and e.request_event_id in requests and not e.is_error]

        tool_outputs: dict[str, list[str]] = defaultdict(list)
        for resp in responses:
            req = requests.get(resp.request_event_id)
            if not req or not req.params or not resp.result:
                continue
            if resp.result.get("isError"):
                continue
            content = resp.result.get("content", [])
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    tool_outputs[req.params.get("name", "")].append(item.get("text", ""))

        for tool_name, outputs in tool_outputs.items():
            if len(outputs) < 3:
                continue
            prose_count = 0
            for output in outputs:
                try:
                    json.loads(output)
                except (json.JSONDecodeError, TypeError):
                    if len(output) > 100 and not any(c in output for c in ("{", "[", "|")):
                        prose_count += 1

            if prose_count > len(outputs) * 0.5:
                findings.append(
                    Finding(
                        check_id="composability.programmatic-readiness",
                        severity=Severity.MEDIUM,
                        title=f"Tool `{tool_name}` returns prose instead of structured data",
                        description=f"{prose_count} of {len(outputs)} responses appear to be unstructured text, making programmatic tool calling difficult.",
                        evidence={
                            "tool": tool_name,
                            "prose_count": prose_count,
                            "total": len(outputs),
                        },
                        remediation="Return structured JSON data that can be programmatically consumed by agent code.",
                        affected_entity=tool_name,
                    )
                )
        return findings


def _has_retryability_hint(text: str) -> bool:
    return any(hint in text for hint in RETRYABILITY_HINTS)


def _has_specific_failure_reason(text: str) -> bool:
    if any(hint in text for hint in SPECIFIC_FAILURE_HINTS):
        return True
    if re.search(r"\b(?:http\s*)?\d{3}\b", text):
        return True
    return bool(re.search(r"\b(?:parameter|field|argument|input)\s+[`'\"]?[\w.-]+", text))
